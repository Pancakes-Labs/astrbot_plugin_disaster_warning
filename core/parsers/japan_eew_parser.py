"""
日本地震预警解析器。
负责把 FAN Studio、P2P 与 Wolfx 来源的日本地震预警数据统一转换为领域事件。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class JmaEewFanStudioParser(BaseParser):
    """日本气象厅地震预警解析器 - FAN Studio。"""

    def __init__(self, message_logger=None):
        """初始化 FAN Studio 日本预警解析器。"""
        super().__init__("jma_fanstudio", message_logger)

    def _build_envelope(self, msg_data: dict[str, Any]) -> EventEnvelope:
        """把 FAN Studio 日本预警原始字典封装为统一事件包裹体。"""
        source_entry = get_source_entry(self.source_id)
        # FAN Studio 报次字段通常来自 updates，这里统一规整为整数
        report_num = (
            msg_data.get("updates", 1)
            if isinstance(msg_data.get("updates"), int)
            else 1
        )
        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_warning",
            "report_num": report_num,
            "is_final": bool(msg_data.get("final", False)),
            "is_cancel": bool(msg_data.get("cancel", False)),
            "info_type": msg_data.get("infoTypeName", ""),
            "create_time": self._parse_datetime(msg_data.get("createTime", "")),
            "jma_warning_areas": [],
            "jma_warning_area_ranges": [],
        }

        # 实例化地震预警领域模型
        domain_event = EarthquakeEvent(
            occurred_at=self._parse_datetime(msg_data.get("shockTime", "")),
            latitude=safe_float_convert(msg_data.get("latitude")),
            longitude=safe_float_convert(msg_data.get("longitude")),
            place_name=str(msg_data.get("placeName", "") or ""),
            magnitude=safe_float_convert(msg_data.get("magnitude")),
            depth=safe_float_convert(msg_data.get("depth")),
            scale=ScaleConverter.parse_jma_cwa_scale(msg_data.get("epiIntensity", "")),
            metadata=dict(metadata),
        )

        created_at = self._parse_datetime(msg_data.get("createTime", ""))

        # 构建事件身份模型
        identity = EventIdentity(
            event_id=str(msg_data.get("id", "") or ""),
            source_id=self.source_id,
            event_type="earthquake_warning",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            report_num=report_num,
            published_at=created_at or domain_event.occurred_at,
            is_final=bool(msg_data.get("final", False)),
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

        # 包装为统一包裹体返回
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
        """解析 FAN Studio 日本气象厅地震预警数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 预计震度与情报类型至少应命中其一，否则通常不是正式预警消息
            if "epiIntensity" not in msg_data and "infoTypeName" not in msg_data:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 JMA 地震预警数据，跳过"
                )
                return None

            # 取消报在当前推送链中不作为正式地震事件继续向后处理
            if msg_data.get("cancel", False):
                plugin_logger.info(
                    f"[灾害预警] {self.source_id} 收到取消报，跳过",
                    is_event_linked=True,
                    event_stream="earthquake",
                )
                return None

            envelope = self._build_envelope(msg_data)
            domain_event = envelope.event
            report_num = (
                msg_data.get("updates", 1)
                if isinstance(msg_data.get("updates"), int)
                else 1
            )
            envelope.metadata.update(
                {
                    "report_num": report_num,
                    "is_final": bool(msg_data.get("final", False)),
                    "is_cancel": bool(msg_data.get("cancel", False)),
                    "info_type": msg_data.get("infoTypeName", ""),
                    "create_time": self._parse_datetime(msg_data.get("createTime", "")),
                }
            )

            plugin_logger.info(
                f"[灾害预警] JMA地震预警解析成功: {getattr(domain_event, 'place_name', '')} (M {getattr(domain_event, 'magnitude', None)}), 时间: {getattr(domain_event, 'occurred_at', None)}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class JmaEewP2PParser(BaseParser):
    """日本气象厅紧急地震速报解析器 - P2P。"""

    def __init__(self, message_logger=None):
        super().__init__("jma_p2p", message_logger)

    def parse_message(self, message: str) -> EventEnvelope | None:
        """解析 P2P 消息。"""
        try:
            data = json.loads(message)
            code = data.get("code")

            # P2P 用业务码区分不同类型，其中 556 才是正式紧急地震速报
            if code == 556:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到紧急地震速报（警报）"
                )
                return self._parse_eew_data(data)
            if code == 554:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到紧急地震速报发布检测消息，忽略"
                )
                return None

            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 非地震预警数据，code: {code}"
            )
            return None
        except json.JSONDecodeError as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} JSON解析失败: {exc}")
            return None
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 消息处理失败: {exc}")
            return None

    @staticmethod
    def _collect_p2p_area_scale_candidates(area: dict[str, Any]) -> list[int]:
        """收集区域可用的 P2P 震度业务值，忽略占位值 99（以上）。"""
        candidates: list[int] = []
        for key in ("scaleFrom", "scaleTo"):
            raw = ScaleConverter.normalize_p2p_scale_value(area.get(key))
            if raw is None or raw <= 0 or raw == 99:
                continue
            candidates.append(raw)
        return candidates

    def _parse_eew_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析紧急地震速报数据。"""
        try:
            earthquake_info = data.get("earthquake", {})
            hypocenter = earthquake_info.get("hypocenter", {})
            issue_info = data.get("issue", {})
            areas = data.get("areas", [])
            if not isinstance(areas, list):
                areas = []

            # 最大震度可能直接给出，也可能需要从区域列表中推导
            max_scale_raw = -1
            if "maxScale" in earthquake_info:
                max_scale_raw = earthquake_info.get("maxScale", -1)
            elif "max_scale" in earthquake_info:
                max_scale_raw = earthquake_info.get("max_scale", -1)
            else:
                raw_scales: list[int] = []
                for area in areas:
                    if not isinstance(area, dict):
                        continue
                    candidates = self._collect_p2p_area_scale_candidates(area)
                    if candidates:
                        raw_scales.append(max(candidates))

                max_scale_raw = max(raw_scales) if raw_scales else -1
                if max_scale_raw > 0:
                    plugin_logger.warning(
                        f"[灾害预警] {self.source_id} 使用areas计算maxScale: {max_scale_raw}"
                    )

            try:
                max_scale_raw = int(max_scale_raw)
            except (TypeError, ValueError):
                max_scale_raw = -1

            scale = (
                ScaleConverter.convert_p2p_scale(max_scale_raw)
                if max_scale_raw != -1
                else None
            )

            # 获取发震时间
            shock_time = None
            if "time" in earthquake_info:
                shock_time = self._parse_datetime(earthquake_info.get("time", ""))
            elif "originTime" in earthquake_info:
                shock_time = self._parse_datetime(earthquake_info.get("originTime", ""))
            else:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 缺少地震时间信息")

            # 校验关键字段完整度
            required_hypocenter_fields = ["latitude", "longitude", "name"]
            missing_fields = []
            for field in required_hypocenter_fields:
                if field not in hypocenter or hypocenter[field] is None:
                    missing_fields.append(field)

            if missing_fields:
                plugin_logger.warning(
                    f"[灾害预警] {self.source_id} 缺少震源必填字段: {missing_fields}，继续处理..."
                )

            # 取消报、测试报与假定震源判定
            is_cancelled = data.get("cancelled", False)
            if is_cancelled:
                plugin_logger.info(
                    f"[灾害预警] {self.source_id} 收到取消的EEW事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                )

            is_test = data.get("test", False)
            if is_test:
                plugin_logger.info(
                    f"[灾害预警] {self.source_id} 收到测试模式的EEW事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                )

            is_plum = earthquake_info.get("condition") == "仮定震源要素"
            if not is_plum:
                for area in areas:
                    if isinstance(area, dict) and str(area.get("kindCode", "")) == "19":
                        is_plum = True
                        break

            # PLUM/假定震源下 M1.0 通常是占位震级，不应作为真实震级展示或参与过滤。
            magnitude = safe_float_convert(hypocenter.get("magnitude"))
            magnitude_is_placeholder = bool(
                is_plum and magnitude is not None and abs(magnitude - 1.0) < 0.05
            )
            if magnitude_is_placeholder:
                magnitude = None

            report_num = (
                issue_info.get("serial", 1)
                if isinstance(issue_info.get("serial"), int)
                else 1
            )
            warning_areas: list[str] = []
            warning_area_ranges: list[str] = []
            # range_text -> 分组信息，用于「按震度档汇总区域」展示
            area_groups: dict[str, dict[str, Any]] = {}

            # 日本预警区域列表会同时用于文本展示与影响范围提示，这里先归一化整理
            for area in areas:
                if not isinstance(area, dict):
                    continue
                area_name = str(area.get("name", "") or "").strip()
                scale_from = ScaleConverter.normalize_p2p_scale_value(
                    area.get("scaleFrom")
                )
                scale_to = ScaleConverter.normalize_p2p_scale_value(area.get("scaleTo"))
                range_text = ScaleConverter.format_p2p_scale_range(scale_from, scale_to)
                emoji = ScaleConverter.get_p2p_scale_emoji(scale_from, scale_to)

                scale_candidates = self._collect_p2p_area_scale_candidates(area)
                max_area_scale = max(scale_candidates) if scale_candidates else None
                # 若仅有 from + 99(以上)，仍按 from 作为档位键
                if max_area_scale is None and scale_from is not None and scale_from > 0:
                    max_area_scale = scale_from

                # 震度在 4.5 (5弱) 及以上的警报区域需要保留进警报范围中
                if area_name and max_area_scale is not None and max_area_scale >= 45:
                    kind = str(area.get("kindCode", "") or "").strip()
                    status = "已到达" if kind == "11" else "未到达"
                    area_label = f"{area_name}({status})"
                    warning_areas.append(f"{emoji}{area_label}")

                    if range_text:
                        group = area_groups.get(range_text)
                        if group is None:
                            group = {
                                "range_text": range_text,
                                "scale_from": scale_from
                                if scale_from is not None
                                else max_area_scale,
                                "emoji": emoji,
                                "areas": [],
                            }
                            area_groups[range_text] = group
                        if area_label not in group["areas"]:
                            group["areas"].append(area_label)

                if range_text and range_text not in warning_area_ranges:
                    warning_area_ranges.append(range_text)

            warning_area_groups = sorted(
                area_groups.values(),
                key=lambda item: int(item.get("scale_from") or 0),
                reverse=True,
            )

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "p2p",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "serial": issue_info.get("serial", ""),
                "report_num": report_num,
                "is_final": bool(data.get("is_final", False)),
                "is_cancel": is_cancelled,
                "info_type": "警报",
                "is_training": bool(is_test),
                "is_assumption": bool(is_plum),
                "magnitude_is_placeholder": magnitude_is_placeholder,
                "jma_warning_areas": warning_areas,
                "jma_warning_area_ranges": warning_area_ranges,
                "jma_warning_area_groups": warning_area_groups,
            }

            # 实例化地震预警领域模型
            domain_event = EarthquakeEvent(
                occurred_at=shock_time,
                latitude=safe_float_convert(hypocenter.get("latitude")),
                longitude=safe_float_convert(hypocenter.get("longitude")),
                depth=safe_float_convert(hypocenter.get("depth")),
                magnitude=magnitude,
                place_name=str(hypocenter.get("name", "未知地点") or "未知地点"),
                scale=scale,
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=str(issue_info.get("eventId", "") or data.get("id", "") or ""),
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "p2p",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=shock_time,
                is_final=bool(metadata.get("is_final", False)),
                aliases=tuple(
                    item for item in (str(data.get("id", "") or "").strip(),) if item
                ),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 包装并返回统一包裹层
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "p2p",
                    message_type=str(data.get("code") or "556").strip(),
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
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析EEW数据失败: {exc}")
            return None


class JmaEewWolfxParser(BaseParser):
    """日本气象厅紧急地震速报解析器 - Wolfx。"""

    def __init__(self, message_logger=None):
        """初始化 Wolfx 日本预警解析器。"""
        super().__init__("jma_wolfx", message_logger)

    @staticmethod
    def _normalize_wolfx_warn_areas(warn_area: Any) -> list[dict[str, Any]]:
        """把 Wolfx WarnArea 统一规整为 dict 列表（兼容单对象与数组）。"""
        if isinstance(warn_area, list):
            return [item for item in warn_area if isinstance(item, dict)]
        if isinstance(warn_area, dict):
            return [warn_area]
        return []

    @staticmethod
    def _format_wolfx_shindo_range(shindo1: Any, shindo2: Any) -> str:
        """格式化 Wolfx 区域震度范围文本。"""
        left = ScaleConverter.format_jma_cwa_scale_display(shindo1) if shindo1 else ""
        right = ScaleConverter.format_jma_cwa_scale_display(shindo2) if shindo2 else ""
        if left and right and left != right:
            return f"{left} ～ {right}"
        return left or right

    @staticmethod
    def _wolfx_area_sort_key(shindo1: Any, shindo2: Any) -> float:
        """按区域震度高低排序，优先取较大档。"""
        values: list[float] = []
        for raw in (shindo1, shindo2):
            parsed = (
                ScaleConverter.parse_jma_cwa_scale(raw)
                if raw not in (None, "")
                else None
            )
            if parsed is not None:
                values.append(parsed)
        return max(values) if values else -1.0

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Wolfx 日本地震预警数据。"""
        try:
            # Wolfx 会混发多类日本消息，这里只接收日本地震预警类型
            if data.get("type") != "jma_eew":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 JMA 地震预警数据，跳过"
                )
                return None

            report_num = (
                data.get("Serial", 1) if isinstance(data.get("Serial"), int) else 1
            )
            warn_area_items = self._normalize_wolfx_warn_areas(data.get("WarnArea"))
            warning_areas: list[str] = []
            warning_area_ranges: list[str] = []
            area_groups: dict[str, dict[str, Any]] = {}
            info_type = ""
            jma_warn_area = ""

            for item in warn_area_items:
                area_name = str(item.get("Chiiki", "") or "").strip()
                area_type = str(item.get("Type", "") or "").strip()
                shindo1 = item.get("Shindo1")
                shindo2 = item.get("Shindo2")
                range_text = self._format_wolfx_shindo_range(shindo1, shindo2)
                scale_value = self._wolfx_area_sort_key(shindo1, shindo2)
                emoji = "⚪"
                if scale_value >= 0:
                    # 复用 P2P emoji 阈值：把规范浮点值映射到相近业务档
                    if scale_value >= 6.5:
                        emoji = "🟣"
                    elif scale_value >= 5.5:
                        emoji = "🔴"
                    elif scale_value >= 4.5:
                        emoji = "🟠"
                    elif scale_value >= 3.5:
                        emoji = "🟡"
                    elif scale_value >= 2.5:
                        emoji = "🟢"
                    elif scale_value >= 1.5:
                        emoji = "🔵"

                # 警报区域优先；无 Type 时按震度档兜底；≥5弱 的预报区也纳入分布
                is_warning_type = area_type in {"警報", "警报"}
                if is_warning_type:
                    info_type = "警报"
                elif not info_type and area_type:
                    info_type = area_type

                if area_name and (is_warning_type or scale_value >= 4.5):
                    arrive_raw = item.get("Arrive")
                    if isinstance(arrive_raw, bool):
                        status = "已到达" if arrive_raw else "未到达"
                    else:
                        arrive_text = str(arrive_raw or "").strip()
                        # Wolfx 常给 PLUM 说明字符串，无法判断到达时不硬编码
                        if "到達" in arrive_text and "予測なし" not in arrive_text:
                            status = "已到达"
                        elif arrive_text:
                            status = "未到达"
                        else:
                            status = ""
                    area_label = f"{area_name}({status})" if status else area_name
                    warning_areas.append(f"{emoji}{area_label}")

                    if range_text:
                        group = area_groups.get(range_text)
                        if group is None:
                            group = {
                                "range_text": range_text,
                                "scale_from": scale_value,
                                "emoji": emoji,
                                "areas": [],
                            }
                            area_groups[range_text] = group
                        if area_label not in group["areas"]:
                            group["areas"].append(area_label)

                if range_text and range_text not in warning_area_ranges:
                    warning_area_ranges.append(range_text)

            warning_area_groups = sorted(
                area_groups.values(),
                key=lambda item: float(item.get("scale_from") or 0),
                reverse=True,
            )
            if warning_areas:
                # 兼容旧字段：拼接区域名摘要
                jma_warn_area = "、".join(
                    str(item.get("Chiiki", "") or "").strip()
                    for item in warn_area_items
                    if str(item.get("Chiiki", "") or "").strip()
                    and (
                        str(item.get("Type", "") or "").strip() in {"警報", "警报"}
                        or self._wolfx_area_sort_key(
                            item.get("Shindo1"), item.get("Shindo2")
                        )
                        >= 4.5
                    )
                )

            is_assumption = bool(data.get("isAssumption", False))
            magnitude = safe_float_convert(
                data.get("Magunitude") or data.get("Magnitude")
            )
            magnitude_is_placeholder = bool(
                is_assumption and magnitude is not None and abs(magnitude - 1.0) < 0.05
            )
            if magnitude_is_placeholder:
                magnitude = None

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "wolfx",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "report_num": report_num,
                "is_final": bool(data.get("isFinal", False)),
                "is_cancel": bool(data.get("isCancel", False)),
                "info_type": info_type,
                "is_training": bool(data.get("isTraining", False)),
                "is_assumption": is_assumption,
                "magnitude_is_placeholder": magnitude_is_placeholder,
                "is_sea": bool(data.get("isSea", False)),
                "jma_warn_area": jma_warn_area,
                "jma_warning_areas": warning_areas,
                "jma_warning_area_ranges": warning_area_ranges,
                "jma_warning_area_groups": warning_area_groups,
            }

            # 实例化地震领域模型
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(data.get("OriginTime", "")),
                latitude=safe_float_convert(data.get("Latitude")),
                longitude=safe_float_convert(data.get("Longitude")),
                depth=safe_float_convert(data.get("Depth")),
                magnitude=magnitude,
                place_name=str(data.get("Hypocenter", "") or ""),
                scale=ScaleConverter.parse_jma_cwa_scale(data.get("MaxIntensity", "")),
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=str(data.get("EventID", "") or ""),
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
                    item
                    for item in (str(data.get("EventID", "") or "").strip(),)
                    if item
                ),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 封装为统一包裹层返回
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "wolfx",
                    message_type=str(data.get("type") or "jma_eew").strip(),
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
