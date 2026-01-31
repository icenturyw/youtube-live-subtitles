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
from core.utils import (
    get_video_id, split_text, compress_audio, get_audio_duration, 
    split_audio, enhance_audio_for_speech, converter, CACHE_DIR, TEMP_DIR, RAW_CACHE_DIR
)
from core.whisper_engine import whisper_engine
from db.postgres_db import postgres_db
from core.lexicon import get_prompt_by_domain

# 幻觉黑名单：Whisper 模型在遇到静音或无意义音频时常产生的“幻觉”短语
LLM_HALLUCINATION_BLACKLIST = [
    "点赞", "订阅", "转发", "打赏", "支持明镜", "点点栏目", "谢看", "多谢看", 
    "字幕由", "制作", "欢迎订阅", "谢谢观看", "观看更多", "关注我的频道"
]

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
                logging.info(f"[Worker] 正在处理任务: {task_id}")
                self._process_task(task)
                logging.info(f"[Worker] 任务处理结束: {task_id}")
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
            # [FIX] 仅当原始识别结果不为空时才命中缓存
            if (raw_cached and raw_cached.get('engine') == engine_type and 
                raw_cached.get('domain') == domain and raw_cached.get('subtitles')):
                logging.info(f"[Task {task_id}] 命中原始识别缓存，跳过 Whisper，直接进入后续流程")
                subtitles = raw_cached.get('subtitles')
                detected_lang = raw_cached.get('language')
            else:
                # Download Audio
                audio_path = self._download_audio(video_url, task_id)
                
                # Transcription Stage
                logging.info(f"[Task {task_id}] 开始识别阶段, 模式: {service}, 引擎: {engine_type}")
                
                # [NEW] 音频预处理：如果是音乐或背景音复杂，进行人声增强
                if domain == 'music':
                    self.update_task(task_id, 'transcribing', 25, '正在进行人声增强预处理（降低背景音乐干扰）...')
                    audio_path = enhance_audio_for_speech(audio_path)
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
                elif service == 'qwen3-asr':
                    from core.qwen3_asr_engine import qwen3_asr_engine
                    logging.info(f"[Task {task_id}] 使用 Qwen3-ASR API 识别")
                    self.update_task(task_id, 'transcribing', 30, '正在使用 Qwen3-ASR 识别...')
                    subtitles, detected_lang = qwen3_asr_engine.transcribe(audio_path, language)
                elif service == 'cloudflare':
                    logging.info(f"[Task {task_id}] 使用 Cloudflare Workers AI 识别")
                    subtitles, detected_lang = self._transcribe_via_cloudflare(audio_path, task_id, language)
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
            trans_start = time.time()
            subtitles = self._translate_subtitles(
                subtitles, 
                target_lang, 
                api_key, 
                service, 
                src_lang=detected_lang, 
                task_id=task_id, 
                llm_correction=llm_correction_enabled
            )
            logging.info(f"[{task_id}] 翻译阶段耗时: {time.time() - trans_start:.2f}s")

        # [MODIFIED] 不再自动删除音频文件，以便复用
        # Cache final results
        if video_url and subtitles:
            self._save_cache(video_id, subtitles, detected_lang, service, domain, engine_type, target_lang)
            self.update_task(task_id, 'completed', 100, '完成', subtitles, detected_lang)
        elif video_url and not subtitles:
            logging.warning(f"[{task_id}] 未识别到任何内容，不保存缓存")
            raise Exception("Groq/Whisper 未识别到任何有效音频内容。请确认视频是否有语音，或尝试开启 '清除缓存并重试'。")
        else:
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
        
        # 2. 尝试从 PostgreSQL 加载
        doc = postgres_db.get_by_video_id(video_id)
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
        
        # 2. 同步到 PostgreSQL
        postgres_db.upsert_subtitles(data)

    def delete_video_cache(self, video_id):
        """
        删除指定视频的所有缓存数据（本地文件、内存、MongoDB/Supabase）
        保留下载的音频文件以供重试时复用
        """
        deleted_items = []
        
        # 1. 删除本地最终 JSON 缓存文件
        cache_file = CACHE_DIR / f"{video_id}.json"
        if cache_file.exists():
            try:
                cache_file.unlink()
                deleted_items.append('final_cache')
                logging.info(f"已删除本地最终缓存文件: {cache_file}")
            except Exception as e:
                logging.error(f"删除本地最终缓存文件失败: {e}")

        # 2. 删除本地原始识别 JSON 缓存文件 (RAW)
        raw_cache_file = RAW_CACHE_DIR / f"{video_id}.json"
        if raw_cache_file.exists():
            try:
                raw_cache_file.unlink()
                deleted_items.append('raw_cache')
                logging.info(f"已删除本地原始缓存文件: {raw_cache_file}")
            except Exception as e:
                logging.error(f"删除本地原始缓存文件失败: {e}")
        
        # 3. 从内存中删除任务记录
        if video_id in self.tasks:
            del self.tasks[video_id]
            deleted_items.append('memory_task')
            logging.info(f"已从内存中删除任务: {video_id}")
        
        # 4. 从 PostgreSQL 中删除记录
        try:
            if postgres_db.delete_by_video_id(video_id):
                deleted_items.append('postgres_record')
        except Exception as e:
            logging.error(f"从 PostgreSQL 删除记录失败: {e}")
        
        return {
            'video_id': video_id,
            'deleted_items': deleted_items,
            'success': len(deleted_items) > 0,
            'preserving': 'audio_file'
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
                word_idx = 0
                last_end = segment.start
                
                for part in parts:
                    part_cleaned = part.replace(" ", "").lower()
                    if not part_cleaned: continue
                    
                    part_start = None
                    current_part_content = ""
                    
                    # 寻找匹配当前 part 的单词
                    while word_idx < len(words):
                        w = words[word_idx]
                        w_clean = w.word.strip().lower()
                        if not w_clean: 
                            word_idx += 1
                            continue
                            
                        if part_start is None:
                            part_start = max(w.start, last_end)
                        
                        current_part_content += w_clean
                        last_end = w.end
                        word_idx += 1
                        
                        # 如果匹配内容长度接近或超过 part_cleaned，停止当前 part 的单词收集
                        if len(current_part_content) >= len(part_cleaned):
                            break
                    
                    # 确保 start < end 且不为 0 (除非确实在开头)
                    # 如果没找到匹配的单词，则 fallback 到 segment 的时间比例
                    s_time = round(part_start if part_start is not None else last_end, 2)
                    e_time = round(last_end, 2)
                    
                    if e_time <= s_time:
                         e_time = s_time + 0.1 # 最小持续时间

                    text_clean = part.replace(" ", "").replace(",", "").replace(".", "").replace("!", "").replace("?", "").lower()
                    
                    # 判据 1：完全匹配黑名单词 (例如纯粹的 "点赞" 占用了很长时间)
                    is_exact_match = any(text_clean == term.replace(" ", "").lower() for term in LLM_HALLUCINATION_BLACKLIST)
                    # 判据 2：包含特定的、极其独特的幻觉短语 (明镜/点点栏目)
                    specific_hallucinations = ["支持明镜", "点点栏目"]
                    is_specific_pattern = any(term in text_clean for term in specific_hallucinations)
                    
                    filtered = False
                    duration = e_time - s_time
                    if is_exact_match and duration > 5:
                        filtered = True
                    elif is_specific_pattern and (duration > 10 and len(text_clean) < 35):
                        filtered = True
                    
                    if filtered:
                        logging.info(f"[{task_id}] 过滤疑似幻觉内容: {part} ({duration}s)")
                        continue

                    subtitles.append({
                        'start': s_time,
                        'end': e_time,
                        'text': part
                    })
        return subtitles, info.language

    def _transcribe_via_api(self, audio_path, task_id, language, api_key, service, initial_prompt=None):
        if not api_key:
            raise Exception(f"未提供 {service} API Key")
        
        # 检查并压缩大文件
        original_path = audio_path
        audio_path = compress_audio(audio_path)
        
        file_size = os.path.getsize(audio_path)
        logging.info(f"[{task_id}] 上传音频文件大小: {file_size / 1024 / 1024:.2f} MB")
        
        self.update_task(task_id, 'transcribing', 48, f'正在向 {service.upper()} 上传并识别...')
        
        url = "https://api.groq.com/openai/v1/audio/transcriptions" if service == 'groq' else "https://api.openai.com/v1/audio/transcriptions"
        model_name = "whisper-large-v3-turbo" if service == 'groq' else "whisper-1"
        
        headers = {"Authorization": f"Bearer {api_key}"}
        
        try:
            files = {"file": (Path(audio_path).name, open(audio_path, "rb"), "audio/mpeg")}
            # [DEBUG] 暂时移除单词级时间戳，观察是否由于此参数导致内容为空
            data = {
                "model": model_name, 
                "response_format": "verbose_json"
            }
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
                
                result = response.json()
                logging.info(f"[{task_id}] {service.upper()} API 响应内容摘要: {str(result)[:200]}...")
                raw_segments = result.get('segments') or []
                detected_lang = result.get('language', language)

                # [方案二] 强制对齐流程：如果需要拆分长句且 API 没给单词级时间戳，则调用本地模型补全
                # 先把 API 的结果转成标准格式
                subtitles = []
                for seg in raw_segments:
                    text = (seg.get('text') or '').strip()
                    if not text: continue
                    
                    # 幻觉预过滤 (API 响应级)
                    short_text = text.replace(" ", "").replace(",", "").replace(".", "").replace("!", "").replace("?", "").lower()
                    
                    # 判据 1：完全匹配黑名单词
                    is_exact_match = any(short_text == term.replace(" ", "").lower() for term in LLM_HALLUCINATION_BLACKLIST)
                    # 判据 2：特征幻觉匹配
                    specific_hallucinations = ["支持明镜", "点点栏目"]
                    is_specific_pattern = any(term in short_text for term in specific_hallucinations)
                    
                    filtered = False
                    duration = seg.get('end', 0) - seg.get('start', 0)
                    if is_exact_match and duration > 5:
                        filtered = True
                    elif is_specific_pattern and (duration > 10 and len(short_text) < 35):
                        filtered = True

                    if filtered:
                        logging.warning(f"[{task_id}] API 返回内容疑似幻觉，已拦截: {text} ({duration}s)")
                        continue

                    if converter: text = converter.convert(text)
                    parts = split_text(text, lang=detected_lang if detected_lang else 'zh')
                    
                    api_words = seg.get('words') or []
                    
                    # 只有在需要拆分且 API 没给有效单词时间戳时，才考虑通过本地模型补全
                    if len(parts) > 1 and not api_words:
                        logging.info(f"[{task_id}] API 未返回单词时间戳，尝试通过本地模型进行强制对齐...")
                        # 获取这一段对应的本地单词时间戳
                        # 为了性能，我们可以复用本地识别逻辑的一个子集
                        try:
                            # 实际上，这里我们可以让本地 whisper 只跑这一段所在的音频，或者直接全局跑一次 tiny 模型
                            # 考虑到架构简单，我们在这里尝试获取全局的本地单词时间戳（如果还没获取的话）
                            if not getattr(self, '_local_words_cache', None) or self._local_words_cache.get('video_id') != task_id:
                                logging.info(f"[{task_id}] 正在初始化本地轻量级模型进行全局单词对齐...")
                                from core.whisper_engine import whisper_engine
                                
                                # [FIX] Convert language name to code (e.g., 'Chinese' -> 'zh')
                                lang_map = {
                                    'chinese': 'zh', 'english': 'en', 'japanese': 'ja', 'korean': 'ko',
                                    'french': 'fr', 'german': 'de', 'russian': 'ru', 'spanish': 'es',
                                    'cantonese': 'yue'
                                }
                                local_lang = detected_lang.lower() if detected_lang else None
                                if local_lang in lang_map:
                                    local_lang = lang_map[local_lang]
                                
                                # 使用较小的模型或当前的全局模型跑一遍，核心是开启 word_timestamps
                                local_segs, _ = whisper_engine.transcribe(audio_path, language=local_lang)
                                all_local_words = []
                                for ls in local_segs:
                                    if hasattr(ls, 'words') and ls.words:
                                        for lw in ls.words:
                                            all_local_words.append({
                                                'start': lw.start,
                                                'end': lw.end,
                                                'word': lw.word
                                            })
                                    elif isinstance(ls, dict) and ls.get('words'):
                                        all_local_words.extend(ls['words'])
                                        
                                self._local_words_cache = {'video_id': task_id, 'words': all_local_words}
                            
                            api_words = self._local_words_cache['words']
                        except Exception as alignment_err:
                            logging.warning(f"[{task_id}] 本地强制对齐失败: {alignment_err}，回退到按比例拆分")

                    # 执行单词级映射拆分逻辑
                    if len(parts) > 1 and api_words:
                        # 此处复用之前的精准映射逻辑，但加入了简单的模糊匹配或位置匹配
                        word_idx = 0
                        # 缩小单词搜索范围：仅寻找在该段 API segment 时间窗口附近的本地单词
                        seg_start = seg.get('start', 0)
                        seg_end = seg.get('end', 0)
                        relevant_words = [w for w in api_words if w.get('start', 0) >= seg_start - 1 and w.get('end', 0) <= seg_end + 1]
                        
                        if not relevant_words: relevant_words = api_words # Fallback

                        last_end = seg_start
                        for part in parts:
                            part_cleaned = part.replace(" ", "").lower()
                            if not part_cleaned: continue
                            
                            part_start = None
                            current_part_content = ""
                            
                            while word_idx < len(relevant_words):
                                w = relevant_words[word_idx]
                                w_text = w.get('word', '').strip().lower()
                                if not w_text:
                                    word_idx += 1
                                    continue
                                
                                if part_start is None:
                                    part_start = max(w.get('start', 0), last_end)
                                
                                current_part_content += w_text
                                last_end = w.get('end', last_end)
                                word_idx += 1
                                
                                # 只要识别到的本地单词内容覆盖了 API 拆分后的片段
                                if len(current_part_content) >= len(part_cleaned):
                                    break
                            
                            s_time = round(part_start if part_start is not None else last_end, 2)
                            e_time = round(last_end, 2)
                            if e_time <= s_time:
                                e_time = s_time + 0.1

                            subtitles.append({
                                'start': s_time,
                                'end': e_time,
                                'text': part
                            })
                    else:
                        # 没拆分或者没有单词时间戳的回退：按比例
                        if len(parts) > 1:
                            duration = seg['end'] - seg['start']
                            p_dur = duration / len(parts)
                            for i, p in enumerate(parts):
                                subtitles.append({
                                    'start': round(seg['start'] + i * p_dur, 2),
                                    'end': round(seg['start'] + (i + 1) * p_dur, 2),
                                    'text': p
                                })
                        else:
                            subtitles.append({
                                'start': round(seg.get('start', 0), 2),
                                'end': round(seg.get('end', 0), 2),
                                'text': text
                            })
                
                return subtitles, detected_lang
        except Exception as e:
            raise Exception(f"{service.upper()} API 调用失败: {str(e)}")

    def _transcribe_via_cloudflare(self, audio_path, task_id, language):
        """
        使用 Cloudflare Workers AI 的 Whisper 模型进行语音识别
        API 文档: https://developers.cloudflare.com/workers-ai/models/whisper/
        """
        account_id = os.environ.get('CLOUDFLARE_ACCOUNT_ID')
        api_token = os.environ.get('CLOUDFLARE_API_TOKEN')
        
        if not account_id or not api_token:
            raise Exception("未配置 CLOUDFLARE_ACCOUNT_ID 或 CLOUDFLARE_API_TOKEN 环境变量")
        
        self.update_task(task_id, 'transcribing', 48, '正在处理音频...')
        
        # 1. 压缩音频
        audio_path = compress_audio(audio_path)
        
        # 2. 检查大小并决定是否分段
        file_size = os.path.getsize(audio_path)
        if file_size > 25 * 1024 * 1024:
            logging.info(f"[{task_id}] 压缩后音频仍然较大 ({file_size / 1024 / 1024:.2f} MB)，执行分段识别流程")
            # 每 5 分钟分一段 (约 300 秒) 以确保万无一失
            chunks = split_audio(audio_path, segment_duration=300)
            logging.info(f"[{task_id}] 音频已被分割为 {len(chunks)} 个片段")
        else:
            chunks = [audio_path]
        
        all_subtitles = []
        detected_lang = language if language != 'auto' else 'zh'
        current_time_offset = 0
        
        # 使用最新的 whisper-large-v3-turbo 模型
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/openai/whisper-large-v3-turbo"
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        
        try:
            import base64
            for i, chunk_path in enumerate(chunks):
                chunk_idx = i + 1
                progress = 48 + int((i / len(chunks)) * 45)
                self.update_task(task_id, 'transcribing', progress, f'正在识别第 {chunk_idx}/{len(chunks)} 个分段...')
                
                with open(chunk_path, 'rb') as f:
                    audio_data = f.read()
                    base64_audio = base64.b64encode(audio_data).decode('utf-8')
                
                payload = {
                    "audio": base64_audio,
                    "task": "transcribe",
                    "vad_filter": True
                }
                
                # 如果指定了语言且不是 auto，则传递给 API
                if detected_lang and detected_lang != 'auto':
                    payload["language"] = detected_lang

                # Cloudflare AI REST API 接受 Base64 编码的 JSON
                with httpx.Client(timeout=300.0) as client:
                    response = client.post(url, headers=headers, json=payload)
                    
                    if response.status_code == 413:
                        logging.error(f"[{task_id}] 第 {chunk_idx} 个片段仍然太大 (413)")
                        continue # 尝试下一个或报错
                    
                    if response.status_code != 200:
                        logging.error(f"[{task_id}] 第 {chunk_idx} 个片段识别失败 ({response.status_code})")
                        continue
                    
                    result = response.json()
                    if not result.get('success', False):
                        continue
                        
                    ai_result = result.get('result', {})
                    chunk_subs = []
                    segments = ai_result.get('segments') or ai_result.get('words') or []
                    full_text = ai_result.get('text', '')
                    
                    if segments:
                        for seg in segments:
                            text = seg.get('text', '').strip()
                            if not text: continue
                            if converter: text = converter.convert(text)
                            
                            chunk_subs.append({
                                'start': round(seg.get('start', 0) + current_time_offset, 2),
                                'end': round(seg.get('end', 0) + current_time_offset, 2),
                                'text': text
                            })
                    elif full_text:
                        # 估算时间对齐
                        sentences = split_text(full_text, lang=detected_lang)
                        duration = ai_result.get('duration') or get_audio_duration(chunk_path) or 600.0
                        time_per_sentence = duration / len(sentences) if sentences else 0
                        for j, sentence in enumerate(sentences):
                            if converter: sentence = converter.convert(sentence)
                            chunk_subs.append({
                                'start': round(j * time_per_sentence + current_time_offset, 2),
                                'end': round((j + 1) * time_per_sentence + current_time_offset, 2),
                                'text': sentence
                            })
                    
                    all_subtitles.extend(chunk_subs)
                    
                    # 更新时间偏移
                    current_time_offset += get_audio_duration(chunk_path)
                    
                    # 清理分理出的临时片段文件
                    if chunk_path != audio_path:
                        try: os.remove(chunk_path)
                        except: pass

            if not all_subtitles:
                raise Exception("所有音频片段识别均失败或未返回内容")
                
            return all_subtitles, detected_lang
                
        except Exception as e:
            raise Exception(f"Cloudflare Workers AI 调用失败: {str(e)}")

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

    def _translate_subtitles(self, subtitles, target_lang, api_key, service='groq', src_lang='auto', task_id=None, llm_correction=False):
        if not subtitles or not target_lang:
            return subtitles

        # Normalize languages for comparison
        lang_norm = {
            'simplified chinese': 'zh', 'chinese': 'zh', 'zh-cn': 'zh', 'zh-tw': 'zh', 
            'traditional chinese': 'zh', 'zh-hans': 'zh', 'zh-hant': 'zh', 'zh-hk': 'zh',
            'english': 'en', 'en-us': 'en', 'en-gb': 'en',
            'japanese': 'ja', 'ja-jp': 'ja',
            'korean': 'ko', 'ko-kr': 'ko'
        }
        
        def normalize(l):
            if not l: return 'auto'
            l = str(l).lower().strip().replace('_', '-')
            if l in lang_norm: return lang_norm[l]
            base = l.split('-')[0]
            return lang_norm.get(base, base)

        src_base = normalize(src_lang)
        tgt_base = normalize(target_lang)

        # 增加内容检测：如果包含大量中文字符且目标是中文，直接跳过翻译
        def has_chinese(text):
            return any('\u4e00' <= char <= '\u9fff' for char in text)

        is_source_chinese = has_chinese("\n".join([s['text'] for s in subtitles[:10]]))
        
        logging.info(f"[{task_id}] Translation check: src_detected={src_lang} (normed:{src_base}) -> tgt={target_lang} (normed:{tgt_base}). Source has Chinese: {is_source_chinese}")

        if (src_base == tgt_base and src_base != 'auto') or (is_source_chinese and tgt_base == 'zh'):
            lm_studio_url = os.environ.get('LM_STUDIO_API_URL')
            if not llm_correction or lm_studio_url:
                logging.info(f"[{task_id}] Skipping translation: languages compatible or source is already Chinese.")
                return subtitles

        self.update_task(task_id, 'transcribing', 95, f'正在翻译为 {target_lang}...')

        # Prepare LLM config
        lm_studio_url = os.environ.get('LM_STUDIO_API_URL')
        lm_studio_model = os.environ.get('LM_STUDIO_MODEL_NAME', 'local-model')
        siliconflow_key = os.environ.get('SILICONFLOW_API_KEY')
        
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
            # 自动选择服务
            openai_key = os.environ.get('OPENAI_API_KEY')
            groq_key = os.environ.get('GROQ_API_KEY')
            
            # 判断有效 Key (排除占位符)
            def is_valid(k):
                return k and 'your_' not in k.lower()

            # 优先级：SiliconFlow -> OpenAI -> Groq
            effective_service = service
            e_key = None
            
            if is_valid(api_key): # 优先使用前端传入的
                e_key = api_key
                effective_service = service
            elif is_valid(siliconflow_key):
                e_key = siliconflow_key
                effective_service = 'siliconflow'
            elif is_valid(openai_key):
                e_key = openai_key
                effective_service = 'openai'
            elif is_valid(groq_key):
                # 除非用户明确指定使用 Groq，或者没别的选了，否则不优先使用 Groq (因为额度限制)
                if service == 'groq' or not any([is_valid(siliconflow_key), is_valid(openai_key)]):
                    e_key = groq_key
                    effective_service = 'groq'
            
            if not e_key:
                logging.warning(f"[{task_id}] No valid API Key found for translation, skipping.")
                return subtitles

            if effective_service == 'siliconflow':
                url = "https://api.siliconflow.cn/v1/chat/completions"
                model_name = "deepseek-ai/DeepSeek-V3"
                headers["Authorization"] = f"Bearer {e_key}"
            elif effective_service == 'groq':
                # 用户反映 Groq 翻译没额度，这里做个防护
                if not service == 'groq': # 如果不是用户明确点的，就不尝试 Groq
                     logging.warning(f"[{task_id}] Potential Groq quota issue, skipping translation.")
                     return subtitles
                url = "https://api.groq.com/openai/v1/chat/completions"
                model_name = "llama-3.3-70b-versatile"
                headers["Authorization"] = f"Bearer {e_key}"
            else: # openai
                url = "https://api.openai.com/v1/chat/completions"
                model_name = "gpt-4o-mini"
                headers["Authorization"] = f"Bearer {e_key}"

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
    def sync_local_cache_to_postgres(self):
        """
        启动时后台同步：将本地 cache 目录下的所有 json 同步到 PostgreSQL
        """
        logging.info("开始扫描本地缓存并同步到 PostgreSQL 云端...")
        count = 0
        try:
            # 获取云端已有的所有 ID
            cloud_ids = set(postgres_db.get_all_video_ids())
            
            for file in CACHE_DIR.glob("*.json"):
                video_id = file.stem
                try:
                    if video_id in cloud_ids:
                        continue
                    
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    if not data.get('video_id') or not data.get('subtitles'):
                        continue
                    
                    if postgres_db.upsert_subtitles(data):
                        count += 1
                        if count % 10 == 0:
                            logging.info(f"已同步 {count} 个文件到 PostgreSQL...")
                except Exception as e:
                    logging.error(f"同步文件 {video_id} 到 PostgreSQL 失败: {e}")
            
            if count > 0:
                logging.info(f"PostgreSQL 同步完成，共上传 {count} 条新记录")
            else:
                logging.info("本地与 PostgreSQL 云端已同步")
        except Exception as e:
            logging.error(f"PostgreSQL 同步过程出错: {e}")

task_manager = TaskManager()
