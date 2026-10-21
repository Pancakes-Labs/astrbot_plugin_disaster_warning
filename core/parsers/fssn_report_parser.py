"""FSSN 地震信息解析器。

职责：
- 接收并解析来自 fssn-api.fanstudio.tech 的 FSSN 地震速报。
- 字段对齐官方文档：shockTime / placeName_zh / infoTypeName 等。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.payload_guards import looks_like_fssn_cmt_payload, looks_like_fssn_payload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class FssnReportParser(BaseParser):
    """FSSN 地震信息解析器。"""

    def __init__(self, message_logger=None, source_id: str = "fssn_fanstudio"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 FSSN 地震信息。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            if self._is_heartbeat_message(msg_data):
                return None

            # 拒绝 CMT 载荷，避免与 fssn-cmt 混淆
            if looks_like_fssn_cmt_payload(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 缺少事件 ID，跳过")
                return None

            # 非 FSSN 特征且无 FSSN 前缀时拒绝，避免误吃 ShakeAlert 等
            if not looks_like_fssn_payload(msg_data, event_id=event_id):
                return None

            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            place_zh = str(msg_data.get("placeName_zh") or "").strip()
            place_en = str(msg_data.get("placeName") or "").strip()
            place_name = place_zh or place_en
            info_type = str(msg_data.get("infoTypeName") or "").strip()

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "fan_studio",
                "source_enum": source_entry.source_enum
                if source_entry
                else "fan_studio_fssn",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
                "info_type": info_type,
                "place_name_en": place_en,
                "place_name_zh": place_zh,
                "create_time": str(msg_data.get("createTime") or "").strip(),
            }

            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(msg_data.get("shockTime", "")),
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum
                if source_entry
                else "fan_studio_fssn",
                published_at=domain_event.occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": source_entry.parser_name
                    if source_entry
                    else "fssn_report_parser",
                    "config_key": source_entry.config_key if source_entry else "fssn",
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
                f"[灾害预警] FSSN 地震信息解析成功: {domain_event.place_name} "
                f"(M {domain_event.magnitude}, {info_type})",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


__all__ = ["FssnReportParser"]
