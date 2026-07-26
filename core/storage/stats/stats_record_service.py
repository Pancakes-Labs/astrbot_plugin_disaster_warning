"""
统计记录更新服务。
负责事件摘要记录在 recent_pushes / major_events 中的合并、数据库写入与列表裁剪，
减少 StatisticsManager 中的写模型编排职责。
"""

from __future__ import annotations

from astrbot.api import logger

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
)
from .event_record_factory import EventRecordFactory
from .event_record_merger import EventRecordMerger


class StatsRecordService:
    """统计记录更新服务。"""

    def __init__(self, manager):
        self.manager = manager

    async def update_push_list(
        self,
        target_list: list,
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        max_len: int = 100,
        is_major: bool = False,
        persist_db: bool = True,
    ) -> None:
        """更新事件摘要列表（支持合并更新与数据库同步）。"""
        # recent_pushes / major_events 在统计侧统一视为事件摘要缓存，仅通过 is_major / max_len 控制差异。
        description = (
            self.manager.event_support_service.get_event_description_from_envelope(
                event
            )
        )
        earthquake_level = (
            self.manager.event_support_service.get_earthquake_level(event.event)
            if isinstance(event.event, EarthquakeEvent)
            else None
        )

        updated_record = EventRecordMerger.merge_existing_record(
            target_list,
            event,
            source_id=source_id,
            event_unique_id=event_unique_id,
            current_time=current_time,
            description=description,
            earthquake_level=earthquake_level,
        )

        if updated_record is not None:
            # 命中已有记录时走 update，而非重复 insert，保证数据库中的同一事件多报按更新演进。
            if persist_db:
                try:
                    if is_major:
                        updated_record["is_major"] = True
                    await self.manager.db.update_event(source_id, updated_record)
                except Exception as e:
                    logger.error(f"[灾害预警] 更新数据库事件失败: {e}")
        else:
            push_record = EventRecordFactory.build_base_record(
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                earthquake_level=earthquake_level,
            )
            target_list.insert(0, push_record)

            if persist_db:
                try:
                    if is_major:
                        push_record["is_major"] = True
                    # 内存列表可能在重启/裁剪后丢失已有记录；写库前再查一次数据库，
                    # 避免海啸/台风等被反复 insert 成多行。
                    existing = await self._find_existing_db_record(push_record, event)
                    if existing:
                        # 实际是否抬升由 DatabaseManager 按内容指纹决定
                        next_count = int(existing.get("update_count", 1) or 1) + 1
                        push_record["update_count"] = next_count
                        # 以数据库已有行的定位键为准，避免 source/unique_id 别名导致 update 未命中。
                        existing_source = str(
                            existing.get("source")
                            or existing.get("source_id")
                            or push_record.get("source")
                            or source_id
                            or ""
                        ).strip()
                        if existing_source:
                            push_record["source"] = existing_source
                            push_record.setdefault("source_id", existing_source)
                        existing_unique = str(existing.get("unique_id") or "").strip()
                        if existing_unique:
                            push_record["unique_id"] = existing_unique
                        existing_real = str(existing.get("real_event_id") or "").strip()
                        if existing_real:
                            push_record["real_event_id"] = existing_real
                        # 海啸多报：用 update_count 作为报次，便于前端时间线展示
                        if isinstance(event.event, TsunamiEvent):
                            push_record["report_num"] = next_count
                            if not push_record.get("real_event_id"):
                                push_record["real_event_id"] = (
                                    existing_real or push_record.get("event_id")
                                )
                        await self.manager.db.update_event(
                            existing_source or source_id,
                            push_record,
                        )
                    else:
                        if isinstance(event.event, TsunamiEvent):
                            push_record.setdefault("report_num", 1)
                        await self.manager.db.insert_event(push_record)
                except Exception as e:
                    logger.debug(f"[灾害预警] 保存到数据库失败（可能已存在）: {e}")

        if len(target_list) > max_len:
            del target_list[max_len:]

    async def _find_existing_db_record(
        self,
        push_record: dict,
        event: EventEnvelope,
    ) -> dict | None:
        """按 real_event_id / unique_id 查找数据库中已有记录。"""
        source = str(push_record.get("source") or event.source_id or "").strip()
        real_event_id = str(push_record.get("real_event_id") or "").strip()
        unique_id = str(push_record.get("unique_id") or "").strip()
        db = self.manager.db

        if real_event_id and source:
            existing = await db.find_event_by_real_id(real_event_id, source)
            if existing:
                return existing

        if unique_id and source:
            finder = getattr(db, "find_event_by_unique_id", None)
            if callable(finder):
                existing = await finder(unique_id, source)
                if existing:
                    return existing

        # 海啸历史数据常见：real_event_id 为空、unique_id 仅为裸 id
        if isinstance(event.event, TsunamiEvent) and unique_id:
            finder = getattr(db, "find_event_by_unique_id", None)
            if callable(finder):
                bare = unique_id.split("|", 1)[-1]
                for candidate in dict.fromkeys(
                    [
                        unique_id,
                        bare,
                        f"{source}|{bare}" if source and bare else "",
                        f"china_tsunami_fanstudio|{bare}" if bare else "",
                        f"fan_studio_tsunami|{bare}" if bare else "",
                    ]
                ):
                    if not candidate:
                        continue
                    for src in dict.fromkeys(
                        [source, "china_tsunami_fanstudio", "fan_studio_tsunami"]
                    ):
                        if not src:
                            continue
                        existing = await finder(candidate, src)
                        if existing:
                            return existing
        return None
