import os
import logging
from supabase import create_client, Client

class SupabaseDB:
    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL")
        self.key = os.environ.get("SUPABASE_KEY")
        self.client: Client = None
        
        if self.url and self.key:
            try:
                self.client = create_client(self.url, self.key)
                logging.info(f"Supabase 客户端初始化成功: {self.url}")
            except Exception as e:
                logging.error(f"Supabase 初始化失败: {e}")

    def get_by_video_id(self, video_id: str):
        if not self.client:
            return None
        
        try:
            # 查询 subtitles 表
            response = self.client.table("subtitles").select("*").eq("video_id", video_id).execute()
            if response.data and len(response.data) > 0:
                logging.info(f"Supabase 命中缓存: {video_id}")
                return response.data[0]
        except Exception as e:
            logging.error(f"Supabase 查询失败 ({video_id}): {e}")
        return None

    def upsert_subtitles(self, data: dict):
        if not self.client:
            return False
        
        try:
            # [ROBUST] Filter data to keys that are likely to exist
            # If target_lang fails, we try again without it to ensure at least core data is synced
            try:
                self.client.table("subtitles").upsert(data).execute()
            except Exception as e:
                if 'target_lang' in str(e):
                    logging.warning(f"Supabase 缺少 'target_lang' 字段，尝试排除该字段后重新同步")
                    subset = {k: v for k, v in data.items() if k != 'target_lang'}
                    self.client.table("subtitles").upsert(subset).execute()
                else:
                    raise e
            
            logging.info(f"Supabase 数据已同步: {data.get('video_id')}")
            return True
        except Exception as e:
            logging.error(f"Supabase 同步失败 ({data.get('video_id')}): {e}")
            return False

# 全局实例
supabase_db = SupabaseDB()
