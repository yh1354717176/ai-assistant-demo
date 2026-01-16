import streamlit as st
import psycopg
from psycopg_pool import ConnectionPool
from config import DB_URI

@st.cache_resource
def get_db_pool():
    """初始化数据库连接池"""
    print("🔌 正在连接 PostgreSQL 数据库...")
    # autocommit=True 对于 langgraph checkpoint 是推荐的
    return ConnectionPool(conninfo=DB_URI, max_size=20, kwargs={"autocommit": True})

def init_db_schema():
    """初始化业务表结构"""
    pool = get_db_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            # 1. 用户表
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            
            # 2. 对话线程表 (关联用户)
            # 记录 thread_id 和 user_id 的关系
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_threads (
                thread_id UUID PRIMARY KEY,
                user_id INTEGER REFERENCES users(id),
                title TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            
            # 3. 图片存储表
            # 存储生成的图片 Base64，与 thread_id 关联
            cur.execute("""
            CREATE TABLE IF NOT EXISTS app_images (
                id SERIAL PRIMARY KEY,
                thread_id UUID NOT NULL,
                prompt TEXT,
                base64_data TEXT,
                mime_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            
            # 创建索引
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_threads_user_id ON user_threads(user_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_app_images_thread_id ON app_images(thread_id);")
