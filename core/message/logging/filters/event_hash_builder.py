"""
事件哈希构建器。
从 MessageLogger 中拆出事件类型识别与哈希生成逻辑，供日志去重复用。
"""

from __future__ import annotations

import hashlib
from typing import Any


class EventHashBuilder:
    """事件哈希构建器。"""

    def __init__(self, payload_extractor):
        # 载荷提取能力由外部注入，便于兼容不同消息封装结构。
        self._payload_extractor = payload_extractor

    def generate_event_hash(self, data: dict[str, Any], source_id: str) -> str:
        """根据消息内容生成事件去重哈希。"""
        # 先抽取 payload，再统一判定事件类型，以便不同来源消息走相同的哈希生成分支。
        payload = self._payload_extractor(data)
        hash_parts = [f"source:{source_id}"]
        event_type = self.detect_event_type(data, payload)
        hash_parts.append(f"etype:{event_type}")

        if event_type == "weather":
            return self.generate_weather_hash(payload, hash_parts)
        if event_type == "earthquake":
            return self.generate_earthquake_hash(payload, hash_parts)
        if event_type == "tsunami":
            return self.generate_tsunami_hash(payload, hash_parts)
        return self.generate_generic_hash(payload, hash_parts)

    def detect_event_type(self, data: dict[str, Any], payload: dict[str, Any]) -> str:
        """根据原始字典与业务载荷推断事件类型。"""
        msg_type = str(data.get("type", "") or payload.get("type", "")).lower()
        if any(k in msg_type for k in ["weather", "alarm", "warning"]):
            return "weather"
        if any(k in msg_type for k in ["tsunami", "津波"]):
            return "tsunami"
        if any(k in msg_type for k in ["eew", "quake", "earthquake", "地震"]):
            return "earthquake"

        data_str = str(data).lower() + str(payload).lower()
        if any(k in data_str for k in ["headline", "alert", "weather", "气象"]):
            return "weather"
        if any(k in data_str for k in ["tsunami", "津波", "海啸"]):
            return "tsunami"
        if any(k in data_str for k in ["earthquake", "地震", "magnitude", "震级"]):
            return "earthquake"
        return "generic"

    def generate_weather_hash(self, data: dict[str, Any], hash_parts: list[str]) -> str:
        """生成气象类消息的去重哈希。"""
        event_id = data.get("id") or data.get("alertId") or data.get("identifier")
        if event_id:
            hash_parts.append(f"wid:{event_id}")
            return "|".join(hash_parts)

        title_text = data.get("title") or data.get("headline") or ""
        if title_text:
            hash_parts.append(f"wh:{title_text[:30]}")

        area = data.get("areaDesc") or data.get("sender") or ""
        if area:
            hash_parts.append(f"wa:{area}")

        time_info = (
            data.get("effective")
            or data.get("issue_time")
            or data.get("time")
            or data.get("sendTime")
        )
        if time_info:
            hash_parts.append(f"wt:{str(time_info)[:16]}")

        return "|".join(hash_parts)

    def generate_earthquake_hash(
        self, data: dict[str, Any], hash_parts: list[str]
    ) -> str:
        # 地震类优先使用稳定 eventId；仅当缺失时才退化到时间/震级/坐标近似哈希。
        event_id = data.get("eventId") or data.get("EventID") or data.get("id")
        if event_id:
            hash_parts.append(f"eid:{event_id}")

            # OpenQuake/GQ 使用 revisionId；其他 EEW 使用 updates/ReportNum/Serial。
            report_num = (
                data.get("updates")
                or data.get("ReportNum")
                or data.get("Serial")
                or data.get("revisionId")
                or data.get("revision_id")
            )
            if report_num is not None and str(report_num) != "":
                hash_parts.append(f"rn:{report_num}")

            # 动作维度：同一事件的 update / archived / cancelled 应分别落盘。
            action = data.get("action")
            if action:
                hash_parts.append(f"act:{action}")

            if report_num is None or str(report_num) == "":
                updated = (
                    data.get("updated")
                    or data.get("updateTime")
                    or data.get("lastUpdateMs")
                    or data.get("timestampMs")
                )
                if updated:
                    hash_parts.append(f"up:{str(updated)}")

                mag = data.get("magnitude") or data.get("Magnitude")
                if mag:
                    hash_parts.append(f"m:{mag}")

            return "|".join(hash_parts)

        time_info = (
            data.get("shockTime")
            or data.get("time")
            or data.get("OriginTime")
            or data.get("originTimeIso")
            or data.get("originTimeMs")
        )
        if time_info:
            hash_parts.append(f"et:{str(time_info)[:16]}")

        mag = data.get("magnitude") or data.get("Magnitude")
        if mag:
            hash_parts.append(f"em:{mag}")

        lat = data.get("latitude") or data.get("Latitude")
        lon = data.get("longitude") or data.get("Longitude")
        if lat and lon:
            try:
                hash_parts.append(f"el:{float(lat):.1f},{float(lon):.1f}")
            except (ValueError, TypeError):
                pass

        action = data.get("action")
        if action:
            hash_parts.append(f"act:{action}")

        return "|".join(hash_parts)

    def generate_tsunami_hash(self, data: dict[str, Any], hash_parts: list[str]) -> str:
        """生成海啸类消息的去重哈希。"""
        # EQSC 使用 eventID；中国海啸常用 id/code；一并兼容
        event_id = (
            data.get("id")
            or data.get("code")
            or data.get("eventID")
            or data.get("eventId")
        )
        if event_id:
            hash_parts.append(f"tid:{event_id}")
            time_info = (
                data.get("issue_time") or data.get("time") or data.get("register")
            )
            if time_info:
                hash_parts.append(f"tt:{str(time_info)[:16]}")
            return "|".join(hash_parts)

        title = data.get("title") or ""
        if title:
            hash_parts.append(f"tt:{title}")

        time_info = data.get("issue_time") or data.get("time") or data.get("effective")
        if time_info:
            hash_parts.append(f"tm:{str(time_info)[:16]}")

        return "|".join(hash_parts)

    def generate_generic_hash(self, data: dict[str, Any], hash_parts: list[str]) -> str:
        """生成通用兜底哈希。"""
        for key in ["id", "ID", "eventId", "EventID", "code", "md5"]:
            if val := data.get(key):
                hash_parts.append(f"gid:{val}")
                return "|".join(hash_parts)

        content_hash = hashlib.md5(str(data).encode()).hexdigest()[:8]
        hash_parts.append(f"gh:{content_hash}")
        return "|".join(hash_parts)
