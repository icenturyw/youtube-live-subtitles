from fastapi import APIRouter, Header, HTTPException, Depends
from typing import Optional
import uuid
import os
from ..models.models import TranscribeRequest, TaskStatus, PlaylistRequest
from ..core.task_manager import task_manager

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

@router.get("/health")
async def health():
    return {"status": "ok"}
