"""台风编号格式转换与规范化。"""

from __future__ import annotations


def _clean_id(typhoon_id: object) -> str:
    return str(typhoon_id or "").strip()


def to_eqsc_id(typhoon_id: object) -> str:
    """将 4/6 位编号转换为 EQSC 4 位形式。"""
    text = _clean_id(typhoon_id)
    if not text:
        return ""
    if len(text) >= 4 and text.isdigit():
        return text[-4:]
    return text


def to_fan_id(typhoon_id: object) -> str:
    """将 4 位 EQSC 编号转换为 Fan 6 位形式。"""
    text = _clean_id(typhoon_id)
    if not text:
        return ""
    if len(text) == 4 and text.isdigit():
        return f"20{text}"
    return text


def normalize_typhoon_id(typhoon_id: object) -> str:
    """返回用于缓存、去重和跨来源匹配的稳定编号。

    规则：
    - 纯数字编号（202607 / 2607）：统一为 4 位年份短编号（2607）；
    - NAMELESS 无名低压（NAMELESS_07 / NAMELESS-2604 / TD07）：
      统一为 TD + 两位短编号（TD07 / TD04），
      与 FAN Studio 侧 TDxx 编号归一到同一去重键，避免同源台风重复推送；
    - 其他非标准编号：原样返回，不从混合文本硬抠数字。
    """
    raw = _clean_id(typhoon_id)
    if not raw:
        return ""
    upper = raw.upper()
    digits = "".join(char for char in raw if char.isdigit())

    # NAMELESS / TD 前缀的无名低压：统一为 TD + 两位短编号
    if upper.startswith("NAMELESS") or upper.startswith("TD"):
        if digits:
            return f"TD{digits[-2:]}"
        # 无数字可提取（如裸 TD / NAMELESS）：保持前缀本身
        if upper.startswith("NAMELESS"):
            return "TD"
        return "TD"

    return digits[-4:] if len(digits) >= 4 else raw
