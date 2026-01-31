"""
Qwen3-ASR 语音识别引擎
通过 Gradio Client 调用远程 Qwen3-ASR API 进行语音识别
"""

import os
import logging
from pathlib import Path

class Qwen3ASREngine:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Qwen3ASREngine, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.api_url = os.environ.get("QWEN3_ASR_API_URL", "http://10.242.15.77:8000/")
        self.client = None
        self._initialized = True
        
        # 语言代码映射：项目代码 -> Qwen3-ASR lang_disp
        self.lang_map = {
            'auto': 'Auto',
            'zh': 'Chinese',
            'cn': 'Chinese',
            'chinese': 'Chinese',
            '中文': 'Chinese',
            'en': 'English',
            'english': 'English',
            'ja': 'Japanese',
            'japanese': 'Japanese',
            'ko': 'Korean',
            'korean': 'Korean',
            'fr': 'French',
            'french': 'French',
            'de': 'German',
            'german': 'German',
            'es': 'Spanish',
            'spanish': 'Spanish',
            'ru': 'Russian',
            'russian': 'Russian',
            'it': 'Italian',
            'italian': 'Italian',
            'pt': 'Portuguese',
            'portuguese': 'Portuguese',
            'ar': 'Arabic',
            'arabic': 'Arabic',
            'yue': 'Cantonese',
            'cantonese': 'Cantonese',
            '粤语': 'Cantonese',
            # 新增支持的语言
            'id': 'Indonesian',
            'indonesian': 'Indonesian',
            'th': 'Thai',
            'thai': 'Thai',
            'vi': 'Vietnamese',
            'vietnamese': 'Vietnamese',
            'tr': 'Turkish',
            'turkish': 'Turkish',
            'hi': 'Hindi',
            'hindi': 'Hindi',
            'ms': 'Malay',
            'malay': 'Malay',
            'nl': 'Dutch',
            'dutch': 'Dutch',
            'sv': 'Swedish',
            'swedish': 'Swedish',
            'da': 'Danish',
            'danish': 'Danish',
            'fi': 'Finnish',
            'finnish': 'Finnish',
            'pl': 'Polish',
            'polish': 'Polish',
            'cs': 'Czech',
            'czech': 'Czech',
            'tl': 'Filipino',
            'filipino': 'Filipino',
            'fa': 'Persian',
            'persian': 'Persian',
            'el': 'Greek',
            'greek': 'Greek',
            'ro': 'Romanian',
            'romanian': 'Romanian',
            'hu': 'Hungarian',
            'hungarian': 'Hungarian',
            'mk': 'Macedonian',
            'macedonian': 'Macedonian'
        }
        
        # 服务端允许的所有合法选项
        self.valid_choices = [
            'Auto', 'Chinese', 'English', 'Cantonese', 'Arabic', 'German', 'French', 
            'Spanish', 'Portuguese', 'Indonesian', 'Italian', 'Korean', 'Russian', 
            'Thai', 'Vietnamese', 'Japanese', 'Turkish', 'Hindi', 'Malay', 'Dutch', 
            'Swedish', 'Danish', 'Finnish', 'Polish', 'Czech', 'Filipino', 'Persian', 
            'Greek', 'Romanian', 'Hungarian', 'Macedonian'
        ]
    
    def _ensure_client(self):
        """确保 Gradio Client 已初始化"""
        if self.client is None:
            try:
                from gradio_client import Client
                print("\n" + "="*40)
                print(" [QWEN3-ASR ENGINE] 正在连接远程服务...")
                print(f" > API 地址: {self.api_url}")
                print("="*40 + "\n")
                
                self.client = Client(self.api_url)
                print(" [QWEN3-ASR ENGINE] 连接成功！")
            except Exception as e:
                print(f" [QWEN3-ASR ENGINE] 连接失败: {e}")
                raise Exception(f"无法连接到 Qwen3-ASR 服务 ({self.api_url}): {e}")
    
    def transcribe(self, audio_path, language="auto"):
        """
        使用 Qwen3-ASR 进行语音识别
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码 (auto, zh, en, ja, ...)
            
        Returns:
            tuple: (subtitles, detected_language)
                - subtitles: [{'start': float, 'end': float, 'text': str}, ...]
                - detected_language: 检测到的语言代码
        """
        self._ensure_client()
        
        logging.info(f"[Qwen3-ASR] 开始识别: {audio_path}, 语言: {language}")
        
        # 映射语言代码，确保 lang_disp 在 self.valid_choices 中
        lang_disp = 'Auto'
        if language:
            if language in self.valid_choices:
                lang_disp = language
            else:
                lang_disp = self.lang_map.get(language.lower(), 'Auto')
                # 兜底：如果映射出的值依然不合法，尝试在 valid_choices 中寻找匹配项
                if lang_disp not in self.valid_choices:
                    found = False
                    for choice in self.valid_choices:
                        if choice.lower() == language.lower():
                            lang_disp = choice
                            found = True
                            break
                    if not found:
                        lang_disp = 'Auto'
        
        try:
            from gradio_client import handle_file
            
            # 调用 /run 端点
            result = self.client.predict(
                audio_upload=handle_file(audio_path),
                lang_disp=lang_disp,
                return_ts=True,
                api_name="/run"
            )
            
            # 记录原始结果以便调试
            try:
                debug_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "qwen3_raw.txt")
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(str(result))
            except:
                pass
            
            detected_lang = result[0] if result[0] else language
            text_result = result[1] if len(result) > 1 else ""
            timestamps = result[2] if len(result) > 2 else None
            
            logging.info(f"[Qwen3-ASR] 检测语言: {detected_lang}")
            logging.info(f"[Qwen3-ASR] 识别结果长度: {len(text_result)} 字符")
            logging.info(f"[Qwen3-ASR] 时间戳数据类型: {type(timestamps)}")
            
            # 解析时间戳数据
            subtitles = self._parse_timestamps(timestamps, text_result)
            
            # 将检测语言转回项目代码格式
            detected_lang_code = self._reverse_lang_map(detected_lang)
            
            return subtitles, detected_lang_code
            
        except Exception as e:
            logging.error(f"[Qwen3-ASR] 识别失败: {e}")
            raise Exception(f"Qwen3-ASR 识别失败: {e}")
    
    def _parse_timestamps(self, timestamps, fallback_text):
        """解析时间戳数据"""
        raw_subtitles = []
        
        def find_list(data):
            """递归查找第一个列表"""
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                # 增加了 'value' 键名支持
                for key in ['segments', 'words', 'data', 'items', 'result', 'value']:
                    if key in data and isinstance(data[key], list):
                        return data[key]
                for val in data.values():
                    found = find_list(val)
                    if found: return found
            return None

        if timestamps:
            try:
                if isinstance(timestamps, str):
                    try:
                        import json
                        timestamps = json.loads(timestamps)
                    except:
                        pass
                
                seg_list = find_list(timestamps)
                
                if seg_list:
                    for item in seg_list:
                        s, e, t = None, None, None
                        if isinstance(item, dict):
                            # 增加了 start_time 和 end_time 支持
                            s = item.get('start_time', item.get('start', item.get('s', item.get('begin'))))
                            e = item.get('end_time', item.get('end', item.get('e', item.get('finish'))))
                            t = item.get('text', item.get('t', item.get('content')))
                        elif isinstance(item, (list, tuple)) and len(item) >= 2:
                            s = item[0]
                            e = item[1]
                            t = item[2] if len(item) > 2 else ""
                            
                        if s is not None and e is not None:
                            try:
                                raw_subtitles.append({
                                    'start': float(s),
                                    'end': float(e),
                                    'text': str(t).strip() if t else ""
                                })
                            except (ValueError, TypeError):
                                continue
                
                logging.info(f"[Qwen3-ASR] 提取出 {len(raw_subtitles)} 条原始片段")
            except Exception as e:
                logging.error(f"[Qwen3-ASR] 时间戳提取错误: {e}")
        
        # 如果没有提取到片段，使用 fallback
        if not raw_subtitles:
            if fallback_text:
                return [{'start': 0.0, 'end': 0.0, 'text': fallback_text.strip()}]
            return []

        # 聚合逻辑：Qwen3-ASR 返回的片段非常碎（往往是按字），需要按标点或长度进行聚合成句
        processed_subtitles = []
        if raw_subtitles:
            current_seg = None
            # 定义标点符号，遇到这些符号即断句
            split_chars = "。！？；.!?;，,、"
            
            for seg in raw_subtitles:
                if not current_seg:
                    current_seg = seg.copy()
                    continue
                
                last_char = current_seg['text'][-1] if current_seg['text'] else ""
                gap = seg['start'] - current_seg['end']
                current_len = len(current_seg['text'])

                # [优化建议] 增强合并判断逻辑，避免在语义中间强行切断 (特别是没有标点的情况)
                # 1. 遇到显式标点符号，必须断句
                if last_char in split_chars:
                    should_combine = False
                # 2. 如果字数很少 (<15)，只有在停顿极其明显 (>1.5s) 时才切断，否则大概率是短语。
                elif current_len < 15:
                    should_combine = gap < 1.5
                # 3. 如果字数在 15-30 之间 (正常字幕长度)：
                #    - 如果间隙很小 (<0.4s)，大概率是连贯的话语，继续合并以保持完整性
                #    - 如果停顿明显 (>0.6s)，即便没到字数限制也建议切断，视觉上更清晰
                elif current_len < 30:
                    should_combine = gap < 0.5
                # 4. 如果字数超出常规 (30-45)：
                #    - 只有在非常连贯 (<0.1s) 且还没到极限时才勉强合并
                #    - 稍微有停顿即切断
                elif current_len < 45:
                    should_combine = gap < 0.1
                # 5. 绝对物理上限 (45)：防止单条字幕过长遮挡画面
                else:
                    should_combine = False
                
                if should_combine:
                    current_seg['text'] += seg['text']
                    current_seg['end'] = seg['end']
                else:
                    processed_subtitles.append({
                        'start': round(current_seg['start'], 2),
                        'end': round(current_seg['end'], 2),
                        'text': current_seg['text'].strip()
                    })
                    current_seg = seg.copy()
            
            # 记录最后一个片段
            if current_seg:
                processed_subtitles.append({
                    'start': round(current_seg['start'], 2),
                    'end': round(current_seg['end'], 2),
                    'text': current_seg['text'].strip()
                })

        logging.info(f"[Qwen3-ASR] 聚合后生成 {len(processed_subtitles)} 条字幕")
        return processed_subtitles

    def _reverse_lang_map(self, lang_name):
        """将语言名称转回项目通用的语言代码"""
        if not lang_name:
            return 'auto'
            
        # 优先映射表（标准 ISO 代码）
        primary_map = {
            'Chinese': 'zh',
            'English': 'en',
            'Japanese': 'ja',
            'Korean': 'ko',
            'French': 'fr',
            'German': 'de',
            'Spanish': 'es',
            'Russian': 'ru',
            'Italian': 'it',
            'Portuguese': 'pt',
            'Arabic': 'ar',
            'Cantonese': 'yue',
            'Indonesian': 'id',
            'Thai': 'th',
            'Vietnamese': 'vi',
            'Turkish': 'tr',
            'Hindi': 'hi',
            'Malay': 'ms',
            'Dutch': 'nl',
            'Swedish': 'sv',
            'Danish': 'da',
            'Finnish': 'fi',
            'Polish': 'pl',
            'Czech': 'cs',
            'Filipino': 'tl',
            'Persian': 'fa',
            'Greek': 'el',
            'Romanian': 'ro',
            'Hungarian': 'hu',
            'Macedonian': 'mk',
            'Auto': 'auto'
        }
        
        # 不区分大小写匹配
        for k, v in primary_map.items():
            if k.lower() == lang_name.lower():
                return v
                
        return 'zh'  # 默认回退


# 全局单例
qwen3_asr_engine = Qwen3ASREngine()
