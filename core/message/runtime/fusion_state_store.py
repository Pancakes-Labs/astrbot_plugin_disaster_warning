"""
融合状态仓储。
负责维护 CENC / CWA EEW 融合过程中使用的 pending 队列与 Wolfx 缓存，
减少 MessagePushManager 对状态容器与清理逻辑的直接持有。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ....utils.converters import safe_float_convert
from ....utils.time_converter import TimeConverter
from ...domain.event_identity import EventIdentity
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...domain.event_payload import SourcePayload


class FusionStateStore:
    """融合状态存储与清理服务。"""

    def __init__(self, ttl_seconds: int = 120):
        # 两组 pending 与缓存分别服务于 CENC 融合和 CWA EEW 融合。
        self.ttl_seconds = ttl_seconds
        self.cenc_pending: dict[str, dict[str, Any]] = {}
        self.cenc_wolfx_cache: dict[str, dict[int, dict[str, Any]]] = {}
        self.cwa_eew_pending: dict[str, dict[str, Any]] = {}
        self.cwa_eew_wolfx_cache: dict[str, dict[int, dict[str, Any]]] = {}

    def prune(self) -> None:
        """清理融合 pending 与缓存中过期条目。"""
        now_ts = time.time()
        ttl = self.ttl_seconds

        for pending_dict in [self.cenc_pending, self.cwa_eew_pending]:
            expired_keys: list[str] = []
            for pending_key, item in pending_dict.items():
                if not isinstance(item, dict):
                    expired_keys.append(pending_key)
                    continue
                created_at = float(item.get("created_at", 0.0) or 0.0)
                if created_at > 0 and (now_ts - created_at) > ttl:
                    future = item.get("future")
                    # 超时 pending 在清理时主动唤醒 future，保证等待协程能自然回落到 timeout 路径。
                    if (
                        future is not None
                        and hasattr(future, "done")
                        and not future.done()
                    ):
                        future.set_result("timeout")
                    expired_keys.append(pending_key)
            for pending_key in expired_keys:
                pending_dict.pop(pending_key, None)

        for cache_dict in [self.cenc_wolfx_cache, self.cwa_eew_wolfx_cache]:
            expired_event_keys: list[str] = []
            for event_key, reports in cache_dict.items():
                if not isinstance(reports, dict):
                    expired_event_keys.append(event_key)
                    continue

                expired_reports: list[int] = []
                for report_num, payload in reports.items():
                    created_at = 0.0
                    if isinstance(payload, dict):
                        created_at = float(payload.get("created_at", 0.0) or 0.0)
                    if created_at > 0 and (now_ts - created_at) > ttl:
                        expired_reports.append(report_num)

                for report_num in expired_reports:
                    reports.pop(report_num, None)

                if not reports:
                    expired_event_keys.append(event_key)

            for event_key in expired_event_keys:
                cache_dict.pop(event_key, None)

    @staticmethod
    def _normalize_report_num(raw_value: Any) -> int:
        """把任意报次值稳妥归一为正整数。"""
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return 1
        except Exception:
            return 1
        return value if value > 0 else 1

    @staticmethod
    def _normalize_created_at(raw_value: Any) -> float:
        """把缓存/等待项中的 created_at 安全归一为浮点时间戳。"""
        try:
            return float(raw_value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        except Exception:
            return 0.0

    @staticmethod
    def normalize_cenc_measurement_type(raw_value: Any) -> str:
        """把 CENC 测定类型统一归一为 automatic / reviewed / unknown。"""
        value = str(raw_value or "").strip()
        if not value:
            return "unknown"

        value_lower = value.lower()
        if "正式测定" in value or value_lower == "reviewed":
            return "reviewed"
        if "自动测定" in value or value_lower == "automatic":
            return "automatic"
        return "unknown"

    @staticmethod
    def _extract_event_key_from_payload(payload: Any) -> str:
        """从统一原始载荷中回退提取可用于融合的事件键。"""
        if isinstance(payload, SourcePayload):
            raw = payload.raw
        elif isinstance(payload, dict):
            raw = payload
        else:
            raw = {}

        for key in (
            "eventId",
            "EventID",
            "event_id",
            "originId",
            "OriginID",
            "id",
            "ID",
            "md5",
        ):
            value = str(raw.get(key) or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def get_fusion_event_key(
        cls,
        data: EarthquakeEvent | EventEnvelope | object,
    ) -> str:
        """融合事件键：优先 identity.event_id，其次回退 metadata/payload。"""
        if isinstance(data, EventEnvelope):
            identity = getattr(data, "identity", None)
            if isinstance(identity, EventIdentity):
                event_id = str(identity.event_id or "").strip()
                if event_id:
                    return event_id

            payload_event_id = cls._extract_event_key_from_payload(
                getattr(data, "payload", None)
            )
            if payload_event_id:
                return payload_event_id

            metadata = getattr(data, "metadata", None)
            if isinstance(metadata, dict):
                for key in ("event_id", "eventId", "EventID", "id", "ID", "md5"):
                    value = str(metadata.get(key) or "").strip()
                    if value:
                        return value
            return str(data.id or "").strip()

        if isinstance(data, EarthquakeEvent):
            metadata = getattr(data, "metadata", None)
            if isinstance(metadata, dict):
                for key in (
                    "event_id",
                    "eventId",
                    "EventID",
                    "originId",
                    "OriginID",
                    "id",
                    "ID",
                    "md5",
                ):
                    value = str(metadata.get(key) or "").strip()
                    if value:
                        return value
            return str(getattr(data, "id", "") or "").strip()

        return str(getattr(data, "id", "") or "").strip()

    @classmethod
    def get_fusion_report_num(
        cls,
        data: EarthquakeEvent | EventEnvelope | object,
    ) -> int:
        """融合序号：优先 identity.report_num，其次回退 metadata/payload。"""
        try:
            if isinstance(data, EventEnvelope):
                identity = getattr(data, "identity", None)
                if (
                    isinstance(identity, EventIdentity)
                    and identity.report_num is not None
                ):
                    return cls._normalize_report_num(identity.report_num)

                payload = getattr(data, "payload", None)
                raw = (
                    payload.raw
                    if isinstance(payload, SourcePayload)
                    else payload
                    if isinstance(payload, dict)
                    else {}
                )
                for key in ("report_num", "ReportNum", "updates", "serial", "Serial"):
                    if key in raw and raw.get(key) is not None:
                        return cls._normalize_report_num(raw.get(key))

                metadata = getattr(data, "metadata", None)
                if isinstance(metadata, dict):
                    for key in ("report_num", "updates", "serial"):
                        if key in metadata and metadata.get(key) is not None:
                            return cls._normalize_report_num(metadata.get(key))
                return 1

            if isinstance(data, EarthquakeEvent):
                metadata = getattr(data, "metadata", None)
                if isinstance(metadata, dict):
                    for key in ("report_num", "updates", "serial"):
                        if key in metadata and metadata.get(key) is not None:
                            return cls._normalize_report_num(metadata.get(key))
                return 1

            return 1
        except Exception:
            return 1

    @classmethod
    def select_cenc_cached_payload(
        cls,
        reports: dict[int, dict[str, Any]],
        target_report: int,
        measurement_type: str,
    ) -> dict[str, Any] | None:
        """选择 CENC 融合缓存：优先同测定类型，其次回退最近缓存。"""
        if not reports:
            return None

        normalized_type = cls.normalize_cenc_measurement_type(measurement_type)
        preferred = reports.get(target_report)
        if isinstance(preferred, dict):
            preferred_type = cls.normalize_cenc_measurement_type(
                preferred.get("measurement_type")
            )
            if normalized_type == "unknown" or preferred_type == normalized_type:
                return preferred

        matching_candidates: list[tuple[int, dict[str, Any]]] = []
        fallback_candidates: list[tuple[int, dict[str, Any]]] = []
        for report_num, payload in reports.items():
            if not isinstance(payload, dict):
                continue
            fallback_candidates.append((report_num, payload))
            payload_type = cls.normalize_cenc_measurement_type(
                payload.get("measurement_type")
            )
            if normalized_type != "unknown" and payload_type == normalized_type:
                matching_candidates.append((report_num, payload))

        if matching_candidates:
            matching_candidates.sort(
                key=lambda item: (
                    abs(int(item[0]) - int(target_report)),
                    -FusionStateStore._normalize_created_at(item[1].get("created_at")),
                )
            )
            return matching_candidates[0][1]

        if normalized_type == "unknown" and fallback_candidates:
            fallback_candidates.sort(
                key=lambda item: -FusionStateStore._normalize_created_at(
                    item[1].get("created_at")
                )
            )
            return fallback_candidates[0][1]

        return None

    @staticmethod
    def _parse_similarity_time(raw_value: Any) -> datetime | None:
        """复用统一时间转换工具，解析近似匹配所需的时间字段。"""
        return TimeConverter.parse_datetime(raw_value)

    @staticmethod
    def _safe_float(raw_value: Any) -> float | None:
        """复用统一浮点转换工具，避免在融合仓储内重复实现数值清洗。"""
        return safe_float_convert(raw_value)

    @classmethod
    def build_cenc_similarity_profile(
        cls,
        data: EventEnvelope | EarthquakeEvent | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """提取 CENC 近似匹配所需的时空与震级特征。

        这里故意只保留“跨源同时稳定存在”的字段：
        发震时间、经纬度、震级。
        这样既能避开 event_key 不一致的问题，也能把回退匹配约束在足够像同一场地震的候选上。
        """
        profile = {
            "occurred_at": None,
            "latitude": None,
            "longitude": None,
            "magnitude": None,
        }
        if data is None:
            return profile

        if isinstance(data, EventEnvelope):
            domain_event = getattr(data, "event", None)
            if isinstance(domain_event, EarthquakeEvent):
                profile["occurred_at"] = getattr(domain_event, "occurred_at", None)
                profile["latitude"] = getattr(domain_event, "latitude", None)
                profile["longitude"] = getattr(domain_event, "longitude", None)
                profile["magnitude"] = getattr(domain_event, "magnitude", None)
            return profile

        if isinstance(data, EarthquakeEvent):
            profile["occurred_at"] = getattr(data, "occurred_at", None)
            profile["latitude"] = getattr(data, "latitude", None)
            profile["longitude"] = getattr(data, "longitude", None)
            profile["magnitude"] = getattr(data, "magnitude", None)
            return profile

        if isinstance(data, dict):
            profile["occurred_at"] = cls._parse_similarity_time(
                data.get("occurred_at") or data.get("time") or data.get("shockTime")
            )
            profile["latitude"] = cls._safe_float(data.get("latitude"))
            profile["longitude"] = cls._safe_float(data.get("longitude"))
            profile["magnitude"] = cls._safe_float(data.get("magnitude"))
            return profile

        return profile

    @classmethod
    def _is_cenc_profile_compatible(
        cls,
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> bool:
        """判断两个 CENC 候选是否足够像同一场地震。

        注意这里仍然要求上层先完成测定类型过滤；
        本函数只负责比较时空与震级，不负责判断 reviewed / automatic 是否一致。
        """
        left_time = cls._parse_similarity_time(left.get("occurred_at"))
        right_time = cls._parse_similarity_time(right.get("occurred_at"))
        if left_time is None or right_time is None:
            return False
        if abs((left_time - right_time).total_seconds()) > 90:
            return False

        left_lat = cls._safe_float(left.get("latitude"))
        right_lat = cls._safe_float(right.get("latitude"))
        left_lon = cls._safe_float(left.get("longitude"))
        right_lon = cls._safe_float(right.get("longitude"))
        left_mag = cls._safe_float(left.get("magnitude"))
        right_mag = cls._safe_float(right.get("magnitude"))
        if None in (left_lat, right_lat, left_lon, right_lon, left_mag, right_mag):
            return False

        if abs(left_lat - right_lat) > 0.8:
            return False
        if abs(left_lon - right_lon) > 0.8:
            return False
        if abs(left_mag - right_mag) > 0.5:
            return False
        return True

    @classmethod
    def select_cenc_cached_payload_from_all(
        cls,
        cache_dict: dict[str, dict[int, dict[str, Any]]],
        target_report: int,
        measurement_type: str,
        reference_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """跨所有 CENC 缓存选择回退载荷：先按测定类型，再按时空与震级相似度筛选。

        这是 Wolfx 先到、Fan 后到场景的兜底路径：
        当 event_key 无法对齐时，改为在同测定类型候选中寻找“最像同一场地震”的缓存条目。
        """
        if not cache_dict:
            return None

        normalized_type = cls.normalize_cenc_measurement_type(measurement_type)
        if normalized_type == "unknown":
            return None

        candidates: list[dict[str, Any]] = []
        for reports in cache_dict.values():
            if not isinstance(reports, dict):
                continue
            payload = cls.select_cenc_cached_payload(
                reports, target_report, measurement_type
            )
            if not isinstance(payload, dict):
                continue
            payload_type = cls.normalize_cenc_measurement_type(
                payload.get("measurement_type")
            )
            if payload_type != normalized_type:
                continue
            if reference_profile and not cls._is_cenc_profile_compatible(
                reference_profile, payload
            ):
                continue
            candidates.append(payload)

        if not candidates:
            return None
        candidates.sort(
            key=lambda item: FusionStateStore._normalize_created_at(
                item.get("created_at")
            ),
            reverse=True,
        )
        return candidates[0]

    @classmethod
    def select_cached_payload_from_all(
        cls,
        cache_dict: dict[str, dict[int, dict[str, Any]]],
        target_report: int,
        value_key: str,
    ) -> dict[str, Any] | None:
        """跨所有缓存按报次聚合回退：在时间窗口内取最近且包含目标值的缓存。"""
        if not cache_dict:
            return None

        candidates: list[dict[str, Any]] = []
        for reports in cache_dict.values():
            if not isinstance(reports, dict):
                continue
            payload = cls.select_cached_report_payload(reports, target_report)
            if not isinstance(payload, dict):
                continue
            if payload.get(value_key) is not None:
                candidates.append(payload)

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        newest_created_at = max(
            FusionStateStore._normalize_created_at(item.get("created_at"))
            for item in candidates
        )
        time_window_seconds = 15.0
        window_candidates = [
            item
            for item in candidates
            if abs(
                FusionStateStore._normalize_created_at(item.get("created_at"))
                - newest_created_at
            )
            <= time_window_seconds
        ]
        if not window_candidates:
            return None

        window_candidates.sort(
            key=lambda item: FusionStateStore._normalize_created_at(
                item.get("created_at")
            ),
            reverse=True,
        )
        return window_candidates[0]

    @staticmethod
    def select_cached_report_payload(
        reports: dict[int, dict[str, Any]], target_report: int
    ) -> dict[str, Any] | None:
        """按报次精确匹配缓存（保留给仍按报次推进的融合链路使用）。"""
        if not reports:
            return None
        return reports.get(target_report)

    @classmethod
    def find_best_cenc_pending_key(
        cls,
        pending_dict: dict[str, dict[str, Any]],
        event_key: str,
        measurement_type: str,
    ) -> str | None:
        """在同 event_key 的 pending 中优先匹配同测定类型；未命中时回退到同类型单候选，最后允许按短时间窗口选择最近等待项。"""
        normalized_type = cls.normalize_cenc_measurement_type(measurement_type)
        candidates = [
            (k, v)
            for k, v in pending_dict.items()
            if isinstance(v, dict) and v.get("event_key") == event_key
        ]

        if candidates:
            typed_candidates = [
                (k, v)
                for k, v in candidates
                if cls.normalize_cenc_measurement_type(v.get("measurement_type"))
                == normalized_type
            ]
            selected = typed_candidates or candidates
            selected.sort(
                key=lambda item: FusionStateStore._normalize_created_at(
                    item[1].get("created_at")
                )
            )
            return selected[0][0]

        if normalized_type == "unknown":
            return None

        global_typed_candidates = [
            (k, v)
            for k, v in pending_dict.items()
            if isinstance(v, dict)
            and cls.normalize_cenc_measurement_type(v.get("measurement_type"))
            == normalized_type
        ]
        if len(global_typed_candidates) == 1:
            return global_typed_candidates[0][0]

        if not global_typed_candidates:
            return None

        newest_created_at = max(
            FusionStateStore._normalize_created_at(item[1].get("created_at"))
            for item in global_typed_candidates
        )
        time_window_seconds = 30.0
        window_candidates = [
            (k, v)
            for k, v in global_typed_candidates
            if abs(
                FusionStateStore._normalize_created_at(v.get("created_at"))
                - newest_created_at
            )
            <= time_window_seconds
        ]
        if not window_candidates:
            return None

        window_candidates.sort(
            key=lambda item: FusionStateStore._normalize_created_at(
                item[1].get("created_at")
            ),
            reverse=True,
        )
        return window_candidates[0][0]

    @classmethod
    def find_best_cenc_pending_key_by_profile(
        cls,
        pending_dict: dict[str, dict[str, Any]],
        measurement_type: str,
        reference_profile: dict[str, Any] | None,
    ) -> str | None:
        """按测定类型与事件时空特征，从 CENC pending 中挑选最相似候选。

        这是 Fan 先到、Wolfx 后到场景的兜底路径：
        只有在常规 event_key 匹配失败后才启用，尽量恢复功能，同时把串事件风险限制在较低水平。
        """
        normalized_type = cls.normalize_cenc_measurement_type(measurement_type)
        if normalized_type == "unknown" or not reference_profile:
            return None

        typed_candidates: list[tuple[str, dict[str, Any]]] = []
        for pending_key, item in pending_dict.items():
            if not isinstance(item, dict):
                continue
            if (
                cls.normalize_cenc_measurement_type(item.get("measurement_type"))
                != normalized_type
            ):
                continue
            profile = item.get("similarity_profile")
            if not isinstance(profile, dict):
                continue
            if cls._is_cenc_profile_compatible(reference_profile, profile):
                typed_candidates.append((pending_key, item))

        if not typed_candidates:
            return None
        typed_candidates.sort(
            key=lambda item: FusionStateStore._normalize_created_at(
                item[1].get("created_at")
            ),
            reverse=True,
        )
        return typed_candidates[0][0]

    @staticmethod
    def find_best_pending_key(
        pending_dict: dict[str, dict[str, Any]],
        event_key: str,
        report_num: int,
    ) -> str | None:
        """在同 event_key 的 pending 中按报次精确匹配；未命中时按时间窗口回退最近等待项。"""
        exact = [
            (k, v)
            for k, v in pending_dict.items()
            if isinstance(v, dict)
            and v.get("event_key") == event_key
            and int(v.get("report_num", 1) or 1) == report_num
        ]
        if exact:
            exact.sort(
                key=lambda item: FusionStateStore._normalize_created_at(
                    item[1].get("created_at")
                )
            )
            return exact[0][0]

        report_candidates = [
            (k, v)
            for k, v in pending_dict.items()
            if isinstance(v, dict) and int(v.get("report_num", 1) or 1) == report_num
        ]
        if len(report_candidates) == 1:
            return report_candidates[0][0]
        if not report_candidates:
            return None

        newest_created_at = max(
            FusionStateStore._normalize_created_at(item[1].get("created_at"))
            for item in report_candidates
        )
        time_window_seconds = 15.0
        window_candidates = [
            (k, v)
            for k, v in report_candidates
            if abs(
                FusionStateStore._normalize_created_at(v.get("created_at"))
                - newest_created_at
            )
            <= time_window_seconds
        ]
        if not window_candidates:
            return None

        window_candidates.sort(
            key=lambda item: FusionStateStore._normalize_created_at(
                item[1].get("created_at")
            ),
            reverse=True,
        )
        return window_candidates[0][0]
