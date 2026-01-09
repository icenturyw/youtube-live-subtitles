"""
YouTube 字幕生成 - 本地 Whisper 服务
使用 faster-whisper 实现本地识别，无需外部 API
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import re
import hashlib
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime

try:
    from faster_whisper import WhisperModel
    HAS_LOCAL_WHISPER = True
except ImportError:
    HAS_LOCAL_WHISPER = False

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
MODEL_SIZE = "large-v3"  # 可选: tiny, base, small, medium, large-v3
DEVICE = "cuda"       # 如果有 NVIDIA 显卡并安装了 CUDA，可改为 "cuda"
COMPUTE_TYPE = "float16" # cpu 推荐 int8, gpu 推荐 float16
CPU_THREADS = 0      # 线程数，0 为自动。如果 CPU 使用率低，可以尝试设为 4, 8 或 16
NUM_WORKERS = 4      # 工作进程数，增加此值可以提高并发处理能力

# MongoDB 配置
MONGO_URI = "mongodb+srv://youtube_live:MZJwO7LcdUd4x64a@cluster0.v91xaip.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
MONGO_DB_NAME = "youtube_subtitles"
MONGO_COLLECTION_NAME = "videos"

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
        return False
    
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        # 简单检查连接
        mongo_client.admin.command('ping')
        db = mongo_client[MONGO_DB_NAME]
        mongo_collection = db[MONGO_COLLECTION_NAME]
        # 创建索引以加速查询
        mongo_collection.create_index("video_id", unique=True)
        print(f"[MongoDB] 连接成功: {MONGO_URI} ({MONGO_DB_NAME}.{MONGO_COLLECTION_NAME})")
        return True
    except Exception as e:
        print(f"[MongoDB] 连接失败: {e}")
        print(f"[MongoDB] 将仅使用本地缓存模式运行")
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
            print(f"[Cache] 本地缓存读取错误: {e}")

    # 2. 查 MongoDB
    if mongo_collection is not None:
        try:
            doc = mongo_collection.find_one({"video_id": video_id}, {"_id": 0})
            if doc:
                print(f"[Cache] 命中 MongoDB 云端缓存: {video_id}")
                # 顺便写回本地，下次就不用查库了
                save_subtitles_cache(video_id, doc.get('subtitles'), doc.get('language'))
                return doc
        except Exception as e:
            print(f"[MongoDB] 查询错误: {e}")
            
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
        print(f"[Cache] 本地写入失败: {e}")

    # 2. 保存到 MongoDB
    if mongo_collection is not None:
        try:
            mongo_collection.update_one(
                {"video_id": video_id},
                {"$set": data},
                upsert=True
            )
            print(f"[MongoDB] 字幕已上传/更新: {video_id}")
        except Exception as e:
            print(f"[MongoDB] 写入失败: {e}")

def update_task(task_id, status, progress, message, subtitles=None, language=None):
    tasks[task_id] = {
        'task_id': task_id,
        'status': status,
        'progress': progress,
        'message': message,
        'subtitles': subtitles,
        'detected_language': language
    }

def get_model():
    global whisper_model
    if whisper_model is None:
        with model_lock:
            if whisper_model is None:
                print(f"[信息] 正在加载本地模型 ({MODEL_SIZE})... 首次运行可能需要下载")
                whisper_model = WhisperModel(
                    MODEL_SIZE, 
                    device=DEVICE, 
                    compute_type=COMPUTE_TYPE,
                    cpu_threads=CPU_THREADS,
                    num_workers=NUM_WORKERS
                )
                print("[信息] 模型加载完成")
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
        # 增加超时时间到 10 分钟，以应对大型视频或慢速网络
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

def transcribe_locally(audio_path, task_id, language):
    update_task(task_id, 'transcribing', 50, '正在本地识别音频 (请稍候)...')
    
    model = get_model()
    
    # 开始转录，添加 vad_filter 帮助自然切分
    # initial_prompt 引导模型输出简体中文
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
    
    def split_text(text, max_len=25):
        # 转换繁体为简体
        if converter:
            text = converter.convert(text)
        
        if len(text) <= max_len:
            return [text]
        
        # 优先按标点拆分
        parts = re.split(r'([，。！？, \.! \?])', text)
        result = []
        current = ""
        
        # 如果只有一个部分（没有匹配到分隔符）
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
            # 强行按长度切分剩余部分
            while len(current) > max_len:
                result.append(current[:max_len].strip())
                current = current[max_len:]
            if current.strip():
                result.append(current.strip())
        
        return [r for r in result if r] or [text] # 兜底逻辑

    # segments 是一个生成器，需要遍历
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
            
        # 强制转换并根据长度拆分
        if converter:
            text = converter.convert(text)
            
        # 如果单段内容太长，尝试逻辑拆分
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
            
        # 简单估算进度
        if len(subtitles) % 10 == 0:
             progress = min(98, 60 + (len(subtitles) / 10))
             update_task(task_id, 'transcribing', int(progress), f'已生成 {len(subtitles)} 条字幕...')

    return subtitles, detected_lang

def process_video(video_url, task_id, language):
    try:
        if not HAS_LOCAL_WHISPER:
            raise Exception("本地 Whisper 依赖未安装。请运行: pip install faster-whisper")
            
        video_id = get_video_id(video_url)
        
        # 检查缓存
        cached = get_cached_subtitles(video_id)
        if cached:
            update_task(task_id, 'completed', 100, '从缓存加载',
                       cached['subtitles'], cached.get('language'))
            return
        
        # 下载
        audio_path = download_audio(video_url, task_id)
        
        # 本地转录
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
        update_task(task_id, 'error', 0, str(e))

# ============ HTTP 服务 ============
class RequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        if self.path == '/':
            self._send_json({
                'service': 'YouTube 本地 Whisper 服务',
                'status': 'running',
                'local_whisper': HAS_LOCAL_WHISPER
            })
        elif self.path.startswith('/status/'):
            task_id = self.path[8:]
            if task_id in tasks:
                self._send_json(tasks[task_id])
            else:
                self._send_json({'error': '任务不存在'}, 404)
        else:
            self._send_json({'error': 'Not Found'}, 404)
    
    def do_POST(self):
        if self.path == '/transcribe':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            try:
                data = json.loads(body.decode())
            except:
                self._send_json({'error': '无效的 JSON'}, 400)
                return
            
            video_url = data.get('video_url')
            if not video_url:
                self._send_json({'error': '缺少 video_url'}, 400)
                return
            
            language = data.get('language')
            video_id = get_video_id(video_url)
            task_id = f"{video_id}_{int(time.time())}"
            
            update_task(task_id, 'pending', 0, '任务已创建')
            
            # 后台处理
            thread = threading.Thread(
                target=process_video,
                args=(video_url, task_id, language)
            )
            thread.start()
            
            self._send_json({
                'task_id': task_id,
                'status': 'pending',
                'message': '任务已提交到本地处理队列'
            })
        else:
            self._send_json({'error': 'Not Found'}, 404)

# ============ 启动 ============
if __name__ == '__main__':
    print("=" * 50)
    print("YouTube 本地 Whisper 字幕服务 (Faster-Whisper)")
    print("=" * 50)
    print(f"服务地址: http://127.0.0.1:{PORT}")
    print(f"当前模型: {MODEL_SIZE} (运行在 {DEVICE})")
    
    # 初始化 MongoDB
    init_mongo()
    
    if not HAS_LOCAL_WHISPER:
        print("⚠ 错误: 未找到 faster-whisper 依赖!")
        print("  请先运行: pip install faster-whisper")
    print("=" * 50)
    print("首次识别时会加载模型，请耐心等待...")
    
    server = HTTPServer(('127.0.0.1', PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
