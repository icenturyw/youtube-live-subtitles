import os
import re
import json
import hashlib
import subprocess
import logging
from pathlib import Path
from datetime import datetime
import itertools

# OpenCC
try:
    from opencc import OpenCC
    converter = OpenCC('t2s')
except ImportError:
    converter = None

# Spacy
try:
    import spacy
    HAS_SPACY = True
except ImportError:
    HAS_SPACY = False

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
RAW_CACHE_DIR = CACHE_DIR / "raw"
TEMP_DIR = BASE_DIR / "temp"

# Ensure directories exist
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

NLP_MODELS = {}

def get_video_id(url):
    patterns = [
        r'(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#\&\?\n]*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:11]

def load_spacy_model(lang):
    if not HAS_SPACY:
        return None
    
    model_name = "en_core_web_md" if lang == "en" else "zh_core_web_md"
    if lang not in ["en", "zh"]:
        # Fallback
        model_name = "en_core_web_md"

    if model_name in NLP_MODELS:
        return NLP_MODELS[model_name]

    try:
        logging.info(f"Loading Spacy model: {model_name}...")
        nlp = spacy.load(model_name)
        NLP_MODELS[model_name] = nlp
        return nlp
    except Exception as e:
        logging.warning(f"Failed to load Spacy model {model_name}: {e}")
        return None

def robust_split_by_length(text, max_len, lang="zh"):
    """
    Split text by length while respecting word boundaries for English.
    """
    if len(text) <= max_len:
        return [text]
    
    if lang == "en":
        # Split by spaces if English
        words = text.split(' ')
        lines = []
        current_line = ""
        for word in words:
            if not current_line:
                current_line = word
            elif len(current_line) + 1 + len(word) <= max_len:
                current_line += " " + word
            else:
                lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        
        # If a single word is still longer than max_len (rare), force split it
        result = []
        for line in lines:
            if len(line) > max_len:
                # Force split long words
                while len(line) > max_len:
                    result.append(line[:max_len])
                    line = line[max_len:]
                if line: result.append(line)
            else:
                result.append(line)
        return result
    else:
        # Chinese or other: simple split
        result = []
        while len(text) > max_len:
            result.append(text[:max_len])
            text = text[max_len:]
        if text: result.append(text)
        return result

def split_text_by_spacy(text, nlp, max_len=25, lang="zh"):
    """
    Split text using Spacy dependency parsing (based on VideoLingo approach)
    """
    doc = nlp(text)
    sentences = []
    start = 0
    
    # Helper to check if a phrase is valid for splitting
    def is_valid_phrase(phrase):
        has_subject = any(token.dep_ in ["nsubj", "nsubjpass"] or token.pos_ == "PRON" for token in phrase)
        has_verb = any((token.pos_ == "VERB" or token.pos_ == 'AUX') for token in phrase)
        return (has_subject and has_verb)

    # Helper to analyze if we should split at comma
    def analyze_comma(start, doc, token):
        # Look ahead and behind to see if we have valid clauses
        left_phrase = doc[max(start, token.i - 9):token.i]
        right_phrase = doc[token.i + 1:min(len(doc), token.i + 10)]
        
        suitable = is_valid_phrase(right_phrase)
        
        # Check lengths
        left_words = [t for t in left_phrase if not t.is_punct]
        right_words = list(itertools.takewhile(lambda t: not t.is_punct, right_phrase))
        
        if len(left_words) <= 3 or len(right_words) <= 3:
            suitable = False
            
        return suitable

    for i, token in enumerate(doc):
        # Spacy split logic (simplified from VideoLingo's split_by_comma.py)
        if token.text in [",", "，"]:
            if analyze_comma(start, doc, token):
                part = doc[start:token.i].text.strip()
                if len(part) > max_len * 0.3: # Avoid extremely short splits
                    sentences.append(part)
                    start = token.i + 1
                    
        # Also split at major punctuation
        if token.text in ["。", "！", "？", ".", "!", "?", ";", "；"]:
             part = doc[start:token.i+1].text.strip() # Include punct
             sentences.append(part)
             start = token.i + 1

    # Add remaining
    if start < len(doc):
        sentences.append(doc[start:].text.strip())
    
    # Post-processing: Merge short segments if possible or enforce max_len
    final_sentences = []
    current = ""
    
    for s in sentences:
        if not s: continue
        
        # If adding this segment exceeds max, push current
        if len(current) + len(s) > max_len:
             if current:
                 final_sentences.append(current)
                 current = s
             else:
                 # Single segment is too long, force split (fallback to regex/length)
                 # Recursively call simple split if still too long
                 if len(s) > max_len:
                      # Fallback to robust simple split for this chunk (Non-recursive)
                      final_sentences.extend(robust_split_by_length(s, max_len, lang)) 
                 else:
                     final_sentences.append(s)
        else:
             current += s if not current else (current + " " + s)
    
    if current:
        final_sentences.append(current)
        
    return [s.strip() for s in final_sentences if s.strip()]

def split_text(text, max_len=25, lang="zh"):
    if converter:
        text = converter.convert(text)
    
    # Try Spacy first
    if HAS_SPACY:
        nlp = load_spacy_model(lang)
        if nlp:
            return split_text_by_spacy(text, nlp, max_len, lang)

    # Robust word-aware fallback
    return robust_split_by_length(text, max_len, lang)

def compress_audio(audio_path):
    MAX_SIZE = 24 * 1024 * 1024
    if os.path.getsize(audio_path) <= MAX_SIZE:
        return audio_path
    
    compressed_path = str(Path(audio_path).parent / f"compressed_{Path(audio_path).name}")
    # 第一级压缩：提升到 64k 以保持更好的识别质量
    cmd = ['ffmpeg', '-y', '-i', audio_path, '-ar', '16000', '-ac', '1', '-b:a', '64k', compressed_path]
    try:
        subprocess.run(cmd, capture_output=True, text=True)
        if os.path.getsize(compressed_path) > MAX_SIZE:
            # 第二级压缩：最后的保底，32k
            final_path = str(Path(audio_path).parent / f"final_{Path(audio_path).name}")
            cmd = ['ffmpeg', '-y', '-i', compressed_path, '-ar', '16000', '-ac', '1', '-b:a', '32k', final_path]
            subprocess.run(cmd, capture_output=True)
            return final_path
        return compressed_path
    except Exception as e:
        logging.error(f"Compression error: {e}")
        return audio_path

def get_audio_duration(audio_path):
    """获取音频时长（秒）"""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logging.error(f"获取音频时长失败: {e}")
        return 0

def split_audio(audio_path, segment_duration=300):
    """
    将音频分割为指定时长的片段（默认 5 分钟）
    分割时进行转码压缩以确保文件大小受控
    """
    duration = get_audio_duration(audio_path)
    if duration <= segment_duration:
        # 即便只有一段，如果原文件很大也需要转码
        if os.path.getsize(audio_path) > 25 * 1024 * 1024:
            return [compress_audio(audio_path)]
        return [audio_path]
    
    output_pattern = str(Path(audio_path).parent / f"chunk_%03d_{Path(audio_path).stem}.mp3")
    cmd = [
        'ffmpeg', '-y', '-i', audio_path, 
        '-f', 'segment', '-segment_time', str(segment_duration), 
        '-ac', '1', '-ar', '16000', '-b:a', '64k', 
        output_pattern
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        # 获取生成的片段列表
        chunks = sorted(list(Path(audio_path).parent.glob(f"chunk_*_{Path(audio_path).stem}.mp3")))
        return [str(c) for c in chunks]
    except Exception as e:
        logging.error(f"分割音频失败: {e}")
        return [audio_path]

def enhance_audio_for_speech(audio_path):
    """
    使用 ffmpeg 增强人声并抑制背景噪音/伴奏
    适用于背景音乐较强的场景
    """
    # 如果文件已经处理过，直接返回
    if "enhanced_" in audio_path:
        return audio_path
        
    enhanced_path = str(Path(audio_path).parent / f"enhanced_{Path(audio_path).name}")
    
    # 组合滤镜说明：
    # highpass=f=200: 移除 200Hz 以下的低频伴奏（如鼓点、贝斯）
    # lowpass=f=3500: 移除 3500Hz 以上的高频杂音（如镲片、高频底噪）
    # afftdn: FFT 采样降噪
    # loudnorm: 响度标准化，确保音量适中
    cmd = [
        'ffmpeg', '-y', '-i', audio_path, 
        '-af', 'highpass=f=200,lowpass=f=3500,afftdn,loudnorm', 
        '-ar', '16000', # 采样率设为 ASR 偏好的 16k
        enhanced_path
    ]
    
    try:
        logging.info(f"正在进行人声增强预处理: {audio_path}")
        subprocess.run(cmd, capture_output=True, check=True)
        return enhanced_path
    except Exception as e:
        logging.error(f"人声增强失败: {e}")
        return audio_path
