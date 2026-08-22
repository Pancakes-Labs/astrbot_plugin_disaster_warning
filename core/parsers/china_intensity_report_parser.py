"""
中国地震台网烈度速报解析器。
负责把 FAN Studio /cenc-ir 推送转换为统一领域事件。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser

# 从烈度概述文本中抽取最高烈度的常见表述。
_MAX_INTENSITY_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"最高烈度[为是]?\s*(\d+(?:\.\d+)?)\s*度?"),
    re.compile(r"推测最高烈度[为是]?\s*(\d+(?:\.\d+)?)\s*度?"),
    re.compile(r"烈度[为是]\s*(\d+(?:\.\d+)?)\s*度"),
)

# contour_geojson feature.properties 中可能出现的烈度字段名。
_CONTOUR_INTENSITY_KEYS: tuple[str, ...] = (
    "intensity",
    "INT",
    "int",
    "value",
    "level",
    "Intensity",
    "maxIntensity",
)


class CencIntensityReportParser(BaseParser):
    """中国地震台网烈度速报解析器（FAN Studio `/cenc-ir`）。"""

    def __init__(self, message_logger=None):
        super().__init__("cenc_ir_fanstudio", message_logger)

    @staticmethod
    def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
        """按优先级取第一个非 None 字段，保留合法 0 值。"""
        for key in keys:
            if key not in mapping:
                continue
            value = mapping.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _first_nonempty_str(mapping: dict[str, Any], *keys: str) -> str:
        """按优先级取第一个非空字符串字段。"""
        for key in keys:
            if key not in mapping:
                continue
            value = mapping.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @classmethod
    def _normalize_stations(cls, raw_stations: Any) -> list[dict[str, Any]]:
        """把台站仪器烈度数组规范化为内部结构，并按烈度降序。"""
        if not isinstance(raw_stations, list):
            return []

        stations: list[dict[str, Any]] = []
        for item in raw_stations:
            if not isinstance(item, dict):
                continue
            intensity = safe_float_convert(
                cls._first_present(item, "INT", "int", "intensity", "Intensity")
            )
            name = cls._first_nonempty_str(
                item, "stName", "stationName", "name", "stCode", "code"
            )
            lat = safe_float_convert(
                cls._first_present(item, "lat", "latitude", "stLat", "epiLat")
            )
            lon = safe_float_convert(
                cls._first_present(item, "lon", "longitude", "stLon", "epiLon")
            )
            stations.append(
                {
                    "name": name or "未知台站",
                    "intensity": intensity,
                    "lat": lat,
                    "lon": lon,
                    "raw": dict(item),
                }
            )

        stations.sort(
            key=lambda row: (
                row.get("intensity") is not None,
                float(row.get("intensity") or -1.0),
            ),
            reverse=True,
        )
        return stations

    @classmethod
    def _max_from_stations(cls, stations: list[dict[str, Any]]) -> float | None:
        """从规范化台站列表取最高仪器烈度。"""
        values = [
            float(item["intensity"])
            for item in stations
            if item.get("intensity") is not None
        ]
        if not values:
            return None
        return max(values)

    @staticmethod
    def _max_from_intensity_text(text: str) -> float | None:
        """从烈度概述文本解析最高烈度。"""
        content = str(text or "").strip()
        if not content:
            return None
        for pattern in _MAX_INTENSITY_TEXT_PATTERNS:
            match = pattern.search(content)
            if not match:
                continue
            value = safe_float_convert(match.group(1))
            if value is not None:
                return value
        return None

    @staticmethod
    def _max_from_contour(contour: Any) -> float | None:
        """从等震线 GeoJSON 属性中尝试提取最高烈度。"""
        if not isinstance(contour, dict):
            return None
        features = contour.get("features")
        if not isinstance(features, list):
            return None

        values: list[float] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties")
            if not isinstance(props, dict):
                continue
            for key in _CONTOUR_INTENSITY_KEYS:
                if key not in props:
                    continue
                value = safe_float_convert(props.get(key))
                if value is not None:
                    values.append(value)
                    break
        if not values:
            return None
        return max(values)

    @classmethod
    def _resolve_max_intensity(
        cls,
        *,
        stations: list[dict[str, Any]],
        intensity_info_text: str,
        contour_geojson: Any,
    ) -> float | None:
        """按优先级推导最高烈度：台站实测 > 文本 > 等震线属性。"""
        station_max = cls._max_from_stations(stations)
        if station_max is not None:
            return station_max
        text_max = cls._max_from_intensity_text(intensity_info_text)
        if text_max is not None:
            return text_max
        return cls._max_from_contour(contour_geojson)

    @staticmethod
    def _count_contour_features(contour: Any) -> int:
        """统计等震线 feature 数量。"""
        if not isinstance(contour, dict):
            return 0
        features = contour.get("features")
        if not isinstance(features, list):
            return 0
        return len(features)

    def _build_envelope(self, msg_data: dict[str, Any]) -> EventEnvelope:
        """把烈度速报原始字典封装为统一事件包裹体。"""
        magnitude = safe_float_convert(msg_data.get("magnitude"))
        if magnitude is not None:
            magnitude = round(magnitude, 1)

        depth = safe_float_convert(msg_data.get("focDepth") or msg_data.get("depth"))
        if depth is not None:
            depth = round(depth, 1)

        latitude = safe_float_convert(
            msg_data.get("epiLat") or msg_data.get("latitude")
        )
        longitude = safe_float_convert(
            msg_data.get("epiLon") or msg_data.get("longitude")
        )
        if latitude is not None:
            latitude = round(latitude, 4)
        if longitude is not None:
            longitude = round(longitude, 4)

        place_name = str(
            msg_data.get("locName") or msg_data.get("placeName") or ""
        ).strip()
        name_by_info = str(msg_data.get("nameByInfo") or "").strip()
        intensity_info_text = str(msg_data.get("intensity_info_text") or "").strip()
        subject_codes = str(msg_data.get("subjectCodes") or "").strip()
        uni_event_id = str(
            msg_data.get("uniEventId") or msg_data.get("eventId") or ""
        ).strip()
        source_record_id = str(msg_data.get("id") or "").strip()

        stations = self._normalize_stations(msg_data.get("instrument_intensity_json"))
        stations_topn = stations[:10]
        contour_geojson = msg_data.get("contour_geojson")
        max_intensity = self._resolve_max_intensity(
            stations=stations,
            intensity_info_text=intensity_info_text,
            contour_geojson=contour_geojson,
        )
        if max_intensity is not None:
            max_intensity = round(max_intensity, 1)

        source_entry = get_source_entry(self.source_id)
        published_at = self._parse_datetime(msg_data.get("gmtCreate", ""))
        occurred_at = self._parse_datetime(msg_data.get("oriTime", ""))

        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_info",
            "info_type": "烈度速报",
            "event_id": uni_event_id,
            "headline": name_by_info,
            "name_by_info": name_by_info,
            "subject_codes": subject_codes,
            # 产品策略：intensity_info_text 走全文，不做截断。
            "intensity_info_text": intensity_info_text,
            "published_at": published_at,
            "max_instrument_intensity": self._max_from_stations(stations),
            "station_count": len(stations),
            "stations": stations_topn,
            "stations_topn": stations_topn,
            "contour_feature_count": self._count_contour_features(contour_geojson),
            # 等震线体积可能很大，仅保留引用标记；完整对象仍在 payload.raw。
            "has_contour_geojson": isinstance(contour_geojson, dict),
        }

        domain_event = EarthquakeEvent(
            occurred_at=occurred_at,
            latitude=latitude,
            longitude=longitude,
            place_name=place_name or name_by_info or "未知地点",
            magnitude=magnitude,
            depth=depth,
            intensity=max_intensity,
            headline=name_by_info,
            metadata=dict(metadata),
        )

        identity = EventIdentity(
            event_id=uni_event_id
            or source_record_id
            or f"cenc_ir_{int(datetime.now(timezone.utc).timestamp())}",
            source_id=self.source_id,
            event_type="earthquake",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            published_at=published_at or occurred_at,
            aliases=tuple(item for item in (source_record_id, uni_event_id) if item),
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
        """解析中国地震台网烈度速报数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 烈度速报至少应具备事件唯一标识，并与正式测定字段集区分开。
            uni_event_id = str(msg_data.get("uniEventId") or "").strip()
            info_type_name = str(msg_data.get("infoTypeName") or "").strip()
            # 正式/自动测定报文即使混入速报字段名，也不按烈度速报处理。
            if "[正式测定]" in info_type_name or "[自动测定]" in info_type_name:
                return None

            has_report_body = any(
                key in msg_data
                for key in (
                    "intensity_info_text",
                    "instrument_intensity_json",
                    "contour_geojson",
                    "nameByInfo",
                )
            )
            if not uni_event_id or not has_report_body:
                # 非烈度速报数据为混流常态，不逐一记录
                return None

            envelope = self._build_envelope(msg_data)
            domain_event = envelope.event
            plugin_logger.info(
                f"[灾害预警] 烈度速报解析成功: {getattr(domain_event, 'place_name', '')} "
                f"(M {getattr(domain_event, 'magnitude', None)}, "
                f"Imax {getattr(domain_event, 'intensity', None)}), "
                f"时间: {getattr(domain_event, 'occurred_at', None)}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
