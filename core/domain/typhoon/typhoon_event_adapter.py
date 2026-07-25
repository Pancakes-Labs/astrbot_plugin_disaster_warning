"""EQSC 原始台风数据到领域事件的适配器。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ....utils.time_converter import TimeConverter
from ..event_identity import EventIdentity
from ..event_models import EventEnvelope, TyphoonEvent
from ..event_payload import SourcePayload
from .typhoon_ids import to_fan_id
from .typhoon_levels import level_weight
from .typhoon_values import clean_text, is_nullish, to_float
from .typhoon_winds import extract_max_radius


def _normalize_time(value: Any) -> tuple[str | None, datetime | None]:
    """把 EQSC 时间转换为带时区的 datetime 与 ISO 文本。"""
    # EQSC 的无时区时间按北京时间解释，避免历史记录被错误当作 UTC。
    if is_nullish(value):
        return None, None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = TimeConverter.parse_datetime(str(value).strip())
    if parsed is None:
        return None, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.isoformat(timespec="seconds"), parsed


def _valid_history_nodes(history_track: Any) -> list[dict[str, Any]]:
    """筛选可参与峰值计算的历史观测节点。"""
    # 无时间且无坐标/风速的节点无法支撑时间线或强度统计，因此直接忽略。
    if not isinstance(history_track, list):
        return []
    nodes: list[dict[str, Any]] = []
    for node in history_track:
        if not isinstance(node, dict):
            continue
        time_text, _ = _normalize_time(node.get("time"))
        latitude = to_float(node.get("latitude"))
        longitude = to_float(node.get("longitude"))
        wind_speed = to_float(node.get("windSpeed"))
        if wind_speed is not None and wind_speed <= 0:
            wind_speed = None
        if not time_text or (
            (latitude is None or longitude is None) and wind_speed is None
        ):
            continue
        nodes.append(node)
    return nodes


def _select_peak_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择最高等级节点；同等级选择风速更大的节点。"""
    # 峰值节点同时提供主等级、位置、移动信息和风圈的优先来源。
    peak_node: dict[str, Any] | None = None
    peak_weight = -1
    peak_wind: float | None = None
    for node in nodes:
        level = clean_text(node.get("typeNameCN"))
        weight = level_weight(level)
        wind_speed = to_float(node.get("windSpeed"))
        if wind_speed is not None and wind_speed <= 0:
            wind_speed = None
        if weight > peak_weight or (
            weight == peak_weight
            and wind_speed is not None
            and (peak_wind is None or wind_speed > peak_wind)
        ):
            peak_node = node
            peak_weight = weight
            peak_wind = wind_speed
    return peak_node or (nodes[-1] if nodes else None)


def _extract_peak_values(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """从 EQSC 历史节点提取 TyphoonEvent 初始峰值指标。

    职责边界：此处只负责从 EQSC raw 中选出最佳历史观测值来构建
    TyphoonEvent 的初始状态；后续主表更新时的峰值合并由
    domain.merge_peak_metrics 统一处理，两者不重复。
    """
    # 等级/风速/气压分别采用各自领域序，不把一次观测误当成完整历史峰值。
    peak_node = _select_peak_node(nodes)
    peak_level = clean_text((peak_node or {}).get("typeNameCN"))
    peak_wind: float | None = None
    min_pressure: float | None = None
    for node in nodes:
        wind_speed = to_float(node.get("windSpeed"))
        if wind_speed is not None and wind_speed > 0:
            peak_wind = max(peak_wind or wind_speed, wind_speed)
        pressure = to_float(node.get("pressure"))
        if pressure is not None and pressure > 0:
            min_pressure = min(min_pressure or pressure, pressure)
    peak_time_text, peak_time = _normalize_time((peak_node or {}).get("time"))
    return {
        "peak_node": peak_node,
        "peak_level": peak_level,
        "peak_wind": peak_wind,
        "min_pressure": min_pressure,
        "peak_time_text": peak_time_text,
        "peak_time": peak_time,
    }


def build_typhoon_event_envelope(
    raw: dict[str, Any],
    *,
    source_id: str = "typhoon_fanstudio",
    data_mode: str = "eqsc_rebuild",
) -> EventEnvelope | None:
    """将单个 EQSC 台风对象转换为领域事件。

    Args:
        raw: EQSC 台风原始字典。
        source_id: 统一数据源标识。
        data_mode:
            - eqsc_rebuild：冷启动历史重建，主字段取峰值节点；
            - eqsc：实时轮询，主字段取最新观测节点。
    """
    if not isinstance(raw, dict):
        return None
    eqsc_id = clean_text(raw.get("id"))
    if not eqsc_id:
        return None

    mode = str(data_mode or "eqsc_rebuild").strip().lower()
    is_live = mode in {"eqsc", "eqsc_live", "eqsc_poll"}

    # 对外领域身份继续使用 6 位编号，便于跨源去重与查询。
    fan_id = to_fan_id(eqsc_id)
    name_cn = clean_text(raw.get("nameCN") or raw.get("name"))
    name_en = clean_text(raw.get("nameEN") or raw.get("name_en"))
    history_track = raw.get("historyTrack") or raw.get("history_track") or []
    future_track = raw.get("futureTrack") or raw.get("future_track") or []
    if not isinstance(history_track, list):
        history_track = []
    if not isinstance(future_track, list):
        future_track = []

    # 必须有可排序的有效观测，否则不能可靠写入时间线。
    nodes = _valid_history_nodes(history_track)
    if not nodes:
        return None

    # EQSC historyTrack 的返回顺序不作保证，不能直接用原数组最后一项作为最新报。
    # 统一按观测时间升序排列，确保主记录时间对应最后一报，而非首报或峰值报。
    def node_time(node: dict[str, Any]) -> float:
        _, parsed = _normalize_time(node.get("time"))
        return parsed.timestamp() if parsed is not None else 0.0

    nodes = sorted(nodes, key=node_time)
    latest_node = nodes[-1]
    peak_values = _extract_peak_values(nodes)
    peak_node = peak_values["peak_node"] or latest_node
    peak_time = peak_values["peak_time"]
    _, latest_time = _normalize_time(latest_node.get("time"))
    if latest_time is None:
        latest_time = peak_time
    if peak_time is None:
        peak_time = latest_time
    if latest_time is None:
        return None

    # 实时轮询：主字段取最新观测；历史重建：主字段取峰值快照。
    primary_node = latest_node if is_live else peak_node
    wind_circle = (
        primary_node.get("windCircle") or primary_node.get("wind_circle") or {}
    )
    if not isinstance(wind_circle, dict):
        wind_circle = {}

    if is_live:
        level = clean_text(latest_node.get("typeNameCN")) or peak_values["peak_level"]
        latitude = to_float(latest_node.get("latitude"))
        longitude = to_float(latest_node.get("longitude"))
        pressure_value = to_float(latest_node.get("pressure"))
        # 与历史峰值路径一致：气压/风速非正值视为缺失，避免写入 0 或负值。
        if pressure_value is not None and pressure_value <= 0:
            pressure_value = None
        wind_speed_value = to_float(latest_node.get("windSpeed"))
        if wind_speed_value is not None and wind_speed_value <= 0:
            wind_speed_value = None
        move_direction = clean_text(latest_node.get("directionCN"))
        move_speed = to_float(latest_node.get("speed"))
        message_type = "typhoon"
        mode_label = "eqsc"
    else:
        level = peak_values["peak_level"] or clean_text(latest_node.get("typeNameCN"))
        latitude = to_float(peak_node.get("latitude"))
        longitude = to_float(peak_node.get("longitude"))
        if latitude is None or longitude is None:
            latitude = to_float(latest_node.get("latitude"))
            longitude = to_float(latest_node.get("longitude"))
        pressure_value = peak_values["min_pressure"]
        wind_speed_value = peak_values["peak_wind"]
        move_direction = clean_text(peak_node.get("directionCN"))
        move_speed = to_float(peak_node.get("speed"))
        message_type = "typhoon_history"
        mode_label = "eqsc_rebuild"

    # EventEnvelope.metadata 只记录流水线形态，原始 EQSC 字典留在 SourcePayload。
    identity = EventIdentity(
        event_id=fan_id,
        source_id=source_id,
        event_type="typhoon",
        provider_family="eqsc",
    )
    domain_event = TyphoonEvent(
        typhoon_id=fan_id,
        name=name_cn,
        name_en=name_en,
        typhoon_type=level,
        latitude=latitude,
        longitude=longitude,
        pressure=(
            int(pressure_value)
            if pressure_value is not None and float(pressure_value).is_integer()
            else pressure_value
        ),
        wind_speed=wind_speed_value,
        move_direction=move_direction,
        move_speed=move_speed,
        radius7=(
            int(radius7)
            if (radius7 := extract_max_radius(wind_circle, "30KTS")) is not None
            and float(radius7).is_integer()
            else radius7
        ),
        radius10=(
            int(radius10)
            if (radius10 := extract_max_radius(wind_circle, "50KTS")) is not None
            and float(radius10).is_integer()
            else radius10
        ),
        # 历史列表缺少 isActive 时按非活跃处理，避免冷启动历史误进入活跃过滤。
        # 实时轮询侧会再按 raw.isActive 覆盖。
        is_active=bool(raw.get("isActive", False if not is_live else True)),
        # updated_at 表示最新观测时间。
        updated_at=latest_time,
        history_track=history_track,
        future_track=future_track,
        wind_circle=wind_circle,
        metadata={},
    )
    return EventEnvelope(
        identity=identity,
        event=domain_event,
        payload=SourcePayload(
            source_id=source_id,
            provider_family="eqsc",
            message_type=message_type,
            raw=dict(raw),
        ),
        metadata={
            "data_source": mode_label,
            "typhoon_data_mode": mode_label,
            "info_type": mode_label,
        },
    )


__all__ = ["build_typhoon_event_envelope"]
