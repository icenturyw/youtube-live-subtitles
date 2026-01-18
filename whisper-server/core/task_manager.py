import queue
import threading
import logging
import time
import os
import re
import httpx
import json
import subprocess
from pathlib import Path
from datetime import datetime
import json_repair
from core.utils import get_video_id, split_text, compress_audio, converter, CACHE_DIR, TEMP_DIR, RAW_CACHE_DIR
from core.whisper_engine import whisper_engine
from db.supabase_db import supabase_db
from core.lexicon import get_prompt_by_domain
from core.prompts import (
    get_prompt_faithfulness, 
    get_prompt_correction, 
    get_summary_prompt, 
    get_prompt_expressiveness, 
    generate_shared_prompt
)


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
        for task in self.tasks.values():
            if task.get('video_id') == video_id and task.get('status') == 'completed':
                # 简单返回第一个匹配的已完成任务（通常是最近的一个）
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
        task_id = task_data['task_id']
        self.task_queue.put(task_data)
        self.update_task(task_id, 'pending', 0, '等待队列处理...')
        # Store metadata in tasks dict early (or update existing)
        if task_id not in self.tasks:
            self.tasks[task_id] = {'task_id': task_id}
            
        self.tasks[task_id].update({
            'video_url': task_data.get('video_url'),
            'service': task_data.get('service', 'local'),
            'domain': task_data.get('domain', 'general'),
            'engine': task_data.get('engine', 'whisper'),
            'target_lang': task_data.get('target_lang'),
            'llm_correction': task_data.get('llm_correction', False),
            'status': 'pending',
            'progress': 0,
            'message': '等待队列处理...',
            'subtitles': []
        })
        # Store video_id for lookup
        if 'video_url' in task_data:
            from core.utils import get_video_id
            self.tasks[task_data['task_id']]['video_id'] = get_video_id(task_data['video_url'])

    def add_upload_task(self, task_id, file_content, service='local', api_key=None, language='auto', target_lang=None):
        # Save file to temp
        temp_file = TEMP_DIR / f"upload_{task_id}.mp3"
        with open(temp_file, "wb") as f:
            f.write(file_content)
        
        task_data = {
            'task_id': task_id,
            'local_file': str(temp_file),
            'service': service,
            'api_key': api_key,
            'language': language,
            'target_lang': target_lang
        }
        self.task_queue.put(task_data)
        self.update_task(task_id, 'pending', 0, '文件已上传，等待处理...')

    def _worker(self):
        logging.info("TaskManager Worker 启动")
        while True:
            task = self.task_queue.get()
            start_time = time.time()
            task_id = task.get('task_id')
            try:
                logging.info(f"[{task_id}] 开始处理任务")
                self._process_task(task)
                duration = time.time() - start_time
                logging.info(f"[{task_id}] 任务处理成功, 总耗时: {duration:.2f}s")
            except Exception as e:
                duration = time.time() - start_time
                logging.error(f"[{task_id}] 任务处理失败 (耗时: {duration:.2f}s): {e}", exc_info=True)
                self.update_task(task_id, 'error', 0, f"处理失败: {str(e)}")
            finally:
                self.task_queue.task_done()

    def _process_task(self, task):
        task_id = task['task_id']
        video_url = task.get('video_url')
        service = task.get('service', 'local')
        language = task.get('language', 'auto')
        domain = task.get('domain', 'general')
        api_key = task.get('api_key') or os.environ.get('SILICONFLOW_API_KEY') or os.environ.get('GROQ_API_KEY') or os.environ.get('OPENAI_API_KEY')
        target_lang = task.get('target_lang')
        engine_type = task.get('engine', 'whisper')
        
        # 确定 initial_prompt
        initial_prompt = get_prompt_by_domain(domain)
        
        # 添加调试日志
        logging.info(f"[Task {task_id}] Engine: {engine_type}, Service: {service}, Domain: {domain}, Language: {language}")

        if not video_url:
            # Handle local file
            audio_path = task.get('local_file')
            if not audio_path or not os.path.exists(audio_path):
                self.update_task(task_id, 'error', 0, "找不到本地音频文件")
                return
            video_id = task_id # Use task_id as reference for uploads
        else:
            # Check Cache - ONLY for YouTube videos
            video_id = get_video_id(video_url)
            
            # 1. 检查最终缓存 (完全匹配)
            cached = self._get_cached_subtitles(video_id)
            if cached:
                if (service == cached.get('service', 'local') and 
                    domain == cached.get('domain', 'general') and
                    engine_type == cached.get('engine', 'whisper') and
                    target_lang == cached.get('target_lang')):
                    logging.info(f"[Task {task_id}] 命中最终缓存: {video_id}")
                    self.update_task(task_id, 'completed', 100, '从缓存加载', cached.get('subtitles'), cached.get('language'))
                    return

            # 2. 检查阶段性缓存 (原始识别结果)
            raw_cached = self._get_raw_cache(video_id)
            if raw_cached and raw_cached.get('engine') == engine_type and raw_cached.get('domain') == domain:
                logging.info(f"[Task {task_id}] 命中原始识别缓存，跳过 Whisper，直接进入后续流程")
                subtitles = raw_cached.get('subtitles')
                detected_lang = raw_cached.get('language')
            else:
                # Download Audio
                audio_path = self._download_audio(video_url, task_id)
                
                # Transcription Stage
                if service == 'local':
                    if engine_type == 'sensevoice':
                        from core.sensevoice_engine import sensevoice_engine
                        logging.info(f"[Task {task_id}] 使用 SenseVoice 识别 (Prompt: {domain})")
                        self.update_task(task_id, 'transcribing', 30, '正在使用 SenseVoice 识别...')
                        subtitles = sensevoice_engine.transcribe(audio_path, language)
                        detected_lang = language 
                    else:
                        logging.info(f"[Task {task_id}] 使用本地 Whisper 识别 (Prompt: {domain})")
                        self.update_task(task_id, 'transcribing', 30, '正在使用 Whisper 识别...')
                        subtitles, detected_lang = self._transcribe_locally(audio_path, task_id, language, initial_prompt=initial_prompt)
                else:
                    logging.info(f"[Task {task_id}] 使用 {service.upper()} API 识别 (Prompt: {domain})")
                    subtitles, detected_lang = self._transcribe_via_api(audio_path, task_id, language, api_key, service, initial_prompt=initial_prompt)
                
                # 保存原始识别结果
                self._save_raw_cache(video_id, subtitles, detected_lang, domain, engine_type)
        
        # [NEW] Optional Correction Step using LM Studio
        # 仅当前端开启了 llm_correction 并且配置了 LM_STUDIO_API_URL 时才执行
        llm_correction_enabled = task.get('llm_correction', False)
        if llm_correction_enabled and os.environ.get('LM_STUDIO_API_URL'):
            self.update_task(task_id, 'transcribing', 70, '正在使用 LM Studio 修正文本...')
            subtitles = self._correct_transcription(subtitles, detected_lang)

        if target_lang and subtitles:
            self.update_task(task_id, 'transcribing', 90, f'正在翻译为 {target_lang}...')
            trans_start = time.time()
            subtitles = self._translate_subtitles(subtitles, target_lang, api_key, service if service != 'local' else 'groq', src_lang=detected_lang, task_id=task_id)
            logging.info(f"[{task_id}] 翻译完成, 耗时: {time.time() - trans_start:.2f}s")

        # [MODIFIED] 不再自动删除音频文件，以便复用
        # Cache final results
        if video_url:
            self._save_cache(video_id, subtitles, detected_lang, service, domain, engine_type, target_lang)
        
        self.update_task(task_id, 'completed', 100, '完成', subtitles, detected_lang)
        # 确保完成的任务里也包含 domain 信息
        if task_id in self.tasks:
            self.tasks[task_id]['domain'] = domain
            self.tasks[task_id]['service'] = service
            self.tasks[task_id]['engine'] = engine_type

    def _get_raw_cache(self, video_id):
        raw_cache_file = RAW_CACHE_DIR / f"{video_id}.json"
        if raw_cache_file.exists():
            try:
                with open(raw_cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return None

    def _save_raw_cache(self, video_id, subtitles, language, domain, engine):
        RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            'video_id': video_id,
            'language': language,
            'domain': domain,
            'engine': engine,
            'created_at': datetime.now().isoformat(),
            'subtitles': subtitles
        }
        with open(RAW_CACHE_DIR / f"{video_id}.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_cached_subtitles(self, video_id):
        # 1. 优先尝试本地 JSON 缓存文件
        cache_file = CACHE_DIR / f"{video_id}.json"
        if cache_file.exists():
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 2. 尝试从 Supabase 加载
        doc = supabase_db.get_by_video_id(video_id)
        if doc:
            return doc
            
        return None

    def _save_cache(self, video_id, subtitles, language, service, domain, engine='whisper', target_lang=None):
        data = {
            'video_id': video_id,
            'language': language,
            'service': service,
            'domain': domain,
            'engine': engine,
            'target_lang': target_lang,
            'created_at': datetime.now().isoformat(),
            'subtitles': subtitles
        }
        # 1. 保存到本地 JSON
        with open(CACHE_DIR / f"{video_id}.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 2. 异步/同步到 Supabase
        supabase_db.upsert_subtitles(data)

    def delete_video_cache(self, video_id):
        """
        删除指定视频的所有缓存数据（本地文件、内存、MongoDB）
        """
        deleted_items = []
        
        # 1. 删除本地 JSON 缓存文件
        cache_file = CACHE_DIR / f"{video_id}.json"
        if cache_file.exists():
            try:
                cache_file.unlink()
                deleted_items.append('local_cache')
                logging.info(f"已删除本地缓存文件: {cache_file}")
            except Exception as e:
                logging.error(f"删除本地缓存文件失败: {e}")
        
        # 2. 从内存中删除任务记录
        if video_id in self.tasks:
            del self.tasks[video_id]
            deleted_items.append('memory_task')
            logging.info(f"已从内存中删除任务: {video_id}")
        
        # 3. 从 Supabase 中删除记录
        if supabase_db.client:
            try:
                response = supabase_db.client.table("subtitles").delete().eq("video_id", video_id).execute()
                if response.data:
                    deleted_items.append('supabase')
                    logging.info(f"已从 Supabase 删除记录: {video_id}")
            except Exception as e:
                logging.error(f"从 Supabase 删除记录失败: {e}")
        
        return {
            'video_id': video_id,
            'deleted_items': deleted_items,
            'success': len(deleted_items) > 0
        }

    def _download_audio(self, video_url, task_id):
        self.update_task(task_id, 'downloading', 10, '正在下载音频...')
        video_id = get_video_id(video_url)
        output = str(TEMP_DIR / f"{video_id}.mp3")
        
        # Check if file already exists
        if os.path.exists(output) and os.path.getsize(output) > 0:
            logging.info(f"[{task_id}] 音频文件已存在，跳过下载: {output}")
            self.update_task(task_id, 'downloading', 100, '音频已存在，直接使用...')
            return output

        start_time = time.time()
        # 增加 --no-playlist 确保只下载单个视频，防止下载整个列表导致识别错乱
        cmd = ['yt-dlp', '--no-playlist', '-x', '--audio-format', 'mp3', '--audio-quality', '128K', '-o', output, video_url]
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                logging.info(f"[{task_id}] 音频下载完成, 耗时: {time.time() - start_time:.2f}s")
                return output
            except subprocess.CalledProcessError as e:
                if attempt < max_retries - 1:
                    logging.warning(f"[{task_id}] 下载失败, 正在重试 ({attempt + 1}/{max_retries}): {e.stderr.decode() if e.stderr else str(e)}")
                    time.sleep(2 * (attempt + 1))
                else:
                    raise Exception(f"音频下载最终失败: {e.stderr.decode() if e.stderr else str(e)}")

    def _transcribe_locally(self, audio_path, task_id, language, initial_prompt=None):
        self.update_task(task_id, 'transcribing', 50, '正在本地识别...')
        segments, info = whisper_engine.transcribe(audio_path, language, initial_prompt=initial_prompt)
        subtitles = []
        for segment in segments:
            text = segment.text.strip()
            if not text: continue
            if converter: text = converter.convert(text)
            
            # 使用 split_text 拆分长句
            # Use detected language from Whisper info
            parts = split_text(text, lang=info.language if info.language else 'zh')
            
            if len(parts) <= 1 or not segment.words:
                # 没拆分或者没有单词时间戳，按原样处理
                subtitles.append({
                    'start': round(segment.start, 2),
                    'end': round(segment.end, 2),
                    'text': text
                })
            else:
                # 有拆分且有单词时间戳，尝试更精准的对齐
                words = segment.words # List of Word objects (start, end, word)
                full_text_cleaned = "".join([w.word.strip() for w in words])
                
                word_idx = 0
                for part in parts:
                    part_cleaned = part.replace(" ", "")
                    if not part_cleaned: continue
                    
                    part_start = None
                    part_end = segment.start
                    
                    current_part_content = ""
                    while word_idx < len(words) and len(current_part_content) < len(part_cleaned):
                        w = words[word_idx]
                        w_clean = w.word.strip()
                        if not w_clean: 
                            word_idx += 1
                            continue
                            
                        if part_start is None:
                            part_start = w.start
                        
                        part_end = w.end
                        current_part_content += w_clean
                        word_idx += 1
                    
                    subtitles.append({
                        'start': round(part_start if part_start is not None else segment.start, 2),
                        'end': round(part_end, 2),
                        'text': part
                    })
                
                # 补查：如果还有剩余的 words 没分完，合并到最后一个段落
                if word_idx < len(words) and subtitles:
                    subtitles[-1]['end'] = round(words[-1].end, 2)
        return subtitles, info.language

    def _transcribe_via_api(self, audio_path, task_id, language, api_key, service, initial_prompt=None):
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
            if initial_prompt:
                data["prompt"] = initial_prompt

            with httpx.Client() as client:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        response = client.post(url, headers=headers, data=data, files=files, timeout=300)
                        if response.status_code == 200:
                            break
                        elif attempt < max_retries - 1:
                            logging.warning(f"[{task_id}] {service.upper()} 接口返回错误 {response.status_code}, 正在重试...")
                            time.sleep(2 * (attempt + 1))
                        else:
                            raise Exception(f"API 最终返回错误: {response.text}")
                    except Exception as e:
                        if attempt < max_retries - 1:
                            logging.warning(f"[{task_id}] {service.upper()} 接口调用异常, 正在重试: {e}")
                            time.sleep(2 * (attempt + 1))
                        else:
                            raise e

                files["file"][1].close()
                
                if audio_path != original_path and os.path.exists(audio_path):
                    os.remove(audio_path)
                
                result = response.json()
                raw_segments = result.get('segments', [])
                detected_lang = result.get('language', language)
                
                subtitles = []
                for seg in raw_segments:
                    text = seg['text'].strip()
                    if not text: continue
                    if converter: text = converter.convert(text)
                    
                    if len(text) > 25:
                        parts = split_text(text, lang=detected_lang if detected_lang else 'zh')
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

    def _ask_llm(self, prompt, model_name, url, headers, resp_type="json", trust_env=False, task_id=None):
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        # [FIX] LM Studio and some local backends fail with 400 error if response_format is provided
        if resp_type == "json" and "localhost" not in url and "127.0.0.1" not in url:
             payload["response_format"] = {"type": "json_object"}

        try:
            client_kwargs = {"timeout": 150.0}
            if trust_env is False:
                client_kwargs["trust_env"] = False
            
            with httpx.Client(**client_kwargs) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                content = response.json()['choices'][0]['message']['content'].strip()
                if not content:
                    return None
                
                if resp_type == "json":
                    # Use json_repair for robustness
                    try:
                        # Pre-clean for common LLM issues
                        content_clean = content.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "").strip()
                        # Extract from markdown if present
                        match = re.search(r'```(?:json)?\s*(.*?)\s*```', content_clean, re.DOTALL)
                        if match:
                            content_clean = match.group(1).strip()
                        return json_repair.loads(content_clean)
                    except Exception as e:
                        logging.error(f"[{task_id}] JSON Repair failed: {e}. Content: {content[:100]}...")
                        return None
                return content
        except Exception as e:
            logging.error(f"[{task_id}] LLM Request failed: {e}")
            return None

    def _translate_subtitles(self, subtitles, target_lang, api_key, service='groq', src_lang='auto', task_id=None):
        if not subtitles or not target_lang:
            return subtitles

        # Prepare LLM config
        lm_studio_url = os.environ.get('LM_STUDIO_API_URL')
        lm_studio_model = os.environ.get('LM_STUDIO_MODEL_NAME', 'local-model')
        
        headers = {"Content-Type": "application/json"}
        trust_env = True
        if lm_studio_url:
            url = lm_studio_url.rstrip('/')
            if not any(url.endswith(s) for s in ['/chat/completions', '/v1']):
                 url = f"{url}/v1/chat/completions"
            elif url.endswith('/v1'):
                 url = f"{url}/chat/completions"
            model_name = lm_studio_model
            trust_env = False
        else:
            if not api_key: return subtitles
            url = "https://api.groq.com/openai/v1/chat/completions" if service == 'groq' else "https://api.openai.com/v1/chat/completions"
            model_name = "llama-3.3-70b-versatile" if service == 'groq' else "gpt-4o-mini"
            headers["Authorization"] = f"Bearer {api_key}"

        lang_map = {'zh': 'Simplified Chinese', 'en': 'English', 'ja': 'Japanese', 'ko': 'Korean', 'fr': 'French', 'de': 'German', 'es': 'Spanish', 'ru': 'Russian'}
        target_lang_name = lang_map.get(target_lang, target_lang)
        src_lang_name = lang_map.get(src_lang, src_lang)

        # Step 1: Summary & Terminology
        full_text = "\n".join([sub['text'] for sub in subtitles[:50]]) # Sample first 50 lines for summary
        summary_prompt = get_summary_prompt(full_text, src_lang_name, target_lang_name)
        logging.info(f"[{task_id}] Generating video summary and terms...")
        summary_data = self._ask_llm(summary_prompt, model_name, url, headers, task_id=task_id, trust_env=trust_env)
        
        theme_context = ""
        if summary_data and isinstance(summary_data, dict):
            theme = summary_data.get('theme', '')
            terms = summary_data.get('terms', [])
            terms_str = "\n".join([f"- {t.get('src')}: {t.get('tgt')} ({t.get('note')})" for t in terms])
            theme_context = f"Topic: {theme}\nTerms:\n{terms_str}"
        
        # Step 2: Translation with Faithfulness & Expressiveness
        batch_size = 15 if lm_studio_url else 30
        translated_subtitles = []

        for i in range(0, len(subtitles), batch_size):
            batch = subtitles[i : i + batch_size]
            lines_text = "\n".join([sub['text'] for sub in batch])
            
            # Context
            prev_batch = subtitles[max(0, i-3):i]
            after_batch = subtitles[i+batch_size:i+batch_size+2]
            prev_text = "\n".join([s['text'] for s in prev_batch])
            after_text = "\n".join([s['text'] for s in after_batch])
            
            shared_prompt = generate_shared_prompt(prev_text, after_text, theme_context, "")
            
            # --- Faithfulness Step ---
            faith_prompt = get_prompt_faithfulness(lines_text, shared_prompt, src_lang_name, target_lang_name)
            logging.info(f"[{task_id}] Translating batch {i//batch_size + 1} (Faithfulness)...")
            
            faith_result = None
            for attempt in range(2):
                res = self._ask_llm(faith_prompt, model_name, url, headers, task_id=task_id, trust_env=trust_env)
                if res and isinstance(res, dict) and len(res) >= len(batch):
                    faith_result = res
                    break
                logging.warning(f"[{task_id}] Faithfulness mismatch/failure at {i}, retry {attempt+1}")

            if not faith_result:
                # Emergency fallback if faith fails
                logging.error(f"[{task_id}] Faithfulness failed after retries, filling empty.")
                for sub in batch: sub['translation'] = ""
                translated_subtitles.extend(batch)
                continue

            # --- Expressiveness Step ---
            express_prompt = get_prompt_expressiveness(faith_result, lines_text, shared_prompt, src_lang_name, target_lang_name)
            logging.info(f"[{task_id}] Refining batch {i//batch_size + 1} (Expressiveness)...")
            
            final_result = None
            for attempt in range(2):
                res = self._ask_llm(express_prompt, model_name, url, headers, task_id=task_id, trust_env=trust_env)
                if res and isinstance(res, dict) and len(res) >= len(batch):
                    final_result = res
                    break
                logging.warning(f"[{task_id}] Expressiveness mismatch/failure at {i}, retry {attempt+1}")

            # Map back
            use_result = final_result if final_result else faith_result
            for j, sub in enumerate(batch):
                key = str(j)
                val = use_result.get(key, {})
                if isinstance(val, dict):
                    sub['translation'] = val.get('free') or val.get('direct') or ""
                else:
                    sub['translation'] = str(val)
            
            translated_subtitles.extend(batch)
        return translated_subtitles

    def _correct_transcription(self, subtitles, src_lang):
        """
        Use LM Studio (or compatible API) to correct subtitles without changing timestamps or number of lines.
        """
        api_url = os.environ.get('LM_STUDIO_API_URL')
        model_name = os.environ.get('LM_STUDIO_MODEL_NAME', 'local-model')
        
        if not api_url or not subtitles:
            return subtitles
            
        batch_size = 30 # Can be adjusted
        corrected_subtitles = []
        headers = {"Content-Type": "application/json"}
        
        full_url = api_url.rstrip('/')
        
        # Smart URL handling
        if full_url.endswith('/chat/completions'):
            pass # Already full URL
        elif full_url.endswith('/v1'):
            full_url = f"{full_url}/chat/completions"
        else:
            full_url = f"{full_url}/v1/chat/completions"

        for i in range(0, len(subtitles), batch_size):
            batch = subtitles[i : i + batch_size]
            lines_text = "\n".join([sub['text'] for sub in batch])
            
            prompt = get_prompt_correction(lines_text, src_lang)
            
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                # "response_format": {"type": "json_object"} # LM Studio might not support this depending on backend, safer to omit or try
            }
            
            try:
                # Need longer timeout for local inference
                # [MODIFIED] Create a client that ignores env proxies for local connection
                with httpx.Client(trust_env=False, timeout=120.0) as client:
                    response = client.post(full_url, headers=headers, json=payload)
                
                if response.status_code == 200:
                    content = response.json()['choices'][0]['message']['content'].strip()
                    
                    # Parse JSON - Robust extraction
                    import re
                    
                    # 1. Try Markdown code block
                    match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
                    if match:
                        content = match.group(1)
                    else:
                        # 2. Try GLM-style specific format
                        match = re.search(r'<\|begin_of_box\|>\s*(.*?)\s*<\|end_of_box\|>', content, re.DOTALL)
                        if match:
                            content = match.group(1)
                        else:
                            # 3. Fallback: Find first { and last }
                            start_idx = content.find('{')
                            end_idx = content.rfind('}')
                            if start_idx != -1 and end_idx != -1:
                                content = content[start_idx:end_idx+1]
                    
                    try:
                        json_result = json.loads(content)
                        for j, sub in enumerate(batch):
                            key = str(j)
                            if key in json_result and 'corrected' in json_result[key]:
                                # Only update text, keep timestamps
                                sub['text'] = json_result[key]['corrected']
                    except Exception as e:
                         # Fallback to no change
                         logging.error(f"LM Studio JSON parse failed: {e}")
                else:
                     logging.error(f"LM Studio API failed: {response.text}")

            except Exception as e:
                logging.error(f"LM Studio Exception: {e}")
            
            corrected_subtitles.extend(batch)
            
        return corrected_subtitles

task_manager = TaskManager()
