"""
日本地震情报解析器。
负责把 P2P 与 Wolfx 来源的日本地震情报统一转换为领域事件。
"""

from __future__ import annotations

import json
from typing import Any

from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class JmaEarthquakeP2PParser(BaseParser):
    """日本气象厅地震情报解析器 - P2P。"""

    def __init__(self, message_logger=None):
        """初始化 P2P 日本地震情报解析器。"""
        super().__init__("jma_p2p_info", message_logger)

    def parse_message(self, message: str) -> EventEnvelope | None:
        """解析 P2P 地震情报。"""
        try:
            data = json.loads(message)
            code = data.get("code")

            # P2P 中 551 表示日本地震情报，其余业务码直接忽略。
            if code == 551:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到地震情报(code:551)"
                )
                return self._parse_earthquake_data(data)

            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 非地震情报数据，code: {code}"
            )
            return None
        except json.JSONDecodeError as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} JSON解析失败: {exc}")
            return None
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 消息处理失败: {exc}")
            return None

    def _parse_earthquake_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析地震情报数据。"""
        try:
            # 深入提取 P2P 嵌套结构中的字段
            earthquake_info = data.get("earthquake", {})
            hypocenter = earthquake_info.get("hypocenter", {})

            magnitude_raw = hypocenter.get("magnitude")
            place_name = hypocenter.get("name")
            latitude = hypocenter.get("latitude")
            longitude = hypocenter.get("longitude")
            issue = data.get("issue", {})
            issue_type = issue.get("type", "") if isinstance(issue, dict) else ""

            magnitude = safe_float_convert(magnitude_raw)
            # 过滤 P2P 默认的缺省值
            if magnitude == -1:
                magnitude = None

            # 仅在非震度速报场景下把震级视为硬性字段，兼容日本震度速报消息
            if magnitude is None and issue_type != "ScalePrompt":
                plugin_logger.error(
                    f"[灾害预警] {self.source_id} 震级解析失败: {magnitude_raw}"
                )
                return None

            lat = safe_float_convert(latitude)
            lon = safe_float_convert(longitude)
            if lat == -200:
                lat = None
            if lon == -200:
                lon = None

            if (lat is None or lon is None) and issue_type != "ScalePrompt":
                plugin_logger.error(
                    f"[灾害预警] {self.source_id} 经纬度解析失败: lat={latitude}, lon={longitude}"
                )
                return None

            # 提取最大震度并转换为浮点数值形式
            max_scale_raw = earthquake_info.get("maxScale", -1)
            scale = (
                ScaleConverter.convert_p2p_scale(max_scale_raw)
                if max_scale_raw != -1
                else None
            )

            depth = safe_float_convert(hypocenter.get("depth"))
            shock_time = self._parse_datetime(earthquake_info.get("time", ""))

            # 日本地震情报可能带有订正类型，这里统一映射为中文说明
            correct_type = data.get("issue", {}).get("correct", "")
            correct_mapping = {
                "ScaleOnly": "震度订正",
                "DestinationOnly": "震源订正",
                "ScaleAndDestination": "震源・震度订正",
            }
            correct_str = correct_mapping.get(correct_type, "")

            source_entry = get_source_entry(self.source_id)
            # 原始观测点与自由评论保留到元数据中，供后续详情展示复用
            jma_points = [
                point
                for point in list(data.get("points") or [])
                if isinstance(point, dict)
            ]
            jma_comment = ""
            comments = data.get("comments")
            if isinstance(comments, dict):
                free_form_comment = comments.get("freeFormComment")
                if isinstance(free_form_comment, str):
                    jma_comment = free_form_comment.strip()

            # 整合元数据
            metadata = {
                "source_family": "p2p",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "max_scale": max_scale_raw,
                "domestic_tsunami": earthquake_info.get("domesticTsunami"),
                "foreign_tsunami": earthquake_info.get("foreignTsunami"),
                "revision": correct_str if correct_str else None,
                "info_type": issue_type,
                "jma_points": jma_points,
                "jma_comment": jma_comment,
                "jma_warning_areas": [],
                "jma_warning_area_ranges": [],
            }
            event_id = str(data.get("id", "") or "").strip()
            # 缺少正式事件ID时使用时间与地点拼出稳定回退ID
            if not event_id:
                fallback_time = (
                    shock_time.strftime("%Y%m%d%H%M%S")
                    if shock_time
                    else "unknown_time"
                )
                fallback_place = (
                    str(place_name or "unknown_place").strip() or "unknown_place"
                )
                event_id = f"jma_info_{fallback_time}_{fallback_place}"

            # 实例化地震领域事件
            domain_event = EarthquakeEvent(
                occurred_at=shock_time,
                latitude=lat,
                longitude=lon,
                depth=depth,
                magnitude=magnitude,
                place_name=place_name or "未知地点",
                scale=scale,
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "p2p",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=domain_event.occurred_at,
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
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "p2p",
                    message_type=str(data.get("code") or "551").strip(),
                    raw=dict(data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 地震数据解析成功: {domain_event.place_name} (M {domain_event.magnitude}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析地震情报失败: {exc}")
            return None


class JmaEarthquakeWolfxParser(BaseParser):
    """日本气象厅地震情报解析器 - Wolfx。"""

    def __init__(self, message_logger=None):
        """初始化 Wolfx 日本地震情报解析器。"""
        super().__init__("jma_wolfx_info", message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Wolfx 日本气象厅地震列表。"""
        try:
            # Wolfx 中只对日本地震列表消息做处理，其余类型直接跳过
            if data.get("type") != "jma_eqlist":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 JMA 地震列表数据，跳过"
                )
                return None

            eq_info = None
            # Wolfx 列表消息通常以 No1、No2 等键承载首条记录，这里取首个有效项
            for key, value in data.items():
                if key.startswith("No") and isinstance(value, dict):
                    eq_info = value
                    break

            if not eq_info:
                return None

            # 处理带有 "km" 单位的深度字符串
            depth_raw = eq_info.get("depth")
            depth = None
            if depth_raw:
                if isinstance(depth_raw, str) and depth_raw.endswith("km"):
                    try:
                        depth = float(depth_raw[:-2])
                    except (ValueError, TypeError):
                        depth = None
                else:
                    depth = safe_float_convert(depth_raw)

            magnitude = safe_float_convert(eq_info.get("magnitude"))
            info_type = data.get("Title", "")

            # 收集自由文本评论
            comments = eq_info.get("comments")
            jma_comment = ""
            if isinstance(comments, dict):
                free_form_comment = comments.get("freeFormComment")
                if isinstance(free_form_comment, str):
                    jma_comment = free_form_comment.strip()

            jma_points = [
                point
                for point in list(eq_info.get("points") or [])
                if isinstance(point, dict)
            ]

            # 解析预警区域范围与震度区间
            warn_area = eq_info.get("WarnArea")
            jma_warn_area = ""
            jma_warning_area_ranges: list[str] = []
            if isinstance(warn_area, dict):
                jma_warn_area = str(warn_area.get("Chiiki", "") or "").strip()
                shindo1 = warn_area.get("Shindo1")
                shindo2 = warn_area.get("Shindo2")
                if shindo1:
                    range_text = f"{shindo1}"
                    if shindo2 and shindo2 != shindo1:
                        range_text += f" ～ {shindo2}"
                    jma_warning_area_ranges.append(range_text)

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "wolfx",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "domestic_tsunami": eq_info.get("info"),
                "info_type": info_type,
                "jma_warn_area": jma_warn_area,
                "jma_points": jma_points,
                "jma_comment": jma_comment,
                "jma_warning_areas": [jma_warn_area] if jma_warn_area else [],
                "jma_warning_area_ranges": jma_warning_area_ranges,
            }

            event_id = str(eq_info.get("md5", "") or "")

            # 实例化地震领域模型
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(eq_info.get("time", "")),
                latitude=safe_float_convert(eq_info.get("latitude")),
                longitude=safe_float_convert(eq_info.get("longitude")),
                depth=depth,
                magnitude=magnitude,
                scale=ScaleConverter.parse_jma_cwa_scale(eq_info.get("shindo", "")),
                place_name=eq_info.get("location", ""),
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "wolfx",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=domain_event.occurred_at,
                aliases=tuple(
                    item
                    for item in (str(eq_info.get("md5", "") or "").strip(),)
                    if item
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
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "wolfx",
                    message_type=str(data.get("type") or "jma_eqlist").strip(),
                    raw=dict(eq_info),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 地震数据解析成功: {domain_event.place_name} (M {domain_event.magnitude}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
