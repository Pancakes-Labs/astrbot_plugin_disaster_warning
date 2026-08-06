"""
EQSC CENC 烈度速报解析器。

把 /intensityReportCENC.json 详情适配为与 FAN /cenc-ir 同形的中间结构，
再复用 CencIntensityReportParser 的 envelope 构建与最高烈度推导逻辑。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..sources.source_catalog import get_source_entry
from .china_intensity_report_parser import CencIntensityReportParser


class CencIntensityReportEqscParser(CencIntensityReportParser):
    """中国地震台网烈度速报解析器（EQSC HTTP 详情）。"""

    def __init__(self, message_logger=None):
        # 复用 FAN 解析器实现，仅切换 source_id / provider 语义
        super().__init__(message_logger)
        self.source_id = "cenc_ir_eqsc"
        self.source_entry = get_source_entry(self.source_id)
        self.source_config = self.source_entry

    @staticmethod
    def _clean_text(value: Any) -> str:
        """清洗 EQSC 常见空值字符串。"""
        if value is None:
            return ""
        text = str(value).strip()
        if not text or text.lower() in {"null", "none"}:
            return ""
        return text

    @classmethod
    def _normalize_event_id(cls, value: Any) -> str:
        """统一 eventID 为字符串。"""
        if value is None:
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = cls._clean_text(value)
        if text.endswith(".0") and text[:-2].isdigit():
            return text[:-2]
        return text

    @classmethod
    def _parse_contour(cls, raw: Any) -> Any:
        """解析 predictedIntensityContourline（可能是 JSON 字符串或对象）。"""
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        text = cls._clean_text(raw)
        if not text or text in {".", "......", "..."}:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _adapt_stations(cls, raw_stations: Any) -> list[dict[str, Any]]:
        """把 EQSC stations 适配为 FAN instrument_intensity_json 近似结构。"""
        if not isinstance(raw_stations, list):
            return []

        adapted: list[dict[str, Any]] = []
        for item in raw_stations:
            if not isinstance(item, dict):
                continue
            station_info = item.get("stationInfo")
            if not isinstance(station_info, dict):
                station_info = {}

            name = (
                cls._clean_text(station_info.get("name"))
                or cls._clean_text(station_info.get("id"))
                or cls._clean_text(station_info.get("town"))
                or cls._clean_text(station_info.get("county"))
                or "未知台站"
            )
            # 优先展示「县+镇」更可读
            county = cls._clean_text(station_info.get("county"))
            town = cls._clean_text(station_info.get("town"))
            if (
                county
                and town
                and name in {station_info.get("name"), station_info.get("id"), ""}
            ):
                name = f"{county}{town}"
            elif county and name in {
                station_info.get("id"),
                station_info.get("name"),
                "",
            }:
                name = county

            intensity = safe_float_convert(
                item.get("intensity")
                if item.get("intensity") is not None
                else item.get("INT")
            )
            lat = safe_float_convert(
                item.get("latitude")
                if item.get("latitude") is not None
                else item.get("lat")
            )
            lon = safe_float_convert(
                item.get("longitude")
                if item.get("longitude") is not None
                else item.get("lon")
            )
            adapted.append(
                {
                    "stName": name,
                    "name": name,
                    "INT": intensity,
                    "intensity": intensity,
                    "lat": lat,
                    "lon": lon,
                    "latitude": lat,
                    "longitude": lon,
                    "distance": item.get("distance"),
                    "network": station_info.get("network"),
                    "station_id": station_info.get("id"),
                    "station_type": station_info.get("type"),
                    "province": station_info.get("province"),
                    "city": station_info.get("city"),
                    "county": county,
                    "town": town,
                    "pga": item.get("pga"),
                    "pgv": item.get("pgv"),
                    "raw_eqsc": dict(item),
                }
            )
        return adapted

    @classmethod
    def adapt_eqsc_detail_to_fan_shape(
        cls, detail: dict[str, Any]
    ) -> dict[str, Any] | None:
        """将 EQSC 详情映射为 FAN /cenc-ir 同形字典，供共享 envelope 构建。"""
        if not isinstance(detail, dict) or not detail:
            return None

        event_info = detail.get("eventInfo")
        if not isinstance(event_info, dict):
            event_info = {}
        intensity_report = detail.get("intensityReport")
        if not isinstance(intensity_report, dict):
            intensity_report = {}

        event_id = cls._normalize_event_id(
            event_info.get("eventID")
            or event_info.get("eventId")
            or detail.get("eventID")
            or detail.get("eventId")
            or detail.get("id")
        )
        if not event_id:
            return None

        place_name = cls._clean_text(
            event_info.get("placeName") or event_info.get("locName")
        )
        magnitude = safe_float_convert(event_info.get("magnitude"))
        depth = safe_float_convert(
            event_info.get("depth") or event_info.get("focDepth")
        )
        latitude = safe_float_convert(
            event_info.get("latitude") or event_info.get("epiLat")
        )
        longitude = safe_float_convert(
            event_info.get("longitude") or event_info.get("epiLon")
        )
        shock_time = cls._clean_text(
            event_info.get("shockTime") or event_info.get("oriTime")
        )
        info_text = cls._clean_text(
            intensity_report.get("info") or intensity_report.get("intensity_info_text")
        )
        stations = cls._adapt_stations(intensity_report.get("stations"))
        contour = cls._parse_contour(
            intensity_report.get("predictedIntensityContourline")
            or intensity_report.get("contour_geojson")
        )

        mag_text = ""
        if magnitude is not None:
            mag_text = f"{magnitude:.1f}".rstrip("0").rstrip(".")
        name_by_info = ""
        if place_name and mag_text:
            name_by_info = f"{place_name}{mag_text}级地震"
        elif place_name:
            name_by_info = place_name

        return {
            # FAN 同形字段
            "uniEventId": event_id,
            "id": event_id,
            "eventId": event_id,
            "oriTime": shock_time,
            "locName": place_name,
            "placeName": place_name,
            "epiLat": latitude,
            "epiLon": longitude,
            "latitude": latitude,
            "longitude": longitude,
            "focDepth": depth,
            "depth": depth,
            "magnitude": magnitude,
            "nameByInfo": name_by_info,
            "intensity_info_text": info_text,
            "instrument_intensity_json": stations,
            "contour_geojson": contour,
            "subjectCodes": "intensity-report",
            "infoTypeName": "烈度速报",
            "type": "update",
            # EQSC 溯源
            "provider_family_hint": "eqsc",
            "eqsc_raw_event_info": dict(event_info),
        }

    def _build_envelope(self, msg_data: dict[str, Any]):
        """构建 envelope，并覆盖 EQSC 的 source_family 元数据。"""
        envelope = super()._build_envelope(msg_data)
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        metadata = dict(metadata)
        metadata["source_family"] = "eqsc"
        metadata["provider_family"] = "eqsc"
        metadata["info_type"] = "烈度速报"
        # 跨源软去重辅助字段
        metadata["cenc_ir_cross_source"] = True
        if envelope.event is not None and hasattr(envelope.event, "metadata"):
            event_meta = (
                dict(envelope.event.metadata)
                if isinstance(envelope.event.metadata, dict)
                else {}
            )
            event_meta.update(metadata)
            envelope.event.metadata = event_meta
        envelope.metadata = metadata
        if envelope.payload is not None and hasattr(envelope.payload, "attributes"):
            attrs = (
                dict(envelope.payload.attributes)
                if isinstance(envelope.payload.attributes, dict)
                else {}
            )
            attrs.update(metadata)
            envelope.payload.attributes = attrs
            # provider_family / source_id 以 catalog 与本解析器为准
            envelope.payload.source_id = self.source_id
            if self.source_entry is not None:
                envelope.payload.provider_family = (
                    self.source_entry.provider_family.value
                )
        # EventIdentity 为 frozen dataclass，必须用 replace 重建，不能原地赋值
        if envelope.identity is not None:
            identity_kwargs: dict[str, Any] = {"source_id": self.source_id}
            if self.source_entry is not None:
                identity_kwargs["provider_family"] = (
                    self.source_entry.provider_family.value
                )
                identity_kwargs["source_enum"] = self.source_entry.source_enum
            envelope.identity = replace(envelope.identity, **identity_kwargs)
        return envelope

    def _parse_data(self, data: dict[str, Any]):
        """解析 EQSC CENC 烈度速报详情。"""
        try:
            raw = self._extract_data(data) if isinstance(data, dict) else None
            if not isinstance(raw, dict):
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 已是 FAN 同形（例如测试/转发）则直接走父类路径
            if raw.get("uniEventId") and (
                "intensity_info_text" in raw
                or "instrument_intensity_json" in raw
                or "contour_geojson" in raw
            ):
                adapted = raw
            else:
                adapted = self.adapt_eqsc_detail_to_fan_shape(raw)
            if not adapted:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 EQSC CENC 烈度速报详情，跳过"
                )
                return None

            envelope = self._build_envelope(adapted)
            domain_event = envelope.event
            plugin_logger.info(
                f"[灾害预警] EQSC 烈度速报解析成功: "
                f"{getattr(domain_event, 'place_name', '')} "
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


__all__ = ["CencIntensityReportEqscParser"]
