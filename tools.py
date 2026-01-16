import os
import streamlit as st
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_core.tools.retriever import create_retriever_tool
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.agent_toolkits import GmailToolkit
from langchain_google_community import CalendarToolkit
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from google.oauth2.credentials import Credentials

from image_store import get_image_store

@tool
def calculate_bonus(salary: int) -> str:
    """根据工资计算年终奖。"""
    bonus = salary * 0.2
    return f"【系统计算】根据您的工资，年终奖应为 {bonus} 元。"

@tool
def generate_illustration(prompt: str, config: RunnableConfig) -> str:
    """当你需要根据用户的描述生成图片、绘画、或者设计草图时，使用这个工具。
    输入应该是对画面内容的详细英文或中文描述。"""
    try:
        # 延迟导入
        from google import genai
        from google.genai import types
        import base64
        import os
        
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "❌ 错误：未找到 GOOGLE_API_KEY，无法生成图片。"

        client = genai.Client(api_key=api_key)
        
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash-exp',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['Text', 'Image']
                )
            )
            
            # 从响应中提取图片
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    img_data = part.inline_data.data
                    mime_type = part.inline_data.mime_type or 'image/png'
                    b64_data = base64.b64encode(img_data).decode('utf-8')
                    
                    # 优先从 Config 获取 context (Cross-thread safe)
                    thread_id = config.get("configurable", {}).get("thread_id")
                    
                    # Fallback to session_state if config is empty (Main thread dev mode)
                    if not thread_id and "thread_id" in st.session_state:
                        thread_id = st.session_state["thread_id"]

                    try:
                        import auth_service
                        if thread_id:
                            auth_service.save_image_to_db(thread_id, prompt, b64_data, mime_type)
                            print(f"✅ 图片已存储到数据库 app_images (Thread: {thread_id})")
                        else:
                            print(f"⚠️ 无法获取 thread_id，跳过 DB 存储")
                            
                    except Exception as db_e:
                        print(f"❌ 图片入库失败: {db_e}")

                    # 无论是否入库成功，都存一份到内存 Store，用于即时回显
                    store = get_image_store()
                    store.add({
                        'data': b64_data,
                        'mime_type': mime_type,
                        'prompt': prompt[:50]
                    })
                    
                    # 只返回简短消息给 LLM，避免 token 溢出
                    return f"✅ 图片已成功生成！（提示词：{prompt[:30]}...）图片将自动显示在对话中。"
            
            # 如果没有图片，返回文本响应
            text_parts = [p.text for p in response.candidates[0].content.parts if hasattr(p, 'text') and p.text]
            if text_parts:
                return f"⚠️ 模型返回了文字而非图片：\n{''.join(text_parts)}"
            
            return "❌ 生成成功但未返回图片数据。"
            
        except Exception as gemini_e:
            error_msg = str(gemini_e)
            # 检测是否是计费问题
            if "billed" in error_msg.lower() or "billing" in error_msg.lower():
                return "❌ **需要启用 Google Cloud 计费**\n\n您的 API 账户目前是免费层级。Gemini/Imagen 图片生成功能需要在 Google AI Studio 或 Google Cloud 中启用计费。"
            return f"❌ 图片生成失败: {gemini_e}"
            
    except Exception as e:
        return f"❌ 生成图片出错: {str(e)}"

def get_all_tools():
    """初始化并返回所有可用工具"""
    
    # 1. 知识库检索工具
    embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001")
    
    # Qdrant 连接配置
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
    
    if qdrant_api_key:
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
    else:
        client = QdrantClient(url=qdrant_url)
    
    vectorstore = QdrantVectorStore(
        client=client,
        collection_name="knowledge_base",
        embedding=embeddings
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": 2})
    
    retriever_tool = create_retriever_tool(
        retriever,
        name="search_company_policy",
        description="查询关于'幻影科技'的公司规定、福利等。"
    )

    # 2. 搜索工具
    search_tool = DuckDuckGoSearchRun()

    # 3. Calendar 工具
    try:
        if os.path.exists("token.json"):
            calendar_creds = Credentials.from_authorized_user_file("token.json")
            # Debug: Show scopes in sidebar to verify we have calendar permissions
            with st.sidebar:
                st.caption(f"🔧 Debug: Loaded Scopes: {calendar_creds.scopes}")
            calendar_toolkit = CalendarToolkit(credentials=calendar_creds)
            calendar_tools = calendar_toolkit.get_tools()
        else:
            print("Warning: token.json not found, Calendar tools disabled.")
            calendar_tools = []
    except Exception as e:
        print(f"Error loading Calendar tools: {e}")
        calendar_tools = []

    # 4. Gmail 工具
    try:
        gmail_toolkit = GmailToolkit()
        gmail_tools = gmail_toolkit.get_tools()
    except Exception as e:
        print(f"Error loading Gmail tools: {e}")
        gmail_tools = []

    # 组合所有工具
    tools = [
        retriever_tool, 
        calculate_bonus, 
        search_tool, 
        generate_illustration
    ] + calendar_tools + gmail_tools
    
    return tools
