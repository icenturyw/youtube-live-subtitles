from fastapi import APIRouter, Header, HTTPException, Depends
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
    if x_api_key != API_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

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

@router.get("/status/{video_id}")
async def get_video_status(video_id: str):
    # This matches the old API's behavior of checking for cached/completed subtitles for a video_id
    # We check if a completed task exists for this video_id in the manager or cache
    # For now, we'll delegate to task_manager to look up by video_id
    task = task_manager.get_task_by_video_id(video_id)
    if not task:
         raise HTTPException(status_code=404, detail="Status not found")
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
