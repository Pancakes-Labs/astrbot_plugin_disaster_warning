"""
海啸解析器。
负责把中国海啸预警与日本海啸预报数据统一转换为领域事件。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...utils.plugin_logger import plugin_logger
from ...utils.time_converter import TimeConverter
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EventEnvelope, TsunamiEvent
from ..domain.event_payload import SourcePayload
from ..domain.tsunami.jma_tsunami_normalize import (
    build_jma_tsunami_content_fingerprint,
    coerce_bool,
    normalize_jma_tsunami_areas,
    resolve_jma_tsunami_max_grade,
    resolve_jma_tsunami_title,
)
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class TsunamiParser(BaseParser):
    """中国海啸预警解析器。"""

    def __init__(self, message_logger=None):
        """初始化中国海啸预警解析器。"""
        super().__init__("china_tsunami_fanstudio", message_logger)

    def _build_envelope(self, tsunami_data: dict[str, Any]) -> EventEnvelope | None:
        """把海啸原始字典封装为统一事件包裹体。"""
        # 兼容各不同子属性块
        warning_info = tsunami_data.get("warningInfo", {}) or {}
        time_info = tsunami_data.get("timeInfo", {}) or {}
        default_shock_info = tsunami_data.get("shockInfo")
        # 兼容无嵌套属性的直接取值
        shock_info = default_shock_info if isinstance(default_shock_info, dict) else {}
        details = tsunami_data.get("details", {}) or {}

        # 兼容三种不同的发震、更新与发布时间键名
        issue_time_str = (
            time_info.get("alarmDate")
            or time_info.get("issueTime")
            or time_info.get("publishTime")
            or time_info.get("updateDate")
            or ""
        )
        update_time_str = time_info.get("updateDate") or ""
        shock_time_str = shock_info.get("shockTime") or ""

        # 解析时间字符串为日期对象
        issue_time = (
            self._parse_datetime(issue_time_str)
            if issue_time_str
            else datetime.now(timezone.utc)
        )
        update_time = self._parse_datetime(update_time_str) if update_time_str else None
        shock_time = self._parse_datetime(shock_time_str) if shock_time_str else None

        level = (warning_info.get("level") or tsunami_data.get("level") or "").strip()
        title = (warning_info.get("title") or tsunami_data.get("title") or "").strip()

        # 根据海啸警报级别自动归纳缺省的展示标题
        if not title and level:
            if level == "信息":
                title = "海啸信息"
            elif level == "解除":
                title = "海啸解除通告"
            else:
                title = f"海啸{level}警报"

        # 若依然缺失标题，视为无法解析，打印节流警告后返回 None
        if not title:
            warning_msg = f"[灾害预警] {self.source_id} 海啸消息缺少标题，跳过处理"
            if self._should_log_warning("missing_tsunami_title", warning_msg):
                plugin_logger.debug(warning_msg)
            return None

        # 收集预报地区列表、海啸波位水位监测站及分布图附件
        forecasts = tsunami_data.get("forecasts", []) or []
        monitoring_stations = (
            tsunami_data.get("waterLevelMonitoring")
            or tsunami_data.get("monitoringStations")
            or []
        )
        maps = details.get("maps", {}) or {}

        event_id = str(tsunami_data.get("id", "") or "").strip()
        # 缺失 Event ID 时根据海啸报文特征（编号、批次、发布时间等）拼合出回退 ID
        if not event_id:
            stable_parts = [
                str(tsunami_data.get("code", "") or "").strip(),
                str(details.get("batch") or tsunami_data.get("batch") or "").strip(),
                str(title or "").strip(),
                str(issue_time_str or "").strip(),
            ]
            stable_parts = [part for part in stable_parts if part]
            event_id = (
                "tsunami_" + "|".join(stable_parts)
                if stable_parts
                else "tsunami_unknown"
            )
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 海啸消息缺少稳定id，已使用回退事件ID: {event_id}"
            )

        # 整理副标题与发布机构名称
        subtitle = (
            warning_info.get("subtitle")
            or warning_info.get("caption")
            or shock_info.get("placeName")
            or tsunami_data.get("placeName")
            or ""
        )
        org_unit = (
            warning_info.get("orgUnit")
            or tsunami_data.get("publishInfo", {}).get("unitName")
            or "自然资源部海啸预警中心"
        )

        normalized_level = level.replace("级", "") if level else ""
        message_type = "info"
        if normalized_level and normalized_level not in {"信息"}:
            message_type = "warning"
        if "警报" in title or "预警" in title:
            message_type = "warning"

        source_entry = get_source_entry(self.source_id)
        metadata = {
            "code": tsunami_data.get("code", ""),
            "subtitle": subtitle,
            "org_unit": org_unit,
            "update_time": update_time,
            "shock_time": shock_time,
            "message_type": message_type,
            "place_name": shock_info.get("placeName") or tsunami_data.get("placeName"),
            "latitude": shock_info.get("latitude") or tsunami_data.get("latitude"),
            "longitude": shock_info.get("longitude") or tsunami_data.get("longitude"),
            "depth": shock_info.get("depth") or tsunami_data.get("depth"),
            "magnitude": shock_info.get("magnitude") or tsunami_data.get("magnitude"),
            "batch": details.get("batch") or tsunami_data.get("batch"),
            "forecasts": forecasts,
            "monitoring_stations": monitoring_stations,
            "estimated_arrival_time": tsunami_data.get("estimatedArrivalTime"),
            "max_wave_height": tsunami_data.get("maxWaveHeight"),
            "details_url": details.get("htmlUrl") or tsunami_data.get("htmlUrl"),
            "map_urls": {
                "earthquake": maps.get("earthquakeMapUrl", ""),
                "amplitude": maps.get("amplitudeMapUrl", ""),
                "coastal": maps.get("coastalMapUrl", ""),
            },
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "tsunami",
        }

        # 实例化海啸领域模型
        domain_event = TsunamiEvent(
            title=title,
            level=level,
            issued_at=issue_time,
            metadata=dict(metadata),
        )

        # 构造事件身份对象
        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="tsunami",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            published_at=issue_time,
            aliases=tuple(
                item
                for item in (str(tsunami_data.get("id", "") or "").strip(),)
                if item
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
                message_type=str(tsunami_data.get("type") or message_type).strip(),
                raw=dict(tsunami_data),
                attributes=dict(metadata),
            ),
            metadata=metadata,
        )

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析中国海啸预警数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            if self._is_heartbeat_message(msg_data):
                return None

            # 支持列表和单对象两种封装形态
            events = []
            if isinstance(msg_data, dict):
                events = [msg_data]
            elif isinstance(msg_data, list):
                events = msg_data

            if not events:
                return None

            envelope = self._build_envelope(events[0])
            if envelope is None:
                return None

            plugin_logger.info(
                f"[灾害预警] 海啸预警解析成功: {getattr(envelope.event, 'title', '')} ({getattr(envelope.event, 'level', '')}), 发布时间: {getattr(envelope.event, 'issued_at', None)}",
                is_event_linked=True,
                event_stream="tsunami",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析海啸预警数据失败: {exc}, 数据内容: {data}"
            )
            return None


def _ensure_jst_datetime(dt: datetime | None) -> datetime | None:
    """无时区时间按 JST 解释，已有时区则保留。"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=TimeConverter.TIMEZONES["JST"])


def _build_jma_tsunami_envelope(
    *,
    source_id: str,
    source_entry,
    data: dict[str, Any],
    provider_family: str,
    source_family: str,
    message_type: str,
    event_id: str,
    issue_time: datetime | None,
    title: str,
    max_grade: str,
    forecasts: list[dict[str, Any]],
    metadata_extra: dict[str, Any] | None = None,
) -> EventEnvelope:
    """构造统一的 JMA 海啸 EventEnvelope。"""
    metadata: dict[str, Any] = {
        "source_family": source_family,
        "source_enum": source_entry.source_enum if source_entry else "",
        "source_type": source_entry.source_type.value if source_entry else "tsunami",
        "org_unit": "日本气象厅",
        "forecasts": forecasts,
        "level": max_grade,
        "message_type": "info"
        if max_grade in {"Minor", "解除", "None", "Unknown"}
        else "warning",
        "content_fingerprint": build_jma_tsunami_content_fingerprint(
            event_id=event_id,
            cancelled=max_grade == "解除",
            max_grade=max_grade,
            areas=forecasts,
            is_training=bool((metadata_extra or {}).get("is_training")),
        ),
    }
    if metadata_extra:
        metadata.update(metadata_extra)

    domain_event = TsunamiEvent(
        title=title,
        level=max_grade,
        issued_at=issue_time,
        metadata=dict(metadata),
    )
    identity = EventIdentity(
        event_id=event_id,
        source_id=source_id,
        event_type="tsunami",
        provider_family=source_entry.provider_family.value
        if source_entry
        else provider_family,
        source_enum=source_entry.source_enum if source_entry else "",
        published_at=issue_time,
        attributes={
            "parser_name": source_entry.parser_name if source_entry else "",
            "config_key": source_entry.config_key if source_entry else "",
        },
    )
    return EventEnvelope(
        identity=identity,
        event=domain_event,
        received_at=datetime.now(timezone.utc),
        payload=SourcePayload(
            source_id=source_id,
            provider_family=source_entry.provider_family.value
            if source_entry
            else provider_family,
            message_type=message_type,
            raw=dict(data),
            attributes=dict(metadata),
        ),
        metadata=metadata,
    )


class JmaTsunamiP2PParser(BaseParser):
    """日本气象厅海啸预报解析器，处理 P2P 来源数据。"""

    def __init__(self, message_logger=None):
        """初始化 P2P 日本海啸预报解析器。"""
        super().__init__("jma_tsunami_p2p", message_logger)

    def parse_message(self, message: str) -> EventEnvelope | None:
        """解析 P2P 海啸消息。"""
        try:
            data = json.loads(message)
            code = data.get("code")

            # P2P 中 552 业务码专指日本津波予報（海啸警报），其余直接跳过
            if code == 552 or str(code) == "552":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到津波予報(code:552)"
                )
                return self._parse_tsunami_data(data)

            plugin_logger.debug(f"[灾害预警] {self.source_id} 非海啸数据，code: {code}")
            return None
        except json.JSONDecodeError as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} JSON解析失败: {exc}")
            return None
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 消息处理失败: {exc}")
            return None

    def _parse_tsunami_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 P2P 海啸数据。"""
        try:
            issue = data.get("issue", {}) if isinstance(data.get("issue"), dict) else {}
            cancelled = coerce_bool(data.get("cancelled"), default=False)
            forecasts = normalize_jma_tsunami_areas(
                data.get("areas", []), cancelled=cancelled
            )
            max_grade = resolve_jma_tsunami_max_grade(forecasts, cancelled=cancelled)
            title = resolve_jma_tsunami_title(max_grade, cancelled=cancelled)

            issue_time_raw = issue.get("time") or data.get("time") or ""
            issue_time = _ensure_jst_datetime(self._parse_datetime(issue_time_raw))
            source_entry = get_source_entry(self.source_id)

            event_id = str(data.get("id", "") or data.get("_id", "") or "").strip()
            if not event_id:
                stable_parts = [
                    str(data.get("code", "") or "").strip(),
                    str(title or "").strip(),
                    str(issue_time_raw or "").strip(),
                ]
                stable_parts = [part for part in stable_parts if part]
                event_id = (
                    "jma_tsunami_" + "|".join(stable_parts)
                    if stable_parts
                    else "jma_tsunami_unknown"
                )

            envelope = _build_jma_tsunami_envelope(
                source_id=self.source_id,
                source_entry=source_entry,
                data=data,
                provider_family="p2p",
                source_family="p2p",
                message_type=str(data.get("code", 552)),
                event_id=event_id,
                issue_time=issue_time,
                title=title,
                max_grade=max_grade,
                forecasts=forecasts,
                metadata_extra={
                    "code": str(data.get("code", 552)),
                    "cancelled": cancelled,
                },
            )
            plugin_logger.info(
                f"[灾害预警] JMA海啸预报解析成功(P2P): {title}, 时间: {issue_time}",
                is_event_linked=True,
                event_stream="tsunami",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析海啸数据失败: {exc}")
            return None


class JmaTsunamiEqscParser(BaseParser):
    """日本气象厅海啸情报解析器（EQSC HTTP 快照）。

    对应接口：GET /jma_tsunami.json（需 AccessToken 鉴权）。

    EQSC 相对 P2P 的优势字段：
    - 稳定 eventID
    - issueHypocenter（发震时间 / 震央地名 / 震央代码 / 震级 Mj）
    - 区域级 firstHeight.condition、maxHeight.description/value、immediate
    - isTraining / expiresAt / cancelled 状态位

    解析目标：把上述字段完整落入 metadata / forecasts，供展示器与同源去重复用。
    """

    def __init__(self, message_logger=None):
        """初始化 EQSC 日本海啸情报解析器。"""
        super().__init__("jma_tsunami_eqsc", message_logger)

    @staticmethod
    def _clean_eqsc_text(value: Any) -> str:
        """清洗 EQSC 常见空值字符串（null/None/空白）。"""
        if value is None:
            return ""
        text = str(value).strip()
        if not text or text.lower() in {"null", "none"}:
            return ""
        return text

    @staticmethod
    def _parse_optional_float(value: Any) -> float | None:
        """宽松解析数值字段（震级、波高等）。"""
        text = JmaTsunamiEqscParser._clean_eqsc_text(value)
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _parse_eqsc_datetime(self, value: Any) -> datetime | None:
        """解析 EQSC 时间字符串，并按 JST 补齐时区。"""
        text = self._clean_eqsc_text(value)
        if not text:
            return None
        return _ensure_jst_datetime(self._parse_datetime(text))

    def _extract_hypocenter(self, data: dict[str, Any]) -> dict[str, Any]:
        """提取 issueHypocenter 关联地震信息。

        文档字段：
        - originTime: 发震时间（UTC+9）
        - hypoCenterName: 震央地名
        - code: 震央代码
        - magnitude: 震级（Mj）
        """
        raw = data.get("issueHypocenter")
        if not isinstance(raw, dict):
            raw = {}

        place_name = self._clean_eqsc_text(raw.get("hypoCenterName"))
        magnitude = self._parse_optional_float(raw.get("magnitude"))
        shock_time = self._parse_eqsc_datetime(raw.get("originTime"))
        hypocenter_code = self._clean_eqsc_text(raw.get("code"))
        origin_time_raw = self._clean_eqsc_text(raw.get("originTime"))

        return {
            "place_name": place_name,
            "magnitude": magnitude,
            "magnitude_raw": self._clean_eqsc_text(raw.get("magnitude")),
            "shock_time": shock_time,
            "origin_time_raw": origin_time_raw,
            "hypocenter_code": hypocenter_code,
            # 保留原始块，便于日志/排障与后续扩展
            "issue_hypocenter": {
                "originTime": origin_time_raw,
                "hypoCenterName": place_name,
                "code": hypocenter_code,
                "magnitude": self._clean_eqsc_text(raw.get("magnitude")),
            }
            if (
                place_name
                or hypocenter_code
                or magnitude is not None
                or origin_time_raw
            )
            else {},
        }

    @staticmethod
    def _summarize_areas(forecasts: list[dict[str, Any]]) -> dict[str, Any]:
        """从归一化区域列表生成展示/统计摘要。"""
        grade_counts: dict[str, int] = {}
        immediate_names: list[str] = []
        max_height_value: float | None = None
        max_height_description = ""
        max_height_area = ""

        for area in forecasts:
            grade = str(area.get("grade") or "Unknown")
            grade_counts[grade] = grade_counts.get(grade, 0) + 1

            if area.get("immediate"):
                name = str(area.get("name") or "").strip()
                if name:
                    immediate_names.append(name)

            value = area.get("maxHeightValue")
            if isinstance(value, (int, float)):
                numeric = float(value)
                if max_height_value is None or numeric > max_height_value:
                    max_height_value = numeric
                    max_height_description = str(
                        area.get("maxHeightDescription")
                        or area.get("maxWaveHeight")
                        or ""
                    ).strip()
                    max_height_area = str(area.get("name") or "").strip()

        return {
            "area_count": len(forecasts),
            "grade_counts": grade_counts,
            "immediate_area_count": len(immediate_names),
            "immediate_area_names": immediate_names,
            "max_wave_height_value": max_height_value,
            "max_wave_height": max_height_description,
            "max_wave_height_area": max_height_area,
        }

    def _resolve_event_id(
        self,
        data: dict[str, Any],
        *,
        title: str,
        issue_time_raw: str,
        place_name: str,
    ) -> str:
        """解析稳定事件 ID：优先 eventID，缺失时回退组合键。"""
        event_id = self._clean_eqsc_text(
            data.get("eventID") or data.get("eventId") or data.get("id")
        )
        if event_id:
            return event_id

        stable_parts = [part for part in (title, issue_time_raw, place_name) if part]
        if stable_parts:
            return "jma_tsunami_eqsc_" + "|".join(stable_parts)
        return "jma_tsunami_eqsc_unknown"

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 EQSC /jma_tsunami.json 最新海啸情报快照。

        顶层字段（文档）：
        - areas: 海啸预报区列表
        - issueHypocenter: 关联地震震中信息
        - cancelled: 有效期是否已结束（字符串 bool）
        - expiresAt: 若干海面变动取消时间（UTC+9，可为 null）
        - eventID: 事件 ID
        - time / register: 发表时间
        - isTraining: 是否训练报
        """
        try:
            if not isinstance(data, dict) or not data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 空快照，跳过")
                return None

            # ---- 状态位 ----
            # cancelled=true 表示当前海啸情报有效期已结束（解除语义）
            cancelled = coerce_bool(data.get("cancelled"), default=False)
            # isTraining=true 为训练报；默认由轮询服务按配置过滤，这里仍完整保留字段
            is_training = coerce_bool(data.get("isTraining"), default=False)

            # ---- 区域预报 ----
            # 归一化后 forecasts 供展示器与同源内容指纹共用
            forecasts = normalize_jma_tsunami_areas(
                data.get("areas", []), cancelled=cancelled
            )
            max_grade = resolve_jma_tsunami_max_grade(forecasts, cancelled=cancelled)
            title = resolve_jma_tsunami_title(max_grade, cancelled=cancelled)
            area_summary = self._summarize_areas(forecasts)

            # ---- 发表时间 ----
            # time / register 文档均标注为发表时间；优先 time，register 作回退
            issue_time_raw = self._clean_eqsc_text(
                data.get("time") or data.get("register") or data.get("issueTime")
            )
            register_time_raw = self._clean_eqsc_text(data.get("register"))
            issue_time = self._parse_eqsc_datetime(issue_time_raw)
            register_time = self._parse_eqsc_datetime(register_time_raw)

            # ---- 关联地震（issueHypocenter）----
            hypocenter = self._extract_hypocenter(data)
            place_name = str(hypocenter.get("place_name") or "")
            magnitude = hypocenter.get("magnitude")
            shock_time = hypocenter.get("shock_time")
            hypocenter_code = str(hypocenter.get("hypocenter_code") or "")

            # ---- 事件身份 ----
            event_id = self._resolve_event_id(
                data,
                title=title,
                issue_time_raw=issue_time_raw,
                place_name=place_name,
            )

            # ---- 过期/取消时间 ----
            # expiresAt：若干的海面变动取消时间（UTC+9）；null 表示无
            expires_at_raw = self._clean_eqsc_text(data.get("expiresAt"))
            expires_at = self._parse_eqsc_datetime(expires_at_raw)

            source_entry = get_source_entry(self.source_id)
            envelope = _build_jma_tsunami_envelope(
                source_id=self.source_id,
                source_entry=source_entry,
                data=data,
                provider_family="eqsc",
                source_family="eqsc",
                message_type="jma_tsunami",
                event_id=event_id,
                issue_time=issue_time,
                title=title,
                max_grade=max_grade,
                forecasts=forecasts,
                metadata_extra={
                    # 事件编号：EQSC 用 eventID；展示侧 code 字段复用该值
                    "code": event_id,
                    "event_id": event_id,
                    # 状态
                    "cancelled": cancelled,
                    "is_training": is_training,
                    "expires_at": expires_at,
                    "expires_at_raw": expires_at_raw or None,
                    # 关联地震
                    "place_name": place_name,
                    "subtitle": place_name,
                    "magnitude": magnitude,
                    "magnitude_raw": hypocenter.get("magnitude_raw") or None,
                    "shock_time": shock_time,
                    "origin_time_raw": hypocenter.get("origin_time_raw") or None,
                    "hypocenter_code": hypocenter_code,
                    "issue_hypocenter": hypocenter.get("issue_hypocenter") or {},
                    # 时间线
                    "issue_time": issue_time,
                    "issue_time_raw": issue_time_raw or None,
                    "register_time": register_time,
                    "register_time_raw": register_time_raw or None,
                    "update_time": issue_time or register_time,
                    # 区域摘要（展示器可直接用，避免重复扫描 forecasts）
                    "area_count": area_summary["area_count"],
                    "grade_counts": area_summary["grade_counts"],
                    "immediate_area_count": area_summary["immediate_area_count"],
                    "immediate_area_names": area_summary["immediate_area_names"],
                    "max_wave_height": area_summary["max_wave_height"],
                    "max_wave_height_value": area_summary["max_wave_height_value"],
                    "max_wave_height_area": area_summary["max_wave_height_area"],
                },
            )
            plugin_logger.info(
                f"[灾害预警] JMA海啸情报解析成功(EQSC): {title} "
                f"(事件ID：{event_id}, 涉及地区数：{len(forecasts)}, "
                f"是否为训练报：{is_training}, 最高级别：{max_grade})",
                is_event_linked=True,
                event_stream="tsunami",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析 EQSC 海啸数据失败: {exc}"
            )
            return None
