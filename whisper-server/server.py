"""
YouTube 字幕生成 - 本地 Whisper 服务
使用 faster-whisper 实现本地识别，无需外部 API
支持单视频和播放列表批量处理 (队列模式)
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import re
import hashlib
import subprocess
import threading
import time
import queue
from pathlib import Path
from datetime import datetime
try:
    import httpx
except ImportError:
    httpx = None
import logging

# 配置日志
LOG_FILE = Path("server.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

try:
    from faster_whisper import WhisperModel
    HAS_LOCAL_WHISPER = True
except ImportError:
    HAS_LOCAL_WHISPER = False
    logging.warning("未检测到 faster-whisper 依赖，本地识别模式将失效。")

# MongoDB Support
try:
    import pymongo
    from pymongo.errors import ConnectionFailure
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False

# 尝试导入 OpenCC 用于繁简转换
try:
    from opencc import OpenCC
    converter = OpenCC('t2s') # traditional to simplified
except ImportError:
    converter = None

# ============ 配置 ============
PORT = 8765
MODEL_SIZE = "tiny"  # 可选: tiny, base, small, medium, large-v3
DEVICE = "cpu"       # 如果有 NVIDIA 显卡并安装了 CUDA，可改为 "cuda"
COMPUTE_TYPE = "int8" # cpu 推荐 int8, gpu 推荐 float16
CPU_THREADS = 8      # 线程数，0 为自动。如果 CPU 使用率低，可以尝试设为 4, 8 或 16
NUM_WORKERS = 4      # 模型内部工作进程数

# 任务队列配置
MAX_CONCURRENT_TASKS = 1 # 同时进行的转录任务数 (建议为1，以免显存爆炸)
task_queue = queue.Queue()

# MongoDB 配置 (从环境变量读取，更安全)
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb+srv://youtube_live:MZJwO7LcdUd4x64a@cluster0.v91xaip.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME', 'youtube_subtitles')
MONGO_COLLECTION_NAME = os.environ.get('MONGO_COLLECTION_NAME', 'videos')

# API Keys (可选，支持 Groq 和 OpenAI)
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(exist_ok=True)

TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

# 全局变量
tasks = {}
whisper_model = None
model_lock = threading.Lock()
mongo_client = None
mongo_collection = None

# ============ 工具函数 ============ 
def init_mongo():
    global mongo_client, mongo_collection
    if not HAS_MONGO:
        logging.warning("未检测到 pymongo 依赖，MongoDB 云同步功能已禁用")
        logging.warning("      请运行: pip install pymongo dnspython")
        return False
    
    try:
        logging.info(f"正在尝试连接 MongoDB...")
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        # 简单检查连接
        mongo_client.admin.command('ping')
        db = mongo_client[MONGO_DB_NAME]
        mongo_collection = db[MONGO_COLLECTION_NAME]
        # 创建索引以加速查询
        mongo_collection.create_index("video_id", unique=True)
        logging.info(f"MongoDB 连接成功: ({MONGO_DB_NAME}.{MONGO_COLLECTION_NAME})")
        return True
    except Exception as e:
        logging.error(f"MongoDB 连接失败: {e}")
        logging.info(f"将仅使用本地缓存模式运行")
        mongo_client = None
        mongo_collection = None
        return False

def get_video_id(url):
    patterns = [
        r'(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#\&\?\n]*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:11]

def get_cached_subtitles(video_id):
    # 1. 优先查本地文件
    cache_file = CACHE_DIR / f"{video_id}.json"
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"本地缓存读取错误: {e}")

    # 2. 查 MongoDB
    if mongo_collection is not None:
        try:
            doc = mongo_collection.find_one({"video_id": video_id}, {"_id": 0})
            if doc:
                logging.info(f"命中 MongoDB 云端缓存: {video_id}")
                # 顺便写回本地，下次就不用查库了
                save_subtitles_cache(video_id, doc.get('subtitles'), doc.get('language'))
                return doc
        except Exception as e:
            logging.error(f"MongoDB 查询错误: {e}")
            
    return None

def save_subtitles_cache(video_id, subtitles, language):
    data = {
        'video_id': video_id,
        'language': language,
        'created_at': datetime.now().isoformat(),
        'subtitles': subtitles
    }
    
    # 1. 保存到本地
    try:
        cache_file = CACHE_DIR / f"{video_id}.json"
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"本地写入失败: {e}")

    # 2. 保存到 MongoDB
    if mongo_collection is not None:
        try:
            mongo_collection.update_one(
                {"video_id": video_id},
                {"$set": data},
                upsert=True
            )
            logging.info(f"字幕已上传/同步到云端: {video_id}")
        except Exception as e:
            logging.error(f"同步到云端失败: {e}")
    else:
        logging.info(f"MongoDB 未连接，字幕仅保存至本地缓存")

def update_task(task_id, status, progress, message, subtitles=None, language=None):
    tasks[task_id] = {
        'task_id': task_id,
        'status': status,
        'progress': progress,
        'message': message,
        'subtitles': subtitles,
        'detected_language': language,
        'updated_at': time.time()
    }

def get_model():
    global whisper_model
    if not HAS_LOCAL_WHISPER:
        raise Exception("本地 Whisper 依赖 (faster-whisper) 未安装，请在设置中选择 Groq/OpenAI API 模式")
        
    if whisper_model is None:
        with model_lock:
            if whisper_model is None:
                logging.info(f"正在加载本地模型 ({MODEL_SIZE})... 首次运行可能需要下载")
                whisper_model = WhisperModel(
                    MODEL_SIZE, 
                    device=DEVICE, 
                    compute_type=COMPUTE_TYPE,
                    cpu_threads=CPU_THREADS,
                    num_workers=NUM_WORKERS
                )
                logging.info("模型加载完成")
    return whisper_model

def download_audio(video_url, task_id):
    update_task(task_id, 'downloading', 10, '正在下载音频...')
    video_id = get_video_id(video_url)
    output_template = str(TEMP_DIR / f"{video_id}.%(ext)s")
    
    cmd = [
        'yt-dlp',
        '-x', '--audio-format', 'mp3',
        '--audio-quality', '128K',
        '--no-part',  # 直接写入文件，避免重命名时的 WinError 32 占用错误
        '--force-overwrites', # 强制覆盖旧文件
        '-o', output_template,
        '--no-playlist',
        video_url
    ]
    
    try:
        # 增加超时时间到 10 分钟
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise Exception(f"下载失败: {result.stderr}")
        
        for file in TEMP_DIR.glob(f"{video_id}.*"):
            if file.suffix in ['.mp3', '.m4a', '.webm', '.opus']:
                update_task(task_id, 'downloading', 40, '音频下载完成，准备识别...')
                return str(file)
        raise Exception("音频文件未找到")
    except subprocess.TimeoutExpired:
        raise Exception("音频下载超时 (超过 10 分钟)")
    except Exception as e:
        raise Exception(f"下载错误: {str(e)}")

def compress_audio_for_api(audio_path, task_id):
    """
    如果音频超过 25MB，压缩它以便 API 接受
    """
    MAX_SIZE = 24 * 1024 * 1024  # 稍微留一点余量
    if os.path.getsize(audio_path) <= MAX_SIZE:
        return audio_path
    
    update_task(task_id, 'transcribing', 46, '音频文件过大，正在进行识别优化压缩...')
    
    compressed_path = str(Path(audio_path).parent / f"compressed_{Path(audio_path).name}")
    
    # 压缩参数：16kHz, 单声道, 32k 比特率 (足够 Whisper 识别)
    cmd = [
        'ffmpeg', '-y', '-i', audio_path,
        '-ar', '16000', '-ac', '1', '-b:a', '32k',
        compressed_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"ffmpeg 压缩失败: {result.stderr}")
            return audio_path # 尝试原样上传，虽然可能会失败
            
        if os.path.getsize(compressed_path) > MAX_SIZE:
            # 如果还是太大，进一步降低比特率
            update_task(task_id, 'transcribing', 47, '正在极度压缩超长视频音频...')
            final_path = str(Path(audio_path).parent / f"final_{Path(audio_path).name}")
            cmd = [
                'ffmpeg', '-y', '-i', compressed_path,
                '-ar', '8000', '-ac', '1', '-b:a', '16k',
                final_path
            ]
            subprocess.run(cmd, capture_output=True)
            return final_path
            
        return compressed_path
    except Exception as e:
        logging.error(f"压缩过程出错: {e}")
        return audio_path

def split_text(text, max_len=25):
    if converter:
        text = converter.convert(text)
    
    if len(text) <= max_len:
        return [text]
    
    parts = re.split(r'([，。！？, \.! \?])', text)
    result = []
    current = ""
    
    if len(parts) == 1:
        current = parts[0]
    else:
        for i in range(0, len(parts)-1, 2):
            p = parts[i] + parts[i+1]
            if len(current) + len(p) > max_len and current:
                result.append(current.strip())
                current = p
            else:
                current += p
    
    if current:
        while len(current) > max_len:
            result.append(current[:max_len].strip())
            current = current[max_len:]
        if current.strip():
            result.append(current.strip())
    
    return [r for r in result if r] or [text]

def transcribe_locally(audio_path, task_id, language):
    update_task(task_id, 'transcribing', 50, '正在本地识别音频 (请稍候)...')
    
    model = get_model()
    
    segments, info = model.transcribe(
        audio_path, 
        language=None if not language or language == 'auto' else language,
        beam_size=1,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        initial_prompt="以下是普通话的句子，请用简体中文。以下是普通話的句子，請用簡體中文。" 
    )
    
    detected_lang = info.language
    update_task(task_id, 'transcribing', 60, f'检测到语言: {detected_lang}, 正在生成字幕...')
    
    subtitles = []

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
            
        if converter:
            text = converter.convert(text)
            
        if len(text) > 25:
            split_parts = split_text(text)
            duration = segment.end - segment.start
            part_duration = duration / len(split_parts)
            
            for i, part in enumerate(split_parts):
                subtitles.append({
                    'start': round(segment.start + i * part_duration, 2),
                    'end': round(segment.start + (i + 1) * part_duration, 2),
                    'text': part
                })
        else:
            subtitles.append({
                'start': round(segment.start, 2),
                'end': round(segment.end, 2),
                'text': text
            })
            
        if len(subtitles) % 10 == 0:
             progress = min(98, 60 + (len(subtitles) / 10))
             update_task(task_id, 'transcribing', int(progress), f'已生成 {len(subtitles)} 条字幕...')

    return subtitles, detected_lang

def transcribe_via_api(audio_path, task_id, language, api_key, service='groq'):
    if not httpx:
        raise Exception("未安装 httpx 依赖，无法使用 API 识别")
    
    # 检查并压缩大文件
    original_path = audio_path
    audio_path = compress_audio_for_api(audio_path, task_id)
    
    update_task(task_id, 'transcribing', 48, f'正在读取音频文件...')
    
    url = "https://api.groq.com/openai/v1/audio/transcriptions" if service == 'groq' else "https://api.openai.com/v1/audio/transcriptions"
    model_name = "whisper-large-v3" if service == 'groq' else "whisper-1"
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        update_task(task_id, 'transcribing', 50, f'正在向 {service.upper()} 上传音频并识别 (请稍候)...')
        
        files = {
            "file": (Path(audio_path).name, open(audio_path, "rb"), "audio/mpeg")
        }
        
        data = {
            "model": model_name,
            "response_format": "verbose_json"
        }
        
        if language and language != 'auto':
            data["language"] = language

        with httpx.Client() as client:
            response = client.post(url, headers=headers, data=data, files=files, timeout=300)
            
            # 关闭文件句柄以允许删除压缩文件
            files["file"][1].close()
            if audio_path != original_path and os.path.exists(audio_path):
                os.remove(audio_path)
            
            if response.status_code != 200:
                try:
                    err_msg = response.json().get('error', {}).get('message', response.text)
                except:
                    err_msg = response.text
                raise Exception(f"API 错误 ({response.status_code}): {err_msg}")
            
            result = response.json()
            raw_segments = result.get('segments', [])
            detected_lang = result.get('language', language)
            
            update_task(task_id, 'transcribing', 80, f'API 识别完成，正在处理字幕...')
            
            subtitles = []
            for seg in raw_segments:
                text = seg['text'].strip()
                if not text:
                    continue
                
                if converter:
                    text = converter.convert(text)
                
                if len(text) > 25:
                    split_parts = split_text(text)
                    duration = seg['end'] - seg['start']
                    part_duration = duration / len(split_parts)
                    
                    for i, part in enumerate(split_parts):
                        subtitles.append({
                            'start': round(seg['start'] + i * part_duration, 2),
                            'end': round(seg['start'] + (i + 1) * part_duration, 2),
                            'text': part
                        })
                else:
                    subtitles.append({
                        'start': round(seg['start'], 2),
                        'end': round(seg['end'], 2),
                        'text': text
                    })
            
            return subtitles, detected_lang
    except Exception as e:
        raise Exception(f"{service.upper()} API 调用失败: {str(e)}")

def process_video_task(video_url, task_id, language, api_key=None, service='local'):
    """
    单个视频处理逻辑，由 Worker 调用
    """
    try:
        video_id = get_video_id(video_url)
        
        # 再次检查缓存 (防止排队期间其他任务已生成)
        cached = get_cached_subtitles(video_id)
        if cached:
            update_task(task_id, 'completed', 100, '从缓存加载',
                       cached['subtitles'], cached.get('language'))
            return
        
        # 下载
        audio_path = download_audio(video_url, task_id)
        
        # 转录
        if service in ['groq', 'openai']:
            effective_key = api_key or (GROQ_API_KEY if service == 'groq' else OPENAI_API_KEY)
            if effective_key:
                subtitles, lang = transcribe_via_api(audio_path, task_id, language, effective_key, service)
            else:
                raise Exception(f"未提供 {service.upper()} API Key，请在设置中输入或配置环境变量")
        else:
            if not HAS_LOCAL_WHISPER:
                raise Exception("本地 Whisper 依赖未安装，且未提供有效的 API Key")
            subtitles, lang = transcribe_locally(audio_path, task_id, language)
        
        # 缓存
        save_subtitles_cache(video_id, subtitles, lang)
        
        # 完成
        update_task(task_id, 'completed', 100, f'完成！共 {len(subtitles)} 条',
                   subtitles, lang)
        
        # 清理临时文件
        if os.path.exists(audio_path):
            os.remove(audio_path)
        
    except Exception as e:
        logging.error(f"任务 {task_id} 失败: {e}")
        update_task(task_id, 'error', 0, str(e))

def worker():
    """
    后台工作线程：不断从队列取任务执行
    """
    logging.info("Worker 线程启动，等待任务...")
    while True:
        try:
            # 阻塞等待任务
            task = task_queue.get()
            video_url = task['video_url']
            task_id = task['task_id']
            language = task.get('language')
            api_key = task.get('api_key')
            service = task.get('service', 'local')
            
            logging.info(f"开始处理任务: {task_id} ({video_url}) [Service: {service}]")
            process_video_task(video_url, task_id, language, api_key, service)
            
            task_queue.task_done()
            logging.info(f"任务完成: {task_id}, 队列剩余: {task_queue.qsize()}")
            
        except Exception as e:
            logging.error(f"Worker 发生异常: {e}")

def fetch_playlist_videos(playlist_url):
    """
    解析播放列表，返回视频列表 [{'id': '...', 'title': '...'}, ...] 
    """
    cmd = [
        'yt-dlp',
        '--flat-playlist',
        '--dump-single-json',
        playlist_url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        
        videos = []
        if 'entries' in data:
            for entry in data['entries']:
                if entry.get('id'):
                    videos.append({
                        'id': entry['id'],
                        'title': entry.get('title', 'Unknown'),
                        'url': f"https://www.youtube.com/watch?v={entry['id']}"
                    })
        return videos
    except Exception as e:
        logging.error(f"[Playlist] 解析错误: {e}")
        return []

def sync_local_cache_to_mongo():
    """
    启动时后台同步：将本地 cache 目录下的所有 json 同步到 MongoDB
    """
    if mongo_collection is None:
        return
    
    logging.info("开始扫描本地缓存并同步到云端...")
    count = 0
    try:
        for file in CACHE_DIR.glob("*.json"):
            video_id = file.stem
            try:
                # 检查云端是否已存在（只查 ID 以节省带宽）
                if mongo_collection.find_one({"video_id": video_id}, {"_id": 1}):
                    continue
                
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                mongo_collection.update_one(
                    {"video_id": video_id},
                    {"$set": data},
                    upsert=True
                )
                count += 1
                if count % 5 == 0:
                    logging.info(f"已同步 {count} 个文件...")
            except Exception as e:
                logging.error(f"同步文件 {video_id} 失败: {e}")
        
        if count > 0:
            logging.info(f"同步完成，共上传 {count} 条新记录")
        else:
            logging.info("本地与云端已同步，无需操作")
    except Exception as e:
        logging.error(f"同步过程出错: {e}")

# ============ HTTP 服务 ============ 
class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # 支持 Private Network Access (Chrome 的安全策略)
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # 支持 Private Network Access (Chrome 的安全策略)
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
    
    def do_GET(self):
        if self.path == '/':
            self._send_json({
                'service': 'YouTube 本地 Whisper 服务 (Queue Mode)',
                'status': 'running',
                'queue_size': task_queue.qsize(),
                'local_whisper': HAS_LOCAL_WHISPER,
                'cloud_sync': HAS_MONGO
            })
        elif self.path.startswith('/status/'):
            task_id = self.path[8:]
            if task_id in tasks:
                self._send_json(tasks[task_id])
            else:
                # 如果任务不在内存，尝试从缓存读取
                cached = get_cached_subtitles(task_id)
                if cached:
                    self._send_json({
                        'task_id': task_id,
                        'status': 'completed',
                        'progress': 100,
                        'message': '从缓存加载',
                        'subtitles': cached.get('subtitles'),
                        'detected_language': cached.get('language')
                    })
                else:
                    self._send_json({'error': '任务不存在'}, 404)
        else:
            self._send_json({'error': 'Not Found'}, 404)
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({'error': f'无效的 JSON: {str(e)}'}, 400)
            return

        # 1. 单个视频转录接口
        if self.path == '/transcribe':
            video_url = data.get('video_url')
            if not video_url:
                self._send_json({'error': '缺少 video_url'}, 400)
                return
            
            language = data.get('language')
            api_key = data.get('api_key')
            service = data.get('service', 'local')
            video_id = get_video_id(video_url)
            # 使用简单的 task_id (video_id)，方便前端查询状态
            task_id = video_id 
            
            # 如果任务已在队列或运行中，直接返回
            if task_id in tasks and tasks[task_id]['status'] in ['pending', 'downloading', 'transcribing']:
                 self._send_json({
                    'task_id': task_id,
                    'status': tasks[task_id]['status'],
                    'message': '任务已在运行中'
                })
                 return

            update_task(task_id, 'pending', 0, '已加入队列，等待处理...')
            
            # 加入队列
            task_queue.put({
                'video_url': video_url,
                'task_id': task_id,
                'language': language,
                'api_key': api_key,
                'service': service
            })
            
            self._send_json({
                'task_id': task_id,
                'status': 'pending',
                'queue_position': task_queue.qsize(),
                'message': '任务已提交到队列'
            })
            
        # 2. 播放列表批量转录接口
        elif self.path == '/transcribe_playlist':
            playlist_url = data.get('playlist_url')
            language = data.get('language')
            
            if not playlist_url:
                self._send_json({'error': '缺少 playlist_url'}, 400)
                return
            
            # 异步解析列表，避免阻塞 HTTP 响应
            def process_playlist_background():
                videos = fetch_playlist_videos(playlist_url)
                added_count = 0
                for v in videos:
                    vid = v['id']
                    v_url = v['url']
                    
                    # 检查是否已有字幕（可选：如果已有就不加队列了，节省资源）
                    if get_cached_subtitles(vid):
                        continue
                        
                    task_id = vid
                    # 避免重复添加
                    if task_id in tasks and tasks[task_id]['status'] in ['pending', 'downloading', 'transcribing']:
                        continue
                        
                    update_task(task_id, 'pending', 0, '批量任务: 等待处理...')
                    task_queue.put({
                        'video_url': v_url,
                        'task_id': task_id,
                        'language': language
                    })
                    added_count += 1
                logging.info(f"批量添加完成，新增 {added_count} 个任务")

            threading.Thread(target=process_playlist_background).start()
            
            self._send_json({
                'status': 'success',
                'message': '正在后台解析列表并添加到队列，请稍候...'
            })

        else:
            self._send_json({'error': 'Not Found'}, 404)

# ============ 启动 ============ 
if __name__ == '__main__':
    logging.info("=" * 50)
    logging.info("YouTube 本地 Whisper 字幕服务 (Queue Mode)")
    logging.info("=" * 50)
    logging.info(f"服务地址: http://127.0.0.1:{PORT}")
    logging.info(f"当前模型: {MODEL_SIZE} (运行在 {DEVICE})")
    logging.info(f"并发 Worker 数: {MAX_CONCURRENT_TASKS}")
    
    # 初始化 MongoDB
    if init_mongo():
        # 如果连接成功，启动一个后台线程进行同步，以免阻塞服务启动
        threading.Thread(target=sync_local_cache_to_mongo, daemon=True).start()
    
    # 启动后台 Worker 线程
    for i in range(MAX_CONCURRENT_TASKS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
    
    if not HAS_LOCAL_WHISPER:
        logging.warning("未找到 faster-whisper 依赖，只能使用 API 识别模式")
    logging.info("=" * 50)
    
    server = HTTPServer(('127.0.0.1', PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("服务已停止")