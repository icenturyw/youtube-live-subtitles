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
                     # Fallback to simple split for this chunk
                     final_sentences.extend(split_text(s, max_len)) 
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
