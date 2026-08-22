"""
S-Net 过滤器共享常量与规范化工具。

集中管理 min_shindo / station_min_shindo / min_triggered_stations 的
默认值、合法范围与解析逻辑，避免校验、装配、轮询、解析、规则层各自硬编码。
"""

from __future__ import annotations

from typing import Any

# 計測震度合法范围（MSIL 低端色阶可到负值）
SHINDO_MIN = -3.0
SHINDO_MAX = 7.0

# 默认阈值
DEFAULT_MIN_SHINDO = 1.5
DEFAULT_STATION_MIN_SHINDO = 0.5
DEFAULT_MIN_TRIGGERED_STATIONS = 0  # 0 表示不限制

# 触发测站数合法范围（与 schema slider.max 对齐）
MIN_TRIGGERED_STATIONS_MIN = 0
MIN_TRIGGERED_STATIONS_MAX = 156

DEFAULT_COMBINE_MODE = "any"
VALID_COMBINE_MODES = frozenset({"any", "all"})


def clamp_shindo(value: float) -> float:
    """将震度值钳制到合法范围。"""
    return max(SHINDO_MIN, min(SHINDO_MAX, float(value)))


def normalize_shindo(
    value: Any,
    *,
    default: float = DEFAULT_MIN_SHINDO,
) -> float:
    """解析并钳制震度阈值；非法类型回退 default。"""
    try:
        if value is None:
            return float(default)
        return clamp_shindo(float(value))
    except (TypeError, ValueError):
        return float(default)


def normalize_min_shindo(value: Any) -> float:
    """规范化最大震度门槛（默认 1.5）。"""
    return normalize_shindo(value, default=DEFAULT_MIN_SHINDO)


def normalize_station_min_shindo(value: Any) -> float:
    """规范化测站计数用震度门槛（默认 0.5）。"""
    return normalize_shindo(value, default=DEFAULT_STATION_MIN_SHINDO)


def normalize_min_triggered_stations(value: Any) -> int:
    """规范化最小触发测站数；非法类型回退 0，并钳制到 0~156。"""
    try:
        if value is None:
            return DEFAULT_MIN_TRIGGERED_STATIONS
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MIN_TRIGGERED_STATIONS
    return max(MIN_TRIGGERED_STATIONS_MIN, min(MIN_TRIGGERED_STATIONS_MAX, count))


def normalize_combine_mode(value: Any) -> str:
    """规范化条件组合方式，仅允许 any/all。"""
    mode = str(value or DEFAULT_COMBINE_MODE).strip().lower()
    return mode if mode in VALID_COMBINE_MODES else DEFAULT_COMBINE_MODE


def count_triggered_stations(stations: Any, station_min_shindo: float) -> int:
    """按给定震度阈值统计触发测站数；非法 shindo 跳过。"""
    if not isinstance(stations, list):
        return 0
    count = 0
    for item in stations:
        if not isinstance(item, dict):
            continue
        try:
            shindo_val = float(item.get("shindo", -999.0))
        except (TypeError, ValueError):
            continue
        if shindo_val >= station_min_shindo:
            count += 1
    return count


__all__ = [
    "SHINDO_MIN",
    "SHINDO_MAX",
    "DEFAULT_MIN_SHINDO",
    "DEFAULT_STATION_MIN_SHINDO",
    "DEFAULT_MIN_TRIGGERED_STATIONS",
    "MIN_TRIGGERED_STATIONS_MIN",
    "MIN_TRIGGERED_STATIONS_MAX",
    "DEFAULT_COMBINE_MODE",
    "VALID_COMBINE_MODES",
    "clamp_shindo",
    "normalize_shindo",
    "normalize_min_shindo",
    "normalize_station_min_shindo",
    "normalize_min_triggered_stations",
    "normalize_combine_mode",
    "count_triggered_stations",
]
