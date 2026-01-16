import streamlit as st
import base64
import datetime
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

# 导入自定义模块
import config
from agent import get_graph
from image_store import get_image_store

# ==========================================
# 0. 初始化配置
# ==========================================
config.init_environment()

# ==========================================
# 1. 页面配置 & 标题
# ==========================================
st.set_page_config(page_title="幻影科技 AI 助手", page_icon="🤖")
st.title("🤖 幻影科技员工助手 (Agent版 v5.0)")
st.caption("我是由 LangGraph 驱动的智能体，能查文档，也能算工资。")

# ==========================================
# 2. 缓存资源
# ==========================================
# 加载图 (使用 @st.cache_resource 是在 agent.py 或外部不好做，
# 因为 get_graph 内部每次运行都要重新从 db 取 pool，但 pool 本身是 cached 的。
# 这里的 graph 对象本身应该被 cache 吗？
# graph 编译后是 stateless 的 (除了 checkpointer 连接)，可以 cache。
# 为了稳妥起见，我们在 agent.py 没有加 cache，在这里加。
# 但是 Streamlit 的 hash 可能会因为 graph 对象太复杂而失败。
# 让我们试着直接调用，因为 get_db_pool 和 get_image_store 都是 cached 的，
# 构建 graph 的开销主要在初始化 LLM 和 Tools，稍微有点大。
# 我们可以给 get_graph 加个简单的 cache wrapper。
@st.cache_resource
def get_cached_graph():
    return get_graph()

graph = get_cached_graph()
image_store = get_image_store()

# ==========================================
# 3. 会话状态管理 (Session State)
# ==========================================
if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "tool_calls" not in st.session_state:
    st.session_state["tool_calls"] = []

if "thread_id" not in st.session_state:
    import uuid
    st.session_state["thread_id"] = str(uuid.uuid4())

if "uploaded_image" not in st.session_state:
    st.session_state["uploaded_image"] = None

# 🔄 清除对话按钮
col1, col2 = st.columns([6, 1])
with col2:
    if st.button("🗑️ 清除对话"):
        import uuid
        st.session_state["messages"] = []
        st.session_state["tool_calls"] = []
        st.session_state["thread_id"] = str(uuid.uuid4())
        st.rerun()

# ==========================================
# 4. 渲染聊天界面
# ==========================================

# 显示历史消息
for msg in st.session_state["messages"]:
    if msg["role"] == "user":
        st.chat_message("user").write(msg["content"])
    else:
        with st.chat_message("assistant"):
            st.write(msg["content"])
            # 如果消息包含图片，显示图片
            if "images" in msg and msg["images"]:
                for img in msg["images"]:
                    try:
                        image_data = base64.b64decode(img['data'])
                        st.image(image_data, caption=f"🎨 {img.get('prompt', '生成的图片')}...", use_container_width=True)
                    except Exception as e:
                        st.error(f"图片加载失败: {e}")

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

    with st.spinner("🧠 Agent 正在思考并调用工具..."):
        
        # === 构建多模态消息 ===
        message_content = []
        message_content.append({"type": "text", "text": user_input})
        
        if st.session_state["uploaded_image"]:
            image_bytes = st.session_state["uploaded_image"].getvalue()
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")
            image_type = st.session_state["uploaded_image"].type
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
        
        st.session_state["uploaded_image"] = None

        # 提取 AI 的最后一条回复
        messages = response.get("messages", [])
        ai_message = messages[-1]
        ai_content = ai_message.content

        if not isinstance(ai_content, str):
            ai_content = str(ai_content)

        # === 提取工具调用信息 ===
        tool_calls_in_turn = []
        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_info = {
                        "name": tc.get("name", "未知工具"),
                        "args": tc.get("args", {}),
                        "id": tc.get("id", ""),
                        "timestamp": datetime.datetime.now().strftime("%H:%M:%S")
                    }
                    tool_calls_in_turn.append(tool_info)
            if isinstance(msg, ToolMessage):
                for tc_info in tool_calls_in_turn:
                    if tc_info["id"] == msg.tool_call_id:
                        tc_info["result"] = str(msg.content)[:200]
                        break
        
        if tool_calls_in_turn:
            st.session_state["tool_calls"].append({
                "user_query": user_input,
                "tools": tool_calls_in_turn
            })

        # === 智能回溯机制 ===
        ai_content = "⚠️ 未能获取有效回答"
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                continue
            if msg.content and str(msg.content).strip():
                ai_content = msg.content
                if isinstance(ai_content, list):
                    text_parts = []
                    for item in ai_content:
                        if isinstance(item, dict) and "text" in item:
                            text_parts.append(item["text"])
                        elif isinstance(item, str):
                            text_parts.append(item)
                    ai_content = "\n".join(text_parts) if text_parts else str(ai_content)
                
                if "ToolMessage" in type(msg).__name__:
                    ai_content = f"【系统检索结果】\n{ai_content}"
                
                if not isinstance(ai_content, str):
                    ai_content = str(ai_content)
                break

    # 3. 显示 AI 回复
    with st.chat_message("assistant"):
        st.markdown(ai_content)
        
        # 获取生成的图片
        generated_imgs = image_store.get_and_clear()
        print(f"🔍 检查图片: ImageStore 中有 {len(generated_imgs)} 张图片")
        
        if generated_imgs:
            st.divider()
            st.caption("🎨 生成的图片：")
            for img in generated_imgs:
                try:
                    image_data = base64.b64decode(img['data'])
                    st.image(image_data, caption=f"{img['prompt']}...", use_container_width=True)
                except Exception as img_e:
                    st.error(f"图片显示失败: {img_e}")
                    
    # 保存消息到历史记录
    st.session_state["messages"].append({
        "role": "assistant", 
        "content": ai_content,
        "images": generated_imgs if generated_imgs else []
    })

# ==========================================
# 5. 侧边栏
# ==========================================
with st.sidebar:
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
    
    st.header("🔧 工具调用追踪")
    st.caption("查看 AI 在每次对话中调用了哪些工具")
    
    if st.button("🗑️ 清空历史"):
        st.session_state["tool_calls"] = []
        st.rerun()
    
    if not st.session_state["tool_calls"]:
        st.info("暂无工具调用记录，开始对话后将在这里显示。")
    else:
        for i, call_record in enumerate(reversed(st.session_state["tool_calls"])):
            idx = len(st.session_state["tool_calls"]) - i
            with st.expander(f"🔹 对话 #{idx}: {call_record['user_query'][:30]}...", expanded=(i == 0)):
                for tool in call_record["tools"]:
                    st.markdown(f"**🛠️ 工具名称:** `{tool['name']}`")
                    st.markdown(f"**⏰ 调用时间:** {tool['timestamp']}")
                    if tool.get("args"):
                        st.markdown("**📥 输入参数:**")
                        st.json(tool["args"])
                    if tool.get("result"):
                        st.markdown("**📤 返回结果:**")
                        st.code(tool["result"], language=None)
                    st.divider()
