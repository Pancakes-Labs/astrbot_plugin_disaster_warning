"""
原始消息过滤器。
从 MessageLogger 中拆出消息过滤判定链，专注于过滤原因识别。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger


class RawMessageFilter:
    """原始消息过滤器。"""

    def __init__(
        self,
        *,
        enabled: bool,
        filter_heartbeat: bool,
        filter_types: list[str],
        filter_p2p_areas: bool,
        filter_duplicate_events: bool,
        filter_connection_status: bool,
        filter_stats: dict[str, int],
        is_p2p_areas_message,
        is_duplicate_event,
        generate_event_hash,
        is_connection_status_message,
        try_parse_binary_message,
    ):
        # 过滤器本身不直接依赖主记录器对象，而是通过注入回调获取判定能力。
        self.enabled = enabled
        self.filter_heartbeat = filter_heartbeat
        self.filter_types = filter_types
        self.filter_p2p_areas = filter_p2p_areas
        self.filter_duplicate_events = filter_duplicate_events
        self.filter_connection_status = filter_connection_status
        self.filter_stats = filter_stats
        self._is_p2p_areas_message = is_p2p_areas_message
        self._is_duplicate_event = is_duplicate_event
        self._generate_event_hash = generate_event_hash
        self._is_connection_status_message = is_connection_status_message
        self._try_parse_binary_message = try_parse_binary_message

    def _check_openquake_api_type(self, data: dict[str, Any], source_id: str) -> str:
        """OpenQuakeAPI 聚合连接仅保留 earthquake（GQ）与 weather（CMA 气象）业务类型。

        字符串与字典两个入口共用该检查，避免字符串形式的 tsunami/station/status
        消息绕过过滤而增大日志存储与处理压力。
        """
        if "global_quake" in source_id.lower() or "openquake" in source_id.lower():
            inner_type = str(data.get("type") or "").lower()
            if inner_type not in ("earthquake", "weather"):
                return f"OpenQuakeAPI 非地震/气象业务JSON消息过滤: {inner_type}"
        return ""

    def should_filter_message(self, payload_data: Any, source_id: str = "") -> str:
        """判断是否应该过滤该消息，返回过滤原因。"""
        # 返回空字符串表示不过滤；返回原因文本则既用于统计也用于调试日志。
        if not self.enabled or not self.filter_heartbeat:
            return ""

        try:
            if isinstance(payload_data, str) and payload_data.strip():
                return self._handle_string_message(payload_data, source_id)
            if isinstance(payload_data, (bytes, bytearray, memoryview)):
                parsed_binary = self._try_parse_binary_message(
                    payload_data,
                    source=source_id,
                    message_type="websocket_message",
                    connection_info={"connection_type": "websocket"},
                )
                # 只有当二进制解析成功且确实是结构化 dict 时，我们才继续交给 common 过滤器
                if isinstance(parsed_binary, dict):
                    # 判断如果解析出来的不是实际需要的地震业务类型（比如是 status、heartbeat 或 ping/pong），直接执行过滤
                    inner_type = str(parsed_binary.get("type") or "").lower()
                    if inner_type != "earthquake":
                        return f"非地震业务的二进制消息过滤: {inner_type}"
                    return self.should_filter_message(parsed_binary, source_id)
                else:
                    # 对于无法成功解析出业务数据的二进制包（比如非地震业务的心跳握手或状态广播），选择不过滤直接拦截不记日志
                    return "未识别或不需要记录的二进制数据帧"
            if isinstance(payload_data, dict):
                reason = self._check_openquake_api_type(payload_data, source_id)
                if reason:
                    return reason
                return self._handle_dict_message(payload_data, source_id)
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            pass

        return ""

    def _handle_string_message(self, payload_data: str, source_id: str) -> str:
        """处理字符串形态的原始消息。"""
        try:
            data = json.loads(payload_data)
        except json.JSONDecodeError:
            logger.debug(
                f"[灾害预警] 消息记录器 - JSON解析失败，消息前100字符: {payload_data[:100]}..."
            )
            return ""

        msg_type = data.get("type", "")
        logger.debug(
            f"[灾害预警] 消息记录器 - 检查消息过滤，来源: {source_id}, 类型: {msg_type}, 数据长度: {len(payload_data)}"
        )

        # 字符串入口同样执行 OpenQuakeAPI 业务类型检查，
        # 与字典入口保持一致，避免 tsunami/station/status 等字符串消息绕过过滤。
        reason = self._check_openquake_api_type(data, source_id)
        if reason:
            return reason

        reason = self._handle_common_dict_checks(data, source_id)
        if reason:
            return reason

        nested_payload = data.get("payload_data")
        if isinstance(nested_payload, str):
            try:
                inner_data = json.loads(nested_payload)
                inner_reason = self._handle_inner_dict_checks(inner_data, source_id)
                if inner_reason:
                    return inner_reason
            except (json.JSONDecodeError, AttributeError):
                pass

        return ""

    def _handle_dict_message(self, payload_data: dict[str, Any], source_id: str) -> str:
        """处理字典形态的原始消息。"""
        msg_type = payload_data.get("type", "")
        logger.debug(
            f"[灾害预警] 消息记录器 - 检查字典类型消息，来源: {source_id}, 类型: {msg_type}"
        )
        return self._handle_common_dict_checks(payload_data, source_id)

    def _handle_inner_dict_checks(
        self, inner_data: dict[str, Any], source_id: str
    ) -> str:
        inner_type = inner_data.get("type", "").lower()
        if inner_type in self.filter_types:
            self.filter_stats["heartbeat_filtered"] += 1
            return f"内层消息类型过滤: {inner_type}"

        if self.filter_p2p_areas and self._is_p2p_areas_message(inner_data):
            self.filter_stats["p2p_areas_filtered"] += 1
            return "内层P2P节点状态消息"

        if self.filter_duplicate_events and self._is_duplicate_event(
            inner_data, source_id
        ):
            self.filter_stats["duplicate_events_filtered"] += 1
            return "内层重复事件"

        return ""

    def _handle_common_dict_checks(self, data: dict[str, Any], source_id: str) -> str:
        msg_type = data.get("type", "")
        # 通用过滤顺序按“类型 -> P2P节点状态 -> 重复事件 -> 连接状态”执行，
        # 保证统计口径稳定且便于理解每条消息被过滤的首要原因。
        if msg_type and msg_type.lower() in self.filter_types:
            self.filter_stats["heartbeat_filtered"] += 1
            logger.debug(f"[灾害预警] 消息记录器 - 消息类型过滤: {msg_type}")
            return f"消息类型过滤: {msg_type}"

        if self.filter_p2p_areas and self._is_p2p_areas_message(data):
            self.filter_stats["p2p_areas_filtered"] += 1
            return "P2P节点状态消息"

        if self.filter_duplicate_events:
            event_hash = self._generate_event_hash(data, source_id)
            is_duplicate = self._is_duplicate_event(data, source_id)
            if is_duplicate:
                self.filter_stats["duplicate_events_filtered"] += 1
                logger.debug(
                    f"[灾害预警] 消息记录器 - 重复事件过滤，哈希: {event_hash}"
                )
                return f"重复事件 (哈希: {event_hash})"
            elif event_hash:
                logger.debug(
                    f"[灾害预警] 消息记录器 - 事件哈希生成: {event_hash}, 允许记录"
                )

        if self.filter_connection_status and self._is_connection_status_message(data):
            self.filter_stats["connection_status_filtered"] += 1
            logger.debug("[灾害预警] 消息记录器 - 连接状态消息过滤")
            return "连接状态消息"

        return ""
