"""
统计事件辅助服务。
负责事件描述、地区提取和地震展示级别解析，
减少 StatisticsManager 中残留的领域辅助逻辑。
"""

from __future__ import annotations

from typing import Any

from ....utils.converters import ScaleConverter
from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...domain.event_payload import SourcePayload
from ...domain.tsunami.tsunami_levels import (
    resolve_tsunami_region,
    to_optional_float,
)
from ...domain.tsunami.tsunami_title import build_tsunami_list_title
from ..source_compat import is_cenc_intensity_report, is_fssn_cmt_report

CHINA_PROVINCES = [
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "台湾",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
]


class StatsEventSupportService:
    """统计事件辅助服务。"""

    def __init__(self, manager):
        self.manager = manager

    def extract_region(self, text: str, strict: bool = False) -> str | None:
        """从文本中提取地区（省份）信息。"""
        if not text:
            return None if strict else "未知"

        for province in CHINA_PROVINCES:
            if text.startswith(province):
                return province

        if strict:
            return None

        return text[:2]

    def get_earthquake_level(self, data) -> float | None:
        """提取可展示的地震震度值（优先 scale / max_scale / intensity）。"""
        candidates = [getattr(data, "scale", None), getattr(data, "intensity", None)]
        max_scale = getattr(data, "max_scale", None)
        if max_scale is not None:
            candidates.insert(1, max_scale)

        for candidate in candidates:
            if candidate is None:
                continue
            if isinstance(candidate, (int, float)):
                return float(candidate)
            parsed = ScaleConverter.parse_jma_cwa_scale(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _merge_tsunami_context(envelope: EventEnvelope) -> dict[str, Any]:
        """合并海啸 envelope 各层元数据，供列表标题构建。"""
        domain_event = envelope.event
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        event_metadata = (
            domain_event.metadata
            if isinstance(getattr(domain_event, "metadata", None), dict)
            else {}
        )
        merged: dict[str, Any] = {}
        payload = envelope.payload
        if isinstance(payload, SourcePayload):
            payload_dict = payload.to_dict()
            attributes = payload_dict.get("attributes")
            if isinstance(attributes, dict):
                merged.update(attributes)
            merged.update(payload_dict)
        elif isinstance(payload, dict):
            attributes = payload.get("attributes")
            if isinstance(attributes, dict):
                merged.update(attributes)
            merged.update(payload)
        merged.update(event_metadata)
        merged.update(metadata)

        hypocenter = merged.get("issue_hypocenter")
        if isinstance(hypocenter, dict):
            for key in ("place_name", "magnitude", "latitude", "longitude", "depth"):
                if merged.get(key) in (None, "") and hypocenter.get(key) not in (
                    None,
                    "",
                ):
                    merged[key] = hypocenter.get(key)
            # EQSC 原始键名兼容
            if merged.get("place_name") in (None, ""):
                hypo_name = hypocenter.get("hypoCenterName") or hypocenter.get(
                    "place_name"
                )
                if hypo_name not in (None, ""):
                    merged["place_name"] = hypo_name
            if merged.get("magnitude") in (None, ""):
                mag = hypocenter.get("magnitude")
                if mag not in (None, ""):
                    merged["magnitude"] = mag
        return merged

    def get_event_description_from_envelope(self, envelope: EventEnvelope) -> str:
        """基于新领域包络生成简短事件描述。"""
        domain_event = envelope.event
        if isinstance(domain_event, EarthquakeEvent):
            place_name = domain_event.place_name or "未知地点"
            if domain_event.magnitude is None:
                base = (
                    "震源参数调查中"
                    if place_name in ["未知地点", "未知位置"]
                    else place_name
                )
            else:
                base = f"M{domain_event.magnitude:.1f} {place_name}"

            event_metadata = getattr(domain_event, "metadata", None)
            if not isinstance(event_metadata, dict):
                event_metadata = {}
            envelope_metadata = (
                envelope.metadata if isinstance(envelope.metadata, dict) else {}
            )
            info_type = str(
                event_metadata.get("info_type")
                or envelope_metadata.get("info_type")
                or ""
            ).strip()

            if is_fssn_cmt_report(envelope.source_id or "", info_type=info_type):
                # CMT 列表展示：震级 · 震中 矩心深xx
                cmt_depth = event_metadata.get(
                    "centroid_depth"
                ) or envelope_metadata.get("centroid_depth")
                if cmt_depth is not None:
                    try:
                        cmt_depth_num = float(cmt_depth)
                        return f"{base} · 矩心深{cmt_depth_num:.0f}km"
                    except (TypeError, ValueError):
                        pass
                return base

            if is_cenc_intensity_report(envelope.source_id or "", info_type=info_type):
                intensity = domain_event.intensity
                if intensity is None:
                    intensity = event_metadata.get(
                        "max_instrument_intensity"
                    ) or envelope_metadata.get("max_instrument_intensity")
                if intensity is not None:
                    try:
                        intensity_num = float(intensity)
                        if intensity_num == int(intensity_num):
                            intensity_text = str(int(intensity_num))
                        else:
                            intensity_text = f"{intensity_num:.1f}".rstrip("0").rstrip(
                                "."
                            )
                    except (TypeError, ValueError):
                        intensity_text = str(intensity).strip()
                    if intensity_text:
                        return f"{base} · 最高烈度{intensity_text}"
            return base

        if isinstance(domain_event, TsunamiEvent):
            # 列表标题：级别 · 震中 震级（字段缺失时优雅降级）
            merged = self._merge_tsunami_context(envelope)
            region = resolve_tsunami_region(envelope.source_id, merged)
            place_name = str(
                merged.get("place_name") or merged.get("subtitle") or ""
            ).strip()
            magnitude = to_optional_float(merged.get("magnitude"))
            cancelled = bool(
                merged.get("cancelled")
                or merged.get("is_cancelled")
                or domain_event.level == "解除"
                or "解除" in str(domain_event.title or "")
            )
            is_training = bool(merged.get("is_training") or merged.get("isTraining"))
            max_wave = str(
                merged.get("max_wave_height") or merged.get("maxWaveHeight") or ""
            ).strip()
            area_count = merged.get("area_count")
            if area_count is None:
                forecasts = merged.get("forecasts")
                if isinstance(forecasts, list):
                    area_count = len(forecasts)
            try:
                area_count_int = int(area_count) if area_count is not None else None
            except (TypeError, ValueError):
                area_count_int = None
            return build_tsunami_list_title(
                region=region,
                level=str(domain_event.level or merged.get("level") or "").strip(),
                title=str(domain_event.title or "").strip(),
                place_name=place_name,
                magnitude=magnitude,
                batch=merged.get("batch"),
                cancelled=cancelled,
                is_training=is_training,
                max_wave_height=max_wave or None,
                area_count=area_count_int,
            )

        if isinstance(domain_event, TyphoonEvent):
            # 台风描述包含名称与当前等级，便于在统计列表中快速识别。
            name_label = (
                domain_event.name or domain_event.name_en or domain_event.typhoon_id
            )
            type_label = domain_event.typhoon_type or "台风"
            return f"{type_label} {name_label}"

        if isinstance(domain_event, WeatherEvent):
            return f"{domain_event.title or domain_event.headline}"

        return "未知事件"

    def get_event_description(self, event: EventEnvelope) -> str:
        """生成简短的事件描述。"""
        return self.get_event_description_from_envelope(event)
