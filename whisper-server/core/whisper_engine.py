import threading
import logging
import os
from pathlib import Path
from faster_whisper import WhisperModel

class WhisperEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(WhisperEngine, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self.model_size = os.environ.get("MODEL_SIZE", "tiny")
        self.device = os.environ.get("DEVICE", "cpu")
        self.compute_type = os.environ.get("COMPUTE_TYPE", "int8")
        self.cpu_threads = int(os.environ.get("CPU_THREADS", "8"))
        self.num_workers = int(os.environ.get("NUM_WORKERS", "4"))
        self.model = None
        self._initialized = True

    def get_model(self):
        if self.model is None:
            with self._lock:
                if self.model is None:
                    logging.info(f"正在加载本地模型 ({self.model_size})...")
                    self.model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                        cpu_threads=self.cpu_threads,
                        num_workers=self.num_workers
                    )
                    logging.info("模型加载完成")
        return self.model

    def transcribe(self, audio_path, language=None, initial_prompt=None):
        model = self.get_model()
        segments, info = model.transcribe(
            audio_path,
            language=language if language and language != 'auto' else None,
            beam_size=1,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt=initial_prompt or "以下是普通话的句子，请用简体中文。"
        )
        return segments, info

whisper_engine = WhisperEngine()
