"""
中国地震情报解析器。
负责把 FAN Studio 与 Wolfx 来源的中国地震测定数据转换为统一领域事件。
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


class CencEarthquakeParser(BaseParser):
    """中国地震台网地震测定解析器，处理 FAN Studio 来源数据。"""

    def __init__(self, message_logger=None):
        super().__init__("cenc_fanstudio", message_logger)

    def _build_envelope(self, msg_data: dict[str, Any]) -> EventEnvelope:
        """把中国地震测定原始字典封装为统一事件包裹体。"""
        # 震级与深度统一在解析阶段做一位小数归一化，减少后续展示层重复处理
        magnitude = safe_float_convert(msg_data.get("magnitude"))
        if magnitude is not None:
            magnitude = round(magnitude, 1)

        depth = safe_float_convert(msg_data.get("depth"))
        if depth is not None:
            depth = round(depth, 1)

        source_entry = get_source_entry(self.source_id)
        event_id = str(msg_data.get("eventId", "") or "")

        # 整理事件的元数据字典，包含来源族、数据源静态标识、信息类别及事件ID
        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_info",
            "info_type": msg_data.get("infoTypeName", ""),
            "event_id": event_id,
        }

        # 实例化地震领域模型
        domain_event = EarthquakeEvent(
            occurred_at=self._parse_datetime(msg_data.get("shockTime", "")),
            latitude=safe_float_convert(msg_data.get("latitude")),
            longitude=safe_float_convert(msg_data.get("longitude")),
            place_name=str(msg_data.get("placeName", "") or ""),
            magnitude=magnitude,
            depth=depth,
            metadata=dict(metadata),
        )

        # 构建唯一的事件身份模型
        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            published_at=domain_event.occurred_at,
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
        """解析中国地震台网数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 这类消息至少应具备情报类型与事件标识，否则通常不是正式测定数据
            if "infoTypeName" not in msg_data or "eventId" not in msg_data:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 CENC 地震测定数据，跳过"
                )
                return None

            envelope = self._build_envelope(msg_data)
            envelope.metadata.update(
                {
                    "info_type": msg_data.get("infoTypeName", ""),
                }
            )

            domain_event = envelope.event
            plugin_logger.info(
                f"[灾害预警] 地震数据解析成功: {getattr(domain_event, 'place_name', '')} (M {getattr(domain_event, 'magnitude', None)}), 时间: {getattr(domain_event, 'occurred_at', None)}",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class CencEarthquakeWolfxParser(BaseParser):
    """中国地震台网地震测定解析器，处理 Wolfx 来源数据。"""

    def __init__(self, message_logger=None):
        super().__init__("cenc_wolfx", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Wolfx 中国地震台网地震列表。"""
        try:
            # WebSocket 标准消息通常会带 type=cenc_eqlist，
            # 但 HTTP 列表接口可能直接返回 No1/No2... 结构而没有顶层 type。
            raw_type = str(data.get("type") or "").strip()
            no_keys = [
                key
                for key, value in data.items()
                if key.startswith("No") and isinstance(value, dict)
            ]
            if raw_type and raw_type != "cenc_eqlist":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 CENC 地震列表数据，跳过"
                )
                return None
            if not raw_type and not no_keys:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 未识别到 Wolfx CENC 列表结构，跳过"
                )
                return None

            eq_info = None
            # 列表消息通常以 No1、No2 这类键名承载首条地震记录，这里取首个有效项
            for key, value in data.items():
                if key.startswith("No") and isinstance(value, dict):
                    eq_info = value
                    break

            if not eq_info:
                plugin_logger.warning(
                    f"[灾害预警] {self.source_id} 这次收到的 Wolfx CENC 列表里没有找到可解析的地震条目"
                )
                return None

            event_id = str(
                eq_info.get("eventId")
                or eq_info.get("EventID")
                or eq_info.get("id")
                or eq_info.get("ID")
                or eq_info.get("originId")
                or eq_info.get("OriginID")
                or eq_info.get("md5")
                or ""
            ).strip()

            # CENC 地震测定链路并不存在稳定的“第几报”语义，因此统一回退为单槽 1
            report_num = 1

            source_record_id = str(eq_info.get("md5") or event_id or "").strip()

            raw_payload = dict(data)
            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "wolfx",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
                "updates": report_num,
                "report_num": report_num,
                "info_type": eq_info.get("type", ""),
            }

            # 实例化地震领域事件
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(eq_info.get("time", "")),
                latitude=safe_float_convert(eq_info.get("latitude")),
                longitude=safe_float_convert(eq_info.get("longitude")),
                depth=safe_float_convert(eq_info.get("depth")),
                magnitude=safe_float_convert(eq_info.get("magnitude")),
                intensity=safe_float_convert(eq_info.get("intensity")),
                place_name=eq_info.get("location", ""),
                metadata=dict(metadata),
            )

            # 构建事件身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "wolfx",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=domain_event.occurred_at,
                aliases=tuple(item for item in (source_record_id,) if item),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 封装并返回统一事件包裹层
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "wolfx",
                    message_type=str(data.get("type") or "cenc_eqlist").strip(),
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.debug(
                f"[灾害预警] 地震数据解析成功: {domain_event.place_name} (M {domain_event.magnitude}), 时间: {domain_event.occurred_at}"
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
