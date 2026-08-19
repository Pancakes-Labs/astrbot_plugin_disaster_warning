"""
台风展示上下文构建器。
负责把统一投影输入整理为台风展示上下文，供文本展示与地图渲染复用。
"""

from __future__ import annotations

from ....domain.display_models import TyphoonDisplayModel
from ....domain.event_context import TyphoonDisplayContext
from .common import build_projection_view, coerce_dict, first_non_empty


def _extract_typhoon_projection_details(metadata, source_payload, domain_event=None):
    """提取台风展示所需的投影细节字段。

    优先使用 TyphoonEvent 领域状态；投影视图仅兼容历史事件中残留的
    metadata / attributes 规范化字段副本。
    """
    projection_view = build_projection_view(
        payload_attributes=coerce_dict(getattr(source_payload, "attributes", None)),
        metadata=metadata,
    )
    return {
        "typhoon_id": str(
            first_non_empty(
                getattr(domain_event, "typhoon_id", None),
                projection_view.get("typhoon_id"),
                "",
            )
            or ""
        ).strip(),
        "name": str(
            first_non_empty(
                getattr(domain_event, "name", None),
                projection_view.get("name"),
                "",
            )
            or ""
        ).strip(),
        "name_en": str(
            first_non_empty(
                getattr(domain_event, "name_en", None),
                projection_view.get("name_en"),
                "",
            )
            or ""
        ).strip(),
        "typhoon_type": str(
            first_non_empty(
                getattr(domain_event, "typhoon_type", None),
                projection_view.get("typhoon_type"),
                "",
            )
            or ""
        ).strip(),
        "is_active": bool(
            first_non_empty(
                getattr(domain_event, "is_active", None)
                if domain_event is not None and hasattr(domain_event, "is_active")
                else None,
                projection_view.get("is_active"),
                True,
            )
        ),
        "latitude": first_non_empty(
            getattr(domain_event, "latitude", None)
            if domain_event is not None and hasattr(domain_event, "latitude")
            else None,
            projection_view.get("latitude"),
        ),
        "longitude": first_non_empty(
            getattr(domain_event, "longitude", None)
            if domain_event is not None and hasattr(domain_event, "longitude")
            else None,
            projection_view.get("longitude"),
        ),
        "pressure": first_non_empty(
            getattr(domain_event, "pressure", None)
            if domain_event is not None and hasattr(domain_event, "pressure")
            else None,
            projection_view.get("pressure"),
        ),
        "wind_speed": first_non_empty(
            getattr(domain_event, "wind_speed", None)
            if domain_event is not None and hasattr(domain_event, "wind_speed")
            else None,
            projection_view.get("wind_speed"),
        ),
        "power": first_non_empty(
            getattr(domain_event, "power", None)
            if domain_event is not None and hasattr(domain_event, "power")
            else None,
            projection_view.get("power"),
        ),
        "move_direction": str(
            first_non_empty(
                getattr(domain_event, "move_direction", None),
                projection_view.get("move_direction"),
                "",
            )
        ).strip(),
        "move_speed": first_non_empty(
            getattr(domain_event, "move_speed", None)
            if domain_event is not None and hasattr(domain_event, "move_speed")
            else None,
            projection_view.get("move_speed"),
        ),
        "radius7": first_non_empty(
            getattr(domain_event, "radius7", None)
            if domain_event is not None and hasattr(domain_event, "radius7")
            else None,
            projection_view.get("radius7"),
        ),
        "radius10": first_non_empty(
            getattr(domain_event, "radius10", None)
            if domain_event is not None and hasattr(domain_event, "radius10")
            else None,
            projection_view.get("radius10"),
        ),
        "wind_circle": dict(
            getattr(domain_event, "wind_circle", None)
            or projection_view.get("wind_circle")
            or {}
        ),
        "history_track": list(
            getattr(domain_event, "history_track", None)
            or projection_view.get("history_track")
            or []
        ),
        "future_track": list(
            getattr(domain_event, "future_track", None)
            or projection_view.get("future_track")
            or []
        ),
        "data_source": str(
            first_non_empty(
                metadata.get("data_source") if isinstance(metadata, dict) else None,
                projection_view.get("data_source"),
                "fan_studio",
            )
        ).strip(),
        "updated_at": first_non_empty(
            getattr(domain_event, "updated_at", None),
            projection_view.get("updated_at"),
        ),
    }


def build_typhoon_display_context(projection: dict, options: dict | None = None):
    """构建台风展示上下文主入口。"""
    envelope = projection["envelope"]
    resolved_source_id = projection["resolved_source_id"]
    source_descriptor = projection["source_descriptor"]
    source_payload = projection["source_payload"]
    metadata = projection["metadata"]
    title = projection["title"]
    domain_event = envelope.event
    display_options = dict(options or {})

    # 与地震 local_monitoring.enabled 类似：仅在展示开关打开时透出本地预估。
    typhoon_config = display_options.get("typhoon_config", {})
    if not isinstance(typhoon_config, dict):
        typhoon_config = {}
    show_local_estimation = bool(typhoon_config.get("show_local_estimation", False))

    # 会话级 typhoon 开关：关闭时展示层回退 Fan 字段，不暴露 EQSC 轨迹/四象限风圈。
    data_sources = display_options.get("data_sources", {})
    if not isinstance(data_sources, dict):
        data_sources = {}
    eqsc_cfg = data_sources.get("eqsc", {})
    if not isinstance(eqsc_cfg, dict):
        eqsc_cfg = {}
    if "typhoon" in eqsc_cfg:
        allow_eqsc_enrichment = bool(eqsc_cfg.get("typhoon"))
    else:
        # 兼容旧配置：缺省时跟随通道 enabled
        allow_eqsc_enrichment = bool(eqsc_cfg.get("enabled", True))

    # 领域事件优先提供规范化台风状态，投影视图只承担旧数据回退。
    payload_details = _extract_typhoon_projection_details(
        metadata,
        source_payload,
        domain_event=domain_event,
    )
    display_metadata = {
        **metadata,
        "event_id": envelope.id,
        "source_id": resolved_source_id,
        "event_type": "typhoon",
    }
    # 过滤链路可能已写入 typhoon_local_estimation；展示层按开关决定是否保留。
    if show_local_estimation:
        estimation = first_non_empty(
            metadata.get("typhoon_local_estimation"),
            display_options.get("typhoon_local_estimation"),
        )
        if isinstance(estimation, dict) and estimation:
            display_metadata["typhoon_local_estimation"] = dict(estimation)
        else:
            display_metadata.pop("typhoon_local_estimation", None)
    else:
        display_metadata.pop("typhoon_local_estimation", None)

    wind_circle = dict(payload_details["wind_circle"] or {})
    history_track = list(payload_details["history_track"] or [])
    future_track = list(payload_details["future_track"] or [])
    data_source = str(payload_details["data_source"] or "fan_studio").strip()
    if not allow_eqsc_enrichment:
        # 会话关闭台风富化：仅保留 Fan 单值风圈语义，去掉 EQSC 富化痕迹。
        wind_circle = {}
        history_track = []
        future_track = []
        data_source = "fan_studio"
        display_metadata["data_source"] = "fan_studio"
        display_metadata["info_type"] = "fan"
        display_metadata["typhoon_data_mode"] = "fan"

    return TyphoonDisplayContext(
        event_id=envelope.id,
        source_id=resolved_source_id,
        title=title,
        typhoon_id=(
            getattr(domain_event, "typhoon_id", None)
            or payload_details["typhoon_id"]
            or ""
        ),
        name=(getattr(domain_event, "name", None) or payload_details["name"] or ""),
        name_en=(
            getattr(domain_event, "name_en", None) or payload_details["name_en"] or ""
        ),
        typhoon_type=(
            getattr(domain_event, "typhoon_type", None)
            or payload_details["typhoon_type"]
            or ""
        ),
        is_active=bool(
            getattr(domain_event, "is_active", True)
            if hasattr(domain_event, "is_active")
            else payload_details["is_active"]
        ),
        latitude=(
            getattr(domain_event, "latitude", None)
            if hasattr(domain_event, "latitude")
            else payload_details["latitude"]
        ),
        longitude=(
            getattr(domain_event, "longitude", None)
            if hasattr(domain_event, "longitude")
            else payload_details["longitude"]
        ),
        pressure=(
            getattr(domain_event, "pressure", None)
            if hasattr(domain_event, "pressure")
            else payload_details["pressure"]
        ),
        wind_speed=(
            getattr(domain_event, "wind_speed", None)
            if hasattr(domain_event, "wind_speed")
            else payload_details["wind_speed"]
        ),
        power=(
            getattr(domain_event, "power", None)
            if hasattr(domain_event, "power")
            else payload_details["power"]
        ),
        move_direction=(
            getattr(domain_event, "move_direction", None)
            or payload_details["move_direction"]
            or ""
        ),
        move_speed=(
            getattr(domain_event, "move_speed", None)
            if hasattr(domain_event, "move_speed")
            else payload_details["move_speed"]
        ),
        radius7=(
            getattr(domain_event, "radius7", None)
            if hasattr(domain_event, "radius7")
            else payload_details["radius7"]
        ),
        radius10=(
            getattr(domain_event, "radius10", None)
            if hasattr(domain_event, "radius10")
            else payload_details["radius10"]
        ),
        wind_circle=wind_circle,
        history_track=history_track,
        future_track=future_track,
        data_source=data_source,
        updated_at=(
            getattr(domain_event, "updated_at", None) or payload_details["updated_at"]
        ),
        metadata=display_metadata,
        options=display_options,
        display_model=TyphoonDisplayModel(
            title=title,
            extras=dict(display_metadata),
        ),
        source_descriptor=source_descriptor,
        payload=source_payload,
    )


__all__ = ["build_typhoon_display_context"]
