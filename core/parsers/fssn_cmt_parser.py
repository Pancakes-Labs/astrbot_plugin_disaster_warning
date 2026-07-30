"""FSSN CMT (矩心矩张量解) 解析器。

职责：
- 接收并解析来自 FAN Studio 的 FSSN CMT 数据包。
- 提取多震级、节面参数并转换为标准化 EarthquakeEvent，细节封装进 metadata。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.earthquake.cmt_normalize import (
    FSSN_CMT_SOURCE_ID,
    build_cmt_metadata,
)
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class FssnCmtParser(BaseParser):
    """FSSN 矩心矩张量解 (CMT) 解析器。"""

    def __init__(self, message_logger=None):
        super().__init__(FSSN_CMT_SOURCE_ID, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 FSSN CMT 数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 过滤空心跳包
            if self._is_heartbeat_message(msg_data):
                return None

            # 核心必要字段校验
            cmt_id = str(msg_data.get("id") or "").strip()
            event_id = str(msg_data.get("eventId") or "").strip()
            shock_time_str = str(msg_data.get("shockTime") or "").strip()

            if not cmt_id or not event_id:
                plugin_logger.warning(
                    f"[灾害预警] {self.source_id} 缺少 CMT 唯一 ID 或事件 ID，跳过"
                )
                return None

            # 规范化 metadata 携带的 CMT 附加信息
            source_entry = get_source_entry(self.source_id)
            metadata = build_cmt_metadata(
                raw_payload=msg_data,
                source_enum=source_entry.source_enum
                if source_entry
                else "fan_studio_fssn_cmt",
                source_type=source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                source_family="fan_studio",
            )

            # 提取发震主参数
            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            place_name = str(msg_data.get("placeName") or "").strip()

            # 使用选出的展示主震级和提取的深度值作为 EarthquakeEvent 的主字段
            magnitude = metadata.get("display_magnitude")
            depth = metadata.get("depth")

            # 实例化地震领域模型
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(shock_time_str),
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                metadata=metadata,
            )

            # 构建事件身份模型，以 CMT ID 作为事件主键，关联事件 ID 设为 alias
            identity = EventIdentity(
                event_id=cmt_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum
                if source_entry
                else "fan_studio_fssn_cmt",
                published_at=domain_event.occurred_at,
                aliases=tuple(item for item in (event_id, cmt_id) if item),
                attributes={
                    "parser_name": source_entry.parser_name
                    if source_entry
                    else "fssn_cmt_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "fssn_cmt",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "fan_studio",
                    message_type=str(msg_data.get("type") or "update").strip(),
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] FSSN CMT 地震解析成功: {domain_event.place_name} "
                f"(主选 M {domain_event.magnitude or 0.0}), 关联事件: {event_id}",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope

        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 CMT 报文失败: {exc}")
            return None
