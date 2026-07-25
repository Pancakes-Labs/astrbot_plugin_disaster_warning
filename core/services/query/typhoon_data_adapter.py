"""台风查询数据适配。

负责把 EQSC raw / 本地数据库记录标准化为统一查询结果项，
并生成轨迹摘要与本地 weather_detail 回填字段。
"""

from __future__ import annotations

import re
from datetime import timezone
from typing import Any

from ....utils.time_converter import TimeConverter
from ...domain.typhoon import (
    clean_text,
    extract_max_radius,
    format_display_name,
    resolve_data_mode,
    to_eqsc_id,
    to_fan_id,
    to_float,
    to_int,
)
from ...domain.typhoon.typhoon_display_format import format_coordinates
from .typhoon_query_models import TyphoonQueryItem
from .typhoon_query_parser import DETAIL_FULL


def format_cn_time(value: Any, with_seconds: bool = False) -> str:
    """将任意时间值格式化为北京时间中文展示。

    无时区信息时按 UTC+8 解释，与 EQSC/Fan 业务时区习惯一致。
    """
    parsed = TimeConverter.parse_datetime(value)
    if parsed is None:
        text = clean_text(value)
        return text or "未知时间"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))
    cn_dt = parsed.astimezone(TimeConverter._get_timezone("UTC+8"))
    fmt = (
        "%Y年%m月%d日 %H时%M分%S秒 (UTC+8)" if with_seconds else "%Y年%m月%d日 %H时%M分"
    )
    return TimeConverter._safe_strftime(cn_dt, fmt)


def _history_node_time_key(node: dict[str, Any]) -> float | None:
    """历史节点排序键：按观测时间升序。

    Returns:
        有效时间戳；无法解析时返回 None（调用方应沉底并避免当作最新观测）。
    """
    parsed = TimeConverter.parse_datetime(node.get("time"))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        # 与 EQSC/Fan 业务习惯一致：无时区按北京时间解释。
        parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))
    return parsed.timestamp()


def sort_history_track(history_track: list[Any] | None) -> list[dict[str, Any]]:
    """将 EQSC historyTrack 规范为按观测时间升序的字典节点列表。

    EQSC 返回顺序不保证，不能直接用数组末项当作最新报。
    无效时间节点沉底，避免排在正常时间之前。
    """
    if not isinstance(history_track, list):
        return []
    nodes = [node for node in history_track if isinstance(node, dict)]
    if not nodes:
        return []
    # 有效时间在前按时间升序；无效时间沉底并保持相对稳定。
    return sorted(
        nodes,
        key=lambda node: (
            1 if _history_node_time_key(node) is None else 0,
            _history_node_time_key(node) or 0.0,
        ),
    )


def latest_history_node(history_track: list[Any]) -> dict[str, Any] | None:
    """取历史轨迹中时间最新的有效观测节点。

    跳过无法解析时间的节点，避免把无效时间误判为最新观测。
    """
    nodes = sort_history_track(history_track)
    for node in reversed(nodes):
        if _history_node_time_key(node) is not None:
            return node
    return None


def build_track_summary(
    history_track: list[dict[str, Any]] | None,
    future_track: list[dict[str, Any]] | None,
    *,
    max_history: int | None = None,
    max_future: int | None = None,
) -> dict[str, Any]:
    """构建可读的路径摘要。

    完整路径查询默认不做节点截断（max_history / max_future 为 None）。
    若调用方显式传入正整数，则分别截取历史末段与预报前段。
    """
    # 摘要展示同样按时间排序，避免完整路径列表时间乱序。
    history = sort_history_track(history_track)
    future = [node for node in (future_track or []) if isinstance(node, dict)]

    def _node_line(node: dict[str, Any], *, prefix: str) -> str:
        time_text = format_cn_time(node.get("time"))
        level = clean_text(
            node.get("typeNameCN") or node.get("type") or node.get("level")
        )
        lat = to_float(node.get("latitude"))
        lon = to_float(node.get("longitude"))
        coords = format_coordinates(lat, lon)
        wind = to_float(node.get("windSpeed") or node.get("wind_speed"))
        pressure = to_float(node.get("pressure"))
        parts = [f"{prefix}{time_text}"]
        if level:
            parts.append(level)
        if coords:
            parts.append(coords)
        if wind is not None and wind > 0:
            parts.append(f"{wind:g} m/s")
        if pressure is not None and pressure > 0:
            parts.append(f"{pressure:g} hPa")
        return " · ".join(parts)

    if isinstance(max_history, int) and max_history > 0:
        history_nodes = history[-max_history:]
    else:
        history_nodes = history
    if isinstance(max_future, int) and max_future > 0:
        future_nodes = future[:max_future]
    else:
        future_nodes = future

    history_lines = [_node_line(node, prefix="") for node in history_nodes]
    future_lines = [_node_line(node, prefix="预计 ") for node in future_nodes]
    return {
        "history_count": len(history),
        "future_count": len(future),
        "history_lines": history_lines,
        "future_lines": future_lines,
    }


def parse_local_detail_fields(weather_detail: str) -> dict[str, Any]:
    """从本地 weather_detail 文本中尽力解析气压/移向/风圈等字段。

    本地库未单独落库这些字段时，依赖入库摘要字符串回填展示。
    """
    text = clean_text(weather_detail)
    result: dict[str, Any] = {}
    if not text:
        return result

    pressure_match = re.search(r"(?:气压|最低气压)\s*([0-9]+(?:\.[0-9]+)?)\s*hPa", text)
    if pressure_match:
        result["pressure"] = to_int(pressure_match.group(1))

    move_dir_match = re.search(r"移向\s*([^\s，,]+)", text)
    if move_dir_match:
        result["move_direction"] = move_dir_match.group(1)

    move_speed_match = re.search(r"移速\s*([0-9]+(?:\.[0-9]+)?)\s*km/h", text, re.I)
    if move_speed_match:
        result["move_speed"] = to_float(move_speed_match.group(1))

    power_match = re.search(r"风力\s*([0-9]+)\s*级", text)
    if power_match:
        result["power"] = to_int(power_match.group(1))

    radius7_match = re.search(r"(?:七级|7级)风圈\s*([0-9]+(?:\.[0-9]+)?)\s*km", text)
    if radius7_match:
        result["radius7"] = to_int(radius7_match.group(1))

    radius10_match = re.search(r"(?:十级|10级)风圈\s*([0-9]+(?:\.[0-9]+)?)\s*km", text)
    if radius10_match:
        result["radius10"] = to_int(radius10_match.group(1))

    return result


def normalize_eqsc_typhoon(
    raw: dict[str, Any],
    *,
    detail: str,
    data_source: str = "eqsc",
) -> TyphoonQueryItem | None:
    """把 EQSC 原始台风对象标准化为查询结果项。"""
    if not isinstance(raw, dict):
        return None

    eqsc_id = clean_text(raw.get("id"))
    if not eqsc_id:
        return None

    fan_id = to_fan_id(eqsc_id)
    name_cn = clean_text(raw.get("nameCN") or raw.get("name"))
    name_en = clean_text(raw.get("nameEN") or raw.get("name_en"))
    # EQSC 历史轨迹顺序不保证：先按时间升序，再取最新观测。
    history_track = sort_history_track(
        raw.get("historyTrack") or raw.get("history_track") or []
    )
    future_track = raw.get("futureTrack") or raw.get("future_track") or []
    if not isinstance(future_track, list):
        future_track = []
    else:
        future_track = [node for node in future_track if isinstance(node, dict)]

    latest = latest_history_node(history_track) or {}
    wind_circle = latest.get("windCircle") or latest.get("wind_circle") or {}
    if not isinstance(wind_circle, dict):
        wind_circle = {}

    latitude = to_float(latest.get("latitude"))
    longitude = to_float(latest.get("longitude"))
    pressure = to_int(latest.get("pressure"))
    wind_speed = to_float(latest.get("windSpeed") or latest.get("wind_speed"))
    move_direction = clean_text(
        latest.get("directionCN")
        or latest.get("direction")
        or latest.get("moveDirection")
    )
    move_speed = to_float(latest.get("speed") or latest.get("moveSpeed"))
    typhoon_type = clean_text(
        latest.get("typeNameCN") or latest.get("type") or raw.get("type")
    )
    radius7_raw = extract_max_radius(wind_circle, "30KTS")
    radius10_raw = extract_max_radius(wind_circle, "50KTS")
    radius7 = int(radius7_raw) if radius7_raw is not None else None
    radius10 = int(radius10_raw) if radius10_raw is not None else None
    updated_at = clean_text(
        latest.get("time") or raw.get("updateTime") or raw.get("updated_at")
    )
    is_active = raw.get("isActive")
    if is_active is None:
        is_active = True

    item: TyphoonQueryItem = {
        "typhoon_id": fan_id,
        "eqsc_id": eqsc_id if len(eqsc_id) == 4 else to_eqsc_id(fan_id),
        "name": name_cn,
        "name_en": name_en,
        "display_name": format_display_name(name_cn, name_en, fan_id or eqsc_id),
        "typhoon_type": typhoon_type,
        "is_active": bool(is_active),
        "latitude": latitude,
        "longitude": longitude,
        "pressure": pressure,
        "wind_speed": wind_speed,
        "power": None,
        "move_direction": move_direction,
        "move_speed": move_speed,
        "radius7": radius7,
        "radius10": radius10,
        "wind_circle": wind_circle or {},
        "updated_at": updated_at,
        "updated_at_text": format_cn_time(updated_at, with_seconds=True),
        "info_type": "eqsc",
        "data_source": data_source,
        "source_label": "EQSC",
        "weather_detail": "",
        "level_key": typhoon_type or "",
    }

    if detail == DETAIL_FULL:
        item["history_track"] = history_track
        item["future_track"] = future_track
        item["track_summary"] = build_track_summary(history_track, future_track)
    else:
        item["history_track"] = []
        item["future_track"] = []
        item["track_summary"] = {
            "history_count": len(history_track),
            "future_count": len(future_track),
            "history_lines": [],
            "future_lines": [],
        }
    return item


def normalize_local_typhoon(
    raw: dict[str, Any],
    *,
    detail: str,
) -> TyphoonQueryItem | None:
    """把本地数据库台风记录标准化为查询结果项。"""
    if not isinstance(raw, dict):
        return None

    real_event_id = clean_text(raw.get("real_event_id") or raw.get("unique_id"))
    if not real_event_id:
        return None

    subtitle = clean_text(raw.get("subtitle"))
    place_name = clean_text(raw.get("place_name"))
    description = clean_text(raw.get("description"))
    display_name = subtitle or place_name or description or real_event_id

    name_cn = display_name
    name_en = ""
    match = re.match(r"^(.*?)[（(]([A-Za-z0-9\-\s]+)[）)]\s*$", display_name)
    if match:
        name_cn = match.group(1).strip() or display_name
        name_en = match.group(2).strip()

    detail_fields = parse_local_detail_fields(str(raw.get("weather_detail") or ""))
    info_type = clean_text(raw.get("info_type")) or "fan"
    mode = resolve_data_mode(info_type, default="fan")
    source_label_map = {
        "fan": "本地数据库 (Fan)",
        "enriched": "本地数据库 (Fan+EQSC)",
        "eqsc_rebuild": "本地数据库 (EQSC历史)",
    }
    typhoon_type = clean_text(raw.get("level"))

    item: TyphoonQueryItem = {
        "typhoon_id": to_fan_id(real_event_id),
        "eqsc_id": to_eqsc_id(real_event_id),
        "name": name_cn,
        "name_en": name_en,
        "display_name": display_name,
        "typhoon_type": typhoon_type,
        "is_active": mode != "eqsc_rebuild",
        "latitude": to_float(raw.get("latitude")),
        "longitude": to_float(raw.get("longitude")),
        "pressure": detail_fields.get("pressure"),
        "wind_speed": to_float(raw.get("wind_speed")),
        "power": detail_fields.get("power"),
        "move_direction": detail_fields.get("move_direction") or "",
        "move_speed": detail_fields.get("move_speed"),
        "radius7": detail_fields.get("radius7"),
        "radius10": detail_fields.get("radius10"),
        "wind_circle": {},
        "updated_at": clean_text(raw.get("time") or raw.get("updated_at")),
        "updated_at_text": format_cn_time(
            raw.get("time") or raw.get("updated_at"), with_seconds=True
        ),
        "info_type": info_type,
        "data_source": "local",
        "source_label": source_label_map.get(mode, "本地数据库"),
        "weather_detail": clean_text(raw.get("weather_detail")),
        "history_track": [],
        "future_track": [],
        "track_summary": {
            "history_count": 0,
            "future_count": 0,
            "history_lines": [],
            "future_lines": [],
        },
        "level_key": typhoon_type or "",
    }

    if detail == DETAIL_FULL and item["weather_detail"]:
        item["track_summary"]["history_lines"] = [f"本地摘要：{item['weather_detail']}"]

    if mode == "eqsc_rebuild":
        item["is_active"] = False
    return item


def sort_items_stable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """活跃优先，其次按更新时间倒序（时间戳解析失败时视为最旧）。"""

    def parse_ts(value: Any) -> float:
        parsed = TimeConverter.parse_datetime(value)
        if parsed is None:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    return sorted(
        items,
        key=lambda item: (
            0 if item.get("is_active") else 1,
            -parse_ts(item.get("updated_at")),
        ),
    )


def filter_items(
    items: list[dict[str, Any]],
    *,
    keyword: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    """按关键词与活跃状态过滤结果集。"""
    filtered: list[dict[str, Any]] = []
    keyword_text = clean_text(keyword).lower()
    for item in items:
        if active_only and not item.get("is_active"):
            continue
        if keyword_text:
            haystack = " ".join(
                [
                    str(item.get("display_name") or ""),
                    str(item.get("name") or ""),
                    str(item.get("name_en") or ""),
                    str(item.get("typhoon_id") or ""),
                    str(item.get("eqsc_id") or ""),
                    str(item.get("typhoon_type") or ""),
                ]
            ).lower()
            if keyword_text not in haystack:
                continue
        filtered.append(item)
    return filtered


__all__ = [
    "build_track_summary",
    "filter_items",
    "format_cn_time",
    "latest_history_node",
    "normalize_eqsc_typhoon",
    "normalize_local_typhoon",
    "parse_local_detail_fields",
    "sort_history_track",
    "sort_items_stable",
]
