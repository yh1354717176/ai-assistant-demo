import streamlit as st
import base64
import datetime
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

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
# 1. Session State 管理
# ==========================================
# 用户登录状态
if "user_id" not in st.session_state:
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
                        st.success(f"{msg}，正在跳转...")
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
            
            # 检查是否有新生成的图片 (从 DB 查，或者从内存 Fallback)
            # 策略：不管怎样，我们去 app_images 查一下该 thread 最近生成的图片
            # 为了避免把历史图片全查出来，我们可以基于 created_at 查最近几秒的？
            # 或者更简单：Agent 工具在返回 ToolMessage 时，虽然没有返回图片数据，
            # 但我们在 render loop 里可以用 auth_service.get_images_for_thread(tid)
            # 然后把它们挂载到当前最新的这条消息上？
            # 这里的逻辑稍微有点 trick：
            # 如果我们每次都全量查图片然后全量 render，就不用把图片挂在 message 对象上了。
            # 但那样排版会乱（所有图片堆在一起）。
            # 更好的做法：工具生成图片后，image_store 依然保留了一份（为了实时显示），
            # 同时 DB 里也存了一份（为了持久化）。
            
            # 从内存 store 拿（保证实时性）
            current_generated_imgs = image_store.get_and_clear()
            
            # 如果内存空了（因为存 DB 去了），我们尝试从 DB 捞最近的一张？ 
            # 之前的 tools.py 修改里，如果存 DB 成功，image_store 是没有数据的。
            # 所以我们需要去 DB 捞。
            # 这里简单起见：每次回复如果 tools.py 说是"生成图片成功"，我们就去 DB 拿最新的 n 张图。
            # 或者 tools.py 存 DB 后，同时也存 image_store 一份专门用于 "Current Turn Display"？
            # 让我们修改 tools.py 比较麻烦，不如在这里查 DB。
            
            new_images = []
            if "图片已成功生成" in str(ai_text) or "图片已生成" in str(ai_text):
                # 查 DB 所有图片，然后过滤？或者只查最后一张？
                # 这是一个弊端，我们不知道哪张是刚生成的。
                # 改进方案：auth_service.save_image_to_db 返回 image_id，
                # 但 tool output 只是 string。
                # **最终方案**：tools.py 里，除了存 DB，也 `store.add()` 一份用于当次即时回显。
                # 请务必执行下面的 run_command 来再次给 tools.py 打补丁，加上 store.add。
                pass
            
            # 为了稳妥，我们暂时还是依靠 image_store 做当次显示，
            # 只有在 reload/restore 时才从 DB 读。
            # 所以 tools.py 需要更新：既存 DB，也存 Memory Store。
            
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
