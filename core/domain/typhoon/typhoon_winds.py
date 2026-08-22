"""台风风圈领域工具。"""

from __future__ import annotations

from typing import Any

from .typhoon_values import is_nullish, to_float

WIND_CIRCLE_KEYS = ("30KTS", "50KTS", "64KTS")
WIND_CIRCLE_LABELS = {"30KTS": "七级风圈", "50KTS": "十级风圈", "64KTS": "十二级风圈"}
NULLISH_RADIUS_TEXTS = {"", "NULL", "NONE", "无数据", "-", "null", "None"}


def has_valid_radius(value: Any) -> bool:
    """判断风圈半径是否为有效正数。"""
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        if not text or text in NULLISH_RADIUS_TEXTS or text.upper() in {"NULL", "NONE"}:
            return False
        number = to_float(text)
        return number is not None and number > 0
    if isinstance(value, (int, float)):
        return float(value) > 0
    return False


def clean_wind_circle(raw_wind_circle: Any) -> dict[str, Any]:
    """清洗 EQSC 四象限风圈，剔除 NULL/空象限。"""
    if not isinstance(raw_wind_circle, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for circle_key, circle_data in raw_wind_circle.items():
        if not isinstance(circle_data, dict):
            continue
        clean_circle: dict[str, Any] = {}
        for quadrant, radius in circle_data.items():
            if is_nullish(radius):
                continue
            if isinstance(radius, str) and radius.strip().upper() in {
                "",
                "NULL",
                "NONE",
                "无数据",
            }:
                continue
            number = to_float(radius)
            if number is None or number <= 0:
                continue
            clean_circle[quadrant] = (
                int(number) if float(number).is_integer() else number
            )
        if clean_circle:
            cleaned[str(circle_key)] = clean_circle
    return cleaned


def constrain_wind_circle_by_fan_radius(
    wind_circle: dict[str, Any] | None,
    *,
    fan_radius7: Any = None,
    fan_radius10: Any = None,
) -> dict[str, Any]:
    """用 FAN Studio 当前风圈空值约束 EQSC 四象限风圈。

    30KTS ≈ 7 级，50KTS ≈ 10 级，64KTS ≈ 12 级。
    FAN 当前观测若明确没有对应等级，则不允许历史节点补回。
    """
    has_radius7 = has_valid_radius(fan_radius7)
    has_radius10 = has_valid_radius(fan_radius10)
    if not has_radius7 and not has_radius10:
        return {}
    if not wind_circle:
        return {}

    constrained = dict(wind_circle)
    if not has_radius7:
        constrained.pop("30KTS", None)
    if not has_radius10:
        constrained.pop("50KTS", None)
        # 12 级风圈隐含更高强度，FAN 未给出 10 级时不应单独补 12 级
        constrained.pop("64KTS", None)
    return constrained


def extract_max_radius(wind_circle: Any, circle_key: str) -> float | None:
    """从四象限风圈中提取指定风圈的最大正半径。"""
    if not isinstance(wind_circle, dict):
        return None
    circle = wind_circle.get(circle_key)
    if not isinstance(circle, dict):
        return None
    values = [
        number
        for number in (to_float(value) for value in circle.values())
        if number is not None and number > 0
    ]
    return max(values) if values else None


__all__ = [
    "WIND_CIRCLE_KEYS",
    "WIND_CIRCLE_LABELS",
    "clean_wind_circle",
    "constrain_wind_circle_by_fan_radius",
    "extract_max_radius",
    "has_valid_radius",
]
