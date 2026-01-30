import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from datetime import datetime
import json

class PostgresDB:
    def __init__(self):
        self.connection_pool = None
        self.host = os.environ.get('POSTGRES_HOST', '83.229.124.177')
        self.port = os.environ.get('POSTGRES_PORT', '5432')
        self.database = os.environ.get('POSTGRES_DB', 'ytb_subtitles')
        self.user = os.environ.get('POSTGRES_USER', 'ytb_subtitles')
        self.password = os.environ.get('POSTGRES_PASSWORD', '3rnmdw4D3EYTkPRZ')
        
    def connect(self):
        """初始化数据库连接池"""
        try:
            logging.info(f"正在连接 PostgreSQL 数据库: {self.host}:{self.port}/{self.database}")
            
            # 创建连接池
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                1,  # 最小连接数
                10,  # 最大连接数
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
            
            # 测试连接并创建表
            conn = self.connection_pool.getconn()
            try:
                self._create_tables(conn)
                logging.info(f"PostgreSQL 连接成功: {self.host}:{self.port}/{self.database}")
                return True
            finally:
                self.connection_pool.putconn(conn)
                
        except Exception as e:
            logging.error(f"PostgreSQL 连接失败: {e}")
            self.connection_pool = None
            return False
    
    def _create_tables(self, conn):
        """创建必要的数据表"""
        cursor = conn.cursor()
        try:
            # 创建 subtitles 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subtitles (
                    id SERIAL PRIMARY KEY,
                    video_id VARCHAR(255) UNIQUE NOT NULL,
                    language VARCHAR(50),
                    target_lang VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    subtitles JSONB NOT NULL
                )
            """)
            
            # 创建索引以加速查询
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_video_id ON subtitles(video_id)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON subtitles(created_at)
            """)
            
            conn.commit()
            logging.info("数据表创建/验证成功")
        except Exception as e:
            conn.rollback()
            logging.error(f"创建数据表失败: {e}")
            raise
        finally:
            cursor.close()
    
    def get_by_video_id(self, video_id: str):
        """根据 video_id 查询字幕数据"""
        if not self.connection_pool:
            return None
        
        conn = None
        try:
            conn = self.connection_pool.getconn()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute(
                "SELECT * FROM subtitles WHERE video_id = %s",
                (video_id,)
            )
            
            result = cursor.fetchone()
            cursor.close()
            
            if result:
                logging.info(f"PostgreSQL 命中缓存: {video_id}")
                # 转换为字典格式
                return dict(result)
            
            return None
            
        except Exception as e:
            logging.error(f"PostgreSQL 查询失败 ({video_id}): {e}")
            return None
        finally:
            if conn:
                self.connection_pool.putconn(conn)
    
    def upsert_subtitles(self, data: dict):
        """插入或更新字幕数据"""
        if not self.connection_pool:
            return False
        
        conn = None
        try:
            conn = self.connection_pool.getconn()
            cursor = conn.cursor()
            
            video_id = data.get('video_id')
            language = data.get('language')
            target_lang = data.get('target_lang')
            subtitles = data.get('subtitles')
            
            if not video_id or not subtitles:
                logging.error("缺少必要字段: video_id 或 subtitles")
                return False
            
            # 将 subtitles 转换为 JSON 字符串
            subtitles_json = json.dumps(subtitles, ensure_ascii=False)
            
            # 使用 ON CONFLICT 实现 upsert
            cursor.execute("""
                INSERT INTO subtitles (video_id, language, target_lang, subtitles, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) 
                DO UPDATE SET 
                    language = EXCLUDED.language,
                    target_lang = EXCLUDED.target_lang,
                    subtitles = EXCLUDED.subtitles,
                    updated_at = EXCLUDED.updated_at
            """, (
                video_id,
                language,
                target_lang,
                subtitles_json,
                datetime.now(),
                datetime.now()
            ))
            
            conn.commit()
            cursor.close()
            
            logging.info(f"PostgreSQL 数据已同步: {video_id}")
            return True
            
        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"PostgreSQL 同步失败 ({data.get('video_id')}): {e}")
            return False
        finally:
            if conn:
                self.connection_pool.putconn(conn)
    
    def get_all_video_ids(self):
        """获取所有 video_id (用于迁移)"""
        if not self.connection_pool:
            return []
        
        conn = None
        try:
            conn = self.connection_pool.getconn()
            cursor = conn.cursor()
            
            cursor.execute("SELECT video_id FROM subtitles")
            results = cursor.fetchall()
            cursor.close()
            
            return [row[0] for row in results]
            
        except Exception as e:
            logging.error(f"获取 video_id 列表失败: {e}")
            return []
        finally:
            if conn:
                self.connection_pool.putconn(conn)
    
    def close(self):
        """关闭连接池"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logging.info("PostgreSQL 连接池已关闭")

    def delete_by_video_id(self, video_id: str):
        """根据 video_id 删除字幕数据"""
        if not self.connection_pool:
            return False
        
        conn = None
        try:
            conn = self.connection_pool.getconn()
            cursor = conn.cursor()
            
            cursor.execute(
                "DELETE FROM subtitles WHERE video_id = %s",
                (video_id,)
            )
            
            conn.commit()
            cursor.close()
            logging.info(f"PostgreSQL 数据已删除: {video_id}")
            return True
            
        except Exception as e:
            if conn:
                conn.rollback()
            logging.error(f"PostgreSQL 删除失败 ({video_id}): {e}")
            return False
        finally:
            if conn:
                self.connection_pool.putconn(conn)

# 全局实例
postgres_db = PostgresDB()
