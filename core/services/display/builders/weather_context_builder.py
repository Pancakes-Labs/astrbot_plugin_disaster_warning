"""
气象展示上下文构建器。
负责把统一投影输入整理为气象展示上下文，供文本展示与管理端视图复用。
"""

from __future__ import annotations

from ....domain.display_models import WeatherDisplayModel
from ....domain.event_context import WeatherDisplayContext
from ....message.presenters.weather_constants import SORTED_WEATHER_TYPES
from .common import build_projection_view, coerce_dict, first_non_empty


def _extract_weather_projection_details(
    metadata, domain_metadata, title: str, headline: str
):
    """提取气象事件展示所需的投影细节。"""
    # 强制安全合并，合并领域上下文与外部元数据，形成基础投影视图字典
    projection_view = build_projection_view(
        domain_metadata=domain_metadata,
        metadata=metadata,
    )
    # 获取详细的预警气象事件防御指南或受灾范围描述
    description = first_non_empty(
        projection_view.get("description"),
        projection_view.get("summary"),
        "",
    )

    # 优先且仅从代表标题的 headline 中提取预警类型，避免描述文本干扰
    weather_type = ""
    search_text = headline if headline else title
    for name in SORTED_WEATHER_TYPES:
        if name in search_text:
            weather_type = name
            break
    if not weather_type:
        weather_type = str(
            first_non_empty(
                projection_view.get("weather_type"),
                projection_view.get("type"),
                projection_view.get("weatherType"),
                title,
                headline,
                "",
            )
        ).strip()

    return {
        # 提取事件大标题/头条
        "headline": str(
            first_non_empty(headline, projection_view.get("headline"), "")
        ).strip(),
        # 详细防御指南及受灾区域说明文本
        "description": description,
        # 兼容多种不同预警级别键名，确定气象预警颜色级别
        "severity_color": str(
            first_non_empty(
                projection_view.get("severity_color"),
                projection_view.get("level"),
                projection_view.get("alert_level"),
                "",
            )
        ).strip(),
        # 提取预警的气象类型，使用多级优先级回退逻辑以确保解析出合理的中文气象类型
        "weather_type": weather_type,
    }


def build_weather_display_context(projection: dict, options: dict | None = None):
    """构建气象展示上下文主入口。"""
    envelope = projection["envelope"]
    resolved_source_id = projection["resolved_source_id"]
    source_descriptor = projection["source_descriptor"]
    source_payload = projection["source_payload"]
    metadata = projection["metadata"]
    title = projection["title"]
    domain_event = envelope.event

    # 标题、头条与说明字段可能散落在领域对象和元数据中，这里先统一归并。
    domain_metadata = coerce_dict(getattr(domain_event, "metadata", None))
    # 合并头条内容
    headline = str(
        first_non_empty(
            getattr(domain_event, "headline", None),
            domain_metadata.get("headline"),
            metadata.get("headline"),
            "",
        )
    )
    # 调用气象投影明细信息提取工具
    payload_details = _extract_weather_projection_details(
        metadata,
        domain_metadata,
        title,
        headline,
    )
    display_metadata = {
        **metadata,
        "event_id": envelope.id,
        "source_id": resolved_source_id,
        "event_type": envelope.event_type or getattr(envelope, "event_type", "weather"),
        "description": payload_details["description"],
    }
    # 扩展数据源的展示器按约定读取 WeatherEvent.metadata；内置数据源保持原有口径不变。
    if getattr(source_descriptor, "provider_family", "") == "extension":
        display_metadata = {**domain_metadata, **display_metadata}
    return WeatherDisplayContext(
        event_id=envelope.id,
        source_id=resolved_source_id,
        title=title,
        headline=payload_details["headline"],
        description=payload_details["description"],
        # 提取预警发布时间或生效时间
        effective_at=(
            getattr(domain_event, "effective_at", None)
            or getattr(domain_event, "issued_at", None)
            or getattr(domain_event, "occurred_at", None)
        ),
        severity_color=str(payload_details["severity_color"] or ""),
        weather_type=str(payload_details["weather_type"] or ""),
        metadata=display_metadata,
        options=dict(options or {}),
        display_model=WeatherDisplayModel(
            title=title,
            extras=dict(display_metadata),
        ),
        source_descriptor=source_descriptor,
        payload=source_payload,
    )


__all__ = ["build_weather_display_context"]
