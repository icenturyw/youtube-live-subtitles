import queue
import threading
import logging
import time
import os
import httpx
import json
import subprocess
from pathlib import Path
from datetime import datetime
from core.utils import get_video_id, split_text, compress_audio, converter, CACHE_DIR, TEMP_DIR
from core.whisper_engine import whisper_engine
from db.mongodb import mongo_db

class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.task_queue = queue.Queue()
        self.max_concurrent = 1
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def update_task(self, task_id, status, progress, message, subtitles=None, language=None):
        self.tasks[task_id] = {
            'task_id': task_id,
            'status': status,
            'progress': progress,
            'message': message,
            'subtitles': subtitles,
            'detected_language': language,
            'updated_at': time.time()
        }

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def get_task_by_video_id(self, video_id):
        # 1. Search in active tasks
        for task in self.tasks.values():
            if task.get('video_id') == video_id and task.get('status') == 'completed':
                return task
        
        # 2. Check cache/db
        cached = self._get_cached_subtitles(video_id)
        if cached:
            return {
                'task_id': f"cached_{video_id}",
                'status': 'completed',
                'progress': 100,
                'message': '从缓存加载',
                'subtitles': cached.get('subtitles'),
                'detected_language': cached.get('language'),
                'updated_at': time.time()
            }
        return None

    def add_task(self, task_data):
        self.task_queue.put(task_data)
        self.update_task(task_data['task_id'], 'pending', 0, '等待队列处理...')
        # Store video_id for lookup
        if 'video_url' in task_data:
            from core.utils import get_video_id
            self.tasks[task_data['task_id']]['video_id'] = get_video_id(task_data['video_url'])

    def add_upload_task(self, task_id, file_content):
        # For simplicity in this fix, we'll save it to temp and add to queue
        temp_file = TEMP_DIR / f"upload_{task_id}.mp3"
        with open(temp_file, "wb") as f:
            f.write(file_content)
        
        task_data = {
            'task_id': task_id,
            'local_file': str(temp_file),
            'service': 'local' # Default to local for uploads
        }
        self.task_queue.put(task_data)
        self.update_task(task_id, 'pending', 0, '文件已上传，等待处理...')

    def _worker(self):
        logging.info("TaskManager Worker 启动")
        while True:
            task = self.task_queue.get()
            try:
                self._process_task(task)
            except Exception as e:
                logging.error(f"Task processing error: {e}")
                self.update_task(task['task_id'], 'error', 0, str(e))
            finally:
                self.task_queue.task_done()

    def _process_task(self, task):
        task_id = task['task_id']
        video_url = task.get('video_url')
        service = task.get('service', 'local')
        language = task.get('language', 'auto')
        api_key = task.get('api_key') or os.environ.get('GROQ_API_KEY') or os.environ.get('OPENAI_API_KEY')
        target_lang = task.get('target_lang')

        if not video_url:
            # Handle local file if needed, skipping for brevity in this MVP
            return

        video_id = get_video_id(video_url)
        
        # Check Cache
        cached = self._get_cached_subtitles(video_id)
        if cached:
            self.update_task(task_id, 'completed', 100, '从缓存加载', cached.get('subtitles'), cached.get('language'))
            return

        # Download Audio
        audio_path = self._download_audio(video_url, task_id)
        
        # Transcribe
        if service == 'local':
            subtitles, detected_lang = self._transcribe_locally(audio_path, task_id, language)
        else:
            subtitles, detected_lang = self._transcribe_via_api(audio_path, task_id, language, api_key, service)

        # Handle Translation
        if target_lang and subtitles:
            self.update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
            subtitles = self._translate_subtitles(subtitles, target_lang, api_key, service if service != 'local' else 'groq')

        # Cleanup audio
        if os.path.exists(audio_path):
            os.remove(audio_path)

        # Cache results
        self._save_cache(video_id, subtitles, detected_lang)
        self.update_task(task_id, 'completed', 100, '识别完成', subtitles, detected_lang)

    def _get_cached_subtitles(self, video_id):
        cache_file = CACHE_DIR / f"{video_id}.json"
        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        col = mongo_db.get_collection()
        if col is not None:
            doc = col.find_one({"video_id": video_id}, {"_id": 0})
            if doc:
                return doc
        return None

    def _save_cache(self, video_id, subtitles, language):
        data = {
            'video_id': video_id,
            'language': language,
            'created_at': datetime.now().isoformat(),
            'subtitles': subtitles
        }
        with open(CACHE_DIR / f"{video_id}.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        col = mongo_db.get_collection()
        if col is not None:
            col.update_one({"video_id": video_id}, {"$set": data}, upsert=True)

    def _download_audio(self, video_url, task_id):
        self.update_task(task_id, 'downloading', 10, '正在下载音频...')
        video_id = get_video_id(video_url)
        output = str(TEMP_DIR / f"{video_id}.mp3")
        cmd = ['yt-dlp', '-x', '--audio-format', 'mp3', '--audio-quality', '128K', '-o', output, video_url]
        subprocess.run(cmd, check=True, capture_output=True)
        return output

    def _transcribe_locally(self, audio_path, task_id, language):
        self.update_task(task_id, 'transcribing', 50, '正在本地识别...')
        segments, info = whisper_engine.transcribe(audio_path, language)
        subtitles = []
        for segment in segments:
            text = segment.text.strip()
            if not text: continue
            if converter: text = converter.convert(text)
            
            # Simple splitting logic integrated
            parts = split_text(text)
            duration = segment.end - segment.start
            p_dur = duration / len(parts)
            for i, p in enumerate(parts):
                subtitles.append({
                    'start': round(segment.start + i * p_dur, 2),
                    'end': round(segment.start + (i + 1) * p_dur, 2),
                    'text': p
                })
        return subtitles, info.language

    def _transcribe_via_api(self, audio_path, task_id, language, api_key, service):
        if not api_key:
            raise Exception(f"未提供 {service} API Key")
        
        # 检查并压缩大文件
        original_path = audio_path
        audio_path = compress_audio(audio_path)
        
        self.update_task(task_id, 'transcribing', 48, f'正在向 {service.upper()} 上传并识别...')
        
        url = "https://api.groq.com/openai/v1/audio/transcriptions" if service == 'groq' else "https://api.openai.com/v1/audio/transcriptions"
        model_name = "whisper-large-v3" if service == 'groq' else "whisper-1"
        
        headers = {"Authorization": f"Bearer {api_key}"}
        
        try:
            files = {"file": (Path(audio_path).name, open(audio_path, "rb"), "audio/mpeg")}
            data = {"model": model_name, "response_format": "verbose_json"}
            if language and language != 'auto':
                data["language"] = language

            with httpx.Client() as client:
                response = client.post(url, headers=headers, data=data, files=files, timeout=300)
                files["file"][1].close()
                
                if audio_path != original_path and os.path.exists(audio_path):
                    os.remove(audio_path)
                
                if response.status_code != 200:
                    raise Exception(f"API 错误: {response.text}")
                
                result = response.json()
                raw_segments = result.get('segments', [])
                detected_lang = result.get('language', language)
                
                subtitles = []
                for seg in raw_segments:
                    text = seg['text'].strip()
                    if not text: continue
                    if converter: text = converter.convert(text)
                    
                    if len(text) > 25:
                        parts = split_text(text)
                        duration = seg['end'] - seg['start']
                        p_dur = duration / len(parts)
                        for i, p in enumerate(parts):
                            subtitles.append({
                                'start': round(seg['start'] + i * p_dur, 2),
                                'end': round(seg['start'] + (i + 1) * p_dur, 2),
                                'text': p
                            })
                    else:
                        subtitles.append({'start': round(seg['start'], 2), 'end': round(seg['end'], 2), 'text': text})
                
                return subtitles, detected_lang
        except Exception as e:
            raise Exception(f"{service.upper()} API 调用失败: {str(e)}")

    def _translate_subtitles(self, subtitles, target_lang, api_key, service='groq'):
        if not subtitles or not target_lang or not api_key:
            return subtitles

        batch_size = 30
        translated_subtitles = []
        url = "https://api.groq.com/openai/v1/chat/completions" if service == 'groq' else "https://api.openai.com/v1/chat/completions"
        model_name = "llama-3.3-70b-versatile" if service == 'groq' else "gpt-4o-mini"
        
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        lang_map = {'zh': 'Simplified Chinese', 'en': 'English', 'ja': 'Japanese', 'ko': 'Korean', 'fr': 'French', 'de': 'German', 'es': 'Spanish', 'ru': 'Russian'}
        target_lang_name = lang_map.get(target_lang, target_lang)

        for i in range(0, len(subtitles), batch_size):
            batch = subtitles[i : i + batch_size]
            batch_text = "\n".join([f"[{j}] {sub['text']}" for j, sub in enumerate(batch)])
            prompt = f"Translate the following {len(batch)} subtitle lines into {target_lang_name}. Maintain exact format: [index] translated_text\n\n{batch_text}"

            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": "You are a professional translator. Return ONLY the translated lines."}, {"role": "user", "content": prompt}],
                "temperature": 0.1
            }

            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=60.0)
                if response.status_code == 200:
                    result = response.json()
                    lines = result['choices'][0]['message']['content'].strip().split('\n')
                    temp_map = {}
                    for line in lines:
                        match = re.search(r'\[(\d+)\]\s*(.*)', line)
                        if match:
                            temp_map[int(match.group(1))] = match.group(2).strip()
                    
                    for j in range(len(batch)):
                        batch[j]['translation'] = temp_map.get(j, "")
                else:
                    for sub in batch: sub['translation'] = ""
            except:
                for sub in batch: sub['translation'] = ""
            translated_subtitles.extend(batch)
        return translated_subtitles

task_manager = TaskManager()
