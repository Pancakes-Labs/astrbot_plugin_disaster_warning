"""
Jian Project 扩展数据源解析器。

覆盖：CEA 省局、ShakeAlert、地方局速报、CWA/JMA 速报、JMA 长周期、
EMSC、JMA 海啸。字段口径严格对齐 Jian Project API 说明文档。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ...utils.time_converter import TimeConverter
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..domain.tsunami.jma_tsunami_normalize import (
    build_jma_tsunami_content_fingerprint,
    coerce_bool,
    normalize_jma_tsunami_areas,
    resolve_jma_tsunami_max_grade,
    resolve_jma_tsunami_title,
)
from ..services.geo.region_service import region_service
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser
from .tsunami_parser import _build_jma_tsunami_envelope

# Jian jma-tsunami area.kind -> 归一化等级
_JMA_TSUNAMI_KIND_TO_GRADE: dict[str, str] = {
    "大津波警報": "MajorWarning",
    "津波警報": "Warning",
    "津波注意報": "Watch",
    "津波予報": "Unknown",
    "津波なし": "None",
}


def _parse_origin_time(raw: Any) -> datetime:
    occurred_at = TimeConverter.parse_datetime(raw)
    if occurred_at:
        return occurred_at
    return datetime.now(timezone.utc)


class CeaPrEewJianProjectParser(BaseParser):
    """中国地震预警网省级分中心 (cea-pr) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "cea_pr_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            raw_report_num = msg_data.get("number", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            province = str(msg_data.get("province") or "").strip() or None
            place_name = str(msg_data.get("placeName") or "").strip()

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_cea_pr",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "event_id": event_id,
                "province": province,
                "report_num": report_num,
                "updates": report_num,
                "is_final": False,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=safe_float_convert(msg_data.get("latitude")),
                longitude=safe_float_convert(msg_data.get("longitude")),
                place_name=place_name,
                magnitude=safe_float_convert(msg_data.get("magnitude")),
                depth=safe_float_convert(msg_data.get("depth")),
                province=province,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_cea_pr",
                report_num=report_num,
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "china_eew_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "china_earthquake_warning_provincial",
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
                    else "jian_project",
                    message_type="cea-pr",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
            plugin_logger.info(
                f"[灾害预警] CEA 省局预警 (Jian Project) 解析成功: "
                f"{domain_event.place_name} (M {domain_event.magnitude}) "
                f"第{report_num}报 province={province or '-'}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class SaEewJianProjectParser(BaseParser):
    """美国 ShakeAlert (sa) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "sa_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            latitude = safe_float_convert(msg_data.get("latitude")) or 0.0
            longitude = safe_float_convert(msg_data.get("longitude")) or 0.0
            if latitude == 0 and longitude == 0:
                return None

            raw_report_num = msg_data.get("number", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            raw_place = str(msg_data.get("placeName") or "").strip()
            place_name = region_service.translate_place_name(
                raw_place,
                latitude,
                longitude,
                fallback_to_original=True,
            )

            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)
            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_sa",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "event_id": event_id,
                "report_num": report_num,
                "updates": report_num,
                "is_final": False,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
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
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_sa",
                report_num=report_num,
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "shakealert_eew_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "usa_shakealert",
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
                    else "jian_project",
                    message_type="sa",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class RegionalReportJianProjectParser(BaseParser):
    """地方局速报通用解析器（yunnan/shanxi/beijing/ningxia）。"""

    def __init__(
        self,
        message_logger=None,
        source_id: str = "yunnan_jianproject",
        message_type: str = "yunnan",
    ):
        super().__init__(source_id, message_logger)
        self._message_type = message_type

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)
            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=safe_float_convert(msg_data.get("latitude")),
                longitude=safe_float_convert(msg_data.get("longitude")),
                place_name=str(msg_data.get("placeName") or "").strip(),
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
                else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "regional_report_parser",
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
                    else "jian_project",
                    message_type=self._message_type,
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class YunnanReportJianProjectParser(RegionalReportJianProjectParser):
    def __init__(self, message_logger=None, source_id: str = "yunnan_jianproject"):
        super().__init__(message_logger, source_id=source_id, message_type="yunnan")


class ShanxiReportJianProjectParser(RegionalReportJianProjectParser):
    def __init__(self, message_logger=None, source_id: str = "shanxi_jianproject"):
        super().__init__(message_logger, source_id=source_id, message_type="shanxi")


class BeijingReportJianProjectParser(RegionalReportJianProjectParser):
    def __init__(self, message_logger=None, source_id: str = "beijing_jianproject"):
        super().__init__(message_logger, source_id=source_id, message_type="beijing")


class NingxiaReportJianProjectParser(RegionalReportJianProjectParser):
    def __init__(self, message_logger=None, source_id: str = "ningxia_jianproject"):
        super().__init__(message_logger, source_id=source_id, message_type="ningxia")


class CwaReportJianProjectParser(BaseParser):
    """台湾中央气象署速报 (cwa) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "cwa_report_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)
            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            scale = None
            if "intensity" in msg_data:
                scale = ScaleConverter.parse_jma_cwa_scale(msg_data.get("intensity"))

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_cwa_report",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=safe_float_convert(msg_data.get("latitude")),
                longitude=safe_float_convert(msg_data.get("longitude")),
                place_name=str(msg_data.get("placeName") or "").strip(),
                magnitude=magnitude,
                depth=depth,
                scale=scale,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_cwa_report",
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "taiwan_report_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "taiwan_cwa_report",
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
                    else "jian_project",
                    message_type="cwa",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class JmaReportJianProjectParser(BaseParser):
    """日本气象厅震源・震度情报 (jma) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "jma_report_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            report_time = TimeConverter.parse_datetime(msg_data.get("reportTime"))
            intensity_raw = str(msg_data.get("intensity") or "").strip()
            scale = (
                ScaleConverter.parse_jma_cwa_scale(intensity_raw)
                if intensity_raw
                else None
            )

            raw_serial = msg_data.get("serial", 1)
            try:
                serial = int(raw_serial)
            except (TypeError, ValueError):
                serial = 1

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_jma_report",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
                "telegram": str(msg_data.get("telegram") or "").strip(),
                "title": str(msg_data.get("title") or "").strip(),
                "info_type": str(msg_data.get("infoType") or "").strip(),
                "info_type_name": str(msg_data.get("infoTypeName") or "").strip(),
                "serial": serial,
                "headline": str(msg_data.get("headline") or "").strip(),
                "magnitude_type": str(msg_data.get("magnitudeType") or "").strip(),
                "area_code": str(msg_data.get("areaCode") or "").strip(),
                "tsunami_comment": str(msg_data.get("tsunamiComment") or "").strip(),
                "is_distant": bool(msg_data.get("isDistant", False)),
                "hypocenter_unknown": bool(msg_data.get("hypocenterUnknown", False)),
                "magnitude_unknown": bool(msg_data.get("magnitudeUnknown", False)),
                "intensity_areas": msg_data.get("intensityAreas")
                if isinstance(msg_data.get("intensityAreas"), list)
                else [],
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=safe_float_convert(msg_data.get("latitude")),
                longitude=safe_float_convert(msg_data.get("longitude")),
                place_name=str(msg_data.get("placeName") or "").strip(),
                magnitude=safe_float_convert(msg_data.get("magnitude")),
                depth=safe_float_convert(msg_data.get("depth")),
                scale=scale,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_jma_report",
                report_num=serial,
                published_at=report_time or occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "japan_report_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "japan_jma_earthquake",
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
                    else "jian_project",
                    message_type="jma",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class JmaLpgmJianProjectParser(BaseParser):
    """日本气象厅长周期地震动观测情报 (jma-lpgm) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "jma_lpgm_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            report_time = TimeConverter.parse_datetime(msg_data.get("reportTime"))
            intensity_raw = str(msg_data.get("intensity") or "").strip()
            scale = (
                ScaleConverter.parse_jma_cwa_scale(intensity_raw)
                if intensity_raw
                else None
            )
            lg_intensity = str(msg_data.get("lgIntensity") or "").strip()

            raw_serial = msg_data.get("serial", 1)
            try:
                serial = int(raw_serial)
            except (TypeError, ValueError):
                serial = 1

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_jma_lpgm",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
                "telegram": str(msg_data.get("telegram") or "").strip(),
                "title": str(msg_data.get("title") or "").strip(),
                "info_type": str(msg_data.get("infoType") or "").strip(),
                "info_type_name": str(msg_data.get("infoTypeName") or "").strip(),
                "serial": serial,
                "headline": str(msg_data.get("headline") or "").strip(),
                "lg_intensity": lg_intensity,
                "intensity_areas": msg_data.get("intensityAreas")
                if isinstance(msg_data.get("intensityAreas"), list)
                else [],
                "tsunami_comment": str(msg_data.get("tsunamiComment") or "").strip(),
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=safe_float_convert(msg_data.get("latitude")),
                longitude=safe_float_convert(msg_data.get("longitude")),
                place_name=str(msg_data.get("placeName") or "").strip(),
                magnitude=safe_float_convert(msg_data.get("magnitude")),
                depth=safe_float_convert(msg_data.get("depth")),
                scale=scale,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_jma_lpgm",
                report_num=serial,
                published_at=report_time or occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "jma_lpgm_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "japan_jma_lpgm",
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
                    else "jian_project",
                    message_type="jma-lpgm",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class EmscReportJianProjectParser(BaseParser):
    """欧洲地中海地震中心 (emsc) - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "emsc_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_origin_time(msg_data.get("originTime"))
            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)
            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            raw_place = str(msg_data.get("placeName") or "").strip()
            place_zh = str(msg_data.get("placename_zh") or "").strip()
            if place_zh:
                place_name = place_zh
            else:
                place_name = region_service.translate_place_name(
                    raw_place,
                    latitude or 0.0,
                    longitude or 0.0,
                    fallback_to_original=True,
                )

            agency = str(msg_data.get("agency") or "").strip()
            info_type = str(msg_data.get("infoTypeName") or "").strip()

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum
                if source_entry
                else "jian_project_emsc",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": event_id,
                "info_type": info_type,
            }
            if agency:
                metadata["agency"] = agency
            if place_zh:
                metadata["placename_zh"] = place_zh

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
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
                else "jian_project",
                source_enum=source_entry.source_enum
                if source_entry
                else "jian_project_emsc",
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "emsc_report_parser",
                    "config_key": source_entry.config_key
                    if source_entry
                    else "emsc_earthquake",
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
                    else "jian_project",
                    message_type="emsc",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class JmaTsunamiJianProjectParser(BaseParser):
    """日本气象厅津波警报 (jma-tsunami) - Jian Project。"""

    def __init__(
        self, message_logger=None, source_id: str = "jma_tsunami_jianproject"
    ):
        super().__init__(source_id, message_logger)

    @staticmethod
    def _adapt_areas(raw_areas: Any, *, cancelled: bool) -> list[dict[str, Any]]:
        if not isinstance(raw_areas, list):
            return []
        adapted: list[dict[str, Any]] = []
        for item in raw_areas:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            grade = _JMA_TSUNAMI_KIND_TO_GRADE.get(kind, "Unknown")
            if coerce_bool(item.get("cleared"), default=False):
                # 解除区仍保留名称，等级按 None 处理，后续由 max_grade/title 综合
                grade = "None"
            max_height_desc = str(
                item.get("maxHeightDesc") or item.get("maxHeight") or ""
            ).strip()
            adapted.append(
                {
                    "name": item.get("name"),
                    "grade": grade,
                    "condition": item.get("condition"),
                    "arrivalTime": item.get("arrivalTime"),
                    "maxWaveHeight": max_height_desc,
                    "max_height": max_height_desc,
                    "code": item.get("code"),
                    "kind": kind,
                    "kindCode": item.get("kindCode"),
                    "cleared": item.get("cleared"),
                    "stations": item.get("stations")
                    if isinstance(item.get("stations"), list)
                    else [],
                }
            )
        return normalize_jma_tsunami_areas(adapted, cancelled=cancelled)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            cancelled = coerce_bool(msg_data.get("cancel"), default=False)
            forecasts = self._adapt_areas(
                msg_data.get("area"), cancelled=cancelled
            )
            max_grade = resolve_jma_tsunami_max_grade(forecasts, cancelled=cancelled)
            # 优先使用上游 title/level；取消报仍走归一化标题
            raw_title = str(msg_data.get("title") or "").strip()
            raw_level = str(msg_data.get("level") or "").strip()
            if cancelled:
                title = resolve_jma_tsunami_title(max_grade, cancelled=True)
            else:
                title = raw_title or raw_level or resolve_jma_tsunami_title(
                    max_grade, cancelled=False
                )

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                stable_parts = [
                    title,
                    str(msg_data.get("number") or "").strip(),
                    str(msg_data.get("originTime") or "").strip(),
                ]
                stable_parts = [part for part in stable_parts if part]
                event_id = (
                    "jma_tsunami_jian_" + "|".join(stable_parts)
                    if stable_parts
                    else "jma_tsunami_jian_unknown"
                )

            issue_time = _parse_origin_time(msg_data.get("originTime"))
            source_entry = get_source_entry(self.source_id)

            raw_number = msg_data.get("number", 1)
            try:
                report_num = int(raw_number)
            except (TypeError, ValueError):
                report_num = 1

            fingerprint = build_jma_tsunami_content_fingerprint(
                event_id=event_id,
                cancelled=cancelled,
                max_grade=max_grade,
                areas=forecasts,
            )

            envelope = _build_jma_tsunami_envelope(
                source_id=self.source_id,
                source_entry=source_entry,
                data=msg_data,
                provider_family="jian_project",
                source_family="jian_project",
                message_type="jma-tsunami",
                event_id=event_id,
                issue_time=issue_time,
                title=title,
                max_grade=max_grade,
                forecasts=forecasts,
                metadata_extra={
                    "code": event_id,
                    "level": raw_level or max_grade,
                    "headline": str(msg_data.get("headline") or "").strip(),
                    "number": report_num,
                    "info_type_name": str(msg_data.get("infoTypeName") or "").strip(),
                    "cancel": cancelled,
                    "cleared": coerce_bool(msg_data.get("cleared"), default=False),
                    "org_unit": str(msg_data.get("orgUnit") or "").strip(),
                    "telegram": str(msg_data.get("telegram") or "").strip(),
                    "end_time": msg_data.get("endTime"),
                    "content_fingerprint": fingerprint,
                    "source_enum": source_entry.source_enum
                    if source_entry
                    else "jian_project_jma_tsunami",
                },
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


__all__ = [
    "CeaPrEewJianProjectParser",
    "SaEewJianProjectParser",
    "YunnanReportJianProjectParser",
    "ShanxiReportJianProjectParser",
    "BeijingReportJianProjectParser",
    "NingxiaReportJianProjectParser",
    "CwaReportJianProjectParser",
    "JmaReportJianProjectParser",
    "JmaLpgmJianProjectParser",
    "EmscReportJianProjectParser",
    "JmaTsunamiJianProjectParser",
]
