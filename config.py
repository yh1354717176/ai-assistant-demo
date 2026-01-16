import os
import json
import streamlit as st
from dotenv import load_dotenv

def init_environment():
    """初始化环境变量和敏感文件配置"""
    # 从 .env 文件加载环境变量
    load_dotenv()

    # 1. 恢复环境变量 (API Key & Tracing)
    # 只要 Secrets 里有的配置，都自动加载到系统环境变量中
    # 这样不仅支持 Google Key，也支持 LangSmith 的配置
    # 注意：排除 JSON 格式的 secrets
    json_secrets = ["credentials_json", "token_json"]
    
    # 检查 st.secrets 是否可用（在非 Streamlit 环境下避免报错）
    try:
        secrets = st.secrets
        for key in secrets:
            if key not in json_secrets:
                os.environ[key] = secrets[key]
        
        # 恢复 credentials.json
        if "credentials_json" in secrets:
            cred_content = secrets["credentials_json"].strip()
            try:
                json.loads(cred_content)
                with open("credentials.json", "w") as f:
                    f.write(cred_content)
            except json.JSONDecodeError as e:
                st.error(f"❌ credentials_json 格式错误: {e}")

        # 恢复 token.json
        if "token_json" in secrets:
            token_content = secrets["token_json"].strip()
            try:
                json.loads(token_content)
                with open("token.json", "w") as f:
                    f.write(token_content)
            except json.JSONDecodeError as e:
                st.error(f"❌ token_json 格式错误: {e}")

    except FileNotFoundError:
        # 本地运行时如果没有 .streamlit/secrets.toml 可能抛出此异常，忽略即可
        pass
    except Exception as e:
        print(f"Environment setup warning: {e}")

# 数据库连接串 (优先从 Secrets 获取)
DB_URI = st.secrets.get("DB_URI", os.getenv("DB_URI"))
