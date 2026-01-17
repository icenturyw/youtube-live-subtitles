import queue
import threading
import logging
import time
import os
import httpx
import json
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

    def add_task(self, task_data):
        self.task_queue.put(task_data)
        self.update_task(task_data['task_id'], 'pending', 0, '等待队列处理...')

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
        if col:
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
        if col:
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
        self.update_task(task_id, 'transcribing', 50, f'正在上传至 {service}...')
        # API logic simplified for brevity - in production would use full transcribe_via_api logic
        # For now, placeholder or full port
        return [], "en"

task_manager = TaskManager()
