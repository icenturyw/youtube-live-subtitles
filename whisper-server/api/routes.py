from fastapi import APIRouter, Header, HTTPException, Depends
from typing import Optional
import uuid
import os
from models.models import TranscribeRequest, TaskStatus, PlaylistRequest
from core.task_manager import task_manager

router = APIRouter()

API_AUTH_KEY = os.environ.get("API_AUTH_KEY", "your-secret-key")

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

@router.post("/transcribe")
async def transcribe(request: TranscribeRequest, auth: str = Depends(verify_api_key)):
    task_id = str(uuid.uuid4())
    task_data = request.dict()
    task_data['task_id'] = task_id
    task_manager.add_task(task_data)
    return {"task_id": task_id}

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

@router.post("/upload")
async def upload_file(file: bytes, auth: str = Depends(verify_api_key)):
    # File upload logic
    task_id = str(uuid.uuid4())
    task_manager.add_upload_task(task_id, file)
    return {"task_id": task_id}

@router.get("/health")
async def health():
    return {"status": "ok"}
