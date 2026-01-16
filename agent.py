from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage

from tools import get_all_tools
from database import get_db_pool

# --- Graph State ---
class State(TypedDict):
    messages: Annotated[list, add_messages]

# --- System Prompt ---
SYSTEM_PROMPT = """你是"幻影科技"公司的智能员工助手。

请遵守以下规则：
1. 当你使用工具获取信息后，必须用简洁的自然语言回答用户的问题。
2. 不要直接复述工具返回的原始内容，而是提炼关键信息。
3. 回答要友好、简洁、直接。
4. **格式警告**: 当工具参数需要 JSON 字符串时（如 calendars_info），**必须**确保内部使用双引号 `"` 包裹键和值（例如 `[{"key": "value"}]`），严禁使用单引号 `'`，否则会导致系统崩溃。
5. **图片生成**: 当用户要求"配图"、"插图"、"画一张图"或提到 Nano Banana 时，请调用 `generate_illustration` 工具。工具成功后会返回确认消息，你只需要简单告诉用户"图片已生成"即可。**重要：不要自己构造任何图片标签如 `![](...)` 或 HTML `<img>` 标签，系统会自动显示图片。**
关于日历工具的使用：
- **步骤**: 查询日程前，**必须先调用** `get_calendars_info` 获取日历列表。
- 然后调用 `search_events`，将 `get_calendars_info` 的完整返回值（保持原样，确保双引号）作为 `calendars_info` 参数传入。
- search_events 工具可以通过 query 参数搜索事件，可以通过 min_datetime 和 max_datetime 过滤时间范围
- 对于"明天"、"下周"等时间相关的查询，请务必自行计算好 'YYYY-MM-DD HH:MM:SS' 格式的 min_datetime 和 max_datetime 传给工具
- 可以用 get_current_datetime 先获取当前时间，再进行计算
- 将查询结果用友好的中文格式呈现，如"您有以下安排：..."
- 如果没有日程，回复"您没有找到相关日程"
"""

def get_graph(_version="v6.0"):
    """初始化图结构"""
    print(f"🔄 正在初始化 LangGraph... (Version: {_version})")

    # --- 模型 ---
    # gemini-2.5-pro 更擅长理解复杂指令和工具调用
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro")

    # --- 工具 ---
    tools = get_all_tools()
    llm_with_tools = llm.bind_tools(tools)

    # --- 节点逻辑 ---
    def chatbot(state: State):
        messages = state["messages"]
        # 确保系统提示词在消息列表最前面
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        
        # 🛡️ 防止历史消息过长导致 token 溢出
        # 保留系统提示词 + 最近 50 条消息
        MAX_HISTORY = 50
        if len(messages) > MAX_HISTORY + 1:  # +1 是系统提示词
            messages = [messages[0]] + list(messages[-(MAX_HISTORY):])
        
        return {"messages": [llm_with_tools.invoke(messages)]}

    # --- 构建图 ---
    graph_builder = StateGraph(State)
    graph_builder.add_node("chatbot", chatbot)
    graph_builder.add_node("tools", ToolNode(tools=tools))

    graph_builder.add_edge(START, "chatbot")
    graph_builder.add_conditional_edges("chatbot", tools_condition)
    graph_builder.add_edge("tools", "chatbot")

    # 编译图 (带 Postgres 记忆)
    pool = get_db_pool()
    checkpointer = PostgresSaver(pool)
    
    try:
        # 首次运行时创建必要的表 (如果不存在)
        checkpointer.setup()
    except Exception as e:
        print(f"Warning: Failed to setup Postgres checkpointer: {e}")
    
    graph = graph_builder.compile(checkpointer=checkpointer)
    return graph
