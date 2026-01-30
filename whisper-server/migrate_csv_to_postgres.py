import os
import csv
import json
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import sys

# 添加项目路径以便导入
sys.path.insert(0, str(Path(__file__).parent))

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('csv_migration.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

from db.postgres_db import postgres_db

def migrate_from_csv(csv_path: str):
    """从 CSV 文件迁移数据到 PostgreSQL"""
    logging.info("=" * 60)
    logging.info(f"开始从 CSV 迁移数据: {csv_path}")
    
    if not Path(csv_path).exists():
        logging.error(f"CSV 文件不存在: {csv_path}")
        return 0
        
    if not postgres_db.connect():
        logging.error("无法连接到 PostgreSQL")
        return 0

    success_count = 0
    error_count = 0
    skip_count = 0

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                try:
                    video_id = row.get('video_id')
                    if not video_id:
                        logging.warning(f"行 {i} 缺少 video_id，跳过")
                        skip_count += 1
                        continue
                    
                    # 处理 subtitles 字段
                    subtitles_raw = row.get('subtitles')
                    if not subtitles_raw:
                        logging.warning(f"视频 {video_id} 缺少字幕数据，跳过")
                        skip_count += 1
                        continue
                        
                    try:
                        subtitles = json.loads(subtitles_raw)
                    except json.JSONDecodeError as e:
                        logging.error(f"视频 {video_id} 字幕 JSON 解析失败: {e}")
                        error_count += 1
                        continue
                    
                    # 构造插入数据
                    data = {
                        'video_id': video_id,
                        'language': row.get('language'),
                        'target_lang': row.get('target_lang'),
                        'subtitles': subtitles
                    }
                    
                    # 插入到数据库
                    if postgres_db.upsert_subtitles(data):
                        success_count += 1
                        if success_count % 10 == 0:
                            logging.info(f"已迁移 {success_count} 条记录...")
                    else:
                        error_count += 1
                        logging.error(f"视频 {video_id} 迁移失败")
                        
                except Exception as e:
                    error_count += 1
                    logging.error(f"处理第 {i} 行时出错: {e}")

        logging.info("=" * 60)
        logging.info(f"CSV 迁移完成: 成功 {success_count} 条, 失败 {error_count} 条, 跳过 {skip_count} 条")
        return success_count

    except Exception as e:
        logging.error(f"读取 CSV 文件时出错: {e}")
        return 0
    finally:
        postgres_db.close()

if __name__ == "__main__":
    # 默认路径
    csv_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'subtitles_rows.csv')
    
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
        
    migrate_from_csv(csv_file)
