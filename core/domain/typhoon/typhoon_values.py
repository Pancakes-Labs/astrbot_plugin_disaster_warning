"""台风领域通用值清洗工具。"""

from __future__ import annotations

import math
from typing import Any

# 通用空值占位符集合：用于名称、等级、方向等文本字段的清洗。
# 注意：NAMELESS 不在此集合中——它是 EQSC 对未命名台风返回的合法 ID 字面量，
# typhoon_ids.py 将裸 NAMELESS 视为合法编号并归一化为 TD。
# 若在此处清洗为空，会导致事件适配器/轮询服务丢弃以 NAMELESS 为 id 的条目。
# 名称字段的 NAMELESS 清洗由 _NAME_NULL_TEXTS 单独维护。
NULL_TEXTS = {"NULL", "NONE", "N/A", "-", "无数据"}

# 名称字段专用空值集合：在通用空值基础上额外包含 NAMELESS。
# EQSC 对未命名台风的 nameCN/nameEN 字段可能返回 "NAMELESS" 占位符，
# 必须在名称清洗时视为空值，否则会屏蔽 fallback 名称生成并直接展示在推送正文。
_NAME_NULL_TEXTS = NULL_TEXTS | {"NAMELESS"}


def is_nullish(value: Any) -> bool:
    """判断值是否表示缺失或 EQSC 常见空值。"""
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.upper() in NULL_TEXTS


def clean_text(value: Any) -> str:
    """清洗文本；缺失值返回空字符串。

    注意：此函数不清洗 NAMELESS——它是 EQSC 合法 ID 字面量。
    名称字段的清洗请使用 clean_name_text。
    """
    if is_nullish(value):
        return ""
    return str(value).strip()


def clean_name_text(value: Any) -> str:
    """清洗名称字段；缺失值与 NAMELESS 占位符返回空字符串。

    EQSC 对未命名台风的 nameCN/nameEN 字段可能返回 "NAMELESS" 占位符，
    必须在名称清洗时视为空值，否则会屏蔽 fallback 名称生成并直接展示在推送正文。
    ID 字段不调用此函数，避免裸 NAMELESS 被误清洗为空导致条目丢弃。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() in _NAME_NULL_TEXTS:
        return ""
    return text


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
