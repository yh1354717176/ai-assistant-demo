import streamlit as st
from psycopg_pool import ConnectionPool
from config import DB_URI

@st.cache_resource
def get_db_pool():
    """初始化数据库连接池"""
    print("🔌 正在连接 PostgreSQL 数据库...")
    # autocommit=True 对于 langgraph checkpoint 是推荐的
    return ConnectionPool(conninfo=DB_URI, max_size=20, kwargs={"autocommit": True})
