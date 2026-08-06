"""
中国地震预警解析器。
负责把 FAN Studio 与 Wolfx 来源的中国地震预警数据转换为统一领域事件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class CEAEEWParser(BaseParser):
    """中国地震预警网解析器，处理 FAN Studio 来源数据。"""

    def __init__(self, message_logger=None, source_id: str = "cea_fanstudio"):
        super().__init__(source_id, message_logger)

    def _build_envelope(self, msg_data: dict[str, Any]) -> EventEnvelope:
        """把中国地震预警原始字典封装为统一事件包裹体。"""
        occurred_at = self._parse_datetime(msg_data.get("shockTime", ""))
        # 报次字段可能来自不同键名，这里统一归一化为正整数
        raw_report_num = msg_data.get("reportNum", msg_data.get("updates", 1))
        try:
            report_num = int(raw_report_num)
        except (TypeError, ValueError):
            report_num = 1
        if report_num <= 0:
            report_num = 1

        source_entry = get_source_entry(self.source_id)
        event_id = str(msg_data.get("eventId", "") or msg_data.get("id", "") or "")

        # 整理元数据
        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_warning",
            "event_id": event_id,
            "province": msg_data.get("province"),
            "report_num": report_num,
            "is_final": bool(msg_data.get("isFinal", False)),
            "updates": msg_data.get("updates", 1),
        }

        # 实例化地震领域模型，注意包含省份和预计最大烈度信息
        domain_event = EarthquakeEvent(
            occurred_at=occurred_at,
            latitude=safe_float_convert(msg_data.get("latitude")),
            longitude=safe_float_convert(msg_data.get("longitude")),
            place_name=str(msg_data.get("placeName", "") or ""),
            magnitude=safe_float_convert(msg_data.get("magnitude")),
            depth=safe_float_convert(msg_data.get("depth")),
            intensity=msg_data.get("epiIntensity"),
            province=msg_data.get("province"),
            metadata=dict(metadata),
        )

        # 构造身份模型
        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake_warning",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            report_num=report_num,
            aliases=tuple(
                item for item in (str(msg_data.get("id", "") or "").strip(),) if item
            ),
            attributes={
                "parser_name": self.source_entry.parser_name
                if self.source_entry
                else "",
                "config_key": source_entry.config_key if source_entry else "",
            },
        )

        # 封装为统一事件包裹层返回
        return EventEnvelope(
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

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析中国地震预警网数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 中国地震预警数据至少应携带预计烈度字段，否则大概率不是目标消息
            if "epiIntensity" not in msg_data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 非地震预警数据，跳过")
                return None

            envelope = self._build_envelope(msg_data)
            raw_report_num = msg_data.get("reportNum", msg_data.get("updates", 1))
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            envelope.metadata.update(
                {
                    "event_id": str(
                        msg_data.get("eventId", "") or msg_data.get("id", "") or ""
                    ),
                    "province": msg_data.get("province"),
                    "report_num": report_num,
                    "is_final": bool(msg_data.get("isFinal", False)),
                    "updates": msg_data.get("updates", 1),
                }
            )

            domain_event = envelope.event
            plugin_logger.info(
                f"[灾害预警] 地震预警解析成功: {getattr(domain_event, 'place_name', '')} (M {getattr(domain_event, 'magnitude', None)}), 时间: {getattr(domain_event, 'occurred_at', None)}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class CEAEEWPRParser(CEAEEWParser):
    """中国地震预警网省级解析器，处理 FAN Studio 来源数据。"""

    def __init__(self, message_logger=None):
        super().__init__(message_logger=message_logger, source_id="cea_pr_fanstudio")


class CEAEEWWolfxParser(BaseParser):
    """中国地震预警网解析器，处理 Wolfx 来源数据。"""

    def __init__(self, message_logger=None):
        super().__init__("cea_wolfx", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Wolfx 中国地震预警数据。"""
        try:
            # Wolfx 会混发多类消息，这里只接收中国地震预警类型
            if data.get("type") != "cenc_eew":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 CENC 地震预警数据，跳过"
                )
                return None

            raw_report_num = data.get("ReportNum", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            source_entry = get_source_entry(self.source_id)
            event_id = str(data.get("EventID", "") or "")
            metadata = {
                "source_family": "wolfx",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "event_id": event_id,
                "report_num": report_num,
                "updates": report_num,
                "is_final": bool(data.get("isFinal", False)),
            }

            # 实例化地震领域事件
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(data.get("OriginTime", "")),
                latitude=safe_float_convert(data.get("Latitude")),
                longitude=safe_float_convert(data.get("Longitude")),
                depth=safe_float_convert(data.get("Depth")),
                magnitude=safe_float_convert(data.get("Magnitude")),
                intensity=safe_float_convert(data.get("MaxIntensity")),
                place_name=data.get("HypoCenter", ""),
                metadata=dict(metadata),
            )

            # 构建事件身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "wolfx",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=domain_event.occurred_at,
                is_final=bool(metadata.get("is_final", False)),
                aliases=tuple(
                    item for item in (str(data.get("ID", "") or "").strip(),) if item
                ),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 封装并返回统一包裹层
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "wolfx",
                    message_type=str(data.get("type") or "cenc_eew").strip(),
                    raw=dict(data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 地震预警解析成功: {domain_event.place_name} (M {domain_event.magnitude}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
