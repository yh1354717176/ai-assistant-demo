import os
import base64
from dotenv import load_dotenv

# 从 .env 文件加载环境变量
load_dotenv()

import streamlit as st
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_core.tools import tool
from langchain_core.tools.retriever import create_retriever_tool
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from typing import Annotated, TypedDict
# 新增这一行，引入 DuckDuckGo 搜索工具
from langchain_community.tools import DuckDuckGoSearchRun
# 新增这一行
from langchain_community.agent_toolkits import GmailToolkit
from langchain_google_community import CalendarToolkit 
# from googleapiclient.discovery import build # Removed as we use Toolkit
# import datetime # Removed as we use Toolkit (standard lib datetime might be needed by other parts, but let's check. lines 307 usages import it locally or use global? Global usage was added by me. I'll remove it. If other code needs it, I'll keep it. Wait, line 307 imports it inside tool_calls logic? No, line 307 is "import datetime". So global import is safe to remove if that was the only global one.)


# 1. 恢复环境变量 (API Key & Tracing)
# 只要 Secrets 里有的配置，都自动加载到系统环境变量中
# 这样不仅支持 Google Key，也支持 LangSmith 的配置
# 注意：排除 JSON 格式的 secrets
json_secrets = ["credentials_json", "token_json"]
for key in st.secrets:
    if key not in json_secrets:
        os.environ[key] = st.secrets[key]

import json  # 提前导入 json 模块

# 恢复 credentials.json
if "credentials_json" in st.secrets:
    cred_content = st.secrets["credentials_json"].strip()  # 去除前后空白和换行符
    try:
        json.loads(cred_content)  # 验证是否为有效 JSON
        with open("credentials.json", "w") as f:
            f.write(cred_content)
    except json.JSONDecodeError as e:
        st.error(f"❌ credentials_json 格式错误: {e}")

# 恢复 token.json
if "token_json" in st.secrets:
    token_content = st.secrets["token_json"].strip()  # 去除前后空白和换行符
    # 验证 JSON 格式是否正确
    try:
        json.loads(token_content)  # 验证是否为有效 JSON
        with open("token.json", "w") as f:
            f.write(token_content)
    except json.JSONDecodeError as e:
        st.error(f"❌ token_json 格式错误: {e}")

# ☁️ 云端部署补丁 (End)

# ==========================================
# 1. 页面配置 & 标题
# ==========================================
st.set_page_config(page_title="幻影科技 AI 助手", page_icon="🤖")
st.title("🤖 幻影科技员工助手 (Agent版 v5.0)")
st.caption("我是由 LangGraph 驱动的智能体，能查文档，也能算工资。")


# ==========================================
# 2. 缓存资源 (避免每次刷新都重连数据库)
# ==========================================
@st.cache_resource
def get_graph(_version="v5.1"):  # 修改版本号强制刷新缓存
    """初始化图结构，只执行一次"""
    print(f"🔄 正在初始化 LangGraph... (Cache Version: {_version})")

    # --- 模型与数据库 ---
    llm = ChatGoogleGenerativeAI(model="gemini-3-flash-preview")  # 使用更强的模型
    embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")

    # Qdrant 连接配置 (支持本地和云端)
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
    
    if qdrant_api_key:
        # 使用 Qdrant Cloud
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    else:
        # 使用本地 Qdrant
        client = QdrantClient(url=qdrant_url)
    
    vectorstore = QdrantVectorStore(
        client=client,
        collection_name="knowledge_base",
        embedding=embeddings
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

    # --- 工具定义 ---
    retriever_tool = create_retriever_tool(
        retriever,
        name="search_company_policy",
        description="查询关于'幻影科技'的公司规定、福利等。"
    )

    @tool
    def calculate_bonus(salary: int) -> str:
        """根据工资计算年终奖。"""
        bonus = salary * 0.2
        return f"【系统计算】根据您的工资，年终奖应为 {bonus} 元。"

    # 初始化搜索工具
    search_tool = DuckDuckGoSearchRun()

    # 初始化 Gmail 工具箱
    # 它会自动读取文件夹里的 token.json
    gmail_toolkit = GmailToolkit()
    
    # 初始化 Calendar 工具箱
    # 直接从 token.json 加载已认证的凭证，避免在云端触发 OAuth 流程
    from google.oauth2.credentials import Credentials
    calendar_creds = Credentials.from_authorized_user_file(
        "token.json",
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    calendar_toolkit = CalendarToolkit(credentials=calendar_creds)

    tools = [retriever_tool, calculate_bonus, search_tool] + gmail_toolkit.get_tools() + calendar_toolkit.get_tools()
    llm_with_tools = llm.bind_tools(tools)

    # --- 构建图 ---
    class State(TypedDict):
        messages: Annotated[list, add_messages]

    # 系统提示词：指导 AI 的行为
    from langchain_core.messages import SystemMessage
    SYSTEM_PROMPT = """你是"幻影科技"公司的智能员工助手。

请遵守以下规则：
1. 当你使用工具获取信息后，必须用简洁的自然语言回答用户的问题。
2. 不要直接复述工具返回的原始内容，而是提炼关键信息。
3. 回答要友好、简洁、直接。

关于日历工具的使用：
- 当用户询问"日程"、"安排"、"会议"时，使用 search_events 工具查询日历事件
- search_events 工具可以通过 query 参数搜索事件，可以通过 min_datetime 和 max_datetime 过滤时间范围
- 对于"明天"、"下周"等时间相关的查询，请务必自行计算好 'YYYY-MM-DD HH:MM:SS' 格式的 min_datetime 和 max_datetime 传给工具
- 可以用 get_current_datetime 先获取当前时间，再进行计算
- 将查询结果用友好的中文格式呈现，如"您有以下安排：..."
- 如果没有日程，回复"您没有找到相关日程"
"""

    def chatbot(state: State):
        messages = state["messages"]
        # 确保系统提示词在消息列表最前面
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        return {"messages": [llm_with_tools.invoke(messages)]}

    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("tools", ToolNode(tools=tools))

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")

    # 编译图 (带记忆)
    memory = MemorySaver()
    graph = graph_builder.compile(checkpointer=memory)
    return graph


# 加载图
graph = get_graph()

# ==========================================
# 3. 会话状态管理 (Session State)
# ==========================================
# Streamlit 每次交互都会重跑代码，所以要用 session_state 存聊天记录

if "messages" not in st.session_state:
    st.session_state["messages"] = []

# 存储工具调用历史
if "tool_calls" not in st.session_state:
    st.session_state["tool_calls"] = []

if "thread_id" not in st.session_state:
    # 给每个用户生成一个随机 ID，或者固定一个方便测试
    import uuid

    st.session_state["thread_id"] = str(uuid.uuid4())

# 存储上传的图片
if "uploaded_image" not in st.session_state:
    st.session_state["uploaded_image"] = None

# ==========================================
# 4. 渲染聊天界面
# ==========================================

# 显示历史消息
for msg in st.session_state["messages"]:
    # 区分是用户还是 AI
    if msg["role"] == "user":
        st.chat_message("user").write(msg["content"])
    else:
        st.chat_message("assistant").write(msg["content"])

# 处理用户输入
if user_input := st.chat_input("请输入问题（例如：公司吉祥物叫什么？）"):
    # 1. 显示用户消息
    st.chat_message("user").write(user_input)
    
    # 如果有上传的图片，也显示出来
    if st.session_state["uploaded_image"]:
        st.chat_message("user").image(st.session_state["uploaded_image"], caption="📷 上传的图片", width=300)
    
    st.session_state["messages"].append({"role": "user", "content": user_input})

    # 2. 调用 LangGraph
    config = {"configurable": {"thread_id": st.session_state["thread_id"]}}

    # 显示一个"思考中"的转圈圈
    with st.spinner("🧠 Agent 正在思考并调用工具..."):
        
        # === 构建多模态消息 ===
        message_content = []
        
        # 添加文本部分
        message_content.append({"type": "text", "text": user_input})
        
        # 如果有图片，转换为 base64 并添加
        if st.session_state["uploaded_image"]:
            image_bytes = st.session_state["uploaded_image"].getvalue()
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            
            # 获取图片 MIME 类型
            image_type = st.session_state["uploaded_image"].type  # 如 "image/png"
            
            message_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_type};base64,{image_base64}"
                }
            })
        
        # 获取最终响应
        response = graph.invoke(
            {"messages": [HumanMessage(content=message_content)]},
            config=config
        )
        
        # 清空已上传的图片（发送后就清除）
        st.session_state["uploaded_image"] = None

        # 提取 AI 的最后一条回复
        ai_message = response["messages"][-1]
        ai_content = ai_message.content

        # 简单处理：如果不是字符串，强制转为字符串
        if not isinstance(ai_content, str):
            ai_content = str(ai_content)

        # === 提取工具调用信息 ===
        from langchain_core.messages import ToolMessage
        import datetime
        
        tool_calls_in_turn = []  # 本轮对话中的工具调用
        messages = response.get("messages", [])
        
        for msg in messages:
            # 查找 AI 消息中的 tool_calls
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_info = {
                        "name": tc.get("name", "未知工具"),
                        "args": tc.get("args", {}),
                        "id": tc.get("id", ""),
                        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                    }
                    tool_calls_in_turn.append(tool_info)
            
            # 查找工具返回的结果
            if isinstance(msg, ToolMessage):
                # 找到对应的工具调用并添加结果
                for tc_info in tool_calls_in_turn:
                    if tc_info["id"] == msg.tool_call_id:
                        tc_info["result"] = str(msg.content)[:200]  # 截取前200字符
                        break
        
        # 存储本轮工具调用
        if tool_calls_in_turn:
            st.session_state["tool_calls"].append({
                "user_query": user_input,
                "tools": tool_calls_in_turn
            })

        # === 智能回溯机制 ===
        # 既然最后一条可能是空的，我们倒序查找最后一条有内容的消息
        ai_content = "⚠️ 未能获取有效回答"
        
        for msg in reversed(messages):
            # 跳过用户发送的消息
            if isinstance(msg, HumanMessage):
                continue
            # 找到有内容的消息（无论是 AI 说的，还是工具查到的）
            if msg.content and str(msg.content).strip():
                ai_content = msg.content
                
                # 处理 content 是列表的情况（Gemini 有时会返回这种格式）
                if isinstance(ai_content, list):
                    text_parts = []
                    for item in ai_content:
                        if isinstance(item, dict) and "text" in item:
                            text_parts.append(item["text"])
                        elif isinstance(item, str):
                            text_parts.append(item)
                    ai_content = "\n".join(text_parts) if text_parts else str(ai_content)
                
                # 如果是工具消息，说明 AI 偷懒没复述，我们可以加个标注
                if "ToolMessage" in type(msg).__name__:
                    ai_content = f"【系统检索结果】\n{ai_content}"
                
                # 确保最终是字符串
                if not isinstance(ai_content, str):
                    ai_content = str(ai_content)
                break

    # 3. 显示 AI 回复
    with st.chat_message("assistant"):
        st.write(ai_content)

    st.session_state["messages"].append({"role": "assistant", "content": ai_content})

# ==========================================
# 5. 侧边栏 - 图片上传 & 工具调用历史
# ==========================================
with st.sidebar:
    # --- 图片上传区域 ---
    st.header("🖼️ 图片上传")
    st.caption("上传图片让 AI 帮你分析")
    
    uploaded_file = st.file_uploader(
        "选择图片",
        type=["jpg", "jpeg", "png", "gif", "webp"],
        help="支持 JPG、PNG、GIF、WebP 格式"
    )
    
    if uploaded_file:
        st.session_state["uploaded_image"] = uploaded_file
        st.image(uploaded_file, caption="📷 待发送的图片", use_container_width=True)
        st.success("✅ 图片已准备好，请在下方输入问题后一起发送！")
        
        if st.button("❌ 取消上传"):
            st.session_state["uploaded_image"] = None
            st.rerun()
    
    st.divider()
    
    # --- 工具调用追踪区域 ---
    st.header("🔧 工具调用追踪")
    st.caption("查看 AI 在每次对话中调用了哪些工具")
    
    if st.button("🗑️ 清空历史"):
        st.session_state["tool_calls"] = []
        st.rerun()
    
    if not st.session_state["tool_calls"]:
        st.info("暂无工具调用记录，开始对话后将在这里显示。")
    else:
        # 倒序显示，最新的在最上面
        for i, call_record in enumerate(reversed(st.session_state["tool_calls"])):
            idx = len(st.session_state["tool_calls"]) - i
            with st.expander(f"🔹 对话 #{idx}: {call_record['user_query'][:30]}...", expanded=(i == 0)):
                for tool in call_record["tools"]:
                    st.markdown(f"**🛠️ 工具名称:** `{tool['name']}`")
                    st.markdown(f"**⏰ 调用时间:** {tool['timestamp']}")
                    
                    # 显示参数
                    if tool.get("args"):
                        st.markdown("**📥 输入参数:**")
                        st.json(tool["args"])
                    
                    # 显示结果
                    if tool.get("result"):
                        st.markdown("**📤 返回结果:**")
                        st.code(tool["result"], language=None)
                    
                    st.divider()



