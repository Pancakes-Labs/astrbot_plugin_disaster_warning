"""台风历史重建服务。

负责冷启动时从 EQSC 列表生成领域事件并写入数据库，
避免把历史回填编排散落在 DisasterService / StatsLoadService 中。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ...domain.event_models import TyphoonEvent
from ...domain.typhoon import build_typhoon_event_envelope
from ...domain.typhoon.typhoon_values import clean_text, to_float
from ...storage.stats.event_record_factory import EventRecordFactory


class TyphoonHistoryRebuildService:
    """EQSC 历史台风冷启动重建服务。"""

    def __init__(
        self,
        *,
        enrichment_service: Any | None = None,
        statistics_manager: Any | None = None,
        min_records_to_skip: int = 5,
        fetch_timeout_seconds: float = 60.0,
    ) -> None:
        self._enrichment_service = enrichment_service
        self._statistics_manager = statistics_manager
        self._min_records_to_skip = max(0, int(min_records_to_skip))
        self._fetch_timeout_seconds = float(fetch_timeout_seconds)

    def bind(
        self,
        *,
        enrichment_service: Any | None = None,
        statistics_manager: Any | None = None,
    ) -> None:
        """允许服务初始化后补齐依赖。"""
        if enrichment_service is not None:
            self._enrichment_service = enrichment_service
        if statistics_manager is not None:
            self._statistics_manager = statistics_manager

    async def rebuild_db_from_eqsc_list(
        self,
        typhoon_list: list[dict[str, Any]],
        *,
        source_id: str = "typhoon_fanstudio",
        db: Any | None = None,
    ) -> int:
        """把 EQSC 原始列表转换为领域事件后入库。"""
        database = db
        if database is None and self._statistics_manager is not None:
            database = getattr(self._statistics_manager, "db", None)
        if database is None or not typhoon_list:
            return 0

        inserted = 0
        for typhoon in typhoon_list:
            if not isinstance(typhoon, dict):
                continue
            try:
                record = self.build_eqsc_typhoon_db_record(typhoon, source_id=source_id)
                if not record:
                    continue
                existing = await database.find_event_by_real_id(
                    record["real_event_id"], record["source"]
                )
                if existing:
                    continue
                new_id = await database.insert_event(record)
                inserted += 1

                # 把 EQSC historyTrack 中的每个有效观测节点批量写入 event_updates，
                # 使前端能展示完整的多报路径点（与实时推送的多报折叠体验一致）。
                track_nodes = self._extract_track_nodes(typhoon, record)
                if track_nodes and new_id:
                    track_count = await database.insert_typhoon_track_updates(
                        new_id,
                        track_nodes,
                        source_event_id=record.get("event_id"),
                    )
                    if track_count > 0:
                        logger.debug(
                            f"[灾害预警] 台风 {record.get('real_event_id')} "
                            f"写入 {track_count} 个路径点"
                        )
            except Exception as exc:
                logger.debug(f"[灾害预警] EQSC 台风记录入库失败: {exc}")
                continue

        if inserted > 0:
            logger.info(
                f"[灾害预警] EQSC 台风数据库重建完成，共插入 {inserted} 条历史台风记录"
            )
        return inserted

    @classmethod
    def build_eqsc_typhoon_db_record(
        cls,
        typhoon: dict[str, Any],
        *,
        source_id: str = "typhoon_fanstudio",
    ) -> dict[str, Any] | None:
        """通过领域事件与记录工厂生成 EQSC 历史数据库记录。"""
        envelope = build_typhoon_event_envelope(typhoon, source_id=source_id)
        if not envelope or not isinstance(envelope.event, TyphoonEvent):
            return None

        domain_event = envelope.event
        event_time = (
            domain_event.updated_at.isoformat() if domain_event.updated_at else None
        )
        name = domain_event.name or domain_event.name_en or domain_event.typhoon_id
        description = f"{domain_event.typhoon_type} {name}".strip()
        record = EventRecordFactory.build_base_record(
            envelope,
            current_time=event_time or "",
            event_unique_id=domain_event.typhoon_id,
            description=description,
        )
        # 历史时间必须来自观测峰值，避免刚启动重建时被当前时间顶到列表最前。
        record["created_at"] = event_time
        record["updated_at"] = event_time
        record["is_major"] = False
        return record

    @staticmethod
    def _extract_track_nodes(
        typhoon: dict[str, Any],
        record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """从 EQSC 原始台风对象中提取有效历史路径节点。

        把 historyTrack 中每个有效观测点转换为 event_updates 兼容的字典，
        包含 time / level / wind_speed / pressure / latitude / longitude 等字段。
        节点按时间升序排列（最旧在前），与 event_updates 的 recorded_at ASC 一致。
        """
        history_track = (
            typhoon.get("historyTrack") or typhoon.get("history_track") or []
        )
        if not isinstance(history_track, list):
            return []

        nodes: list[dict[str, Any]] = []
        for node in history_track:
            if not isinstance(node, dict):
                continue
            time_text = clean_text(node.get("time"))
            level = clean_text(node.get("typeNameCN") or node.get("type"))
            wind_speed = to_float(node.get("windSpeed") or node.get("wind_speed"))
            if wind_speed is not None and wind_speed <= 0:
                wind_speed = None
            pressure = to_float(node.get("pressure"))
            if pressure is not None and pressure <= 0:
                pressure = None
            latitude = to_float(node.get("latitude"))
            longitude = to_float(node.get("longitude"))
            # 至少要有时间和一个有效观测值才纳入路径点
            if not time_text:
                continue
            if wind_speed is None and pressure is None and level is None:
                continue
            nodes.append(
                {
                    "time": time_text,
                    "level": level or "",
                    "wind_speed": wind_speed,
                    "pressure": pressure,
                    "latitude": latitude,
                    "longitude": longitude,
                    "description": record.get("description", ""),
                }
            )

        # 按时间升序排列（最旧在前），与 event_updates 的排序约定一致
        nodes.sort(key=lambda n: str(n.get("time") or ""))
        return nodes

    async def try_cold_start_rebuild(self) -> int:
        """冷启动重建：仅当本地台风记录不足阈值时执行。"""
        enrichment = self._enrichment_service
        stats_manager = self._statistics_manager
        if enrichment is None or stats_manager is None:
            return 0
        is_enabled = getattr(enrichment, "is_enabled", False)
        enabled = bool(is_enabled() if callable(is_enabled) else is_enabled)
        if not enabled:
            return 0

        try:
            await stats_manager.initialize()
            db_stats = await stats_manager.db.get_statistics()
            typhoon_count = 0
            if db_stats:
                by_type = db_stats.get("by_type", {}) or {}
                typhoon_count = int(by_type.get("typhoon", 0) or 0)

            if typhoon_count >= self._min_records_to_skip:
                logger.debug(
                    f"[灾害预警] 数据库已有 {typhoon_count} 条台风记录，跳过 EQSC 重建"
                )
                return 0

            logger.info(
                f"[灾害预警] 数据库台风记录仅 {typhoon_count} 条，尝试从 EQSC 重建历史数据..."
            )

            fetch_history = getattr(enrichment, "fetch_history_typhoons", None)
            if not callable(fetch_history):
                return 0

            try:
                typhoon_list = await asyncio.wait_for(
                    fetch_history(),
                    timeout=self._fetch_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[灾害预警] EQSC 历史台风拉取超时（{self._fetch_timeout_seconds:.0f}s），"
                    "本次跳过重建。不影响主服务与后续实时富化重试"
                )
                return 0

            if not typhoon_list:
                logger.info(
                    "[灾害预警] EQSC 未返回历史台风数据，跳过重建"
                    "（若同时出现 AccessToken 失败，请检查 refresh_token/网络/EQSC 可用性）"
                )
                return 0

            inserted = await self.rebuild_db_from_eqsc_list(typhoon_list)
            if inserted > 0:
                refresh = getattr(
                    stats_manager, "refresh_derived_stats_from_database", None
                )
                if callable(refresh):
                    await refresh()
                logger.info(
                    f"[灾害预警] EQSC 台风数据库重建完成，新增 {inserted} 条记录，已刷新统计"
                )
            else:
                logger.info("[灾害预警] EQSC 台风数据库重建无需插入新记录")
            return inserted
        except asyncio.CancelledError:
            logger.debug("[灾害预警] EQSC 台风数据库重建任务已取消")
            raise
        except Exception as exc:
            logger.warning(
                f"[灾害预警] EQSC 台风数据库重建失败（不影响主服务）: "
                f"{type(exc).__name__}: {exc or repr(exc)}"
            )
            return 0


__all__ = ["TyphoonHistoryRebuildService"]
