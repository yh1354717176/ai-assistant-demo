import streamlit as st
import bcrypt
from database import get_db_pool

def hash_password(password: str) -> str:
    """加密密码"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def register_user(username, password):
    """注册新用户"""
    try:
        pool = get_db_pool()
        hashed = hash_password(password)
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
                    (username, hashed)
                )
                user_id = cur.fetchone()[0]
        return user_id, "注册成功！请登录。"
    except Exception as e:
        if "unique" in str(e).lower():
            return None, "用户名已存在，请重试。"
        return None, f"注册失败: {e}"

def login_user(username, password):
    """用户登录"""
    try:
        pool = get_db_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
                result = cur.fetchone()
                
        if result:
            user_id, stored_hash = result
            if verify_password(password, stored_hash):
                return user_id, "登录成功"
        return None, "用户名或密码错误"
    except Exception as e:
        return None, f"登录出错: {e}"

def create_new_thread(user_id, title="新对话"):
    """为用户创建新对话"""
    import uuid
    new_thread_id = str(uuid.uuid4())
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_threads (thread_id, user_id, title) VALUES (%s, %s, %s)",
                (new_thread_id, user_id, title)
            )
    return new_thread_id

def get_user_threads(user_id):
    """获取用户的所有对话"""
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT thread_id, title, updated_at FROM user_threads WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,)
            )
            return cur.fetchall()

def save_image_to_db(thread_id, prompt, base64_data, mime_type="image/png"):
    """保存图片到数据库"""
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_images (thread_id, prompt, base64_data, mime_type) VALUES (%s, %s, %s, %s)",
                (thread_id, prompt, base64_data, mime_type)
            )

def get_images_for_thread(thread_id):
    """获取对话关联的所有图片"""
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT base64_data, prompt, mime_type FROM app_images WHERE thread_id = %s ORDER BY created_at ASC",
                (thread_id,)
            )
            return [{"data": row[0], "prompt": row[1], "mime_type": row[2]} for row in cur.fetchall()]

def get_recent_images(thread_id, limit=1):
    """获取最近生成的图片（用于即时回显Fallback）"""
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # 获取最近30秒内生成的图片
            cur.execute(
                """
                SELECT base64_data, prompt, mime_type 
                FROM app_images 
                WHERE thread_id = %s 
                AND created_at > NOW() - INTERVAL '30 seconds'
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (thread_id, limit)
            )
            return [{"data": row[0], "prompt": row[1], "mime_type": row[2]} for row in cur.fetchall()]
