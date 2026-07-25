"""
统计记录工厂。
负责将事件转换为事件摘要记录，供 recent_pushes / major_events / 数据库存储共用。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...domain.event_payload import SourcePayload
from ...domain.tsunami.tsunami_levels import (
    build_tsunami_weather_detail,
    normalize_cn_tsunami_level,
    normalize_jp_tsunami_level,
    resolve_tsunami_region,
    to_optional_float,
)
from ...domain.tsunami.tsunami_title import (
    build_tsunami_list_title,
    is_generic_tsunami_title,
)
from ...domain.typhoon import (
    format_display_name,
    merge_peak_metrics,
    resolve_data_mode,
    to_float,
)
from ...services.identity.event_identity import resolve_report_num, resolve_source_id
from .cenc_intensity_record_fields import apply_cenc_intensity_report_fields


def _adapt_event_envelope(event: EventEnvelope) -> EventEnvelope:
    """统一获取领域 envelope。"""
    return event


def _resolve_weather_level(
    weather_event: WeatherEvent | None,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> str | None:
    """统一解析气象预警级别。"""
    # 先从领域事件自身元数据中取值，再逐层回退到统一元数据和原始载荷。
    event_metadata = (
        getattr(weather_event, "metadata", None)
        if isinstance(weather_event, WeatherEvent)
        else None
    )
    if not isinstance(event_metadata, dict):
        event_metadata = {}

    for source_dict, keys in (
        # 不同来源的级别字段命名并不一致，因此这里按一组候选键逐层兜底查找。
        (event_metadata, ["level", "alert_level", "alertLevel", "warningLevel"]),
        (metadata, ["level", "alert_level", "alertLevel", "warningLevel"]),
        (payload, ["alert_level", "alertLevel", "warningLevel", "level"]),
    ):
        for key in keys:
            value = source_dict.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    title_text = ""
    # 若结构化字段全部缺失，则退回到标题文本中按颜色关键词推断预警级别。
    if weather_event is not None:
        title_text = f"{weather_event.title or ''}{weather_event.headline or ''}"
    if not title_text:
        title_text = f"{metadata.get('title', '')}{metadata.get('headline', '')}"
    if not title_text:
        title_text = f"{payload.get('title', '')}{payload.get('headline', '')}"
    for color in ["红色", "橙色", "黄色", "蓝色", "白色"]:
        if color in title_text:
            return color
    return None


class EventRecordFactory:
    """事件记录工厂。"""

    @staticmethod
    def apply_common_fields(
        record: dict[str, Any],
        event: EventEnvelope,
        *,
        current_time: str,
        event_unique_id: str,
        description: str,
        source_id: str | None = None,
        update_count: int = 1,
    ) -> dict[str, Any]:
        """填充各类事件记录共享字段。"""
        envelope = _adapt_event_envelope(event)
        # 来源标识优先使用显式传入值，其次回退到事件自身来源或统一解析结果。
        resolved_source_id = source_id or envelope.source_id or resolve_source_id(event)
        event_id = envelope.id
        event_type = envelope.event_type
        record.update(
            {
                "timestamp": current_time,
                "event_id": event_id,
                "type": event_type,
                "source": resolved_source_id,
                "source_id": envelope.source_id or resolved_source_id,
                "description": description,
                "unique_id": event_unique_id,
                "update_count": update_count,
            }
        )
        return record

    @staticmethod
    def apply_earthquake_fields(
        record: dict[str, Any],
        event: EventEnvelope,
        *,
        earthquake_level: float | None = None,
    ) -> dict[str, Any]:
        """填充地震事件专有字段。"""
        envelope = _adapt_event_envelope(event)
        data = envelope.event
        if not isinstance(data, EarthquakeEvent):
            return record

        occurred_at = data.occurred_at.isoformat() if data.occurred_at else None
        # 地震记录除通用字段外，还需要补齐位置、震级、深度和报次相关信息。
        event_metadata = getattr(data, "metadata", None)
        if not isinstance(event_metadata, dict):
            event_metadata = {}
        envelope_metadata = (
            envelope.metadata if isinstance(envelope.metadata, dict) else {}
        )
        info_type = str(
            event_metadata.get("info_type") or envelope_metadata.get("info_type") or ""
        ).strip()
        record.update(
            {
                "latitude": data.latitude,
                "longitude": data.longitude,
                "place_name": data.place_name,
                "magnitude": data.magnitude,
                "depth": data.depth,
                "time": occurred_at,
                "real_event_id": envelope.id,
                "level": earthquake_level,
                "info_type": info_type,
            }
        )

        report_num = resolve_report_num(event)
        if report_num is not None:
            record["report_num"] = report_num

        apply_cenc_intensity_report_fields(
            record,
            event,
            earthquake_level=earthquake_level,
            info_type=info_type,
            event_metadata=event_metadata,
            envelope_metadata=envelope_metadata,
        )
        return record

    @staticmethod
    def apply_weather_fields(
        record: dict[str, Any],
        event: EventEnvelope,
    ) -> dict[str, Any]:
        """填充气象预警专有字段。"""
        envelope = _adapt_event_envelope(event)
        domain_event = envelope.event
        if not isinstance(domain_event, WeatherEvent):
            return record

        # 气象来源字段差异较大，因此需要同时查看载荷、统一元数据和领域事件对象。
        payload = (
            envelope.payload.to_dict()
            if isinstance(envelope.payload, SourcePayload)
            else {}
        )
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        description = (
            getattr(domain_event, "description", None)
            or metadata.get("description")
            or metadata.get("detail")
            or payload.get("description")
            or payload.get("detail")
            or ""
        )
        record.update(
            {
                "subtitle": domain_event.headline or "",
                "weather_detail": description,
                "time": domain_event.effective_at.isoformat()
                if domain_event.effective_at
                else None,
            }
        )
        event_metadata = (
            getattr(domain_event, "metadata", None)
            if isinstance(domain_event, WeatherEvent)
            else None
        )
        if not isinstance(event_metadata, dict):
            event_metadata = {}

        # 天气类型编码可能分散在多个来源字段中，这里统一做多层兜底提取。
        weather_type_code = (
            event_metadata.get("weather_code")
            or event_metadata.get("weather_type")
            or event_metadata.get("type")
            or event_metadata.get("alert_code")
            or event_metadata.get("alertCode")
            or event_metadata.get("code")
            or metadata.get("weather_code")
            or metadata.get("weather_type")
            or metadata.get("type")
            or metadata.get("alert_code")
            or metadata.get("alertCode")
            or metadata.get("code")
            or payload.get("weather_code")
            or payload.get("weather_type")
            or payload.get("type")
            or payload.get("alert_code")
            or payload.get("alertCode")
            or payload.get("code")
            or ""
        )
        if weather_type_code:
            record["weather_type_code"] = weather_type_code
        else:
            record.pop("weather_type_code", None)

        level = _resolve_weather_level(domain_event, payload, metadata)
        if level is not None:
            record["level"] = level
        else:
            record.pop("level", None)
        return record

    @staticmethod
    def _build_tsunami_forecast_highlights(
        forecasts: list[Any],
        *,
        region: str,
        limit: int = 6,
    ) -> list[str]:
        """从预报区列表提炼列表用亮点（对齐推送展示器语义）。"""
        highlights: list[str] = []
        ordered: list[dict[str, Any]] = []
        normal: list[dict[str, Any]] = []
        for item in forecasts or []:
            if not isinstance(item, dict):
                continue
            name = str(
                item.get("name")
                or item.get("forecastArea")
                or item.get("forecastPoint")
                or ""
            ).strip()
            if not name:
                continue
            if item.get("immediate"):
                ordered.append(item)
            else:
                normal.append(item)

        def grade_rank(item: dict[str, Any]) -> int:
            grade = str(item.get("grade") or item.get("warningLevel") or "").strip()
            order_map = {
                "MajorWarning": 5,
                "红色": 5,
                "Warning": 4,
                "橙色": 4,
                "Watch": 3,
                "黄色": 3,
                "蓝色": 2,
                "Minor": 1,
                "信息": 0,
            }
            return order_map.get(grade, 0)

        ordered.extend(sorted(normal, key=grade_rank, reverse=True))

        # 日本等级展示用中文，避免列表亮点出现裸 Warning/Watch
        jp_grade_labels = {
            "MajorWarning": "大海啸警报",
            "Warning": "海啸警报",
            "Watch": "海啸注意报",
            "Minor": "若干海面变动",
            "None": "无",
            "Unknown": "不明",
            "解除": "解除",
        }

        for item in ordered:
            if len(highlights) >= limit:
                break
            name = str(
                item.get("name")
                or item.get("forecastArea")
                or item.get("forecastPoint")
                or ""
            ).strip()
            grade = str(item.get("grade") or item.get("warningLevel") or "").strip()
            if region == "japan":
                grade_label = jp_grade_labels.get(grade, grade)
            else:
                grade_label = grade
            arrival = str(
                item.get("estimatedArrivalTime") or item.get("condition") or ""
            ).strip()
            wave = str(
                item.get("maxWaveHeight") or item.get("maxHeightDescription") or ""
            ).strip()
            bits: list[str] = [name]
            if grade_label:
                bits.append(f"[{grade_label}]")
            if item.get("immediate"):
                bits.append("立即")
            if arrival:
                bits.append(arrival)
            if wave:
                if region == "china" and not any(
                    token in wave for token in ("cm", "CM", "米", "m", "ｍ")
                ):
                    bits.append(f"波高{wave}cm")
                else:
                    bits.append(f"🌊{wave}")
            highlights.append(" ".join(bits))
        return highlights

    @staticmethod
    def _build_tsunami_station_highlights(
        stations: list[Any],
        *,
        limit: int = 4,
    ) -> list[str]:
        """从监测站列表提炼亮点。"""
        scored: list[tuple[float, str]] = []
        for item in stations or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("stationName") or item.get("name") or "监测站").strip()
            location = str(item.get("location") or "").strip()
            wave = str(
                item.get("maxWaveHeight") or item.get("max_wave_height") or ""
            ).strip()
            label = name
            if location:
                label = f"{name}({location})"
            if wave:
                if any(token in wave for token in ("cm", "CM", "米", "m", "ｍ")):
                    label = f"{label} 最大波幅 {wave}"
                else:
                    label = f"{label} 最大波幅 {wave}cm"
            value = to_optional_float(wave) or 0.0
            scored.append((value, label))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [label for _, label in scored[:limit]]

    @staticmethod
    def _merge_tsunami_payload_context(
        envelope: EventEnvelope, data: TsunamiEvent
    ) -> dict[str, Any]:
        """合并 payload.attributes / raw / 领域 metadata / envelope.metadata。"""
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        event_metadata = data.metadata if isinstance(data.metadata, dict) else {}
        # SourcePayload.to_dict() 只返回 raw，真正的 attributes 是 dataclass 字段。
        merged: dict[str, Any] = {}
        if isinstance(envelope.payload, SourcePayload):
            payload_attrs = getattr(envelope.payload, "attributes", None)
            if isinstance(payload_attrs, dict):
                merged.update(payload_attrs)
            raw_payload = envelope.payload.to_dict()
            if isinstance(raw_payload, dict):
                nested_attrs = raw_payload.get("attributes")
                if isinstance(nested_attrs, dict):
                    merged.update(nested_attrs)
                merged.update(raw_payload)
        elif isinstance(envelope.payload, dict):
            nested_attrs = envelope.payload.get("attributes")
            if isinstance(nested_attrs, dict):
                merged.update(nested_attrs)
            merged.update(envelope.payload)
        merged.update(event_metadata)
        merged.update(metadata)
        return merged

    @staticmethod
    def _absorb_tsunami_hypocenter(merged: dict[str, Any]) -> None:
        """把 issue_hypocenter 中的震中字段回填到 merged。"""
        hypocenter = merged.get("issue_hypocenter")
        if not isinstance(hypocenter, dict):
            return
        for key in ("latitude", "longitude", "magnitude", "place_name", "depth"):
            if merged.get(key) in (None, "") and hypocenter.get(key) not in (None, ""):
                merged[key] = hypocenter.get(key)
        if merged.get("place_name") in (None, ""):
            hypo_name = hypocenter.get("hypoCenterName") or hypocenter.get("place_name")
            if hypo_name not in (None, ""):
                merged["place_name"] = hypo_name
        if merged.get("magnitude") in (None, ""):
            mag = hypocenter.get("magnitude")
            if mag not in (None, ""):
                merged["magnitude"] = mag

    @staticmethod
    def _resolve_tsunami_place_fields(merged: dict[str, Any]) -> tuple[str, str]:
        """解析海啸 place_name / subtitle，过滤泛化标题。"""
        place_name = str(
            merged.get("place_name") or merged.get("subtitle") or ""
        ).strip()
        subtitle_raw = str(merged.get("subtitle") or "").strip()
        if subtitle_raw and not is_generic_tsunami_title(subtitle_raw):
            subtitle = subtitle_raw
        elif place_name and not is_generic_tsunami_title(place_name):
            subtitle = place_name
        else:
            subtitle = (
                place_name
                if place_name and not is_generic_tsunami_title(place_name)
                else ""
            )
            if is_generic_tsunami_title(place_name):
                place_name = ""
        if is_generic_tsunami_title(place_name):
            place_name = ""
        if not subtitle and place_name:
            subtitle = place_name
        return place_name, subtitle

    @staticmethod
    def apply_tsunami_fields(
        record: dict[str, Any],
        event: EventEnvelope,
    ) -> dict[str, Any]:
        """填充海啸事件专有字段。

        优先吸收 EQSC 丰富字段（震中、震级、区域摘要、最大波高、训练/解除标记），
        并兼容中国海啸与 P2P 结构。
        使用海啸专用列：max_wave_height / area_count / immediate_area_count /
        is_cancelled / is_training，不复用台风 wind_speed。
        """
        envelope = _adapt_event_envelope(event)
        data = envelope.event
        if not isinstance(data, TsunamiEvent):
            return record

        merged = EventRecordFactory._merge_tsunami_payload_context(envelope, data)
        EventRecordFactory._absorb_tsunami_hypocenter(merged)

        source_id = str(envelope.source_id or record.get("source_id") or "")
        region = resolve_tsunami_region(source_id, merged)
        raw_level = str(
            data.level or merged.get("level") or merged.get("max_grade") or ""
        ).strip()
        cancelled = bool(
            merged.get("cancelled")
            or raw_level in {"解除", "取消"}
            or "解除" in str(data.title or "")
        )
        is_training = bool(merged.get("is_training") or merged.get("isTraining"))

        if region == "japan":
            level = normalize_jp_tsunami_level(raw_level, cancelled=cancelled)
        elif region == "china":
            level = normalize_cn_tsunami_level(raw_level)
        else:
            level = raw_level

        place_name, subtitle = EventRecordFactory._resolve_tsunami_place_fields(merged)

        latitude = to_optional_float(merged.get("latitude"))
        longitude = to_optional_float(merged.get("longitude"))
        magnitude = to_optional_float(merged.get("magnitude"))
        depth = to_optional_float(merged.get("depth"))

        max_wave_height_value = to_optional_float(
            merged.get("max_wave_height_value")
            or merged.get("maxHeightValue")
            or merged.get("max_wave_height")
            or merged.get("maxWaveHeight")
        )
        max_wave_height_text = str(
            merged.get("max_wave_height") or merged.get("maxWaveHeight") or ""
        ).strip()
        if not max_wave_height_text and max_wave_height_value is not None:
            max_wave_height_text = f"{max_wave_height_value}m"
        max_wave_height_area = str(
            merged.get("max_wave_height_area") or merged.get("maxWaveHeightArea") or ""
        ).strip()

        forecasts_raw = merged.get("forecasts")
        forecasts = forecasts_raw if isinstance(forecasts_raw, list) else []

        area_count = merged.get("area_count")
        if area_count is None and forecasts:
            area_count = len(forecasts)
        try:
            area_count_int = int(area_count) if area_count is not None else None
        except (TypeError, ValueError):
            area_count_int = None

        immediate_area_count = merged.get("immediate_area_count")
        try:
            immediate_count_int = (
                int(immediate_area_count) if immediate_area_count is not None else None
            )
        except (TypeError, ValueError):
            immediate_count_int = None

        # 监测站：优先显式计数，再回退列表
        stations_raw = (
            merged.get("monitoring_stations")
            or merged.get("waterLevelMonitoring")
            or []
        )
        stations = stations_raw if isinstance(stations_raw, list) else []
        station_count = merged.get("station_count") or merged.get(
            "monitoring_station_count"
        )
        if station_count is None and stations:
            station_count = len(stations)
        try:
            station_count_int = (
                int(station_count) if station_count is not None else None
            )
        except (TypeError, ValueError):
            station_count_int = None

        # 级别分布（JP EQSC）
        grade_counts = merged.get("grade_counts")
        if not isinstance(grade_counts, dict):
            grade_counts = {}
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                grade_key = str(
                    item.get("grade") or item.get("warningLevel") or ""
                ).strip()
                if not grade_key:
                    continue
                grade_counts[grade_key] = grade_counts.get(grade_key, 0) + 1

        # 从 forecasts / stations 提炼亮点，写入 weather_detail 供列表解析
        # （列表 API 不回传完整 forecasts，靠摘要文本对齐 presenter 语义）
        forecast_highlights = EventRecordFactory._build_tsunami_forecast_highlights(
            forecasts,
            region=region,
            limit=6,
        )
        station_highlights = EventRecordFactory._build_tsunami_station_highlights(
            stations,
            limit=4,
        )

        # 若波高区缺失，从 forecasts 推断最高波高区
        if not max_wave_height_area and forecasts:
            best_area = ""
            best_value: float | None = None
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                name = str(
                    item.get("name")
                    or item.get("forecastArea")
                    or item.get("forecastPoint")
                    or ""
                ).strip()
                value = to_optional_float(
                    item.get("maxHeightValue")
                    or item.get("max_wave_height")
                    or item.get("maxWaveHeight")
                )
                if value is None:
                    continue
                if best_value is None or value > best_value:
                    best_value = value
                    best_area = name
                    if not max_wave_height_text:
                        desc = str(
                            item.get("maxHeightDescription")
                            or item.get("maxWaveHeight")
                            or ""
                        ).strip()
                        max_wave_height_text = desc or f"{value}m"
                        max_wave_height_value = value
            if best_area:
                max_wave_height_area = best_area

        if immediate_count_int is None and forecasts:
            immediate_count_int = (
                sum(
                    1
                    for item in forecasts
                    if isinstance(item, dict) and bool(item.get("immediate"))
                )
                or None
            )

        real_event_id = (
            str(
                merged.get("event_id") or merged.get("code") or envelope.id or ""
            ).strip()
            or None
        )

        info_parts: list[str] = []
        if region == "japan":
            info_parts.append("jma")
        elif region == "china":
            info_parts.append("cn")
        if cancelled or level == "解除":
            info_parts.append("cancelled")
        if is_training:
            info_parts.append("training")
        source_family = str(merged.get("source_family") or "").strip()
        if source_family:
            info_parts.append(source_family)
        info_type = "|".join(info_parts) if info_parts else None

        weather_detail = build_tsunami_weather_detail(
            region=region,
            level=level,
            area_count=area_count_int,
            immediate_area_count=immediate_count_int,
            max_wave_height=max_wave_height_text or None,
            max_wave_height_value=max_wave_height_value,
            max_wave_height_area=max_wave_height_area or None,
            station_count=station_count_int,
            cancelled=cancelled,
            is_training=is_training,
            batch=merged.get("batch"),
            magnitude=magnitude,
            depth=depth,
            place_name=place_name or None,
            grade_counts=grade_counts or None,
            forecast_highlights=forecast_highlights or None,
            station_highlights=station_highlights or None,
        )

        # 列表标题始终用结构化字段重建，覆盖旧的「标题 (等级)」模板
        record["description"] = build_tsunami_list_title(
            region=region,
            level=level or raw_level,
            title=str(data.title or "").strip(),
            place_name=place_name,
            magnitude=magnitude,
            batch=merged.get("batch"),
            cancelled=cancelled or level == "解除",
            is_training=is_training,
            max_wave_height=max_wave_height_text or None,
            area_count=area_count_int,
        )

        record.update(
            {
                "time": data.issued_at.isoformat() if data.issued_at else None,
                "level": level or raw_level or None,
                "subtitle": subtitle,
                "place_name": place_name or None,
                "weather_detail": weather_detail,
                "info_type": info_type,
                "real_event_id": real_event_id,
                "latitude": latitude,
                "longitude": longitude,
                "magnitude": magnitude,
                "depth": depth,
                "max_wave_height": max_wave_height_value,
                "area_count": area_count_int,
                "immediate_area_count": immediate_count_int,
                "is_cancelled": bool(cancelled or level == "解除"),
                "is_training": bool(is_training),
            }
        )
        # 首报默认 report_num=1；后续合并会在 merger 中递增
        if record.get("report_num") in (None, "", 0):
            record["report_num"] = 1
        return record

    @staticmethod
    def _resolve_typhoon_data_mode(event: EventEnvelope) -> str:
        """解析台风数据形态标签（thin wrapper）。

        只负责从事件中提取原始 mode 候选值；别名映射统一由
        domain.resolve_data_mode 处理，不在工厂内维护第二套规则。
        规范化字段已收敛到 TyphoonEvent + envelope.metadata，
        payload.attributes 不再作为第三份存储扫描。
        """
        envelope = _adapt_event_envelope(event)
        candidates: list[Any] = []

        # 优先读取流水线元数据（parser / enrichment 写入位置）
        envelope_metadata = (
            envelope.metadata if isinstance(envelope.metadata, dict) else {}
        )
        candidates.extend(
            [
                envelope_metadata.get("typhoon_data_mode"),
                envelope_metadata.get("info_type"),
                envelope_metadata.get("data_source"),
            ]
        )

        # 兼容旧数据：领域事件自身 metadata 可能残留 mode 标记
        data = envelope.event
        if isinstance(data, TyphoonEvent):
            event_metadata = getattr(data, "metadata", None)
            if isinstance(event_metadata, dict):
                candidates.extend(
                    [
                        event_metadata.get("typhoon_data_mode"),
                        event_metadata.get("info_type"),
                        event_metadata.get("data_source"),
                    ]
                )

        for raw in candidates:
            text = str(raw or "").strip()
            if text:
                return resolve_data_mode(text, default="fan")
        return "fan"

    @staticmethod
    def apply_typhoon_fields(
        record: dict[str, Any],
        event: EventEnvelope,
    ) -> dict[str, Any]:
        """填充台风事件专有字段。"""
        envelope = _adapt_event_envelope(event)
        data = envelope.event
        if not isinstance(data, TyphoonEvent):
            return record

        # 台风记录补齐中心位置、强度参数与更新时间，便于历史查询与展示。
        # 注意：magnitude 字段在数据库语义上专属于地震震级，台风不复用该列，
        # 为了避免前端震级筛选器误命中台风事件；全部强度参数统一存入 weather_detail。
        # info_type 复用为台风数据形态标签（fan / enriched / eqsc / eqsc_rebuild）。
        # source_id 保留事件自身来源（typhoon_fanstudio / typhoon_eqsc 等）。
        #
        # 关键：level / wind_speed 列必须保存历史峰值，而不是当前瞬时强度。
        # 否则台风减弱后（如巴威从强台风回落到热带风暴）会把风王榜与
        # by_max_level 统计所需的峰值字段覆盖掉，重启后统计重建就会丢榜。
        data_mode = EventRecordFactory._resolve_typhoon_data_mode(event)
        peak_level, peak_wind, min_pressure = merge_peak_metrics(
            existing_level=record.get("level"),
            existing_wind=record.get("wind_speed"),
            existing_pressure=record.get("pressure"),
            incoming_level=data.typhoon_type,
            incoming_wind=data.wind_speed,
            incoming_pressure=data.pressure,
        )
        # 主表 level/wind_speed/pressure 存峰值；额外保留当次观测，供 event_updates 快照使用。
        current_level = str(data.typhoon_type or "").strip()
        current_wind = to_float(data.wind_speed)
        if current_wind is not None and current_wind <= 0:
            current_wind = None
        current_pressure = to_float(data.pressure)
        if current_pressure is not None and current_pressure <= 0:
            current_pressure = None
        record.update(
            {
                "real_event_id": data.typhoon_id,
                "latitude": data.latitude,
                "longitude": data.longitude,
                "level": peak_level,
                "wind_speed": peak_wind,
                "pressure": min_pressure,
                "time": data.updated_at.isoformat() if data.updated_at else None,
                "place_name": f"{data.name or data.name_en or data.typhoon_id}",
                "info_type": data_mode,
                "_snapshot_level": current_level,
                "_snapshot_wind_speed": current_wind,
                "_snapshot_pressure": current_pressure,
            }
        )
        # weather_detail 仅用于详情展示；统计峰值以独立列 level/wind_speed/pressure 为准。
        # 同时写入活跃态标记，供事件列表“仅活跃台风”筛选复用。
        detail_parts: list[str] = []
        detail_parts.append("状态 活跃" if bool(data.is_active) else "状态 停编")
        if min_pressure is not None:
            pressure_text = (
                int(min_pressure) if float(min_pressure).is_integer() else min_pressure
            )
            detail_parts.append(f"气压 {pressure_text} hPa")
        if peak_wind is not None:
            wind_text = int(peak_wind) if float(peak_wind).is_integer() else peak_wind
            detail_parts.append(f"风速 {wind_text} m/s")
        if data.power is not None:
            detail_parts.append(f"风力 {data.power} 级")
        if data.move_direction:
            detail_parts.append(f"移向 {data.move_direction}")
        if data.move_speed is not None:
            detail_parts.append(f"移速 {data.move_speed} km/h")
        if data.radius7 is not None:
            detail_parts.append(f"七级风圈 {data.radius7} km")
        if data.radius10 is not None:
            detail_parts.append(f"十级风圈 {data.radius10} km")
        record["weather_detail"] = "，".join(detail_parts)
        # 台风名称作为副标题展示；统一走领域展示名格式化，避免中英文拼接双实现。
        record["subtitle"] = format_display_name(
            data.name,
            data.name_en,
            data.typhoon_id,
            fallback=str(data.typhoon_id or "未知台风"),
        )
        # description 同步为可读标题，避免前端回退到“未知事件”
        if not record.get("description"):
            record["description"] = record["subtitle"]
        return record

    @staticmethod
    def build_base_record(
        event: EventEnvelope,
        *,
        current_time: str,
        event_unique_id: str,
        description: str,
        earthquake_level: float | None = None,
    ) -> dict[str, Any]:
        """构建基础统计记录。"""
        envelope = _adapt_event_envelope(event)
        source_id = envelope.source_id or resolve_source_id(event)
        record: dict[str, Any] = {
            "subtitle": "",
            "weather_detail": "",
        }
        # 先填充全部事件共享字段，再按事件类型补专有字段。
        EventRecordFactory.apply_common_fields(
            record,
            event,
            current_time=current_time,
            event_unique_id=event_unique_id,
            description=description,
            source_id=source_id,
            update_count=1,
        )

        if isinstance(envelope.event, EarthquakeEvent):
            # 地震事件需要补齐震级、深度、位置和报次等摘要字段。
            EventRecordFactory.apply_earthquake_fields(
                record,
                event,
                earthquake_level=earthquake_level,
            )
        elif isinstance(envelope.event, WeatherEvent):
            # 气象事件重点补充副标题、详细说明、颜色级别和类型编码。
            EventRecordFactory.apply_weather_fields(record, event)
        elif isinstance(envelope.event, TsunamiEvent):
            # 海啸事件补充发布时间、等级、震中与区域摘要（优先 EQSC 字段）。
            EventRecordFactory.apply_tsunami_fields(record, event)
        elif isinstance(envelope.event, TyphoonEvent):
            # 台风事件补充中心位置、强度参数与更新时间。
            EventRecordFactory.apply_typhoon_fields(record, event)

        return record
