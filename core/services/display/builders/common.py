"""
展示投影公共工具。
负责 metadata/payload 归一化、source/title 解析与投影输入准备，
为各类 display context builder 提供统一 input。
"""

from __future__ import annotations

from typing import Any

from ....domain.event_models import EventEnvelope
from ....domain.event_payload import SourcePayload
from ....domain.source_models import SourceDescriptor
from ....sources.source_catalog import get_source_entry


def normalize_display_text(value: Any) -> str | None:
    """把展示字段统一归一化为可直接显示的文本。"""
    if isinstance(value, list):
        # 提取列表中每个元素的非空字符串表达
        parts = [str(item).strip() for item in value if str(item).strip()]
        # 对列表中的有效字符元素使用“、”拼接，例如 ["台北市", "新北市"] -> "台北市、新北市"
        return "、".join(parts) if parts else None
    if isinstance(value, str):
        # 对单独的字符串剥离首尾空格
        text = value.strip()
        # 规避空字符串干扰，若为空字符串则返回 None 以便后续判定兜底
        return text or None
    return None


def first_non_empty(*values: Any) -> Any:
    """返回首个非空值，便于做多来源字段回退。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            # 剥离无效空格后的空字符串视作无效值，继续搜索下一个候选值
            continue
        return value
    return None


def coerce_dict(value: Any) -> dict[str, Any]:
    """把输入安全转换为字典。"""
    # 强制将入参转为字典类型，防止因类型不匹配（如 None 或 字符串）导致后续的 update 操作报错
    return dict(value) if isinstance(value, dict) else {}


def build_projection_view(
    *,
    domain_metadata: dict[str, Any] | None = None,
    payload_attributes: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把多个元数据层合并为单一投影视图。"""
    projection_view: dict[str, Any] = {}
    # 按来源优先级逐步覆盖合并，层级越高优先级越强 (领域字典 -> 原始载荷 -> 外部注入元数据)
    for layer in (domain_metadata, payload_attributes, metadata):
        if isinstance(layer, dict):
            projection_view.update(layer)
    return projection_view


def normalize_projection_metadata(
    envelope: EventEnvelope,
) -> tuple[SourcePayload | None, dict[str, Any]]:
    """归一化展示投影使用的 payload 与 metadata。"""
    # 提取包装信封中的内部负载对象
    source_payload_input = getattr(envelope, "payload", None)
    # 提取信封附带的自定义元数据字典
    metadata_payload = getattr(envelope, "metadata", None)
    metadata = dict(metadata_payload) if isinstance(metadata_payload, dict) else {}
    source_payload = (
        source_payload_input
        if isinstance(source_payload_input, SourcePayload)
        else None
    )
    # 若存在原始来源属性字典，则与外部元数据进行合并，保证物理原始载荷属性具备第一处理顺位
    if source_payload is not None and isinstance(source_payload.attributes, dict):
        merged = dict(source_payload.attributes)
        merged.update(metadata)
        metadata = merged
    return source_payload, metadata


def resolve_projection_source_id(event: EventEnvelope, source_id: str) -> str:
    """统一解析展示投影使用的 source_id。"""
    # 优先使用显式传递给投影器的 source_id 字符串
    resolved_source_id = (source_id or "").strip()
    if resolved_source_id:
        return resolved_source_id

    # 兜底从事件信封实体 (EventEnvelope) 中提取其注册绑定的原始数据源 ID
    if (
        hasattr(event, "source_id")
        and isinstance(event.source_id, str)
        and event.source_id.strip()
    ):
        return event.source_id.strip()

    return ""


def build_projection_payload(
    source_id: str,
    source_descriptor,
    metadata: dict[str, Any],
) -> SourcePayload:
    """构建投影阶段使用的来源载荷对象。"""
    provider_family = ""
    # 若注册目录中含有该数据源描述，则直接取出所属服务商家族分类（如 wolfx / jian_project）
    if source_descriptor is not None:
        provider_family = getattr(source_descriptor, "provider_family", "") or ""
    # 统一转换消息格式标志字段
    message_type = str(
        metadata.get("message_type") or metadata.get("type") or ""
    ).strip()
    return SourcePayload(
        source_id=source_id,
        provider_family=provider_family,
        message_type=message_type,
        raw={},
        attributes=dict(metadata),
    )


def resolve_projection_title(
    domain_event,
    metadata: dict[str, Any],
    fallback_id: str,
) -> str:
    """统一解析展示标题。

    优先使用地点、标题或头条字段，最后才退回到事件标识。
    """
    # 按照严格的缺省降级层级抽取事件标题
    return (
        getattr(domain_event, "place_name", None)
        or getattr(domain_event, "title", None)
        or getattr(domain_event, "headline", None)
        or metadata.get("title")
        or metadata.get("headline")
        or fallback_id
        or ""
    )


def _build_source_descriptor(source_id: str) -> SourceDescriptor | None:
    """直接从数据源目录构建展示层来源描述对象。"""
    # 从统一数据源静态配置文件中定位条目
    entry = get_source_entry(source_id)
    if entry is None:
        return None
    # 抽取并浅拷贝映射为运行态 SourceDescriptor 对象，隔绝外部模块直接侵染配置目录
    return SourceDescriptor(
        source_id=entry.source_id,
        source_enum=entry.source_enum,
        provider_family=entry.provider_family.value,
        source_type=entry.source_type.value,
        parser_name=entry.parser_name,
        presentation_type=entry.presentation_type,
        text_presenter_key=entry.text_presenter_key,
        config_group=entry.config_group,
        config_key=entry.config_key,
        report_policy=entry.report_policy,
        intensity_mode=entry.intensity_mode,
        priority=entry.priority,
        display_name=entry.display_name,
        default_timezone=entry.default_timezone,
        event_time_field=entry.event_time_field,
        publish_time_field=entry.publish_time_field,
        report_num_field=entry.report_num_field,
        final_flag_field=entry.final_flag_field,
        issue_type_field=entry.issue_type_field,
        fingerprint_prefix=entry.fingerprint_prefix,
        connection_group=entry.connection_group,
        provider_message_types=tuple(entry.provider_message_types),
        provider_source_names=tuple(entry.provider_source_names),
        provider_aliases=tuple(entry.provider_aliases),
        routing_tags=tuple(entry.routing_tags),
        metadata=dict(entry.metadata),
    )


def prepare_display_projection(event: EventEnvelope, source_id: str) -> dict[str, Any]:
    """构建展示投影的公共输入包。

    该步骤会统一补齐来源标识、来源描述、标题、载荷与元数据，供后续展示上下文构建器直接使用。
    """
    envelope = event
    # 校验并提取可用的数据源 ID
    resolved_source_id = resolve_projection_source_id(event, source_id)
    if not resolved_source_id:
        resolved_source_id = getattr(envelope, "source_id", "") or ""

    # 解析载荷对象及其合并元数据字典
    source_payload_input, metadata = normalize_projection_metadata(envelope)
    # 取出该数据源对应的静态定义信息描述器
    source_descriptor = _build_source_descriptor(resolved_source_id)
    # 若信封中无 SourcePayload 副本，则在内存中实时生成一份供下游消费
    source_payload = source_payload_input or build_projection_payload(
        resolved_source_id,
        source_descriptor,
        metadata,
    )
    # 渲染构建最终用于展示的简报首部标题
    title = resolve_projection_title(
        envelope.event,
        metadata,
        envelope.id,
    )
    return {
        "envelope": envelope,
        "resolved_source_id": resolved_source_id,
        "source_descriptor": source_descriptor,
        "source_payload": source_payload,
        "metadata": metadata,
        "title": title,
    }


__all__ = [
    "normalize_display_text",
    "first_non_empty",
    "coerce_dict",
    "build_projection_view",
    "normalize_projection_metadata",
    "resolve_projection_source_id",
    "build_projection_payload",
    "resolve_projection_title",
    "prepare_display_projection",
]
