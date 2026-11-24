# -*- coding: utf-8 -*-
import re
import unicodedata
from typing import Optional

def arabic_to_chinese_num(n: int) -> str:
    """將 1~99 的阿拉伯數字轉為台灣地址通用的中文數字 (例如: 5 -> 五, 10 -> 十, 13 -> 十三, 21 -> 二十一)"""
    if n <= 0 or n >= 100:
        return str(n)
    
    digits = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if n < 10:
        return digits[n]
    elif n == 10:
        return "十"
    elif n < 20:
        return f"十{digits[n % 10]}"
    else:
        tens = digits[n // 10]
        units = digits[n % 10]
        return f"{tens}十{units}"

def fullwidth_to_halfwidth(s: str) -> str:
    """將全形字元 (包含全形數字、英文字母、空格) 轉為半形"""
    if not s:
        return ""
    return unicodedata.normalize('NFKC', s)

def normalize_road_name(text: str) -> str:
    """
    標準化台灣路名與段別：
    1. 統一 臺 -> 台
    2. 全形轉半形 (如 ５段 -> 5段)
    3. 數字轉國字段別 (如 向上路5段 -> 向上路五段, 台灣大道3段 -> 台灣大道三段)
    4. 重劃區序號路街相容 (如 敦富1街 -> 敦富一街, 軍福13路 -> 軍福十三路)
    5. 保護固有專有名詞 (如 三民路、五權路、八德路 不被誤傷)
    """
    if not text:
        return ""
    
    # 移除 HTML 標籤
    s = re.sub(r'<[^>]+>', '', str(text))
    # 全形轉半形
    s = fullwidth_to_halfwidth(s)
    # 統一 臺 -> 台
    s = s.replace('臺', '台')
    
    # 1. 轉換段別：例如「5段」、「10段」 -> 「五段」、「十段」
    def _replace_sec(m):
        num = int(m.group(1))
        return arabic_to_chinese_num(num) + "段"
    
    s = re.sub(r'(\d{1,2})\s*段', _replace_sec, s)
    
    # 2. 轉換重劃區序列街/路：例如「敦富1街」->「敦富一街」、「軍福13路」->「軍福十三路」
    # 條件：數字前必須為中文字，且數字後緊接「街」或「路」
    def _replace_street(m):
        prefix = m.group(1)
        num = int(m.group(2))
        suffix = m.group(3)
        return prefix + arabic_to_chinese_num(num) + suffix
    
    s = re.sub(r'([\u4e00-\u9fa5]{2,})(\d{1,2})([街路])', _replace_street, s)
    
    # 去除前後多餘空白與特殊符號
    return s.strip()

def normalize_taiwan_address(addr: str) -> str:
    """標準化完整台灣地址"""
    if not addr:
        return ""
    return normalize_road_name(addr)
