from dotenv import load_dotenv
import os

# 必须在所有其他本地导入之前加载环境变量
load_dotenv()

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router
from db.supabase_db import supabase_db

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

app = FastAPI(title="Whisper Subtitle Server", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

@app.on_event("startup")
async def startup_event():
    logging.info("服务正在启动...")
    # Supabase 初始化在 db/supabase_db.py 中已经通过全局实例完成
    if supabase_db.client:
        logging.info("云端同步已就绪 (Supabase)")
    else:
        logging.warning("云端同步未启用，将仅使用本地模式")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
