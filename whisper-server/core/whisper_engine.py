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
        
        import torch
        self.model_size = os.environ.get("MODEL_SIZE", "base")
        self.device = os.environ.get("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
        # GPU 建议用 float16, CPU 建议用 int8
        default_compute_type = "float16" if self.device == "cuda" else "int8"
        self.compute_type = os.environ.get("COMPUTE_TYPE", default_compute_type)
        self.cpu_threads = int(os.environ.get("CPU_THREADS", "8"))
        self.num_workers = int(os.environ.get("NUM_WORKERS", "4"))
        self.model = None
        self._initialized = True

    def get_model(self):
        if self.model is None:
            with self._lock:
                if self.model is None:
                    print("\n" + "="*40)
                    print(" [WHISPER ENGINE] 正在初始化模型...")
                    print(f" > 模型大小: {self.model_size}")
                    print(f" > 运行设备: {self.device}")
                    print(f" > 计算类型: {self.compute_type}")
                    print("="*40 + "\n")
                    
                    self.model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                        cpu_threads=self.cpu_threads,
                        num_workers=self.num_workers
                    )
                    print(" [WHISPER ENGINE] 模型加载成功！")
        return self.model

    def transcribe(self, audio_path, language=None, initial_prompt=None):
        model = self.get_model()
        segments, info = model.transcribe(
            audio_path,
            language=language if language and language != 'auto' else None,
            beam_size=1,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt=initial_prompt or "以下是普通话的句子，请用简体中文。",
            word_timestamps=True
        )
        return segments, info

whisper_engine = WhisperEngine()
