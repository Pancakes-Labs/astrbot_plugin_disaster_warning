"""台风统计聚合器。

该模块只处理台风统计状态，不依赖统计管理器、数据库或展示服务。
实时推送与历史重建均通过同一个观测入口更新四类派生统计：
- by_level：每次观测的当前等级频次；
- max_wind_typhoons：每个台风的最大风速及其最低气压；
- min_pressure_typhoons：每个台风的最低中心气压；
- by_max_level：每个台风历史最高等级的去重分布。
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from ...domain.typhoon import level_weight, to_float


def _positive_number(value: Any) -> float | None:
    """返回正数观测值；零、负数和非法文本均视为缺失。"""
    number = to_float(value)
    return number if number is not None and number > 0 else None


def _wind_value(entry: Any) -> float:
    """兼容旧统计状态中的 float 或字典风速结构。"""
    if isinstance(entry, dict):
        return _positive_number(entry.get("wind_speed")) or 0.0
    return _positive_number(entry) or 0.0


def _pressure_value(entry: Any) -> float | None:
    """读取榜单条目中的有效气压。"""
    if not isinstance(entry, dict):
        return None
    return _positive_number(entry.get("pressure"))


def record_typhoon_observation(
    stats: MutableMapping[str, Any],
    *,
    display_name: str,
    level: str,
    wind_speed: Any = None,
    pressure: Any = None,
    identity_key: str | None = None,
) -> None:
    """将一次台风观测合并到统计状态中。

    该函数故意接受宽松字典状态，以便实时统计和数据库重建共享算法，
    同时兼容旧版本保存的榜单条目结构。调用方负责先决定展示名称和当前等级。

    Args:
        display_name: 台风展示名（如"杜苏芮（DOKSURI）"），供榜单直接展示。
        identity_key: 可选稳定身份键（如 normalize_typhoon_id 归一化编号）。
            提供时以身份键作为各榜单 map 的内部 key，并在条目内保留展示名，
            避免同一台风生命周期内展示名变化（如无名低压 TD07 阶段与命名后
            "杜苏芮（DOKSURI）"）导致统计分裂成多个条目；缺省时回退到
            display_name，兼容旧调用方与历史数据结构。
    """
    level_text = str(level or "未知").strip() or "未知"
    stats.setdefault("by_level", {})[level_text] += 1
    if not display_name:
        return

    key = (
        identity_key.strip()
        if (identity_key and identity_key.strip())
        else display_name
    )

    max_wind = stats.setdefault("max_wind_typhoons", {})
    min_pressure = stats.setdefault("min_pressure_typhoons", {})
    max_levels = stats.setdefault("by_max_level", {})
    max_level_map = stats.setdefault("_typhoon_max_level_map", {})

    wind_value = _positive_number(wind_speed)
    pressure_value = _positive_number(pressure)
    current_entry = max_wind.get(key)

    if wind_value is not None:
        current_max = _wind_value(current_entry)
        if wind_value > current_max:
            retained_pressure = pressure_value
            existing_pressure = _pressure_value(current_entry)
            if existing_pressure is not None and (
                retained_pressure is None or existing_pressure < retained_pressure
            ):
                retained_pressure = existing_pressure
            max_wind[key] = {
                "wind_speed": wind_value,
                "pressure": retained_pressure,
                "display_name": display_name,
            }
        elif isinstance(current_entry, dict) and pressure_value is not None:
            existing_pressure = _pressure_value(current_entry)
            if existing_pressure is None or pressure_value < existing_pressure:
                updated_entry = dict(current_entry)
                updated_entry["pressure"] = pressure_value
                max_wind[key] = updated_entry

    if pressure_value is not None:
        current_min_entry = min_pressure.get(key)
        if isinstance(current_min_entry, dict):
            current_min = _positive_number(current_min_entry.get("pressure"))
        else:
            current_min = _positive_number(current_min_entry)
        if current_min is None or pressure_value < current_min:
            min_pressure[key] = {
                "pressure": pressure_value,
                "display_name": display_name,
            }

        current_entry = max_wind.get(key)
        if isinstance(current_entry, dict):
            existing_pressure = _pressure_value(current_entry)
            if existing_pressure is None or pressure_value < existing_pressure:
                updated_entry = dict(current_entry)
                updated_entry["pressure"] = pressure_value
                max_wind[key] = updated_entry

    previous_level = str(max_level_map.get(key) or "").strip()
    if level_weight(level_text) > level_weight(previous_level):
        if previous_level:
            old_count = int(max_levels.get(previous_level, 0) or 0)
            if old_count > 0:
                max_levels[previous_level] = old_count - 1
        max_levels[level_text] += 1
        max_level_map[key] = level_text


__all__ = ["record_typhoon_observation"]
