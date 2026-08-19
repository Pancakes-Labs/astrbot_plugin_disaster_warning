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


def extract_td_short_id(typhoon_id: object) -> str:
    """从 NAMELESS/TD 前缀的无名低压编号中提取 TD + 两位短编号。

    - NAMELESS / TD（裸）-> TD
    - NAMELESS_07 / TD07 / TD_07 -> TD07
    - NAMELESS_2604 -> TD04（仅取两位短编号，避免与正式编号 2604 混淆）

    供去重键（normalize_typhoon_id）与展示格式（format_typhoon_short_id）
    共用，避免两处规则在未来产生差异。
    """
    raw = _clean_id(typhoon_id)
    if not raw:
        return ""
    upper = raw.upper()
    if upper == "NAMELESS" or upper == "TD":
        return "TD"
    if upper.startswith("NAMELESS"):
        remainder = raw[len("NAMELESS") :].lstrip("_-")
    elif upper.startswith("TD"):
        remainder = raw[2:].lstrip("_-")
    else:
        return ""
    if not remainder:
        return "TD"
    digits = "".join(char for char in remainder if char.isdigit())
    if digits:
        return f"TD{digits[-2:]}"
    return "TD"


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
    # 无名低压统一键
    if upper.startswith("NAMELESS") or upper.startswith("TD"):
        return extract_td_short_id(raw)
    # 纯数字官方编号统一为 4 位短编号
    if raw.isdigit():
        return raw[-4:]
    # 如果有其他非标准编号，原样返回，避免不同 ID 因硬抠数字而误共享同一去重键。
    return raw
