"""
数据库迁移脚本: MongoDB/Supabase -> PostgreSQL
将现有的字幕数据从 MongoDB 和 Supabase 迁移到 PostgreSQL
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
import json

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('migration.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# 导入数据库模块
from db.postgres_db import postgres_db

# 可选依赖
try:
    from db.mongodb import mongo_db
    HAS_MONGO = True
except ImportError:
    mongo_db = None
    HAS_MONGO = False
    logging.warning("未安装 MongoDB 依赖,将跳过 MongoDB 迁移")

try:
    from db.supabase_db import supabase_db
    HAS_SUPABASE = True
except ImportError:
    supabase_db = None
    HAS_SUPABASE = False
    logging.warning("未安装 Supabase 依赖,将跳过 Supabase 迁移")

def migrate_from_mongodb():
    """从 MongoDB 迁移数据"""
    logging.info("=" * 60)
    logging.info("开始从 MongoDB 迁移数据...")
    
    if not HAS_MONGO or not mongo_db:
        logging.warning("MongoDB 不可用,跳过 MongoDB 迁移")
        return 0
    
    if not mongo_db.connect():
        logging.warning("MongoDB 连接失败,跳过 MongoDB 迁移")
        return 0
    
    collection = mongo_db.get_collection()
    if not collection:
        logging.warning("无法获取 MongoDB 集合,跳过迁移")
        return 0
    
    try:
        # 获取所有文档
        documents = list(collection.find({}))
        logging.info(f"从 MongoDB 找到 {len(documents)} 条记录")
        
        success_count = 0
        error_count = 0
        
        for doc in documents:
            try:
                # 移除 MongoDB 的 _id 字段
                if '_id' in doc:
                    del doc['_id']
                
                # 确保必要字段存在
                if 'video_id' not in doc or 'subtitles' not in doc:
                    logging.warning(f"跳过无效文档: {doc}")
                    error_count += 1
                    continue
                
                # 插入到 PostgreSQL
                if postgres_db.upsert_subtitles(doc):
                    success_count += 1
                    logging.info(f"✓ 迁移成功: {doc['video_id']}")
                else:
                    error_count += 1
                    logging.error(f"✗ 迁移失败: {doc.get('video_id', 'unknown')}")
                    
            except Exception as e:
                error_count += 1
                logging.error(f"处理文档时出错: {e}")
        
        logging.info(f"MongoDB 迁移完成: 成功 {success_count} 条, 失败 {error_count} 条")
        return success_count
        
    except Exception as e:
        logging.error(f"MongoDB 迁移过程出错: {e}")
        return 0

def migrate_from_supabase():
    """从 Supabase 迁移数据"""
    logging.info("=" * 60)
    logging.info("开始从 Supabase 迁移数据...")
    
    if not HAS_SUPABASE or not supabase_db:
        logging.warning("Supabase 不可用,跳过 Supabase 迁移")
        return 0
    
    if not supabase_db.client:
        logging.warning("Supabase 客户端未初始化,跳过 Supabase 迁移")
        return 0
    
    try:
        # 获取所有记录
        response = supabase_db.client.table("subtitles").select("*").execute()
        
        if not response.data:
            logging.info("Supabase 中没有数据")
            return 0
        
        documents = response.data
        logging.info(f"从 Supabase 找到 {len(documents)} 条记录")
        
        success_count = 0
        error_count = 0
        
        for doc in documents:
            try:
                # 确保必要字段存在
                if 'video_id' not in doc or 'subtitles' not in doc:
                    logging.warning(f"跳过无效文档: {doc}")
                    error_count += 1
                    continue
                
                # 插入到 PostgreSQL
                if postgres_db.upsert_subtitles(doc):
                    success_count += 1
                    logging.info(f"✓ 迁移成功: {doc['video_id']}")
                else:
                    error_count += 1
                    logging.error(f"✗ 迁移失败: {doc.get('video_id', 'unknown')}")
                    
            except Exception as e:
                error_count += 1
                logging.error(f"处理文档时出错: {e}")
        
        logging.info(f"Supabase 迁移完成: 成功 {success_count} 条, 失败 {error_count} 条")
        return success_count
        
    except Exception as e:
        logging.error(f"Supabase 迁移过程出错: {e}")
        return 0

def migrate_from_local_cache():
    """从本地缓存文件迁移数据"""
    logging.info("=" * 60)
    logging.info("开始从本地缓存迁移数据...")
    
    cache_dir = Path("./cache")
    if not cache_dir.exists():
        logging.warning("本地缓存目录不存在,跳过本地缓存迁移")
        return 0
    
    cache_files = list(cache_dir.glob("*.json"))
    logging.info(f"从本地缓存找到 {len(cache_files)} 个文件")
    
    success_count = 0
    error_count = 0
    
    for cache_file in cache_files:
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                doc = json.load(f)
            
            # 确保必要字段存在
            if 'video_id' not in doc or 'subtitles' not in doc:
                logging.warning(f"跳过无效缓存文件: {cache_file.name}")
                error_count += 1
                continue
            
            # 检查是否已存在(避免覆盖更新的数据)
            existing = postgres_db.get_by_video_id(doc['video_id'])
            if existing:
                logging.info(f"⊙ 跳过已存在: {doc['video_id']}")
                continue
            
            # 插入到 PostgreSQL
            if postgres_db.upsert_subtitles(doc):
                success_count += 1
                logging.info(f"✓ 迁移成功: {doc['video_id']}")
            else:
                error_count += 1
                logging.error(f"✗ 迁移失败: {doc.get('video_id', 'unknown')}")
                
        except Exception as e:
            error_count += 1
            logging.error(f"处理缓存文件 {cache_file.name} 时出错: {e}")
    
    logging.info(f"本地缓存迁移完成: 成功 {success_count} 条, 失败 {error_count} 条")
    return success_count

def verify_migration():
    """验证迁移结果"""
    logging.info("=" * 60)
    logging.info("开始验证迁移结果...")
    
    try:
        video_ids = postgres_db.get_all_video_ids()
        logging.info(f"PostgreSQL 中共有 {len(video_ids)} 条记录")
        
        # 随机抽样验证几条数据
        import random
        sample_size = min(5, len(video_ids))
        if sample_size > 0:
            samples = random.sample(video_ids, sample_size)
            logging.info(f"随机抽样验证 {sample_size} 条记录:")
            
            for video_id in samples:
                data = postgres_db.get_by_video_id(video_id)
                if data:
                    logging.info(f"  ✓ {video_id}: {len(data.get('subtitles', []))} 条字幕")
                else:
                    logging.error(f"  ✗ {video_id}: 读取失败")
        
        return True
        
    except Exception as e:
        logging.error(f"验证过程出错: {e}")
        return False

def main():
    """主函数"""
    logging.info("=" * 60)
    logging.info("数据库迁移工具")
    logging.info(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)
    
    # 连接 PostgreSQL
    if not postgres_db.connect():
        logging.error("无法连接到 PostgreSQL,迁移终止")
        return
    
    total_migrated = 0
    
    # 1. 从 MongoDB 迁移
    total_migrated += migrate_from_mongodb()
    
    # 2. 从 Supabase 迁移
    total_migrated += migrate_from_supabase()
    
    # 3. 从本地缓存迁移
    total_migrated += migrate_from_local_cache()
    
    # 4. 验证迁移结果
    verify_migration()
    
    logging.info("=" * 60)
    logging.info(f"迁移完成! 共迁移 {total_migrated} 条记录")
    logging.info(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)
    
    # 关闭连接
    postgres_db.close()

if __name__ == "__main__":
    main()
