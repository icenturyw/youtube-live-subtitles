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

CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(exist_ok=True)

TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

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

def split_text_by_spacy(text, nlp, max_len=25):
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
            return split_text_by_spacy(text, nlp, max_len)

    # Fallback to Regex
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
