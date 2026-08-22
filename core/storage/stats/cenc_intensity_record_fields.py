"""
CENC 烈度速报落库字段辅助。

把烈度速报的概述、台站摘要等写入 subtitle / weather_detail，
便于重启后管理端回看，而不依赖运行时 metadata。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_models import EventEnvelope
from ...domain.event_payload import SourcePayload
from ..source_compat import is_cenc_intensity_report


def format_intensity_value(value: Any) -> str:
    """格式化烈度数值展示文本。"""
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if number == int(number):
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def truncate_text(text: str, *, limit: int = 480) -> str:
    """截断长文本，避免 weather_detail 过大。"""
    content = str(text or "").strip()
    if not content or len(content) <= limit:
        return content
    if limit <= 1:
        return content[:limit]
    return content[: max(0, limit - 1)].rstrip() + "…"


def build_station_summary(stations: Any, *, limit: int = 5) -> str:
    """把台站 TopN 压成单行摘要。"""
    if not isinstance(stations, list):
        return ""
    rows: list[str] = []
    for item in stations:
        if not isinstance(item, dict):
            continue
        name = (
            str(
                item.get("name")
                or item.get("stName")
                or item.get("stationName")
                or "未知台站"
            ).strip()
            or "未知台站"
        )
        intensity_text = format_intensity_value(
            item.get("intensity")
            if item.get("intensity") is not None
            else item.get("INT")
        )
        if intensity_text:
            rows.append(f"{name} {intensity_text}")
        else:
            rows.append(name)
        if len(rows) >= limit:
            break
    return "；".join(rows)


def apply_cenc_intensity_report_fields(
    record: dict[str, Any],
    event: EventEnvelope,
    *,
    earthquake_level: float | None,
    info_type: str,
    event_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
) -> None:
    """为 CENC 烈度速报补充 subtitle / weather_detail 等可落库摘要。"""
    source_id = str(
        event.source_id or record.get("source_id") or record.get("source") or ""
    ).strip()
    if not is_cenc_intensity_report(source_id, info_type=info_type):
        return

    payload = (
        event.payload.to_dict() if isinstance(event.payload, SourcePayload) else {}
    )
    payload_attributes = payload.get("attributes") if isinstance(payload, dict) else {}
    if not isinstance(payload_attributes, dict):
        payload_attributes = {}
    payload_raw = payload.get("raw") if isinstance(payload, dict) else {}
    if not isinstance(payload_raw, dict):
        payload_raw = {}

    def pick(*keys: str) -> Any:
        for key in keys:
            for source in (
                event_metadata,
                envelope_metadata,
                payload_attributes,
                payload_raw,
                payload if isinstance(payload, dict) else {},
            ):
                if not isinstance(source, dict) or key not in source:
                    continue
                value = source.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return value
        return None

    headline = str(
        pick("headline", "name_by_info", "nameByInfo")
        or getattr(event.event, "headline", "")
        or ""
    ).strip()
    intensity_info_text = str(pick("intensity_info_text") or "").strip()
    subject_codes = str(pick("subject_codes", "subjectCodes") or "").strip()
    station_count = pick("station_count")
    contour_feature_count = pick("contour_feature_count")
    max_instrument = pick("max_instrument_intensity")
    stations = pick("stations_topn", "stations", "instrument_intensity_json")
    has_contour = pick("has_contour_geojson")
    if has_contour is None:
        has_contour = isinstance(pick("contour_geojson"), dict)

    max_intensity = (
        earthquake_level
        if earthquake_level is not None
        else getattr(event.event, "intensity", None)
    )
    max_intensity_text = format_intensity_value(max_intensity)
    instrument_text = format_intensity_value(max_instrument)

    if headline:
        record["subtitle"] = headline
    elif max_intensity_text:
        record["subtitle"] = f"最高烈度 {max_intensity_text}"
    else:
        record["subtitle"] = "烈度速报"

    detail_parts: list[str] = ["烈度速报"]
    if max_intensity_text:
        detail_parts.append(f"最高烈度 {max_intensity_text}")
    if instrument_text and instrument_text != max_intensity_text:
        detail_parts.append(f"仪器最高 {instrument_text}")

    try:
        station_count_int = int(station_count) if station_count is not None else None
    except (TypeError, ValueError):
        station_count_int = None
    if station_count_int is None and isinstance(stations, list):
        station_count_int = len(stations)
    if station_count_int is not None:
        detail_parts.append(f"台站 {station_count_int}")

    try:
        contour_count_int = (
            int(contour_feature_count) if contour_feature_count is not None else None
        )
    except (TypeError, ValueError):
        contour_count_int = None
    if contour_count_int is not None:
        detail_parts.append(f"等震线 {contour_count_int}")
    elif has_contour:
        detail_parts.append("含等震线")

    if subject_codes:
        detail_parts.append(f"主题 {subject_codes}")

    station_summary = build_station_summary(stations, limit=5)
    if station_summary:
        detail_parts.append(f"台站Top：{station_summary}")

    if intensity_info_text:
        detail_parts.append("概述：" + truncate_text(intensity_info_text, limit=420))

    record["weather_detail"] = "；".join(detail_parts)

    description = str(record.get("description") or "").strip()
    if max_intensity_text:
        intensity_token = f"最高烈度{max_intensity_text}"
        if intensity_token not in description and "烈度" not in description:
            if description:
                record["description"] = f"{description} · {intensity_token}"
            else:
                place_name = str(
                    getattr(event.event, "place_name", "") or "未知地点"
                ).strip()
                magnitude = getattr(event.event, "magnitude", None)
                if magnitude is not None:
                    record["description"] = (
                        f"M{float(magnitude):.1f} {place_name} · {intensity_token}"
                    )
                else:
                    record["description"] = f"{place_name} · {intensity_token}"
