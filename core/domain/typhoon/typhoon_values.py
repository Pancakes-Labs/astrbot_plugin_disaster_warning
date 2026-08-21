"""台风领域通用值清洗工具。"""

from __future__ import annotations

import math
from typing import Any

# NAMELESS：EQSC 对未命名台风返回的字面量占位符，与 NULL/NONE 语义一致，
# 必须当作空值清洗，否则会屏蔽 fallback 名称生成并直接展示在推送正文。
NULL_TEXTS = {"NULL", "NONE", "N/A", "-", "无数据", "NAMELESS"}


def is_nullish(value: Any) -> bool:
    """判断值是否表示缺失或 EQSC 常见空值。"""
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.upper() in NULL_TEXTS


def clean_text(value: Any) -> str:
    """清洗文本；缺失值返回空字符串。"""
    if is_nullish(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    """宽松转换为 float；空值或非法值返回 None。"""
    if is_nullish(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def to_int(value: Any) -> int | None:
    """先按 float 清洗，再转换为 int。"""
    number = to_float(value)
    return int(number) if number is not None else None
