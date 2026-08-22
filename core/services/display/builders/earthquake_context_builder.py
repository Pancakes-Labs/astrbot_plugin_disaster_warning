"""
地震展示上下文构建器。
负责把统一投影输入整理为地震展示上下文，供文本展示、卡片展示与管理端视图复用。
"""

from __future__ import annotations

from typing import Any

from ....domain.display_models import EarthquakeDisplayModel
from ....domain.event_context import EarthquakeDisplayContext
from .common import (
    build_projection_view,
    coerce_dict,
    first_non_empty,
    normalize_display_text,
)


def _extract_earthquake_domain_details(
    domain_event, source_payload, metadata, envelope
):
    """提取地震领域对象侧的展示细节。"""
    # 强制合并元数据与原始载荷属性以获取全量事件字典
    domain_metadata = coerce_dict(getattr(domain_event, "metadata", None))
    payload_attributes = coerce_dict(getattr(source_payload, "attributes", None))
    # 合并领域层和接入载荷层的属性，建立完整视图，提供多层缺省兜底能力
    projection_view = build_projection_view(
        domain_metadata=domain_metadata,
        payload_attributes=payload_attributes,
        metadata=metadata,
    )
    identity = getattr(envelope, "identity", None)
    # 取首个非空的地震报数/报次字段（兼容多路提供者的不同命名，如 updates / report_num 等）
    report_num = first_non_empty(
        getattr(identity, "report_num", None),
        envelope.report_num,
        projection_view.get("report_num"),
        projection_view.get("updates"),
    )
    # 解析是否为最终报（取消报或最终地震情报通常不再会有后续更新）
    is_final = bool(
        getattr(identity, "is_final", False) or projection_view.get("is_final", False)
    )
    return {
        "domain_metadata": domain_metadata,
        "projection_view": projection_view,
        # 发生省份/县市地名获取
        "province": str(
            first_non_empty(
                getattr(domain_event, "province", None),
                projection_view.get("province"),
                "",
            )
        ).strip(),
        # 信息类型分类
        "info_type": normalize_display_text(projection_view.get("info_type")) or "",
        "report_num": report_num,
        # 修正的报数
        "revision": normalize_display_text(projection_view.get("revision")) or "",
        # 本地沿海海啸预警提示文本信息
        "domestic_tsunami": normalize_display_text(
            projection_view.get("domestic_tsunami")
        )
        or "",
        # 测站数据：Global Quake 等为 dict，S-Net 为 list[dict]
        "stations": first_non_empty(projection_view.get("stations"), {}),
        "is_final": is_final,
        # 是否为作废/取消警报
        "is_cancel": bool(projection_view.get("is_cancel", False)),
        # 是否为演练/测试消息
        "is_training": bool(projection_view.get("is_training", False)),
        # 是否为预设/假定灾害模拟消息
        "is_assumption": bool(projection_view.get("is_assumption", False)),
        # 最大地面加速度（PGA 值）
        "max_pga": first_non_empty(projection_view.get("max_pga")),
    }


def _extract_earthquake_projection_details(
    metadata, domain_province: str, domain_info_type: str
):
    """提取投影侧补充字段，如影响区域与日本震度信息。"""
    # 优先取显式配置的影响区域，无影响区域则回退到省级单位名称
    impact_area = normalize_display_text(metadata.get("impact_area") or domain_province)
    jma_issue_type = str(domain_info_type or metadata.get("info_type") or "").strip()
    jma_warn_area = normalize_display_text(metadata.get("jma_warn_area"))
    # 日本气象厅各测站震度解析，便于后续前台渲染
    jma_points = [
        point
        for point in list(metadata.get("jma_points") or [])
        if isinstance(point, dict)
    ]
    # 提取附加注释信息
    jma_comment = normalize_display_text(metadata.get("jma_comment"))
    jma_warning_areas = [
        str(area).strip()
        for area in list(metadata.get("jma_warning_areas") or [])
        if str(area).strip()
    ]
    jma_warning_area_ranges = [
        str(area_range).strip()
        for area_range in list(metadata.get("jma_warning_area_ranges") or [])
        if str(area_range).strip()
    ]
    jma_warning_area_groups = [
        dict(group)
        for group in list(metadata.get("jma_warning_area_groups") or [])
        if isinstance(group, dict) and str(group.get("range_text") or "").strip()
    ]

    return {
        "impact_area": impact_area,
        "jma_issue_type": jma_issue_type,
        "jma_warn_area": jma_warn_area,
        "jma_points": jma_points,
        "jma_comment": jma_comment,
        "jma_warning_areas": jma_warning_areas,
        "jma_warning_area_ranges": jma_warning_area_ranges,
        "jma_warning_area_groups": jma_warning_area_groups,
        # 预存的本地/网络 URI 地址
        "image_uri": str(metadata.get("image_uri") or "").strip(),
        "shakemap_uri": str(metadata.get("shakemap_uri") or "").strip(),
    }


def build_earthquake_display_context(projection: dict, options: dict | None = None):
    """构建地震展示上下文主入口。"""
    envelope = projection["envelope"]
    resolved_source_id = projection["resolved_source_id"]
    source_descriptor = projection["source_descriptor"]
    source_payload = projection["source_payload"]
    metadata = projection["metadata"]
    title = projection["title"]
    domain_event = envelope.event
    display_options = dict(options or {})
    # 校验本地监控功能配置是否已启用
    local_monitoring_config = display_options.get("local_monitoring", {})
    local_monitoring_enabled = bool(
        isinstance(local_monitoring_config, dict)
        and local_monitoring_config.get("enabled", False)
    )

    domain_details = _extract_earthquake_domain_details(
        domain_event,
        source_payload,
        metadata,
        envelope,
    )
    domain_province = str(domain_details["province"] or "")
    domain_info_type = str(domain_details["info_type"] or "")
    # 获取本地预计烈度/预估信息（仅在本地监控开关打开时才会尝试解析）
    local_estimation = (
        first_non_empty(metadata.get("local_estimation"))
        if local_monitoring_enabled
        else None
    )
    payload_details = _extract_earthquake_projection_details(
        metadata,
        domain_province,
        domain_info_type,
    )
    impact_area = payload_details["impact_area"]
    jma_issue_type = str(payload_details["jma_issue_type"] or "")
    jma_warn_area = payload_details["jma_warn_area"]
    jma_points = list(payload_details["jma_points"])
    jma_comment = payload_details["jma_comment"]
    # 汇总整理好的中间字段表，防止结构化复制时丢失信息
    raw_stations = domain_details["stations"]
    if isinstance(raw_stations, dict):
        normalized_stations: Any = dict(raw_stations)
    elif isinstance(raw_stations, list):
        # S-Net 等来源使用测站对象列表，不可强转 dict（会触发
        # "dictionary update sequence element #0 has length N"）。
        normalized_stations = [
            dict(item) if isinstance(item, dict) else item for item in raw_stations
        ]
    else:
        normalized_stations = {}

    earthquake_kwargs = {
        "report_num": int(domain_details["report_num"] or 1),
        "is_final": bool(domain_details["is_final"]),
        "is_cancel": bool(domain_details["is_cancel"]),
        "is_training": bool(domain_details["is_training"]),
        "is_assumption": bool(domain_details["is_assumption"]),
        "max_pga": domain_details["max_pga"],
        "stations": normalized_stations,
        "image_uri": str(payload_details["image_uri"] or ""),
        "shakemap_uri": str(payload_details["shakemap_uri"] or ""),
    }
    # 组合合并出前台展示及保存用事件元数据字典，方便序列化为 JSON 供前端实时刷新面板
    display_metadata = {
        **metadata,
        "event_id": envelope.id,
        "source_id": resolved_source_id,
        "event_type": "earthquake",
        "impact_area": impact_area,
        "jma_issue_type": jma_issue_type,
        "jma_warn_area": jma_warn_area,
        "jma_points": jma_points,
        "jma_comment": jma_comment,
        "jma_warning_areas": list(payload_details["jma_warning_areas"]),
        "jma_warning_area_ranges": list(payload_details["jma_warning_area_ranges"]),
        "jma_warning_area_groups": list(
            payload_details.get("jma_warning_area_groups") or []
        ),
    }
    if local_estimation is not None:
        # 本地烈度估算仅在存在时写入，避免污染不相关事件的展示元数据。
        display_metadata["local_estimation"] = local_estimation

    return EarthquakeDisplayContext(
        event_id=envelope.id,
        source_id=resolved_source_id,
        title=title,
        occurred_at=(
            getattr(domain_event, "occurred_at", None)
            or getattr(domain_event, "issued_at", None)
            or getattr(domain_event, "effective_at", None)
        ),
        latitude=getattr(domain_event, "latitude", None),
        longitude=getattr(domain_event, "longitude", None),
        magnitude=getattr(domain_event, "magnitude", None),
        depth=getattr(domain_event, "depth", None),
        intensity=getattr(domain_event, "intensity", None),
        scale=getattr(domain_event, "scale", None),
        report_num=earthquake_kwargs["report_num"],
        is_final=earthquake_kwargs["is_final"],
        is_cancel=earthquake_kwargs["is_cancel"],
        is_training=earthquake_kwargs["is_training"],
        is_assumption=earthquake_kwargs["is_assumption"],
        revision=str(domain_details["revision"] or "").strip(),
        province=domain_province,
        domestic_tsunami=str(domain_details["domestic_tsunami"] or "").strip(),
        max_pga=earthquake_kwargs["max_pga"],
        stations=earthquake_kwargs["stations"],
        image_uri=earthquake_kwargs["image_uri"],
        shakemap_uri=earthquake_kwargs["shakemap_uri"],
        impact_area=impact_area,
        local_estimation=local_estimation,
        jma_issue_type=jma_issue_type,
        jma_warn_area=jma_warn_area,
        jma_points=jma_points,
        jma_comment=jma_comment,
        jma_warning_areas=list(payload_details["jma_warning_areas"]),
        jma_warning_area_ranges=list(payload_details["jma_warning_area_ranges"]),
        jma_warning_area_groups=list(
            payload_details.get("jma_warning_area_groups") or []
        ),
        display_model=EarthquakeDisplayModel(
            title=title,
            extras=dict(display_metadata),
        ),
        metadata=display_metadata,
        options=display_options,
        source_descriptor=source_descriptor,
        payload=source_payload,
    )


__all__ = ["build_earthquake_display_context"]
