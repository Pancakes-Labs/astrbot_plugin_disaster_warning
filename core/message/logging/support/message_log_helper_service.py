"""
消息日志辅助工具。
负责 P2P 区域消息判断、连接状态消息识别、载荷提取与二进制时间戳格式化。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class MessageLogHelperService:
    """消息日志辅助工具服务。"""

    @staticmethod
    def is_p2p_areas_message(data: dict[str, Any]) -> bool:
        """判断是否为 P2P areas 节点状态消息。"""
        if "areas" in data and isinstance(data["areas"], list):
            areas = data["areas"]
            if areas and all(
                isinstance(area, dict) and "peer" in area for area in areas[:3]
            ):
                return True
        return False

    @staticmethod
    def extract_payload(data: dict[str, Any]) -> dict[str, Any]:
        """提取实际业务载荷，兼容 FAN / P2P / Wolfx / OpenQuake 等结构。"""
        # 不同来源的消息封装层级不一致，这里统一剥离外层包装，方便后续哈希与展示逻辑复用。
        if not isinstance(data, dict):
            return {}

        if "Data" in data and isinstance(data["Data"], dict):
            return data["Data"]
        if "data" in data and isinstance(data["data"], dict):
            return data["data"]
        # OpenQuakeAPI RealtimeEvent: {source,type,action,timestampMs,payload:{...}}
        # 业务主键（id/revisionId 等）在内层 payload，必须解包，否则去重哈希会退化成
        # 仅 source+etype，导致同一来源后续所有地震更新都被当成重复事件过滤掉。
        if "payload" in data and isinstance(data["payload"], dict):
            inner = dict(data["payload"])
            # 保留外层动作/类型，便于区分 update / archived / cancelled 等不同日志。
            if "action" in data and "action" not in inner:
                inner["action"] = data["action"]
            if "type" in data and "type" not in inner:
                inner["type"] = data["type"]
            if "source" in data and "source" not in inner:
                inner["source"] = data["source"]
            if "timestampMs" in data and "timestampMs" not in inner:
                inner["timestampMs"] = data["timestampMs"]
            return inner
        if "code" in data and "issue" in data:
            return data
        if "type" in data and ("EventID" in data or "ID" in data):
            return data
        return data

    @staticmethod
    def is_connection_status_message(data: dict[str, Any]) -> bool:
        """判断是否为连接建立、断开等状态消息。"""
        msg_type = data.get("type", "").lower()
        if msg_type in ["connect", "disconnect", "connection", "status"]:
            return True

        connection_keywords = [
            "connected",
            "disconnected",
            "connection",
            "status",
            "online",
            "offline",
        ]
        message_str = str(data).lower()
        if any(keyword in message_str for keyword in connection_keywords):
            # 如果同时出现明显灾害业务词，则优先视为业务消息而不是单纯连接状态消息。
            disaster_keywords = [
                "earthquake",
                "地震",
                "震级",
                "magnitude",
                "tsunami",
                "海啸",
                "weather",
                "气象",
            ]
            if not any(keyword in message_str for keyword in disaster_keywords):
                return True

        return False

    @staticmethod
    def format_binary_timestamp(timestamp_ms: int) -> str:
        """格式化二进制消息中的毫秒级时间戳。"""
        if timestamp_ms <= 0:
            return "无数据"

        try:
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except (ValueError, OSError, OverflowError):
            return str(timestamp_ms)
