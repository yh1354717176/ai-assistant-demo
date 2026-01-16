import streamlit as st
import base64
import datetime
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from streamlit_cookies_controller import CookieController

# 导入自定义模块
import config
import auth_service
import database
from agent import get_graph
from image_store import get_image_store

# ==========================================
# 0. 初始化配置 & 数据库
# ==========================================
config.init_environment()
st.set_page_config(page_title="幻影科技 AI 助手", page_icon="🤖", layout="wide")

# 初始化表结构 (Safe to run multiple times)
try:
    database.init_db_schema()
except Exception as e:
    print(f"DB Init Warning: {e}")

# ==========================================
# 1. Session State & Cookie 管理
# ==========================================
# 初始化 Cookie 控制器
controller = CookieController()

# 尝试从 Cookies 恢复登录状态
cookies = controller.getAll()

# 用户登录状态
if "user_id" not in st.session_state:
    # 检查 Cookie 是否有 user_id
    cookie_user_id = cookies.get("user_id")
    cookie_username = cookies.get("username")
    
    if cookie_user_id and cookie_username:
        st.session_state["user_id"] = int(cookie_user_id)
        st.session_state["username"] = cookie_username
    else:
        st.session_state["user_id"] = None
        st.session_state["username"] = None

# 当前对话 Thread ID
query_params = st.query_params
url_thread_id = query_params.get("thread_id", None)

if "thread_id" not in st.session_state:
    # 优先使用 URL 中的 thread_id，否则暂为 None (等待登录或创建新对话)
    st.session_state["thread_id"] = url_thread_id 

if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "tool_calls" not in st.session_state:
    st.session_state["tool_calls"] = []

if "uploaded_image" not in st.session_state:
    st.session_state["uploaded_image"] = None

@st.cache_resource
def get_cached_graph():
    return get_graph()

graph = get_cached_graph()
image_store = get_image_store() # Memory fallback

# ==========================================
# 2. 认证逻辑 (UI)
# ==========================================

def login_page():
    st.title("🔐 登录 / 注册")
    
    tab1, tab2 = st.tabs(["登录", "注册"])
    
    with tab1:
        with st.form("login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            submitted = st.form_submit_button("登录")
            if submitted:
                if not username or not password:
                    st.error("请输入用户名和密码")
                else:
                    uid, msg = auth_service.login_user(username, password)
                    if uid:
                        st.session_state["user_id"] = uid
                        st.session_state["username"] = username
                        # 设置 Cookie (有效期 7 天)
                        controller.set("user_id", str(uid), max_age=604800)
                        controller.set("username", username, max_age=604800)
                        st.success(f"{msg}，正在跳转...")
                        # 强制刷新以应用 Cookie
                        st.rerun()
                    else:
                        st.error(msg)
    
    with tab2:
        with st.form("register_form"):
            new_user = st.text_input("设置用户名")
            new_pass = st.text_input("设置密码", type="password")
            submitted = st.form_submit_button("注册")
            if submitted:
                if not new_user or not new_pass:
                    st.error("请输入用户名和密码")
                else:
                    uid, msg = auth_service.register_user(new_user, new_pass)
                    if uid:
                        st.success(f"注册成功！请切换到登录标签页进行登录。")
                    else:
                        st.error(msg)

# ==========================================
# 3. 主应用逻辑
# ==========================================

def show_chat_interface():
    # --- Sidebar: User Info & History ---
    with st.sidebar:
        st.header(f"👤 {st.session_state['username']}")
        if st.button("退出登录"):
            st.session_state["user_id"] = None
            st.session_state["username"] = None
            st.session_state["thread_id"] = None
            st.session_state["messages"] = []
            # 清除 Cookies
            controller.remove("user_id")
            controller.remove("username")
            st.rerun()
        
        st.divider()
        st.subheader("🗂️ 对话历史")
        
        # 新建对话按钮
        if st.button("➕ 新建对话", use_container_width=True):
            new_tid = auth_service.create_new_thread(st.session_state["user_id"], title="新对话")
            st.session_state["thread_id"] = new_tid
            st.session_state["messages"] = []
            st.query_params["thread_id"] = new_tid
            st.rerun()
            
        # 历史列表
        threads = auth_service.get_user_threads(st.session_state["user_id"])
        if threads:
            for tid, title, updated_at in threads:
                tid_str = str(tid)
                # 简单样式区分当前选中
                label = f"{'🟢' if tid_str == st.session_state['thread_id'] else '📄'} {title or '未命名对话'}"
                if st.button(label, key=tid_str, use_container_width=True):
                    st.session_state["thread_id"] = tid_str
                    st.session_state["messages"] = [] # 清空当前 UI，等待 reload
                    st.query_params["thread_id"] = tid_str
                    st.rerun()
        else:
            st.caption("暂无历史记录")

        st.divider()
        # 图片上传 & 工具追踪 (Keep existing sidebar features)
        st.header("🖼️ 图片上传")
        uploaded_file = st.file_uploader("选择图片", type=["jpg", "png", "webp"])
        if uploaded_file:
            st.session_state["uploaded_image"] = uploaded_file
            st.image(uploaded_file, caption="待发送", use_container_width=True)
            if st.button("❌ 取消"):
                st.session_state["uploaded_image"] = None
                st.rerun()

    # --- Main Chat Area ---
    st.title("🤖 幻影科技员工助手")
    
    # 检查是否有 thread_id，如果没有（刚登录），创建一个默认的
    if not st.session_state.get("thread_id"):
        # 自动创建第一个对话
        new_tid = auth_service.create_new_thread(st.session_state["user_id"], title="默认对话")
        st.session_state["thread_id"] = new_tid
        st.query_params["thread_id"] = new_tid
        st.rerun()

    current_thread_id = st.session_state["thread_id"]
    st.caption(f"Session ID: {current_thread_id}")

    # --- 恢复消息历史 (包括从 DB 加载图片) ---
    if not st.session_state["messages"]:
        restore_history(current_thread_id)

    # 渲染消息
    for msg in st.session_state["messages"]:
        if msg["role"] == "user":
            st.chat_message("user").write(msg["content"])
        else:
            with st.chat_message("assistant"):
                st.write(msg["content"])
                if "images" in msg and msg["images"]:
                    for img in msg["images"]:
                        try:
                            # 兼容 base64 数据
                            if "data" in img: # From memory or DB struct
                                image_data = base64.b64decode(img['data'])
                                st.image(image_data, caption=img.get('prompt', ''), use_container_width=True)
                        except Exception as e:
                            st.warning(f"无法显示图片: {e}")

    # 输入处理
    if user_input := st.chat_input("请输入问题..."):
        # 1. UI 立即显示
        st.chat_message("user").write(user_input)
        if st.session_state["uploaded_image"]:
            st.chat_message("user").image(st.session_state["uploaded_image"], width=300)
        
        st.session_state["messages"].append({"role": "user", "content": user_input})
        
        # 2. 调用 Agent
        config_dict = {"configurable": {"thread_id": current_thread_id}}
        
        # 初始化变量，防止 UnboundLocalError
        ai_text = "⚠️由于未知错误，未能获取回复。"
        new_images = []
        
        with st.spinner("思考中..."):
            # 构建输入
            message_content = [{"type": "text", "text": user_input}]
            if st.session_state["uploaded_image"]:
                img_bytes = st.session_state["uploaded_image"].getvalue()
                b64_img = base64.b64encode(img_bytes).decode("utf-8")
                message_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{st.session_state['uploaded_image'].type};base64,{b64_img}"}
                })
            
            # Invoke
            response = graph.invoke({"messages": [HumanMessage(content=message_content)]}, config=config_dict)
            st.session_state["uploaded_image"] = None # Clear upload

            # 解析结果
            ai_msg = response["messages"][-1]
            ai_text = ai_msg.content
            if isinstance(ai_text, list): # Handle multipart
                texts = [p if isinstance(p, str) else p.get("text", "") for p in ai_text]
                ai_text = "\n".join(texts)
            
            # 从内存 store 拿（保证实时性）
            current_generated_imgs = image_store.get_and_clear()
            if current_generated_imgs:
                new_images = current_generated_imgs

    # 3. 渲染回复
    with st.chat_message("assistant"):
        st.markdown(ai_text)
        if new_images:
            for img in new_images:
                try:
                    data = base64.b64decode(img['data'])
                    st.image(data, caption=img.get('prompt'), use_container_width=True)
                except:
                    pass
    
    st.session_state["messages"].append({
        "role": "assistant",
        "content": ai_text,
        "images": new_images
    })

def restore_history(thread_id):
    """从 LangGraph State 和 DB 恢复历史"""
    try:
        config = {"configurable": {"thread_id": thread_id}}
        current_state = graph.get_state(config)
        restored_msgs = []
        
        # 1. 获取文本历史
        if current_state and current_state.values and "messages" in current_state.values:
            from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
            raw_msgs = current_state.values["messages"]
            
            # 2. 获取该 Thread 所有图片历史 (时间序)
            db_images = auth_service.get_images_for_thread(thread_id)
            # 简单的关联逻辑：将图片分配给它们之后的下一条 Assistant 消息？
            # 或者直接把所有图片合并进流？
            # 这是一个难点：无法精确知道哪张图对应哪条消息。
            # 简易策略：把所有图片收集起来，如果 Assistant 的回复里含有 "图片已生成" 字样，
            # 就按顺序取出一张图片附上去。
            
            img_cursor = 0
            
            for msg in raw_msgs:
                if isinstance(msg, SystemMessage): continue
                if isinstance(msg, ToolMessage): continue # Skip raw tool outputs
                
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content
                if isinstance(content, list):
                     text_parts = [i["text"] for i in content if isinstance(i, dict) and "text" in i]
                     content = "\n".join(text_parts)
                
                # Check for images attachment
                attached_images = []
                if role == "assistant" and ("图片" in str(content) or "generated" in str(content)):
                    # 尝试挂载一张或多张 DB 图片
                    # 这里是模糊匹配，假设顺序一致
                    # 如果 DB 里有足够多的图片，且还没被分配
                    if img_cursor < len(db_images):
                        # 挂载一张
                        attached_images.append(db_images[img_cursor])
                        img_cursor += 1
                
                restored_msgs.append({
                    "role": role,
                    "content": str(content),
                    "images": attached_images
                })
            
            # 如果还有剩余图片没显示（比如刚生成的），挂在最后一条
            while img_cursor < len(db_images):
                if restored_msgs and restored_msgs[-1]["role"] == "assistant":
                    restored_msgs[-1]["images"].append(db_images[img_cursor])
                else:
                    restored_msgs.append({
                        "role": "assistant", 
                        "content": "🖼️ 补充图片",
                        "images": [db_images[img_cursor]]
                    })
                img_cursor += 1
                
            st.session_state["messages"] = restored_msgs

    except Exception as e:
        print(f"Restore Error: {e}")

# ==========================================
# 4. 路由控制
# ==========================================

if st.session_state["user_id"]:
    show_chat_interface()
else:
    login_page()
