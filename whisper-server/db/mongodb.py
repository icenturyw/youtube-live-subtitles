import os
import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

class MongoDB:
    def __init__(self):
        self.client = None
        self.db = None
        self.collection = None
        self.uri = os.environ.get('MONGO_URI', 'mongodb+srv://youtube_live:MZJwO7LcdUd4x64a@cluster0.v91xaip.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
        self.db_name = os.environ.get('MONGO_DB_NAME', 'youtube_subtitles')
        self.collection_name = os.environ.get('MONGO_COLLECTION_NAME', 'videos')

    def connect(self):
        try:
            logging.info(f"正在尝试连接 MongoDB...")
            self.client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[self.db_name]
            self.collection = self.db[self.collection_name]
            self.collection.create_index("video_id", unique=True)
            logging.info(f"MongoDB 连接成功: ({self.db_name}.{self.collection_name})")
            return True
        except Exception as e:
            logging.error(f"MongoDB 连接失败: {e}")
            self.client = None
            return False

    def get_collection(self):
        return self.collection

mongo_db = MongoDB()
