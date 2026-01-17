import os
import re
import json
import hashlib
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# OpenCC
try:
    from opencc import OpenCC
    converter = OpenCC('t2s')
except ImportError:
    converter = None

CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(exist_ok=True)

TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

def get_video_id(url):
    patterns = [
        r'(?:v=|\/videos\/|embed\/|youtu.be\/|\/v\/|\/e\/|watch\?v=|&v=)([^#\&\?\n]*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:11]

def split_text(text, max_len=25):
    if converter:
        text = converter.convert(text)
    if len(text) <= max_len:
        return [text]
    
    connectors = ['但是', '所以', '因此', '然后', '而且', '并且', '或者', '不过', '但', '而', 
                  'but', 'so', 'therefore', 'then', 'and', 'or', 'however', 'yet']
    sentence_endings = r'([。！？；.!?;])'
    minor_punctuations = r'([，,、])'
    
    sentences = re.split(sentence_endings, text)
    result = []
    current = ""
    
    i = 0
    while i < len(sentences):
        part = sentences[i]
        if i + 1 < len(sentences) and re.match(sentence_endings, sentences[i + 1]):
            part += sentences[i + 1]
            i += 2
        else:
            i += 1
        
        if len(part) > max_len:
            sub_parts = re.split(minor_punctuations, part)
            sub_current = ""
            j = 0
            while j < len(sub_parts):
                sub_part = sub_parts[j]
                if j + 1 < len(sub_parts) and re.match(minor_punctuations, sub_parts[j + 1]):
                    sub_part += sub_parts[j + 1]
                    j += 2
                else:
                    j += 1
                
                can_split_at_connector = False
                for conn in connectors:
                    if sub_part.strip().startswith(conn):
                        can_split_at_connector = True
                        break
                
                if len(sub_current) + len(sub_part) > max_len and sub_current:
                    result.append(sub_current.strip())
                    sub_current = sub_part
                elif can_split_at_connector and sub_current and len(sub_current) > max_len * 0.5:
                    result.append(sub_current.strip())
                    sub_current = sub_part
                else:
                    sub_current += sub_part
            
            if sub_current:
                if len(sub_current) > max_len:
                    while len(sub_current) > max_len:
                        result.append(sub_current[:max_len].strip())
                        sub_current = sub_current[max_len:]
                if sub_current.strip():
                    current = sub_current
        else:
            if len(current) + len(part) > max_len and current:
                result.append(current.strip())
                current = part
            else:
                current += part
    
    if current:
        if len(current) > max_len:
            while len(current) > max_len:
                result.append(current[:max_len].strip())
                current = current[max_len:]
        if current.strip():
            result.append(current.strip())
    
    return [r for r in result if r] or [text]

def compress_audio(audio_path):
    MAX_SIZE = 24 * 1024 * 1024
    if os.path.getsize(audio_path) <= MAX_SIZE:
        return audio_path
    
    compressed_path = str(Path(audio_path).parent / f"compressed_{Path(audio_path).name}")
    cmd = ['ffmpeg', '-y', '-i', audio_path, '-ar', '16000', '-ac', '1', '-b:a', '32k', compressed_path]
    try:
        subprocess.run(cmd, capture_output=True, text=True)
        if os.path.getsize(compressed_path) > MAX_SIZE:
            final_path = str(Path(audio_path).parent / f"final_{Path(audio_path).name}")
            cmd = ['ffmpeg', '-y', '-i', compressed_path, '-ar', '8000', '-ac', '1', '-b:a', '16k', final_path]
            subprocess.run(cmd, capture_output=True)
            return final_path
        return compressed_path
    except Exception as e:
        logging.error(f"Compression error: {e}")
        return audio_path
