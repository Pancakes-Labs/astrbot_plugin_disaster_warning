"""
S-Net 测站峰值观测服务。

将连续观测网数据写入峰值仓储，并维护内存 snet_stats。
不进入通用 events / total_events 统计链路。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from ....utils.converters import ScaleConverter
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...storage.snet_peak_repository import SnetPeakRepository


class SnetPeakService:
    """S-Net 峰值观测与统计服务。"""

    SOURCE_ID = "snet_msil"
    # 重大事件回溯阈值：日本震度 5弱（計測震度 >= 4.5）
    MAJOR_SHINDO_THRESHOLD = SnetPeakRepository.MAJOR_SHINDO_THRESHOLD

    def __init__(self, statistics_manager):
        self.manager = statistics_manager
        self.repository = SnetPeakRepository(statistics_manager.db)

    @staticmethod
    def is_snet_event(event: EventEnvelope) -> bool:
        """判断是否为 S-Net 观测事件。"""
        source_id = str(getattr(event, "source_id", "") or "").strip().lower()
        if source_id == SnetPeakService.SOURCE_ID:
            return True
        identity = getattr(event, "identity", None)
        identity_source = str(getattr(identity, "source_id", "") or "").strip().lower()
        return identity_source == SnetPeakService.SOURCE_ID

    async def refresh_stats_from_database(self) -> None:
        """从峰值表重建内存 snet_stats。"""
        summary = await self.repository.build_stats_summary()
        snet_stats = self.manager.stats.setdefault("snet_stats", {})
        snet_stats["station_count"] = int(summary.get("station_count") or 0)
        snet_stats["stations_with_peak"] = int(summary.get("stations_with_peak") or 0)
        snet_stats["global_max"] = summary.get("global_max")
        snet_stats["top_peaks"] = list(summary.get("top_peaks") or [])[:3]
        snet_stats["recent_peak_updates"] = list(
            summary.get("recent_peak_updates") or []
        )
        snet_stats["last_observation_at"] = summary.get("last_observation_at")

    async def observe_stations(
        self,
        stations: list[dict[str, Any]],
        *,
        observed_at: str | datetime | None = None,
        hit_threshold: float | None = None,
    ) -> dict[str, Any]:
        """观测一批测站并更新峰值表与内存统计。"""
        observed_at_text = self._normalize_observed_at(observed_at)
        results = await self.repository.upsert_station_peaks_batch(
            stations,
            observed_at=observed_at_text,
            hit_threshold=hit_threshold,
        )
        peak_updates = [row for row in results if row.get("peak_updated")]
        await self.refresh_stats_from_database()

        # 用本轮刷新的峰值补 recent 列表头部（保持“刚刷新”语义）
        if peak_updates:
            recent = self.manager.stats.setdefault("snet_stats", {}).setdefault(
                "recent_peak_updates", []
            )
            for row in sorted(
                peak_updates,
                key=lambda item: float(item.get("max_shindo") or -999.0),
                reverse=True,
            ):
                view = {
                    "station_id": str(row.get("station_id") or ""),
                    "station_name": str(
                        row.get("station_name") or row.get("station_id") or ""
                    ),
                    "shindo": float(row.get("max_shindo") or 0.0),
                    "shindo_label": ScaleConverter.format_measured_intensity_display(
                        row.get("max_shindo")
                    ),
                    "at": str(row.get("max_shindo_at") or observed_at_text),
                }
                recent = [
                    item
                    for item in recent
                    if str(item.get("station_id") or "") != view["station_id"]
                ]
                recent.insert(0, view)
            self.manager.stats["snet_stats"]["recent_peak_updates"] = recent[:20]
            self.manager.stats["snet_stats"]["last_observation_at"] = observed_at_text

        return {
            "observed_at": observed_at_text,
            "processed": len(results),
            "peak_updates": len(peak_updates),
            "results": results,
        }

    async def observe_event(self, event: EventEnvelope) -> dict[str, Any]:
        """从 EventEnvelope 提取测站并写入峰值。"""
        stations = self._extract_stations(event)
        observed_at = self._extract_observed_at(event)
        # hit_count 使用推送过滤阈值（若有），否则不累计
        hit_threshold = None
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        raw_min = metadata.get("min_shindo")
        if raw_min is not None:
            try:
                hit_threshold = float(raw_min)
            except (TypeError, ValueError):
                hit_threshold = None

        result = await self.observe_stations(
            stations,
            observed_at=observed_at,
            hit_threshold=hit_threshold,
        )
        return result

    async def clear_peaks(self) -> bool:
        """清空峰值表并重置内存统计。

        仅在数据库清空成功后才重置内存，避免前端显示已清空但旧峰值仍可恢复。
        """
        ok = await self.repository.clear_all()
        if not ok:
            return False
        snet_stats = self.manager.stats.setdefault("snet_stats", {})
        snet_stats["station_count"] = 0
        snet_stats["stations_with_peak"] = 0
        snet_stats["global_max"] = None
        snet_stats["top_peaks"] = []
        snet_stats["recent_peak_updates"] = []
        snet_stats["last_observation_at"] = None
        return True

    async def list_major_peak_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """重大事件回溯用：震度 >= 5弱 的测站峰值投影。"""
        return await self.repository.list_major_peak_events(
            min_shindo=self.MAJOR_SHINDO_THRESHOLD,
            limit=limit,
        )

    @staticmethod
    def _extract_stations(event: EventEnvelope) -> list[dict[str, Any]]:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        candidates = metadata.get("stations")
        if not isinstance(candidates, list) or not candidates:
            domain = event.event
            domain_meta = getattr(domain, "metadata", None)
            if isinstance(domain_meta, dict):
                candidates = domain_meta.get("stations")
        if not isinstance(candidates, list):
            return []
        return [item for item in candidates if isinstance(item, dict)]

    @staticmethod
    def _extract_observed_at(event: EventEnvelope) -> str:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        timestamp = str(metadata.get("timestamp") or "").strip()
        if timestamp:
            # MSIL 瓦片时间戳：YYYYMMDDHHMM00（UTC）
            try:
                dt = datetime.strptime(timestamp, "%Y%m%d%H%M00").replace(
                    tzinfo=timezone.utc
                )
                return dt.isoformat()
            except (TypeError, ValueError):
                pass

        domain = event.event
        if isinstance(domain, EarthquakeEvent) and domain.occurred_at is not None:
            occurred = domain.occurred_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            return occurred.isoformat()

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_observed_at(value: str | datetime | None) -> str:
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        text = str(value or "").strip()
        if text:
            # 兼容瓦片时间戳
            if len(text) == 14 and text.isdigit():
                try:
                    dt = datetime.strptime(text, "%Y%m%d%H%M00").replace(
                        tzinfo=timezone.utc
                    )
                    return dt.isoformat()
                except ValueError:
                    pass
            return text
        return datetime.now(timezone.utc).isoformat()


__all__ = ["SnetPeakService"]
