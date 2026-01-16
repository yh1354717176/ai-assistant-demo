import streamlit as st
import base64
import datetime
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import extra_streamlit_components as stx

# 导入自定义模块
import config
# import auth_service  <-- Moved to inner scope
import database
from agent import get_graph
from image_store import get_image_store

# ==========================================
# 0. 初始化配置 & 数据库
# ==========================================
# Force reload trigger
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
# 使用 extra_streamlit_components 的 CookieManager
# 注意：CookieManager 是一个 widget，需要唯一的 key
cookie_manager = stx.CookieManager(key="main_cookie_manager")

# ==========================================
# Cookie 读取与登录状态恢复
# 注意：CookieManager 首次加载时可能返回空，需要重试
# ==========================================

# 初始化用户状态变量
if "user_id" not in st.session_state:
    st.session_state["user_id"] = None
    st.session_state["username"] = None

# Cookie 重试计数器（每次刷新后会被重置为 0）
if "_cookie_retry" not in st.session_state:
    st.session_state["_cookie_retry"] = 0

# 尝试从 Cookie 恢复登录状态
if st.session_state["user_id"] is None:
    retry_count = st.session_state["_cookie_retry"]
    
    try:
        # 获取所有 Cookie
        cookies = cookie_manager.get_all()
        print(f"🍪 Cookie 读取 (尝试 {retry_count}): {cookies}")
        
        # 检查是否有有效数据
        if cookies and isinstance(cookies, dict) and len(cookies) > 0:
            cookie_user_id = cookies.get("user_id")
            cookie_username = cookies.get("username")
            
            if cookie_user_id and cookie_username:
                try:
                    st.session_state["user_id"] = int(cookie_user_id)
                    st.session_state["username"] = cookie_username
                    print(f"✅ 从 Cookie 恢复登录状态: {cookie_username}")
                except (ValueError, TypeError) as e:
                    print(f"⚠️ Cookie 值无效: {e}")
        else:
            # Cookie 还没准备好
            MAX_RETRIES = 3
            if retry_count < MAX_RETRIES:
                st.session_state["_cookie_retry"] = retry_count + 1
                wait_time = 0.3 * (retry_count + 1)  # 0.3s, 0.6s, 0.9s
                print(f"⏳ Cookie 未就绪，等待 {wait_time}s 后重试 ({retry_count + 1}/{MAX_RETRIES})...")
                import time
                time.sleep(wait_time)
                st.rerun()
            else:
                print("⚠️ Cookie 读取超时，显示登录页面")
                    
    except Exception as e:
        print(f"⚠️ Cookie 读取异常: {e}")

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
                    import auth_service
                    uid, msg = auth_service.login_user(username, password)
                    if uid:
                        st.session_state["user_id"] = uid
                        st.session_state["username"] = username
                        # 设置 Cookie (有效期 7 天)
                        import datetime as dt
                        expires = dt.datetime.now() + dt.timedelta(days=7)
                        cookie_manager.set("user_id", str(uid), expires_at=expires)
                        cookie_manager.set("username", username, expires_at=expires)
                        st.success(f"{msg}，正在跳转...")
                        # 等待 Cookie 写入后再刷新
                        import time
                        time.sleep(0.5)
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
                    import auth_service
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
            cookie_manager.delete("user_id")
            cookie_manager.delete("username")
            st.rerun()
        
        st.divider()
        st.subheader("🗂️ 对话历史")
        
        # 新建对话按钮
        if st.button("➕ 新建对话", use_container_width=True):
            import auth_service
            new_tid = auth_service.create_new_thread(st.session_state["user_id"], title="新对话")
            st.session_state["thread_id"] = new_tid
            st.session_state["messages"] = []
            st.query_params["thread_id"] = new_tid
            st.rerun()
            
        # 历史列表
        import auth_service
        threads = auth_service.get_user_threads(st.session_state["user_id"])
        if threads:
            for tid, title, updated_at in threads:
                tid_str = str(tid)
                is_active = (tid_str == st.session_state['thread_id'])
                
                # 使用 columns 布局，左边是对称标题按钮，右边是操作菜单
                col1, col2 = st.columns([0.8, 0.2])
                
                with col1:
                    label = f"{'🟢' if is_active else '📄'} {title or '未命名对话'}"
                    if st.button(label, key=f"btn_{tid_str}", use_container_width=True):
                        st.session_state["thread_id"] = tid_str
                        st.session_state["messages"] = []
                        st.query_params["thread_id"] = tid_str
                        st.rerun()
                
                with col2:
                    # 使用 popover 提供更多操作
                    with st.popover("⋮", use_container_width=True):
                        st.write(f"操作: {title}")
                        
                        # 重命名功能
                        with st.form(key=f"rename_{tid_str}"):
                            new_name = st.text_input("新名称", value=title)
                            if st.form_submit_button("重命名"):
                                auth_service.rename_thread(tid_str, new_name, st.session_state["user_id"])
                                st.rerun()
                        
                        # 删除功能
                        if st.button("🗑️ 删除", key=f"del_{tid_str}", type="primary"):
                            auth_service.delete_thread(tid_str, st.session_state["user_id"])
                            # 如果删除的是当前对话，重置 ID
                            if is_active:
                                st.session_state["thread_id"] = None
                                st.query_params.clear()
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
        import auth_service
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
        if st.session_state.get("uploaded_image"):
            st.chat_message("user").image(st.session_state["uploaded_image"], width=300)
        
        st.session_state["messages"].append({"role": "user", "content": user_input})
        
        # 2. 调用 Agent
        config_dict = {"configurable": {"thread_id": current_thread_id}}
        
        # 预先初始化结果变量
        final_response_text = "⚠️ 暂时无法获取回复，请稍后再试。"
        final_images = []
        
        try:
            with st.spinner("思考中..."):
                # 构建输入
                message_content = [{"type": "text", "text": user_input}]
                
                # 处理图片
                if st.session_state.get("uploaded_image"):
                    try:
                        img_bytes = st.session_state["uploaded_image"].getvalue()
                        b64_img = base64.b64encode(img_bytes).decode("utf-8")
                        message_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{st.session_state['uploaded_image'].type};base64,{b64_img}"}
                        })
                    except Exception as e:
                        print(f"Error processing upload: {e}")
                
                # Invoke Graph
                response = graph.invoke({"messages": [HumanMessage(content=message_content)]}, config=config_dict)
                st.session_state["uploaded_image"] = None # Clear upload after sending

                # 解析结果
                if response and "messages" in response:
                    ai_msg = response["messages"][-1]
                    content = ai_msg.content
                    # Handle list content (common in multimodal models)
                    if isinstance(content, list):
                        texts = [p if isinstance(p, str) else p.get("text", "") for p in content]
                        final_response_text = "\n".join(texts)
                    else:
                        final_response_text = str(content)
                
                # 尝试获取新生成的图片
                current_generated_imgs = image_store.get_and_clear()
                print(f"🖼️ 内存图片: {len(current_generated_imgs)} 张")
                
                if current_generated_imgs:
                    final_images = current_generated_imgs
                else:
                    # Fallback: 如果内存没拿到，去 DB 查最近的
                    # 放宽条件：只要是图片生成相关的回复都尝试获取
                    response_text_lower = str(final_response_text).lower()
                    image_keywords = ["图片", "生成", "绘制", "画", "generated", "image", "✅", "成功"]
                    
                    if any(kw.lower() in response_text_lower for kw in image_keywords):
                        import auth_service
                        recent_db_imgs = auth_service.get_recent_images(current_thread_id, limit=5)
                        if recent_db_imgs:
                            final_images = recent_db_imgs
                            print(f"✅ 从 DB 成功捞取 {len(final_images)} 张最近图片")
                    
        except Exception as e:
            final_response_text = f"❌ 系统错误: {str(e)}"
            print(f"Agent Invoke Error: {e}")

        # 3. 渲染回复 (无论成功与否)
        with st.chat_message("assistant"):
            st.markdown(final_response_text)
            if final_images:
                for img in final_images:
                    try:
                        data = base64.b64decode(img['data'])
                        st.image(data, caption=img.get('prompt'), use_container_width=True)
                    except:
                        pass
        
        # 4. 存入历史 (仅当有内容时)
        if final_response_text.strip() or final_images:
            st.session_state["messages"].append({
                "role": "assistant",
                "content": final_response_text,
                "images": final_images
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
            
            # 2. 获取该 Thread 所有图片历史 (按创建时间升序)
            import auth_service
            db_images = auth_service.get_images_for_thread(thread_id)
            
            # 图片生成成功的关键词（LLM 工具调用成功时会返回这些）
            # 注意："好的，图片已生成" 是 LLM 自己说的，不一定真的生成了
            # 真正成功的标志是 "✅ 图片已成功生成！" 或类似的工具返回
            SUCCESS_KEYWORDS = ["✅", "成功生成", "已成功", "successfully"]
            
            # 收集所有消息
            temp_msgs = []
            for msg in raw_msgs:
                if isinstance(msg, SystemMessage): continue
                if isinstance(msg, ToolMessage): continue
                
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                content = msg.content
                if isinstance(content, list):
                    text_parts = [i["text"] for i in content if isinstance(i, dict) and "text" in i]
                    content = "\n".join(text_parts)
                
                if role == "assistant" and not str(content).strip():
                    continue
                    
                temp_msgs.append({
                    "role": role,
                    "content": str(content),
                    "images": []
                })
            
            # 简单顺序匹配：按时间顺序将图片分配给 Assistant 消息
            # 关键改进：只有包含成功标志的 Assistant 消息才能获得图片
            img_cursor = 0
            
            for msg in temp_msgs:
                if msg["role"] == "assistant" and img_cursor < len(db_images):
                    content = msg["content"]
                    
                    # 检查是否包含成功标志（工具真正执行成功）
                    has_success = any(kw in content for kw in SUCCESS_KEYWORDS)
                    
                    if has_success:
                        # 分配一张图片
                        msg["images"].append(db_images[img_cursor])
                        img_cursor += 1
            
            # 如果还有剩余图片，附加到最后一条 Assistant 消息
            # （可能是成功标志被截断或变体）
            while img_cursor < len(db_images):
                # 找最后一条包含"图片"关键词的 Assistant 消息
                for msg in reversed(temp_msgs):
                    if msg["role"] == "assistant" and "图片" in msg["content"]:
                        msg["images"].append(db_images[img_cursor])
                        img_cursor += 1
                        break
                else:
                    # 实在找不到，挂在最后一条
                    if temp_msgs and temp_msgs[-1]["role"] == "assistant":
                        temp_msgs[-1]["images"].append(db_images[img_cursor])
                    else:
                        temp_msgs.append({
                            "role": "assistant",
                            "content": "🖼️ 生成的图片",
                            "images": [db_images[img_cursor]]
                        })
                    img_cursor += 1
            
            restored_msgs = temp_msgs
            st.session_state["messages"] = restored_msgs
            print(f"✅ 成功恢复 {len(restored_msgs)} 条消息，{len(db_images)} 张图片")

    except Exception as e:
        print(f"Restore Error: {e}")
        import traceback
        traceback.print_exc()

# ==========================================
# 4. 路由控制
# ==========================================

if st.session_state["user_id"]:
    show_chat_interface()
else:
    login_page()
