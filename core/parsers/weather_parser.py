"""
气象预警解析器。
负责把中国气象局来源的气象预警消息转换为统一领域事件。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EventEnvelope, WeatherEvent
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class WeatherAlarmParser(BaseParser):
    """中国气象局气象预警解析器。

    支持 FAN Studio 扁平载荷与 OpenQuakeAPI RealtimeEvent 包装格式。
    构造时按 source_id 区分数据源，各源维护独立的短窗去重队列，
    跨源不去重。
    """

    def __init__(self, source_id: str = "china_weather_fanstudio", message_logger=None):
        """初始化气象预警解析器与短期重复记录缓存。"""
        super().__init__(source_id, message_logger)
        # 短窗去重缓存：{预警 id: 最近处理时间戳}。
        # 带时间窗 + 容量上限，避免高频重推旧预警在重载后被反复放行。
        self._processed_weather_ids: dict[str, float] = {}
        self._WEATHER_DEDUPE_WINDOW_SECONDS = 600  # 10 分钟短窗
        self._WEATHER_DEDUPE_MAX_ENTRIES = 512

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析中国气象局气象预警数据。"""
        try:
            # OpenQuakeAPI RealtimeEvent 解包：外层有 source/type/action/payload
            # 返回 (payload, is_realtime)；is_realtime=True 表示已确认是 RealtimeEvent
            # 但被丢弃（如 action=remove / 非 weather 类型），此时不再回退到
            # FAN Studio 扁平载荷提取，避免把 RealtimeEvent 外层当扁平预警误处理。
            realtime_result = self._extract_realtime_payload(data)
            if realtime_result is not None:
                msg_data, is_realtime = realtime_result
                if not is_realtime:
                    return None
            else:
                # 非 RealtimeEvent 结构：回退到 FAN Studio 扁平/嵌套载荷提取
                msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 过滤心跳包
            if self._is_heartbeat_message(msg_data):
                return None

            # 内存中判定当前事件ID是否已被处理过，避免同一预警短时多次派发
            # 这里也属于去重阶段的日志，如果提示检测到重复，应由 plugin_logger 输出
            weather_id = msg_data.get("id")
            if weather_id and self._is_weather_duplicate(str(weather_id)):
                plugin_logger.info(
                    f"[灾害预警] {self.source_id} 检测到重复的气象预警ID: {weather_id}，忽略",
                    is_event_linked=True,
                    event_stream="weather_alarm",
                )
                return None

            # 对数据源字段完整性做检查，缺失关键字段时记录 debug 方便诊断
            required_fields = ["id", "effective", "description"]
            missing_fields = [
                field
                for field in required_fields
                if field not in msg_data or msg_data[field] is None
            ]
            if missing_fields:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 气象预警数据缺少关键字段: {missing_fields}"
                )

            effective_time = self._parse_datetime(msg_data.get("effective", ""))

            # 预警发布时间优先尝试从标识尾部编码中提取，失败时回退到生效时间。
            issue_time = None
            id_str = msg_data.get("id", "")
            if "_" in id_str:
                time_part = id_str.split("_")[-1]
                if len(time_part) >= 12:
                    try:
                        year = int(time_part[0:4])
                        month = int(time_part[4:6])
                        day = int(time_part[6:8])
                        hour = int(time_part[8:10])
                        minute = int(time_part[10:12])
                        second = int(time_part[12:14]) if len(time_part) >= 14 else 0
                        issue_time = datetime(year, month, day, hour, minute, second)
                    except (ValueError, IndexError):
                        issue_time = effective_time
                else:
                    issue_time = effective_time
            else:
                issue_time = effective_time

            headline = msg_data.get("headline", "")
            title = msg_data.get("title", "") or headline
            description = msg_data.get("description", "")

            # 评估是否有实质展示意义：若标题、名称与具体描述全部缺失，判定为垃圾或测试消息，直接略过
            if not title and not headline and not description:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = f"[灾害预警] {self.source_id} 气象预警缺少标题、名称和描述信息，跳过处理"
                    if self._should_log_warning("missing_weather_fields", warning_msg):
                        plugin_logger.debug(warning_msg)
                return None

            source_entry = get_source_entry(self.source_id)

            # 多重回退以解析气象编码
            weather_code = str(
                msg_data.get("weather_type")
                or msg_data.get("weatherType")
                or msg_data.get("alertCode")
                or msg_data.get("alert_code")
                or msg_data.get("code")
                or msg_data.get("type")
                or ""
            ).strip()

            # 整合元数据
            metadata = {
                "issue_time": issue_time,
                "weather_type": weather_code,
                "weather_code": weather_code,
                "type": weather_code,
                "alert_code": weather_code,
                "code": weather_code,
                "longitude": msg_data.get("longitude"),
                "latitude": msg_data.get("latitude"),
                "title": title,
                "headline": headline,
                "description": description,
                "source_family": source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "weather",
            }

            # 实例化气象领域模型
            event_id = str(msg_data.get("id", "") or "")
            domain_event = WeatherEvent(
                title=title,
                headline=headline,
                effective_at=effective_time,
                metadata=dict(metadata),
            )

            # 构造身份标识
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="weather_alarm",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=issue_time or effective_time,
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )

            # 装配统一事件包裹
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "fan_studio",
                    message_type=str(msg_data.get("type") or "weatheralert").strip(),
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            # 加入防重去噪队列中
            if envelope.id:
                self._remember_weather_id(str(envelope.id))

            plugin_logger.info(
                f"[灾害预警] 气象预警解析成功: {domain_event.title or domain_event.headline}, 生效时间: {issue_time}",
                is_event_linked=True,
                event_stream="weather_alarm",
                is_silent_window=True,
            )

            return envelope
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析气象预警数据失败: {exc}, 数据内容: {data}"
            )
            return None

    def _is_weather_duplicate(self, weather_id: str) -> bool:
        """判断预警 id 是否处于短窗去重窗口内。"""
        if not weather_id:
            return False
        last_ts = self._processed_weather_ids.get(weather_id)
        if last_ts is None:
            return False
        return (time.monotonic() - last_ts) <= self._WEATHER_DEDUPE_WINDOW_SECONDS

    def _remember_weather_id(self, weather_id: str) -> None:
        """登记预警 id 的处理时间，并控制缓存容量。"""
        if not weather_id:
            return
        self._processed_weather_ids[weather_id] = time.monotonic()
        if len(self._processed_weather_ids) > self._WEATHER_DEDUPE_MAX_ENTRIES:
            # 超出容量上限时清理最旧的记录，避免内存无限增长。
            oldest_key = min(
                self._processed_weather_ids,
                key=self._processed_weather_ids.get,
            )
            self._processed_weather_ids.pop(oldest_key, None)

    def _extract_realtime_payload(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, bool] | None:
        """解包 OpenQuakeAPI RealtimeEvent 外层结构。

        RealtimeEvent 格式：
            {source, type, action, timestampMs, payload: {...}}

        返回 (payload, is_realtime)：
        - 返回 None：非 RealtimeEvent 结构（无 payload 字段），
          由调用方回退到 FAN Studio 扁平载荷提取。
        - 返回 (None, True)：已确认是 RealtimeEvent 但被丢弃
          （action=remove / 非 weather 类型 / payload 非 dict），
          调用方不应回退到扁平载荷解析。
        - 返回 (payload, True)：成功解包的气象预警载荷。
        """
        if not isinstance(data, dict):
            return None
        # 必须同时具备 payload 和 source/type/action 才视为 RealtimeEvent
        if "payload" not in data:
            return None
        if not any(key in data for key in ("source", "type", "action")):
            return None

        msg_type = str(data.get("type") or "").strip().lower()
        action = str(data.get("action") or "").strip().lower()

        # 仅处理气象预警新增事件；其余情况确认是 RealtimeEvent 但直接丢弃
        if msg_type and msg_type != "weather":
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 忽略非 weather 类型的 RealtimeEvent: "
                f"{msg_type or '未知'}"
            )
            return None, True
        if action and action not in ("new", ""):
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 忽略非 new 的气象 action: {action}"
            )
            return None, True

        payload = data.get("payload")
        if not isinstance(payload, dict):
            return None, True
        return payload, True
