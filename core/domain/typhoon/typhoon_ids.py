"""台风编号格式转换与规范化。"""

from __future__ import annotations

import re

# 无名低压合法格式：NAMELESS / TD 前缀 + 可选分隔符（_/-）+ 1~4 位数字后缀。
# 完整匹配，拒绝 NAMELESSNESS_07 / TDX_07 等仅以关键词开头的非标准编号，
# 避免去重键误合并不同系统。
_TD_FORMAT = re.compile(
    r"^(?:NAMELESS|TD)(?:[_-]?\d{1,4})?$",
    re.IGNORECASE,
)


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
    """从 NAMELESS/TD 无名低压编号中提取 TD + 两位短编号。

    仅接受完整合法格式（NAMELESS / TD + 可选分隔符 + 数字后缀）：
    - NAMELESS / TD（裸）-> TD
    - NAMELESS_07 / TD07 / TD_07 / NAMELESS07 -> TD07
    - NAMELESS_2604 -> TD04（仅取两位短编号，避免与正式编号 2604 混淆）

    格式不匹配（如 NAMELESSNESS_07 / TDX_07）时返回空字符串，
    由调用方回退到原编号，避免去重键误合并不同系统。

    供去重键（normalize_typhoon_id）与展示格式（format_typhoon_short_id）
    共用，避免两处规则在未来产生差异。
    """
    raw = _clean_id(typhoon_id)
    if not raw or not _TD_FORMAT.match(raw):
        return ""
    upper = raw.upper()
    if upper == "NAMELESS" or upper == "TD":
        return "TD"
    digits = "".join(char for char in raw if char.isdigit())
    if digits:
        return f"TD{digits[-2:]}"
    return "TD"


def normalize_typhoon_id(typhoon_id: object) -> str:
    """返回用于缓存、去重和跨来源匹配的稳定编号。

    规则：
    - 纯数字编号（202607 / 2607）：统一为 4 位年份短编号（2607）；
    - 无名低压（NAMELESS_07 / NAMELESS-2604 / TD07 等合法格式）：
      统一为 TD + 两位短编号（TD07 / TD04），
      与 FAN Studio 侧 TDxx 编号归一到同一去重键，避免同源台风重复推送；
    - 其他非标准编号：原样返回，不从混合文本硬抠数字。
    """
    raw = _clean_id(typhoon_id)
    if not raw:
        return ""
    # 无名低压统一键：仅当格式完全合法时归一化，
    # 否则（如 NAMELESSNESS_07 / TDX_07）回退原编号，避免误合并去重键。
    td_short = extract_td_short_id(raw)
    if td_short:
        return td_short
    # 纯数字官方编号统一为 4 位短编号
    if raw.isdigit():
        return raw[-4:]
    # 如果有其他非标准编号，原样返回，避免不同 ID 因硬抠数字而误共享同一去重键。
    return raw
