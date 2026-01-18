import os
import logging
from funasr import AutoModel
import torch

class SenseVoiceEngine:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SenseVoiceEngine, cls).__new__(cls)
            cls._instance.model = None
            cls._instance.device = "cuda" if torch.cuda.is_available() else "cpu"
        return cls._instance

    def _ensure_model_loaded(self):
        if self.model is None:
            print("\n" + "="*40)
            print(" [SENSEVOICE ENGINE] 正在初始化模型...")
            print(f" > 运行设备: {self.device}")
            print("="*40 + "\n")
            try:
                # Use SenseVoiceSmall for better balance between speed and quality
                self.model = AutoModel(
                    model="iic/SenseVoiceSmall",
                    device=self.device,
                    disable_update=True
                )
                print(" [SENSEVOICE ENGINE] 模型加载成功！")
            except Exception as e:
                print(f" [SENSEVOICE ENGINE] 模型加载失败: {e}")
                raise e

    def transcribe(self, audio_path, language="auto"):
        self._ensure_model_loaded()
        
        logging.info(f"Transcribing {audio_path} via SenseVoice...")
        try:
            # SenseVoice supports emotion, event, and language detection automatically
            res = self.model.generate(
                input=audio_path,
                cache={},
                language=language if language != "auto" else "zh",
                use_itn=True,
                batch_size_s=300,
                merge_vad=True
            )
            
            if not res or len(res) == 0:
                return []
            
            # Format SenseVoice output to segments matching Whisper style
            segments = []
            import opencc
            converter = opencc.OpenCC('t2s') # traditional to simplified
            
            for item in res:
                text = item.get('text', '')
                # Clean up rich text tags like <|HAPPY|>, <|ZH|>, <|Speech|>
                import re
                text = re.sub(r'<\|.*?\|>', '', text).strip()
                
                # Convert to simplified Chinese
                text = converter.convert(text)
                
                if not text: continue
                
                segments.append({
                    'start': 0.0,
                    'end': 0.0, 
                    'text': text
                })
            
            return segments
        except Exception as e:
            logging.error(f"SenseVoice transcription failed: {e}")
            raise e

# Global instance
sensevoice_engine = SenseVoiceEngine()
