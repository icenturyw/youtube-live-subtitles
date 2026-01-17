from pydantic import BaseModel, Field
from typing import List, Optional, Any

class Subtitle(BaseModel):
    start: float
    end: float
    text: str
    translation: Optional[str] = None

class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: int
    message: str
    subtitles: Optional[List[Subtitle]] = None
    detected_language: Optional[str] = None
    updated_at: float

class TranscribeRequest(BaseModel):
    video_url: Optional[str] = None
    language: str = "auto"
    service: str = "local"
    api_key: Optional[str] = None
    target_lang: Optional[str] = None

class PlaylistRequest(BaseModel):
    playlist_url: str
    language: str = "auto"
    service: str = "local"
    api_key: Optional[str] = None
    target_lang: Optional[str] = None
