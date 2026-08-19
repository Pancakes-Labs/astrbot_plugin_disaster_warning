"""台风展示层格式化工具。

供推送 Presenter 与查询 Presenter 共用，避免查询层反向依赖消息展示器。
仅处理展示文案，不改变业务字段语义。
"""

from __future__ import annotations

import math
from typing import Any

from ....utils.severity_emoji import TYPHOON_LEVEL_EMOJI, typhoon_level_emoji
from .typhoon_ids import extract_td_short_id
from .typhoon_values import clean_text, to_float

# 移动方向展示映射：仅用于展示本地化，不改动原始业务字段。
MOVE_DIRECTION_DISPLAY_MAP: dict[str, str] = {
    "北": "正北",
    "东": "正东",
    "南": "正南",
    "西": "正西",
    "正北": "正北",
    "正东": "正东",
    "正南": "正南",
    "正西": "正西",
    "北东": "东北",
    "东北": "东北",
    "南东": "东南",
    "东南": "东南",
    "南西": "西南",
    "西南": "西南",
    "北西": "西北",
    "西北": "西北",
    "北北东": "东北偏北",
    "东北东": "东北偏东",
    "东东北": "东北偏东",
    "东南东": "东南偏东",
    "东东南": "东南偏东",
    "南南东": "东南偏南",
    "南南西": "西南偏南",
    "西南西": "西南偏西",
    "西西南": "西南偏西",
    "西北西": "西北偏西",
    "西西北": "西北偏西",
    "北北西": "西北偏北",
    "东北偏北": "东北偏北",
    "东北偏东": "东北偏东",
    "东南偏东": "东南偏东",
    "东南偏南": "东南偏南",
    "西南偏南": "西南偏南",
    "西南偏西": "西南偏西",
    "西北偏西": "西北偏西",
    "西北偏北": "西北偏北",
    "北偏东": "东北偏北",
    "东偏北": "东北偏东",
    "东偏南": "东南偏东",
    "南偏东": "东南偏南",
    "南偏西": "西南偏南",
    "西偏南": "西南偏西",
    "西偏北": "西北偏西",
    "北偏西": "西北偏北",
}

WIND_CIRCLE_LABELS = {
    "30KTS": "7 级",
    "50KTS": "10级",
    "64KTS": "12级",
}
WIND_QUADRANT_LABELS = {
    "NE": "东北",
    "SE": "东南",
    "SW": "西南",
    "NW": "西北",
}


def format_coordinates(latitude: float | None, longitude: float | None) -> str:
    """把经纬度格式化为带方向标识的文本。"""
    if latitude is None or longitude is None:
        return ""
    lat_dir = "N" if latitude >= 0 else "S"
    lon_dir = "E" if longitude >= 0 else "W"
    return f"{abs(latitude):.1f}°{lat_dir}, {abs(longitude):.1f}°{lon_dir}"


def is_valid_radius_value(value: Any) -> bool:
    """判断单值风圈是否可展示。"""
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"无数据", "NULL", "null", "None", "-"}:
            return False
        try:
            number = float(text)
        except ValueError:
            return False
        return math.isfinite(number) and number > 0
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and value > 0
    return False


def format_wind_circle(wind_circle: dict[str, Any] | None) -> list[str]:
    """格式化 EQSC 四象限风圈数据为紧凑表格文本行。

    输出样例（象限顺序固定为 NE/SE/SW/NW）：
        (NE, SE, SW, NW)
        7 级：450, 420, 420, 450 (KM)
        10级：250, 220, 220, 250 (KM)
        12级：100, 100, 100, 100 (KM)

    缺失象限以「-」占位保持数值列对齐；整级无有效数值时跳过该行。
    """
    lines: list[str] = []
    if not wind_circle or not isinstance(wind_circle, dict):
        return lines

    collected: list[tuple[str, list[str]]] = []
    for circle_key, label in WIND_CIRCLE_LABELS.items():
        circle_data = wind_circle.get(circle_key)
        if not isinstance(circle_data, dict):
            continue
        values: list[str] = []
        valid_count = 0
        for quadrant in WIND_QUADRANT_LABELS:
            radius = circle_data.get(quadrant)
            if isinstance(radius, str) and radius.strip().upper() in {
                "",
                "NULL",
                "NONE",
                "无数据",
            }:
                radius = None
            number = to_float(radius)
            if number is None or not math.isfinite(number) or number <= 0:
                values.append("-")
                continue
            radius_text = (
                str(int(number)) if float(number).is_integer() else str(number)
            )
            values.append(radius_text)
            valid_count += 1
        if valid_count == 0:
            continue
        collected.append((label, values))

    if not collected:
        return lines

    # 表头行（象限缩写）由调用方拼接在“风圈半径：”之后
    lines.append(f"({', '.join(WIND_QUADRANT_LABELS)})")
    for label, values in collected:
        lines.append(f"{label}：{', '.join(values)} (KM)")
    return lines


def format_wind_speed(wind_speed: float | None, power: int | None) -> str | None:
    """把风速与风力合并为「20 m/s（8级）」格式。"""
    if wind_speed is None and power is None:
        return None
    parts: list[str] = []
    if wind_speed is not None:
        parts.append(f"{wind_speed} m/s")
    if power is not None:
        if parts:
            parts.append(f"（{power}级）")
        else:
            parts.append(f"{power}级")
    return " ".join(parts)


def get_typhoon_level_emoji(typhoon_type: str | None) -> str:
    """根据台风强度等级返回圆形颜色 emoji。"""
    return typhoon_level_emoji(typhoon_type)


def format_typhoon_short_id(*candidates: object) -> str:
    """统一输出台风短编号，规则与前端 formatTyphoonShortId 对齐。

    - 纯数字官方编号：202609 / 2609 -> 2609
    - NAMELESS 无名低压：NAMELESS_2604 / NAMELESS_07 -> TD04 / TD07
      （避免与正式编号 2604 冲突；07 与 EQSC 侧 TD07 归一到同一展示格式）
    - 其他非标准编号：原样返回，不从混合文本硬抠数字

    供推送 Presenter 与查询 Presenter 共用，避免展示逻辑分叉。
    """
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"unknown", "未知"}:
            continue

        if text.isdigit() and len(text) >= 4:
            return text[-4:]

        upper = text.upper()
        # NAMELESS/TD 无名低压统一复用共享提取逻辑（与去重键同规则）
        if upper.startswith("NAMELESS") or upper.startswith("TD"):
            short_id = extract_td_short_id(text)
            if short_id:
                return short_id
            continue

        return text
    return ""


def format_move_direction(direction: str | None) -> str:
    """把源侧移动方向本地化为日常可读写法（仅展示层）。"""
    text = clean_text(direction)
    if not text:
        return ""
    mapped = MOVE_DIRECTION_DISPLAY_MAP.get(text)
    if mapped:
        return mapped
    compact = text.replace(" ", "").replace("　", "")
    return MOVE_DIRECTION_DISPLAY_MAP.get(compact, text)


__all__ = [
    "MOVE_DIRECTION_DISPLAY_MAP",
    "TYPHOON_LEVEL_EMOJI",
    "format_coordinates",
    "format_move_direction",
    "format_typhoon_short_id",
    "format_wind_circle",
    "format_wind_speed",
    "get_typhoon_level_emoji",
    "is_valid_radius_value",
]
