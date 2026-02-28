# 专业领域术语词库
import json
import os
import logging
from pathlib import Path

# 内置词典（不可删除）
BUILTIN_LEXICON = {
    "finance": {
        "terms": "价格行为学，信号棒，入场棒，趋势棒，十字星，反转，突破，回调，等长运动，楔形，双顶，双底，交易区间，窄幅通道，磁吸效应，假突破，测试位，挂限价单，支撑压力位，K线组合，止损止盈，均线系统，多头空头，仓位管理，杠杆倍数，流动性衰减，波动率扩张，成交量分布。挂单，限价，市价。",
        "label": "金融交易",
        "replacements": {}
    },
    "programming": {
        "terms": "代码实现，函数调用，类继承，变量作用域，数组索引，字典映射，递归算法，循环控制，异步编程，并发处理，线程安全，进程通信，接口设计，数据库事务，缓存策略。",
        "label": "计算机编程",
        "replacements": {}
    },
    "medical": {
        "terms": "临床诊断，症状表现，治疗方案，药物反应，手术过程，感染控制，免疫系统，血压监测，血糖调节，心率变异，体温测量，副作用说明，处方药管理，病历记录。",
        "label": "医学健康",
        "replacements": {}
    },
    "gaming": {
        "terms": "玩家对战，关卡设计，副本攻略，装备属性，技能冷却，经验值补偿，等级上限，团队合作，竞技模式，任务系统，成就解锁，段位排行，匹配机制，皮肤特效。",
        "label": "游戏电竞",
        "replacements": {}
    },
    "music": {
        "terms": "这是音乐歌词。请准确识别歌词内容，忽略背景伴奏。如果是周杰伦的歌，请注意其独特的吐字风格，确保识别准确。请输出简体中文。",
        "label": "音乐/歌词识别",
        "replacements": {}
    },
    "general": {
        "terms": "以下是普通话的句子，请用简体中文。",
        "label": "通用/自动",
        "replacements": {}
    }
}

# 自定义词典文件路径
CUSTOM_LEXICON_FILE = Path(__file__).parent.parent / "custom_lexicon.json"


def load_custom_lexicon():
    """加载用户自定义词典"""
    if CUSTOM_LEXICON_FILE.exists():
        try:
            with open(CUSTOM_LEXICON_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"加载自定义词典失败: {e}")
    return {}


def save_custom_lexicon(data):
    """保存用户自定义词典"""
    try:
        with open(CUSTOM_LEXICON_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"保存自定义词典失败: {e}")
        return False


def get_all_lexicon():
    """获取所有词典（内置 + 自定义），用于前端展示"""
    result = {}
    # 内置词典
    for domain, data in BUILTIN_LEXICON.items():
        result[domain] = {
            **data,
            "builtin": True
        }
    # 自定义词典（可覆盖内置同名项的 replacements）
    custom = load_custom_lexicon()
    for domain, data in custom.items():
        if domain in result:
            # 合并：内置术语 + 用户自定义替换规则
            result[domain]["replacements"].update(data.get("replacements", {}))
            if data.get("terms"):
                result[domain]["terms"] += "，" + data["terms"]
        else:
            result[domain] = {
                "terms": data.get("terms", ""),
                "label": data.get("label", domain),
                "replacements": data.get("replacements", {}),
                "builtin": False
            }
    return result


def get_prompt_by_domain(domain):
    """根据领域获取对应的 initial_prompt（合并内置 + 自定义术语）"""
    all_lexicon = get_all_lexicon()
    
    if not domain or domain not in all_lexicon:
        return all_lexicon.get("general", {}).get("terms", "以下是普通话的句子，请用简体中文。")
    
    data = all_lexicon[domain]
    keywords = data.get("terms", "")
    label = data.get("label", domain)
    
    if domain == "general":
        return keywords
    if domain == "music":
        return keywords
    
    return f"这是一段关于{label}的专业视频。请确保术语识别准确，特别是：{keywords}。请直接输出简体中文，不要繁体。"


def apply_term_replacements(subtitles, domain):
    """对识别结果应用术语替换规则"""
    all_lexicon = get_all_lexicon()
    if not domain or domain not in all_lexicon:
        return subtitles
    
    replacements = all_lexicon[domain].get("replacements", {})
    if not replacements:
        return subtitles
    
    replaced_count = 0
    for sub in subtitles:
        original_text = sub.get('text', '')
        text = original_text
        for wrong, correct in replacements.items():
            if wrong in text:
                text = text.replace(wrong, correct)
        if text != original_text:
            sub['text'] = text
            replaced_count += 1
    
    if replaced_count:
        logging.info(f"术语替换完成: {replaced_count} 条字幕被修正 (domain={domain})")
    
    return subtitles
