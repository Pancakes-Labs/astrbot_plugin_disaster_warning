"""台风名称展示格式化。"""

from __future__ import annotations

from .typhoon_ids import extract_td_short_id
from .typhoon_values import clean_text


def format_display_name(
    name_cn: object = "",
    name_en: object = "",
    typhoon_id: object = "",
    *,
    fallback: str = "未知台风",
) -> str:
    """生成统一的“中文（EN）”展示名，避免英文名重复拼接。"""
    cn = clean_text(name_cn).replace("(", "（").replace(")", "）")
    en = clean_text(name_en)
    tid = clean_text(typhoon_id)
    if cn and en:
        if en in cn or f"（{en}）" in cn or f"({en})" in cn:
            return cn
        return f"{cn}（{en}）"
    return cn or en or tid or fallback


def build_td_fallback_names(typhoon_id: object, level: object = "") -> tuple[str, str]:
    """为无名低压生成可读回退名称（中文名, 英文名）。

    EQSC/FAN 活跃无名低压条目的 nameCN/nameEN 常为占位符（如 "None"），
    清洗后为空；此处基于编号与等级生成与停编条目一致的展示名：

    - 编号 TD07 / NAMELESS_07 + 等级"热带低压"
      -> ("07号热带低压", "TD No.07")

    编号不匹配 TD/NAMELESS 合法格式或提取失败时返回空字符串，
    由调用方保持原样（不臆造名称）。
    """
    short_id = extract_td_short_id(typhoon_id)
    if not short_id or short_id == "TD":
        return "", ""
    digits = short_id[2:]
    if not digits:
        return "", ""
    level_cn = clean_text(level) or "热带低压"
    return f"{digits}号{level_cn}", f"TD No.{digits}"
