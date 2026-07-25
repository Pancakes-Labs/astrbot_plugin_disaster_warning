"""
全球地震源解析器。
负责解析 Global Quake、美国地质调查局与美国 ShakeAlert 来源的全球地震数据，
并统一为领域事件。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...models.websocket_message_pb2 import MessageAction, MessageType, WsMessage
from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..services.geo.region_service import region_service
from ..sources.payload_guards import looks_like_fssn_payload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class GlobalQuakeParser(BaseParser):
    """Global Quake 解析器，同时支持二进制与 JSON 两种消息格式。"""

    def __init__(self, message_logger=None):
        super().__init__("global_quake", message_logger)

    def decode_message(self, message: str | bytes):
        """解码 Global Quake 原始消息。"""
        # 返回原始载荷，交由下层进一步判断类型
        return message

    def parse_payload(self, payload):
        """解析 Global Quake 载荷。"""
        if isinstance(payload, bytes):
            return self._parse_protobuf_message(payload)
        if isinstance(payload, str):
            return self._parse_json_message(payload)
        if isinstance(payload, dict):
            return self._parse_earthquake_data(payload)
        return None

    def _parse_protobuf_message(self, message: bytes) -> EventEnvelope | None:
        """解析二进制格式消息。"""
        try:
            ws_msg = WsMessage()
            ws_msg.ParseFromString(message)

            # 二进制通道会混发地震、心跳和状态消息，这里只把地震消息送入正式解析链
            if ws_msg.type == MessageType.EARTHQUAKE:
                # 适配新 API 中的 CANCELLED (取消报) 动作
                if ws_msg.action == MessageAction.CANCELLED:
                    return self._parse_earthquake_removal_protobuf(ws_msg)
                return self._parse_earthquake_protobuf(ws_msg)
            if ws_msg.type == MessageType.HEARTBEAT:
                return None
            if ws_msg.type == MessageType.STATUS:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到状态消息，服务器状态为 {ws_msg.status_data.server_status}"
                )
                return None

            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 收到未知类型的消息，类型值为 {ws_msg.type}"
            )
            return None
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} Protobuf 解析失败: {exc}")
            return None

    def _parse_json_message(self, message: str) -> EventEnvelope | None:
        """解析 JSON 格式消息。"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            action = data.get("action")

            # JSON 通道当前主要关心地震消息，其余类型直接忽略。
            if msg_type == "earthquake":
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 收到 JSON 地震消息，动作为 {action}"
                )
                # 适配新 API 中的 cancelled (取消报) 动作
                if action == "cancelled":
                    return self._parse_earthquake_removal_json(data)
                return self._parse_earthquake_data(data)

            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 已忽略类型为 {msg_type} 的消息"
            )
            return None
        except json.JSONDecodeError as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} JSON解析失败: {exc}")
            return None

    def _build_removal_envelope(
        self, event_id: str, raw_payload: dict, source_entry, message_type: str
    ) -> EventEnvelope | None:
        """构建取消/撤销事件的统一包裹对象。"""
        metadata = {
            "source_family": "global_quake",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_warning",
            "is_cancel": True,
            "report_num": 0,
        }

        # 实例化取消的地震领域事件
        domain_event = EarthquakeEvent(
            occurred_at=datetime.now(timezone.utc),
            latitude=0.0,
            longitude=0.0,
            depth=0.0,
            magnitude=0.0,
            intensity="",
            place_name="[已撤销地震]",
            metadata=dict(metadata),
        )

        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "global_quake",
            source_enum=source_entry.source_enum if source_entry else "",
            report_num=0,
            published_at=domain_event.occurred_at,
            aliases=(event_id,),
            attributes={
                "parser_name": self.source_entry.parser_name
                if self.source_entry
                else "",
                "config_key": source_entry.config_key if source_entry else "",
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
                else "global_quake",
                message_type=message_type,
                raw=raw_payload,
                attributes=dict(metadata),
            ),
            metadata=metadata,
        )
        return envelope

    def _parse_earthquake_removal_protobuf(
        self, ws_msg: WsMessage
    ) -> EventEnvelope | None:
        """解析 Protobuf 二进制取消地震消息。"""
        try:
            removal_data = ws_msg.earthquake_removal_data
            event_id = str(removal_data.id or "")
            if not event_id:
                return None

            source_entry = get_source_entry(self.source_id)
            raw_payload = {"protobuf": True, "id": event_id, "is_cancel": True}
            envelope = self._build_removal_envelope(
                event_id, raw_payload, source_entry, "protobuf"
            )

            plugin_logger.info(
                f"[灾害预警] Global Quake 收到地震取消广播: ID={event_id}",
                is_event_linked=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析 Protobuf 取消消息失败: {exc}"
            )
            return None

    def _parse_earthquake_removal_json(
        self, data: dict[str, Any]
    ) -> EventEnvelope | None:
        """解析 JSON 格式取消地震消息。"""
        try:
            eq_data = data.get("data") or {}
            event_id = str(eq_data.get("id", "") or "")
            if not event_id:
                return None

            source_entry = get_source_entry(self.source_id)
            envelope = self._build_removal_envelope(
                event_id, dict(data), source_entry, "earthquake"
            )

            plugin_logger.info(
                f"[灾害预警] Global Quake 收到地震取消广播 (JSON): ID={event_id}",
                is_event_linked=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析 JSON 取消消息失败: {exc}"
            )
            return None

    def _parse_earthquake_protobuf(self, ws_msg: WsMessage) -> EventEnvelope | None:
        """解析二进制地震数据。"""
        try:
            eq_data = ws_msg.earthquake_data

            # 震源时间优先使用标准时间字符串，缺失时再回退到毫秒时间戳。
            shock_time = None
            if eq_data.origin_time_iso:
                shock_time = self._parse_datetime(eq_data.origin_time_iso)
            elif eq_data.origin_time_ms:
                shock_time = datetime.fromtimestamp(
                    eq_data.origin_time_ms / 1000, tz=timezone.utc
                )

            # 解析罗马数字格式的最大烈度
            intensity = ScaleConverter.convert_roman_intensity(eq_data.intensity)
            magnitude = round(eq_data.magnitude, 1) if eq_data.magnitude else None
            depth = round(eq_data.depth, 1) if eq_data.depth is not None else None

            # 全球震中地点优先翻译为适合中文展示的地名，必要时保留原始英文地名
            place_name = region_service.translate_place_name(
                eq_data.region,
                eq_data.latitude,
                eq_data.longitude,
                fallback_to_original=True,
            )

            # 获取测站数量统计
            station_count = None
            if eq_data.HasField("station_count"):
                station_count = {
                    "total": eq_data.station_count.total,
                    "selected": eq_data.station_count.selected,
                    "used": eq_data.station_count.used,
                    "matching": eq_data.station_count.matching,
                }

            # 获取定位精度质量
            quality_data = None
            if eq_data.HasField("quality"):
                quality_data = {
                    "err_origin": eq_data.quality.err_origin,
                    "err_depth": eq_data.quality.err_depth,
                    "err_ns": eq_data.quality.err_ns,
                    "err_ew": eq_data.quality.err_ew,
                    "pct": eq_data.quality.pct,
                    "stations": eq_data.quality.stations,
                }

            raw_payload = {
                "protobuf": True,
                "id": eq_data.id,
                "data": {"quality": quality_data} if quality_data else {},
            }

            # 获取版本报次
            raw_report_num = eq_data.revision_id or 1
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            source_entry = get_source_entry(self.source_id)

            # 判断是否为归档报/结束报
            is_archived = ws_msg.action == MessageAction.ARCHIVED

            metadata = {
                "source_family": "global_quake",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "max_pga": eq_data.max_pga if eq_data.max_pga else None,
                "stations": station_count,
                "report_num": report_num,
                "quality": quality_data,
                "is_final": is_archived,  # 归档报视为最终报
            }
            event_id = str(eq_data.id or "")

            # 实例化地震领域事件
            domain_event = EarthquakeEvent(
                occurred_at=shock_time or datetime.now(timezone.utc),
                latitude=eq_data.latitude,
                longitude=eq_data.longitude,
                depth=depth,
                magnitude=magnitude,
                intensity=intensity,
                place_name=place_name,
                metadata=dict(metadata),
            )

            # 构造身份标识
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "global_quake",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=shock_time,
                is_final=is_archived,
                aliases=tuple(
                    item for item in (str(eq_data.id or "").strip(),) if item
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
                    else "global_quake",
                    message_type="protobuf",
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] Global Quake地震解析成功: {domain_event.place_name} "
                f"(M {domain_event.magnitude or 0.0:.1f}), 烈度: {eq_data.intensity}, "
                f"时间: {domain_event.occurred_at}",
                is_event_linked=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析 Protobuf 地震数据失败: {exc}"
            )
            return None

    def _parse_earthquake_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析 Global Quake 监测端 JSON 地震数据。"""
        try:
            eq_data = self._extract_data(data)
            if not eq_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # JSON 格式同样兼容两种时间表达，优先用更明确的字符串时间
            shock_time = None
            origin_time_iso = eq_data.get("originTimeIso")
            if origin_time_iso:
                shock_time = self._parse_datetime(origin_time_iso)
            elif eq_data.get("originTimeMs"):
                shock_time = datetime.fromtimestamp(
                    eq_data["originTimeMs"] / 1000, tz=timezone.utc
                )

            intensity_str = eq_data.get("intensity", "")
            intensity = ScaleConverter.convert_roman_intensity(intensity_str)
            latitude = eq_data.get("latitude", 0)
            longitude = eq_data.get("longitude", 0)

            magnitude = safe_float_convert(eq_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(eq_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            # 中文翻译地名
            original_region = eq_data.get("region", "未知地点")
            place_name = region_service.translate_place_name(
                original_region, latitude, longitude, fallback_to_original=True
            )

            raw_report_num = eq_data.get("revisionId", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            source_entry = get_source_entry(self.source_id)
            raw_payload = dict(data)

            # 判断是否为归档报/结束报
            action = data.get("action", "")
            is_archived = action == "archived"

            metadata = {
                "source_family": "global_quake",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_warning",
                "max_pga": eq_data.get("maxPGA"),
                "stations": eq_data.get("stationCount"),
                "report_num": report_num,
                "is_final": is_archived,  # 归档报视为最终报
            }
            event_id = str(eq_data.get("id", "") or "")

            # 领域模型
            domain_event = EarthquakeEvent(
                occurred_at=shock_time or datetime.now(timezone.utc),
                latitude=latitude,
                longitude=longitude,
                depth=depth,
                magnitude=magnitude,
                intensity=intensity,
                place_name=place_name,
                metadata=dict(metadata),
            )

            # 身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "global_quake",
                source_enum=source_entry.source_enum if source_entry else "",
                report_num=report_num,
                published_at=shock_time,
                is_final=is_archived,
                aliases=tuple(
                    item for item in (str(eq_data.get("id", "") or "").strip(),) if item
                ),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 封装包裹层
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "global_quake",
                    message_type=str(data.get("type") or "earthquake").strip(),
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] Global Quake地震解析成功: {domain_event.place_name} "
                f"(M {domain_event.magnitude or 0.0:.1f}), 烈度: {intensity_str}, "
                f"时间: {domain_event.occurred_at}",
                is_event_linked=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析地震数据失败: {exc}")
            return None

    def _parse_text_message(self, message: str) -> EventEnvelope | None:
        """保留文本消息兼容处理。"""
        plugin_logger.debug(f"[灾害预警] {self.source_id} 文本消息: {message}")
        return None

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """实现基类抽象方法，默认按 JSON 地震数据处理。"""
        return self._parse_earthquake_data(data)


class UsgsEarthquakeParser(BaseParser):
    """美国地质调查局地震情报解析器。"""

    def __init__(self, message_logger=None):
        super().__init__("usgs_fanstudio", message_logger)

    @staticmethod
    def _get_field(data: dict[str, Any], field_name: str):
        # USGS 来源字段大小写并不总是稳定；用显式 key 判断，避免 0 / 0.0 被 or 吞掉。
        if field_name in data:
            return data[field_name]
        # camelCase → PascalCase（shockTime → ShockTime），比 str.capitalize() 更稳妥。
        pascal_name = (
            field_name[:1].upper() + field_name[1:] if field_name else field_name
        )
        if pascal_name in data:
            return data[pascal_name]
        return None

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析美国地质调查局地震数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 过滤空心跳包
            if self._is_heartbeat_message(msg_data):
                return None

            # 检测关键字段完整度（复用 _get_field，兼容 camelCase / PascalCase）
            required_fields = ["id", "magnitude", "latitude", "longitude", "shockTime"]
            missing_fields = [
                field
                for field in required_fields
                if self._get_field(msg_data, field) is None
            ]
            if missing_fields:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 数据缺少部分字段: {missing_fields}，继续处理..."
                )

            magnitude = safe_float_convert(self._get_field(msg_data, "magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(self._get_field(msg_data, "depth"))
            if depth is not None:
                depth = round(depth, 1)

            usgs_id = self._get_field(msg_data, "id") or ""
            usgs_latitude = (
                safe_float_convert(self._get_field(msg_data, "latitude")) or 0.0
            )
            usgs_longitude = (
                safe_float_convert(self._get_field(msg_data, "longitude")) or 0.0
            )
            usgs_place_name_en = self._get_field(msg_data, "placeName") or ""

            # 地震唯一ID缺失时判定为无法入去重链的脏数据，不予继续解析
            if not usgs_id:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = f"[灾害预警] {self.source_id} 缺少地震ID，跳过处理"
                    if self._should_log_warning("missing_usgs_id", warning_msg):
                        plugin_logger.warning(warning_msg)
                return None

            if usgs_latitude == 0 and usgs_longitude == 0:
                return None

            if not usgs_place_name_en and not magnitude:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = (
                        f"[灾害预警] {self.source_id} 缺少地点名称和震级信息，跳过处理"
                    )
                    if self._should_log_warning(
                        "missing_usgs_place_magnitude", warning_msg
                    ):
                        plugin_logger.warning(warning_msg)
                return None

            # 翻译全球震中地点英文名称为中文
            usgs_place_name = region_service.translate_place_name(
                usgs_place_name_en,
                usgs_latitude,
                usgs_longitude,
                fallback_to_original=True,
            )

            source_entry = get_source_entry(self.source_id)
            raw_payload = dict(msg_data)
            update_time = self._parse_datetime(self._get_field(msg_data, "updateTime"))
            metadata = {
                "source_family": "fan_studio",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "info_type": self._get_field(msg_data, "infoTypeName") or "",
                "update_time": update_time,
            }
            event_id = str(usgs_id or "")

            # 实例化地震领域事件
            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(
                    self._get_field(msg_data, "shockTime")
                ),
                latitude=usgs_latitude,
                longitude=usgs_longitude,
                depth=depth,
                magnitude=magnitude,
                place_name=usgs_place_name,
                metadata=dict(metadata),
            )

            # 构造身份模型
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=update_time or domain_event.occurred_at,
                aliases=tuple(item for item in (str(usgs_id or "").strip(),) if item),
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
                    else "fan_studio",
                    message_type=str(msg_data.get("type") or "update").strip(),
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 地震数据解析成功: {domain_event.place_name} (M {domain_event.magnitude or 0.0}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class ShakeAlertEewParser(BaseParser):
    """美国 ShakeAlert 地震预警解析器 - FAN Studio。"""

    def __init__(self, message_logger=None):
        super().__init__("sa_fanstudio", message_logger)

    @staticmethod
    def _get_field(data: dict[str, Any], field_name: str):
        # ShakeAlert 字段大小写可能不稳定；用显式 key 判断，避免 0 / 0.0 被 or 吞掉。
        if field_name in data:
            return data[field_name]
        # camelCase → PascalCase（shockTime → ShockTime），比 str.capitalize() 更稳妥。
        pascal_name = (
            field_name[:1].upper() + field_name[1:] if field_name else field_name
        )
        if pascal_name in data:
            return data[pascal_name]
        return None

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析美国 ShakeAlert 地震预警数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            if self._is_heartbeat_message(msg_data):
                return None

            # 与 USGS 区分：ShakeAlert 预警载荷不含官方事件页 url
            if self._get_field(msg_data, "url"):
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 检测到 USGS 特征字段 url，跳过"
                )
                return None

            # 与 FSSN 区分：共享守卫（ID 前缀 + 特征字段）
            if looks_like_fssn_payload(
                msg_data,
                get_value=lambda field: self._get_field(msg_data, field),
            ):
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 检测到 FSSN 特征，跳过"
                )
                return None

            # 检测关键字段完整度（复用 _get_field，兼容 camelCase / PascalCase）
            required_fields = ["id", "magnitude", "latitude", "longitude", "shockTime"]
            missing_fields = [
                field
                for field in required_fields
                if self._get_field(msg_data, field) is None
            ]
            if missing_fields:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 数据缺少部分字段: {missing_fields}，继续处理..."
                )

            magnitude = safe_float_convert(self._get_field(msg_data, "magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(self._get_field(msg_data, "depth"))
            if depth is not None:
                depth = round(depth, 1)

            event_raw_id = self._get_field(msg_data, "id") or ""
            latitude = safe_float_convert(self._get_field(msg_data, "latitude")) or 0.0
            longitude = (
                safe_float_convert(self._get_field(msg_data, "longitude")) or 0.0
            )
            place_name_en = self._get_field(msg_data, "placeName") or ""

            if not event_raw_id:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = f"[灾害预警] {self.source_id} 缺少地震ID，跳过处理"
                    if self._should_log_warning("missing_sa_id", warning_msg):
                        plugin_logger.warning(warning_msg)
                return None

            if latitude == 0 and longitude == 0:
                return None

            if not place_name_en and magnitude is None:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = (
                        f"[灾害预警] {self.source_id} 缺少地点名称和震级信息，跳过处理"
                    )
                    if self._should_log_warning(
                        "missing_sa_place_magnitude", warning_msg
                    ):
                        plugin_logger.warning(warning_msg)
                return None

            place_name = region_service.translate_place_name(
                place_name_en,
                latitude,
                longitude,
                fallback_to_original=True,
            )

            source_entry = get_source_entry(self.source_id)
            raw_payload = dict(msg_data)
            # 统计/入库语义按地震事件处理（与 Global Quake 一致），不计入 EEW 预警类型
            metadata = {
                "source_family": "fan_studio",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "earthquake_info",
                "event_id": str(event_raw_id),
                "md5": data.get("md5") if isinstance(data, dict) else None,
            }
            event_id = str(event_raw_id)

            domain_event = EarthquakeEvent(
                occurred_at=self._parse_datetime(
                    self._get_field(msg_data, "shockTime")
                ),
                latitude=latitude,
                longitude=longitude,
                depth=depth,
                magnitude=magnitude,
                place_name=place_name,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=domain_event.occurred_at,
                aliases=tuple(
                    item for item in (str(event_raw_id or "").strip(),) if item
                ),
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
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
                    else "fan_studio",
                    message_type=str(msg_data.get("type") or "update").strip(),
                    raw=raw_payload,
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] ShakeAlert 地震解析成功: {domain_event.place_name} (M {domain_event.magnitude or 0.0}), 时间: {domain_event.occurred_at}",
                is_event_linked=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
