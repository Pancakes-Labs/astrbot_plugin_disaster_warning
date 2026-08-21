"""台风名称展示格式化。"""

from __future__ import annotations

from .typhoon_ids import extract_td_short_id, is_eqsc_placeholder_id
from .typhoon_levels import LEVEL_EN_ABBR
from .typhoon_values import clean_name_text, clean_text


def format_display_name(
    name_cn: object = "",
    name_en: object = "",
    typhoon_id: object = "",
    *,
    fallback: str = "未知台风",
) -> str:
    """生成统一的"中文（EN）"展示名，避免英文名重复拼接。"""
    # 名称字段用 clean_name_text：额外清洗 NAMELESS 占位符，
    # 避免未命名台风的占位文本直接展示在推送正文。
    cn = clean_name_text(name_cn).replace("(", "（").replace(")", "）")
    en = clean_name_text(name_en)
    # ID 字段用 clean_text：NAMELESS 是合法 ID 字面量，不应被清洗为空。
    tid = clean_text(typhoon_id)
    if cn and en:
        if en in cn or f"（{en}）" in cn or f"({en})" in cn:
            return cn
        return f"{cn}（{en}）"
    return cn or en or tid or fallback


def _level_to_en_abbr(level_cn: str) -> str:
    """把中文等级映射为英文缩写；未知等级回退为 TD。"""
    return LEVEL_EN_ABBR.get(level_cn, "TD")


def build_td_fallback_names(typhoon_id: object, level: object = "") -> tuple[str, str]:
    """为无名低压生成可读回退名称（中文名, 英文名）。

    EQSC/FAN 活跃无名低压条目的 nameCN/nameEN 常为占位符（如 "None"/"NAMELESS"），
    清洗后为空；此处基于编号与等级生成与停编条目一致的展示名：

    - 编号 TD07 / NAMELESS_07 + 等级"热带低压"
      -> ("07号热带低压", "TD No.07")
    - EQSC 占位编号 26XX（尚未正式编号）+ 等级"热带风暴"
      -> ("无名热带风暴", "Unnamed TS")
      （无编号可提取，用"无名"+等级生成可读名称）

    编号不匹配 TD/NAMELESS 合法格式且非占位编号时返回空字符串，
    由调用方保持原样（不臆造名称）。
    """
    raw = str(typhoon_id or "").strip()
    level_cn = clean_text(level) or "热带低压"
    level_en = _level_to_en_abbr(level_cn)

    # EQSC 占位编号（26XX）：尚未正式编号，无法提取数字编号，
    # 用"无名"+等级生成可读名称，避免推送正文缺失名称行。
    if is_eqsc_placeholder_id(raw):
        return f"无名{level_cn}", f"Unnamed {level_en}"

    short_id = extract_td_short_id(typhoon_id)
    if not short_id or short_id == "TD":
        # 裸 NAMELESS / TD（无数字后缀）：同样用"无名"+等级。
        if short_id == "TD":
            return f"无名{level_cn}", f"Unnamed {level_en}"
        return "", ""
    digits = short_id[2:]
    if not digits:
        return "", ""
    return f"{digits}号{level_cn}", f"TD No.{digits}"
