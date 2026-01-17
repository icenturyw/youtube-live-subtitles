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
    """
    语义化断句：优先在标点符号和自然停顿处断句
    """
    if converter:
        text = converter.convert(text)
    
    if len(text) <= max_len:
        return [text]
    
    # 定义连接词（在这些词之前可以断句）
    connectors = ['但是', '所以', '因此', '然后', '而且', '并且', '或者', '不过', '但', '而', 
                  'but', 'so', 'therefore', 'then', 'and', 'or', 'however', 'yet']
    
    # 定义句子终止符（优先级最高）
    sentence_endings = r'([。！？；.!?;])'
    # 定义次级标点（优先级次之）
    minor_punctuations = r'([，,、])'
    
    # 首先尝试在句子终止符处分割
    sentences = re.split(sentence_endings, text)
    result = []
    current = ""
    
    i = 0
    while i < len(sentences):
        part = sentences[i]
        
        # 如果是标点符号，附加到前一个部分
        if i + 1 < len(sentences) and re.match(sentence_endings, sentences[i + 1]):
            part += sentences[i + 1]
            i += 2
        else:
            i += 1
        
        # 如果当前部分本身就过长，需要进一步拆分
        if len(part) > max_len:
            # 尝试在次级标点处分割
            sub_parts = re.split(minor_punctuations, part)
            sub_current = ""
            
            j = 0
            while j < len(sub_parts):
                sub_part = sub_parts[j]
                
                if j + 1 < len(sub_parts) and re.match(minor_punctuations, sub_parts[j + 1]):
                    sub_part += sub_parts[j + 1]
                    j += 2
                else:
                    j += 1
                
                # 检查是否在连接词前可以断句
                can_split_at_connector = False
                for conn in connectors:
                    if sub_part.strip().startswith(conn):
                        can_split_at_connector = True
                        break
                
                if len(sub_current) + len(sub_part) > max_len and sub_current:
                    result.append(sub_current.strip())
                    sub_current = sub_part
                elif can_split_at_connector and sub_current and len(sub_current) > max_len * 0.5:
                    # 如果遇到连接词且当前已经有一定长度，可以在此断句
                    result.append(sub_current.strip())
                    sub_current = sub_part
                else:
                    sub_current += sub_part
            
            # 处理剩余的子部分
            if sub_current:
                if len(sub_current) > max_len:
                    # 强制按字符数截断（最后的手段）
                    while len(sub_current) > max_len:
                        result.append(sub_current[:max_len].strip())
                        sub_current = sub_current[max_len:]
                if sub_current.strip():
                    current = sub_current
        else:
            # 正常累加
            if len(current) + len(part) > max_len and current:
                result.append(current.strip())
                current = part
            else:
                current += part
    
    # 处理最后剩余的部分
    if current:
        if len(current) > max_len:
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

def process_video_task(video_url, task_id, language, api_key=None, service='local', target_lang=None):
    """
    单个视频处理逻辑，由 Worker 调用
    """
    try:
        video_id = get_video_id(video_url)
        
        # 再次检查缓存
        cached = get_cached_subtitles(video_id)
        if cached:
            # 如果缓存中已有我们需要的翻译语言，或者用户未开启翻译，则直接返回
            cached_subs = cached.get('subtitles', [])
            has_translation = any(sub.get('translation') for sub in cached_subs if sub.get('text'))
            
            if not target_lang or (target_lang == cached.get('language')) or has_translation:
                update_task(task_id, 'completed', 100, '从缓存加载',
                           cached_subs, cached.get('language'))
                return
            else:
                logging.info(f"缓存中缺少翻译内容，将重新处理翻译流程 (Target: {target_lang})")
                # 如果有缓存但没翻译，我们可以直接使用缓存的字幕进行翻译，而不必重新下载识别
                subtitles = cached_subs
                lang = cached.get('language')
                
                # 执行翻译逻辑
                effective_key = api_key or (GROQ_API_KEY if service == 'groq' else OPENAI_API_KEY)
                if effective_key:
                    update_task(task_id, 'transcribing', 90, f'正在为缓存字幕请求翻译 ({target_lang})...')
                    subtitles = translate_subtitles(subtitles, target_lang, effective_key, 'groq' if GROQ_API_KEY else 'openai')
                    # 更新缓存
                    save_subtitles_cache(video_id, subtitles, lang)
                    update_task(task_id, 'completed', 100, '翻译已更新', subtitles, lang)
                    return
                else:
                    logging.warning("需要翻译但未提供 API Key，将继续全量流程或报错")
        
        # 下载 (如果没有缓存或无法直接翻译缓存)
        audio_path = download_audio(video_url, task_id)
        
        # 转录
        effective_key = api_key or (GROQ_API_KEY if service == 'groq' else OPENAI_API_KEY)
        if service in ['groq', 'openai']:
            if effective_key:
                subtitles, lang = transcribe_via_api(audio_path, task_id, language, effective_key, service)
                
                # 如果需要翻译
                if target_lang and target_lang != lang:
                    update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
                    subtitles = translate_subtitles(subtitles, target_lang, effective_key, service)
            else:
                raise Exception(f"未提供 {service.upper()} API Key")
        else:
            if not HAS_LOCAL_WHISPER:
                raise Exception("本地 Whisper 依赖未安装，且未提供有效的 API Key")
            subtitles, lang = transcribe_locally(audio_path, task_id, language)
            
            # 本地模式翻译 (如果有配置 API Key)
            if target_lang and (GROQ_API_KEY or OPENAI_API_KEY):
                update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
                api_key = GROQ_API_KEY or OPENAI_API_KEY
                service = 'groq' if GROQ_API_KEY else 'openai'
                subtitles = translate_subtitles(subtitles, target_lang, api_key, service)
        
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

def translate_subtitles(subtitles, target_lang, api_key, service='groq'):
    """
    使用 LLM 批量翻译字幕
    """
    if not subtitles or not target_lang:
        return subtitles

    logging.info(f"开始翻译 {len(subtitles)} 条字幕到 {target_lang} [Service: {service}]")
    
    # 将字幕合并为带 ID 的文本块，减少 API 调用次数并保持上下文
    # 每 30 条一组进行批处理
    batch_size = 30
    translated_subtitles = []
    
    url = "https://api.groq.com/openai/v1/chat/completions" if service == 'groq' else "https://api.openai.com/v1/chat/completions"
    model_name = "llama-3.3-70b-versatile" if service == 'groq' else "gpt-4o-mini" # Groq 推荐用大模型翻译效果更好
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    lang_map = {
        'zh': '中文 (Simplified Chinese)',
        'en': '英文 (English)',
        'ja': '日文 (Japanese)',
        'ko': '韩文 (Korean)',
        'fr': '法语 (French)',
        'de': '德语 (German)',
        'es': '西班牙语 (Spanish)',
        'ru': '俄语 (Russian)'
    }
    target_lang_name = lang_map.get(target_lang, target_lang)

    for i in range(0, len(subtitles), batch_size):
        batch = subtitles[i : i + batch_size]
        
        # 构建 Prompt
        batch_text = "\n".join([f"[{j}] {sub['text']}" for j, sub in enumerate(batch)])
        
        prompt = f"""You are a professional video subtitle translator. 
Translate the following {len(batch)} subtitle lines into {target_lang_name}.

Rules:
1. Maintain the exact numbering format: [index] translated_text
2. One line per subtitle.
3. Keep the original tone and avoid adding explanations.
4. Return ONLY the translated lines.

Subtitles to translate:
{batch_text}
"""

        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "You are a professional translation engine. You always follow the requested format perfectly."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1, # 降低温度以获得更稳定的格式
            "top_p": 1
        }

        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=60.0)
            if response.status_code == 200:
                result = response.json()
                translated_lines = result['choices'][0]['message']['content'].strip().split('\n')
                
                # 解析翻译结果并对应到原字幕
                temp_map = {}
                logging.info(f"LLM 响应样例: {translated_lines[0] if translated_lines else 'EMPTY'}")
                
                for line in translated_lines:
                    import re
                    # 增强正则，兼容更多格式: [1] text, 1. text, [1]: text, etc.
                    match = re.search(r'(?:\[|(?:\b))(\d+)(?:\]|\.)[:\s]*(.*)', line)
                    if match:
                        try:
                            idx = int(match.group(1))
                            trans_text = match.group(2).strip()
                            if trans_text:
                                temp_map[idx] = trans_text
                        except:
                            continue
                
                # 填充翻译
                for j in range(len(batch)):
                    # 如果匹配失败，保留空串而不是覆盖
                    if j in temp_map:
                        batch[j]['translation'] = temp_map[j]
                    elif 'translation' not in batch[j]:
                        batch[j]['translation'] = ""
            else:
                logging.error(f"翻译 API 失败: {response.text}")
                for sub in batch: sub['translation'] = ""
        except Exception as e:
            logging.error(f"翻译异常: {e}")
            for sub in batch: sub['translation'] = ""
            
        translated_subtitles.extend(batch)

    return translated_subtitles

def process_local_file_task(file_path, task_id, language, api_key=None, service='local', target_lang=None):
    """
    处理本地文件上传的任务
    """
    try:
        update_task(task_id, 'transcribing', 10, '正在处理本地文件...')
        
        # 转录
        if service in ['groq', 'openai']:
            effective_key = api_key or (GROQ_API_KEY if service == 'groq' else OPENAI_API_KEY)
            if effective_key:
                subtitles, lang = transcribe_via_api(file_path, task_id, language, effective_key, service)
                
                # 如果需要翻译
                if target_lang and target_lang != lang:
                    update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
                    subtitles = translate_subtitles(subtitles, target_lang, effective_key, service)
            else:
                raise Exception(f"未提供 {service.upper()} API Key")
        else:
            if not HAS_LOCAL_WHISPER:
                raise Exception("本地 Whisper 依赖未安装，且未提供有效的 API Key")
            subtitles, lang = transcribe_locally(file_path, task_id, language)
            
            # 本地模式目前不支持翻译（除非配置了 API Key）
            if target_lang and (GROQ_API_KEY or OPENAI_API_KEY):
                update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
                api_key = GROQ_API_KEY or OPENAI_API_KEY
                service = 'groq' if GROQ_API_KEY else 'openai'
                subtitles = translate_subtitles(subtitles, target_lang, api_key, service)
        
        # 完成 (本地文件不缓存)
        update_task(task_id, 'completed', 100, f'完成！共 {len(subtitles)} 条',
                   subtitles, lang)
        
        # 清理临时文件
        if os.path.exists(file_path):
            os.remove(file_path)
        
    except Exception as e:
        logging.error(f"本地文件任务 {task_id} 失败: {e}")
        update_task(task_id, 'error', 0, str(e))
        # 清理失败的文件
        if os.path.exists(file_path):
            os.remove(file_path)

def worker():
    """
    后台工作线程：不断从队列取任务执行
    """
    logging.info("Worker 线程启动，等待任务...")
    while True:
        try:
            # 阻塞等待任务
            task = task_queue.get()
            task_id = task['task_id']
            language = task.get('language')
            api_key = task.get('api_key')
            service = task.get('service', 'local')
            
            # 检查是否为本地文件上传
            if 'local_file' in task:
                local_file = task['local_file']
                logging.info(f"开始处理本地文件: {task_id} ({local_file}) [Service: {service}]")
                process_local_file_task(local_file, task_id, language, api_key, service, 
                                      target_lang=task.get('target_lang'))
            else:
                video_url = task['video_url']
                logging.info(f"开始处理任务: {task_id} ({video_url}) [Service: {service}]")
                process_video_task(video_url, task_id, language, api_key, service,
                                 target_lang=task.get('target_lang'))
            
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
            target_lang = data.get('target_lang') # 提取翻译目标语言
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
                'service': service,
                'target_lang': target_lang
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
            service = data.get('service', 'local')
            api_key = data.get('api_key')
            target_lang = data.get('target_lang')
            
            if not playlist_url:
                self._send_json({'error': '缺少 playlist_url'}, 400)
                return
            
            # 异步解析列表，避免阻塞 HTTP 响应
            def process_playlist_background(svc, key):
                videos = fetch_playlist_videos(playlist_url)
                added_count = 0
                for v in videos:
                    vid = v['id']
                    v_url = v['url']
                    
                    # 检查是否已有字幕
                    if get_cached_subtitles(vid):
                        continue
                        
                    task_id = vid
                    # 避免重复添加
                    if task_id in tasks and tasks[task_id]['status'] in ['pending', 'downloading', 'transcribing']:
                        continue
                        
                    update_task(task_id, 'pending', 0, f'批量任务 ({svc.upper()}): 等待处理...')
                    task_queue.put({
                        'video_url': v_url,
                        'task_id': task_id,
                        'language': language,
                        'service': svc,
                        'api_key': key,
                        'target_lang': target_lang
                    })
                    added_count += 1
                logging.info(f"批量添加完成，新增 {added_count} 个任务 (服务: {svc})")

            threading.Thread(target=process_playlist_background, args=(service, api_key)).start()
            
            self._send_json({
                'status': 'success',
                'message': '正在后台解析列表并添加到队列，请稍候...'
            })

        # 3. 本地文件上传接口
        elif self.path == '/upload':
            content_type = self.headers.get('Content-Type', '')
            if not content_type.startswith('multipart/form-data'):
                self._send_json({'error': '需要 multipart/form-data 格式'}, 400)
                return

            try:
                # 解析 multipart 数据
                import cgi
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={'REQUEST_METHOD': 'POST'}
                )

                if 'file' not in form:
                    self._send_json({'error': '未找到文件'}, 400)
                    return

                file_item = form['file']
                if not file_item.file:
                    self._send_json({'error': '文件为空'}, 400)
                    return

                # 生成唯一任务 ID
                task_id = hashlib.md5(f"{file_item.filename}_{time.time()}".encode()).hexdigest()[:11]

                # 保存文件
                file_path = TEMP_DIR / f"{task_id}_{file_item.filename}"
                with open(file_path, 'wb') as f:
                    f.write(file_item.file.read())

                # 获取其他参数
                language = form.getvalue('language', 'auto')
                service = form.getvalue('service', 'local')
                api_key = form.getvalue('api_key', '')

                logging.info(f"接收到本地文件上传: {file_item.filename} ({service})")

                # 创建临时 URL（用于流程兼容）
                # 注意：我们需要修改 process_video_task 以支持直接传入文件路径
                update_task(task_id, 'pending', 0, '文件已上传，等待处理...')

                # 直接处理本地文件
                task_queue.put({
                    'local_file': str(file_path),
                    'task_id': task_id,
                    'language': language,
                    'service': service,
                    'api_key': api_key,
                    'target_lang': target_lang
                })

                self._send_json({
                    'task_id': task_id,
                    'status': 'pending',
                    'message': '文件已接收并加入处理队列'
                })

            except Exception as e:
                logging.error(f"文件上传处理失败: {e}")
                self._send_json({'error': f'文件上传失败: {str(e)}'}, 500)

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