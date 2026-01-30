from dotenv import load_dotenv
import os

# 必须在所有其他本地导入之前加载环境变量
load_dotenv()

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router

# Setup logging
LOG_FILE = "server.log"
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

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
    from db.postgres_db import postgres_db
    from core.task_manager import task_manager
    import threading
    
    if postgres_db.connect():
        logging.info("云端同步已就绪 (PostgreSQL)")
        # 启动后台线程同步本地缓存到 PostgreSQL
        threading.Thread(target=task_manager.sync_local_cache_to_postgres, daemon=True).start()
    else:
        logging.warning("PostgreSQL 连接失败，云端同步未启用")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
