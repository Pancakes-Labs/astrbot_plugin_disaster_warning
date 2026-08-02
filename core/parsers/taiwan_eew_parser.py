"""
台湾地震预警解析器。
负责把 FAN Studio 与 Wolfx 来源的台湾地震预警数据统一转换为领域事件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class CwaEewParser(BaseParser):
    """台湾中央气象署地震预警解析器 - FAN Studio。"""

    def __init__(self, message_logger=None):
        """初始化 FAN Studio 台湾地震预警解析器。"""
        super().__init__("cwa_fanstudio", message_logger)

    def _build_envelope(self, msg_data: dict[str, Any]) -> EventEnvelope:
        """把台湾地震预警原始字典封装为统一事件包裹体。"""
        # 最大烈度可能在不同键名中，这里进行兼容性提取（maxIntensity 与 epiIntensity）
        intensity = msg_data.get("maxIntensity")
        if intensity is None:
            intensity = msg_data.get("epiIntensity")

        raw_shock_time = msg_data.get("shockTime", "")
        occurred_at = self._parse_datetime(raw_shock_time)
        latitude = safe_float_convert(msg_data.get("latitude"))
        longitude = safe_float_convert(msg_data.get("longitude"))
        place_name = str(msg_data.get("placeName", "") or "")

        # 部分低版本或测试报文可能缺失 eventId 键，在此提供稳定的备用回退生成规则
        event_id = str(msg_data.get("eventId") or msg_data.get("eventID") or "").strip()
        if not event_id:
            shock_key = str(raw_shock_time or "").strip()
            place_key = str(place_name or "").strip()
            # 拼合规则：发震时间_经度（三位精度）_纬度（三位精度）_地点
            event_id = f"cwa_fan_{shock_key}_{(latitude or 0.0):.3f}_{(longitude or 0.0):.3f}_{place_key}"
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 缺少 eventId，已使用稳定回退ID: {event_id}"
            )

        impact_area = ""
        # 警戒影响的区域可能作为列表给出，需归一化为逗号分割字符串
        location_desc_list = msg_data.get("locationDesc", [])
        if location_desc_list:
            impact_area = ",".join(
                str(item) for item in location_desc_list if str(item).strip()
            )

        source_entry = get_source_entry(self.source_id)
        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_warning",
            "event_id": event_id,
            "create_time": self._parse_datetime(msg_data.get("createTime", "")),
            "updates": msg_data.get("updates", 1),
            "is_final": bool(msg_data.get("isFinal", False)),
            "impact_area": impact_area,
            "province": impact_area or None,
        }

        # 实例化台湾地震领域事件，填入震度、深度与经纬度
        domain_event = EarthquakeEvent(
            occurred_at=occurred_at,
            latitude=latitude,
            longitude=longitude,
            place_name=place_name,
            magnitude=safe_float_convert(msg_data.get("magnitude")),
            depth=safe_float_convert(msg_data.get("depth")),
            scale=safe_float_convert(intensity),
            metadata=dict(metadata),
        )

        created_at = self._parse_datetime(msg_data.get("createTime", ""))

        # 构建唯一的事件身份模型，报次 updates 转为正整型
        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake_warning",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            report_num=int(msg_data.get("updates", 1) or 1),
            published_at=created_at or occurred_at,
            is_final=bool(msg_data.get("isFinal", False)),
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
        """解析台湾中央气象署地震预警数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 报次或事件标识至少应命中其一，否则通常不是正式台湾地震预警消息
            if "updates" not in msg_data and "eventId" not in msg_data:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非CWA地震预警数据(缺少updates/eventId)，跳过"
                )
                return None

            envelope = self._build_envelope(msg_data)
            domain_event = envelope.event

            plugin_logger.info(
                f"[灾害预警] 地震预警解析成功: {getattr(domain_event, 'place_name', '')} (M {getattr(domain_event, 'magnitude', None)}), 时间: {getattr(domain_event, 'occurred_at', None)}",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class CwaEewWolfxParser(BaseParser):
    """台湾中央气象署地震预警解析器 - Wolfx。"""

    def __init__(self, message_logger=None):
        """初始化 Wolfx 台湾地震预警解析器。"""
        super().__init__("cwa_wolfx", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Wolfx 台湾地震预警数据。"""
        try:
            # Wolfx 中只接收台湾地震预警类型，其余类型直接忽略。
            if data.get("type") != "cwa_eew":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 CWA 地震预警数据，跳过"
                )
                return None

            raw_origin_time = data.get("OriginTime", "")
            shock_time = self._parse_datetime(raw_origin_time)
            latitude = safe_float_convert(data.get("Latitude")) or 0.0
            longitude = safe_float_convert(data.get("Longitude")) or 0.0
            place_name = data.get("HypoCenter", "")

            # 影响区域优先取标准警戒区域字段，缺失时再遍历多种兼容键名回退。
            impact_area = ""
            warn_area = data.get("WarnArea")
            if isinstance(warn_area, dict):
                impact_area = str(warn_area.get("Chiiki") or "").strip()

            # 若 WarnArea 不存在，则循环解析其他几种常见的键名
            if not impact_area:
                for key in [
                    "locationDesc",
                    "impactArea",
                    "ImpactArea",
                    "affectedArea",
                    "AffectedArea",
                    "Area",
                    "area",
                ]:
                    value = data.get(key)
                    if isinstance(value, list):
                        # 如果提取的值是区域列表，采用“、”拼接为长字符串
                        value = "、".join(
                            str(x).strip() for x in value if str(x).strip()
                        )
                    elif not isinstance(value, str):
                        value = ""
                    value = value.strip()
                    if value:
                        impact_area = value
                        break

            # 处理缺失 EventID 的边界情况，防止无法进入去重列表
            event_id = str(data.get("EventID") or data.get("eventId") or "").strip()
            if not event_id:
                shock_key = str(raw_origin_time or "").strip()
                place_key = str(place_name or "").strip()
                event_id = (
                    f"cwa_wolfx_{shock_key}_{latitude:.3f}_{longitude:.3f}_{place_key}"
                )
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 缺少 EventID，已使用稳定回退ID: {event_id}"
                )

            # 在 raw payload 中植入格式化完成的 wolfx_impact_area，便于后续展现直接使用
            raw_payload = (
                {**data, "wolfx_impact_area": impact_area}
                if impact_area
                else dict(data)
            )
            raw_report_num = data.get("ReportNum", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "wolfx",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "event_id": event_id,
                "updates": report_num,
                "report_num": report_num,
                "impact_area": impact_area,
                "province": impact_area or None,
                "is_final": bool(data.get("isFinal", False)),
            }

            # 实例化地震领域模型，利用最大震度转换器转换 MaxIntensity
            domain_event = EarthquakeEvent(
                occurred_at=shock_time,
                latitude=latitude,
                longitude=longitude,
                depth=safe_float_convert(data.get("Depth")),
                magnitude=safe_float_convert(
                    data.get("Magunitude") or data.get("Magnitude")
                ),
                scale=ScaleConverter.parse_jma_cwa_scale(data.get("MaxIntensity", "")),
                place_name=place_name,
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "wolfx",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=shock_time,
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
                    message_type=str(data.get("type") or "cwa_eew").strip(),
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 地震预警解析成功: {domain_event.place_name} (M {domain_event.magnitude}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
                event_stream="earthquake",
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
