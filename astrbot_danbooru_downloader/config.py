# Danbooru API 配置
# 请复制 .env.example 为 .env 并填入你的凭证
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

USERNAME = os.getenv("DANBOORU_USERNAME", "")
API_KEY = os.getenv("DANBOORU_API_KEY", "")

if not USERNAME or not API_KEY:
    raise ValueError("请设置环境变量 DANBOORU_USERNAME 和 DANBOORU_API_KEY")
