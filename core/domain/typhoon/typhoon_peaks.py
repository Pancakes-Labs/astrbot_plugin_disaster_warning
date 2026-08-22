"""台风主表峰值字段合并规则。"""

from __future__ import annotations

from typing import Any

from .typhoon_levels import level_weight, normalize_level
from .typhoon_values import to_float


def _positive_float(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 else None


def merge_peak_metrics(
    existing_level: Any = "",
    existing_wind: Any = None,
    existing_pressure: Any = None,
    incoming_level: Any = "",
    incoming_wind: Any = None,
    incoming_pressure: Any = None,
) -> tuple[str | None, float | None, float | None]:
    """合并主表峰值，返回 (peak_level, peak_wind, min_pressure)。"""
    old_level = normalize_level(existing_level)
    new_level = normalize_level(incoming_level)
    peak_level = (
        new_level
        if level_weight(new_level) > level_weight(old_level)
        else (old_level or new_level or None)
    )

    winds = [
        value
        for value in (_positive_float(existing_wind), _positive_float(incoming_wind))
        if value is not None
    ]
    pressures = [
        value
        for value in (
            _positive_float(existing_pressure),
            _positive_float(incoming_pressure),
        )
        if value is not None
    ]
    return (
        peak_level,
        (max(winds) if winds else None),
        (min(pressures) if pressures else None),
    )


def resolve_storage_peak_fields(
    *,
    existing_level: Any = None,
    existing_wind: Any = None,
    existing_pressure: Any = None,
    event_data: dict[str, Any],
) -> tuple[Any, Any, Any, Any, Any, Any]:
    """为存储层解析主表峰值与 updates 快照字段。

    业务公式只在 domain 层出现：
    - 主表：峰值 level / 最大 wind / 最低 pressure
    - updates：优先使用 `_snapshot_*` 当次观测
    """
    peak_level, peak_wind, min_pressure = merge_peak_metrics(
        existing_level=existing_level,
        existing_wind=existing_wind,
        existing_pressure=existing_pressure,
        incoming_level=event_data.get("level"),
        incoming_wind=event_data.get("wind_speed"),
        incoming_pressure=event_data.get("pressure"),
    )
    level_to_store = peak_level or event_data.get("level")
    wind_to_store = peak_wind if peak_wind is not None else event_data.get("wind_speed")
    pressure_to_store = (
        min_pressure if min_pressure is not None else event_data.get("pressure")
    )
    snapshot_level = event_data.get("_snapshot_level", event_data.get("level"))
    snapshot_wind = event_data.get("_snapshot_wind_speed", event_data.get("wind_speed"))
    snapshot_pressure = event_data.get("_snapshot_pressure", event_data.get("pressure"))
    return (
        level_to_store,
        wind_to_store,
        pressure_to_store,
        snapshot_level,
        snapshot_wind,
        snapshot_pressure,
    )


__all__ = ["merge_peak_metrics", "resolve_storage_peak_fields"]
