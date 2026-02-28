from fastapi import APIRouter, Header, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import StreamingResponse, FileResponse
import asyncio
from typing import Optional
import uuid
import os
from dotenv import load_dotenv
from models.models import TranscribeRequest, TaskStatus, PlaylistRequest
from core.task_manager import task_manager
from core.utils import get_video_id

# 加载 .env 文件
load_dotenv()

router = APIRouter()

API_AUTH_KEY = os.environ.get("API_AUTH_KEY", "your-secret-key")

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if API_AUTH_KEY and x_api_key != API_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

@router.get("/")
@router.get("/health")
async def health():
    return {"status": "ok"}

@router.post("/transcribe")
async def transcribe(request: TranscribeRequest, auth: str = Depends(verify_api_key)):
    import logging
    # 优先使用视频 ID 作为 Task ID，实现去重
    if request.video_url:
        video_id = get_video_id(request.video_url)
        task_id = video_id
    else:
        task_id = str(uuid.uuid4())
        
    task_data = request.dict()
    task_data['task_id'] = task_id
    
    # 检查任务是否已存在且在运行中
    existing_task = task_manager.get_task(task_id)
    if existing_task:
        status = existing_task.get('status')
        # 如果任务没变（服务类型没变），且正在运行或已完成，直接返回
        if status in ['pending', 'downloading', 'transcribing']:
            logging.info(f"[API] 任务已在运行中: {task_id}")
            return {"task_id": task_id, "status": status, "message": "任务已在处理中"}
        elif status == 'completed':
            # 只有当 service, domain, engine 和 llm_correction 都匹配时才返回已完成任务，否则标记为需要重新生成
            is_match = (
                request.service == existing_task.get('service', 'local') and 
                request.domain == existing_task.get('domain', 'general') and
                request.engine == existing_task.get('engine', 'whisper') and
                request.target_lang == existing_task.get('target_lang') and
                request.llm_correction == existing_task.get('llm_correction', False)
            )
            
            if is_match:
                logging.info(f"[API] 任务已完成且配置一致，直接返回缓存: {task_id}")
                return existing_task
            else:
                logging.info(f"[API] 任务配置(service/domain/engine)变更，强制重置并重新生成任务: {task_id}")
                # 显式清除旧的结果，防止前端在 task_manager 还没来得及更新状态前读到 completed
                task_manager.update_task(task_id, 'pending', 0, '配置已变更，准备重新识别...', subtitles=[], language=None)

    logging.info(f"[API] 创建新转录任务: task_id={task_id}, video_url={request.video_url}, domain={request.domain}")
    task_manager.add_task(task_data)
    return {"task_id": task_id}

@router.post("/transcribe_playlist")
async def transcribe_playlist(request: PlaylistRequest):
    import logging
    import subprocess
    import json
    
    playlist_url = request.playlist_url
    logging.info(f"[API] 收到播放列表转录请求: {playlist_url}")
    
    try:
        # 使用 yt-dlp 获取播放列表中的所有视频 URL
        cmd = ['yt-dlp', '--flat-playlist', '--get-id', '--print', 'url', playlist_url]
        process = subprocess.run(cmd, capture_output=True, text=True, check=True)
        video_urls = [line.strip() for line in process.stdout.split('\n') if line.strip()]
        
        if not video_urls:
            raise HTTPException(status_code=400, detail="未能在播放列表中找到有效视频")
            
        task_ids = []
        for video_url in video_urls:
            video_id = get_video_id(video_url)
            
            # 构造单个转录请求数据
            task_data = request.dict()
            task_data.pop('playlist_url')
            task_data['video_url'] = video_url
            task_data['task_id'] = video_id
            
            # 检查任务是否已经在队列或已完成
            existing_task = task_manager.get_task(video_id)
            if existing_task and existing_task.get('status') == 'completed':
                # 如果配置一致且已完成，跳过
                is_match = (
                    request.service == existing_task.get('service', 'local') and 
                    request.domain == existing_task.get('domain', 'general') and
                    request.engine == existing_task.get('engine', 'whisper') and
                    request.target_lang == existing_task.get('target_lang') and
                    request.llm_correction == existing_task.get('llm_correction', False)
                )
                if is_match:
                    logging.info(f"[Playlist] 视频 {video_id} 已有缓存且匹配，跳过")
                    task_ids.append(video_id)
                    continue
            
            task_manager.add_task(task_data)
            task_ids.append(video_id)
            
        return {
            "message": f"成功将播放列表中的 {len(task_ids)} 个视频加入队列",
            "task_ids": task_ids
        }
        
    except Exception as e:
        logging.error(f"解析播放列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"解析播放列表失败: {str(e)}")

@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.get("/task/{task_id}/stream")
async def stream_task_status(task_id: str):
    import json
    
    async def event_generator():
        last_updated_at = 0
        last_progress = -1
        last_subtitles_count = 0
        status_completed = False
        
        while not status_completed:
            task = task_manager.get_task(task_id)
            if not task:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Task not found'})}\n\n"
                break
                
            current_updated_at = task.get('updated_at', 0)
            current_progress = task.get('progress', 0)
            current_subtitles_count = len(task.get('subtitles') or [])
            
            # 只有当状态更新或进度变化或字幕数变化时才推送
            if (current_updated_at > last_updated_at or 
                current_progress != last_progress or 
                current_subtitles_count != last_subtitles_count):
                
                yield f"data: {json.dumps(task)}\n\n"
                
                last_updated_at = current_updated_at
                last_progress = current_progress
                last_subtitles_count = current_subtitles_count
                
                if task.get('status') in ['completed', 'error']:
                    status_completed = True
            
            if not status_completed:
                await asyncio.sleep(0.5)
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/status/{video_id}")
async def get_video_status(video_id: str):
    # 检查任务是否在内存中或缓存中
    task = task_manager.get_task_by_video_id(video_id)
    if not task:
         # 返回 200 而非 404，避免日志干扰，通过 status 区分
         return {"task_id": video_id, "status": "not_found", "message": "No existing data for this video"}
    return task

from fastapi import File, UploadFile, Form

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    service: str = Form("local"),
    language: str = Form("auto"),
    domain: str = Form("general"),
    api_key: Optional[str] = Form(None),
    target_lang: Optional[str] = Form(None),
    auth: str = Depends(verify_api_key)
):
    # File upload logic
    task_id = str(uuid.uuid4())
    content = await file.read()
    task_manager.add_upload_task(
        task_id, 
        content, 
        service=service, 
        api_key=api_key, 
        language=language, 
        target_lang=target_lang,
        domain=domain
    )
    return {"task_id": task_id}

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.delete("/cache/{video_id}")
async def delete_cache(video_id: str, auth: str = Depends(verify_api_key)):
    """
    删除指定视频的所有缓存（本地、内存、MongoDB）
    """
    import logging
    logging.info(f"[API] 收到删除缓存请求: {video_id}")
    
    result = task_manager.delete_video_cache(video_id)
    
    if result['success']:
        return {
            "message": f"成功删除视频 {video_id} 的缓存",
            "deleted_items": result['deleted_items']
        }
    else:
        raise HTTPException(
            status_code=404, 
            detail=f"未找到视频 {video_id} 的任何缓存数据"
        )

# ============ 术语词典管理 ============

from core.lexicon import get_all_lexicon, load_custom_lexicon, save_custom_lexicon
from pydantic import BaseModel

class LexiconUpdateRequest(BaseModel):
    domain: str
    label: str = ""
    terms: str = ""
    replacements: dict = {}

@router.get("/lexicon")
async def get_lexicon():
    """获取所有词典（内置 + 自定义）"""
    return get_all_lexicon()

@router.post("/lexicon")
async def update_lexicon(request: LexiconUpdateRequest, auth: str = Depends(verify_api_key)):
    """添加或更新自定义领域词典"""
    import logging
    custom = load_custom_lexicon()
    custom[request.domain] = {
        "label": request.label or request.domain,
        "terms": request.terms,
        "replacements": request.replacements
    }
    if save_custom_lexicon(custom):
        logging.info(f"[Lexicon] 已更新自定义词典: {request.domain}")
        return {"message": f"词典 '{request.domain}' 已保存", "domain": request.domain}
    raise HTTPException(status_code=500, detail="保存词典失败")

@router.delete("/lexicon/{domain}")
async def delete_lexicon(domain: str, auth: str = Depends(verify_api_key)):
    """删除自定义领域词典（内置词典不可删除）"""
    from core.lexicon import BUILTIN_LEXICON
    if domain in BUILTIN_LEXICON:
        raise HTTPException(status_code=400, detail=f"内置词典 '{domain}' 不可删除")
    
    custom = load_custom_lexicon()
    if domain not in custom:
        raise HTTPException(status_code=404, detail=f"自定义词典 '{domain}' 不存在")
    
    del custom[domain]
    save_custom_lexicon(custom)
    return {"message": f"词典 '{domain}' 已删除"}

# ============ 视频下载功能 ============

from models.models import VideoDownloadRequest
from fastapi.responses import FileResponse
import subprocess
import asyncio
from pathlib import Path

TEMP_DIR = Path(os.path.join(os.path.dirname(__file__), '..', 'temp')).resolve()

@router.post("/download_video")
async def download_video(request: VideoDownloadRequest, auth: str = Depends(verify_api_key)):
    """
    使用 yt-dlp 下载指定分辨率的 YouTube 视频，返回下载文件名
    """
    import logging
    video_url = request.video_url
    resolution = request.resolution  # 720, 1080, 1440, 2160
    
    video_id = get_video_id(video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="无效的视频 URL")
    
    logging.info(f"[Download] 收到视频下载请求: {video_id}, 分辨率: {resolution}p")
    
    # 检查是否已有下载好的视频文件
    for ext in ['mp4', 'mkv', 'webm']:
        existing = TEMP_DIR / f"{video_id}_{resolution}p.{ext}"
        if existing.exists() and existing.stat().st_size > 0:
            logging.info(f"[Download] 视频文件已存在: {existing.name}")
            return {"filename": existing.name, "status": "ready"}
    
    output_template = str(TEMP_DIR / f"{video_id}_{resolution}p.%(ext)s")
    
    # yt-dlp 命令：下载指定分辨率的视频+音频合并为 mp4
    cmd = [
        'yt-dlp',
        '-f', f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]',
        '--merge-output-format', 'mp4',
        '--no-playlist',
        '--no-part',
        '--force-overwrites',
        '--no-cache-dir',
        '-o', output_template,
        video_url
    ]
    
    try:
        # 在线程池中执行以避免阻塞
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True, timeout=1800, 
            encoding='utf-8', errors='ignore'
        )
        
        if result.returncode != 0:
            stderr_msg = result.stderr.strip() if result.stderr else "未知错误"
            logging.error(f"[Download] yt-dlp 下载失败: {stderr_msg[:300]}")
            raise HTTPException(status_code=500, detail=f"视频下载失败: {stderr_msg[:200]}")
        
        # 查找下载好的文件
        for ext in ['mp4', 'mkv', 'webm']:
            downloaded = TEMP_DIR / f"{video_id}_{resolution}p.{ext}"
            if downloaded.exists() and downloaded.stat().st_size > 0:
                logging.info(f"[Download] 视频下载完成: {downloaded.name}, 大小: {downloaded.stat().st_size / 1024 / 1024:.2f} MB")
                return {"filename": downloaded.name, "status": "ready"}
        
        raise HTTPException(status_code=500, detail="下载完成但未找到视频文件")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="视频下载超时（超过 30 分钟）")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"[Download] 下载异常: {e}")
        raise HTTPException(status_code=500, detail=f"下载错误: {str(e)}")

@router.get("/download_file/{filename}")
async def download_file(filename: str):
    """
    提供已下载视频文件的下载流
    """
    import logging
    # 安全性检查：防止路径遍历
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")
    
    file_path = TEMP_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    
    logging.info(f"[Download] 提供文件下载: {filename}")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="video/mp4"
    )
