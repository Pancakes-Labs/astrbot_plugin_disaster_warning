"""
统计记录合并器。
负责事件摘要记录在 recent_pushes / major_events 中的匹配、合并与更新。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from .event_record_factory import EventRecordFactory


class EventRecordMerger:
    """事件记录合并器。"""

    @staticmethod
    def merge_existing_record(
        target_list: list[dict[str, Any]],
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
        earthquake_level: float | None = None,
        record_weather_description: bool = True,
    ) -> dict[str, Any] | None:
        if isinstance(event.event, EarthquakeEvent):
            # 地震事件允许保留报次演进历史，因此走专门的合并分支。
            return EventRecordMerger._merge_earthquake_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
                earthquake_level=earthquake_level,
            )

        if isinstance(event.event, TyphoonEvent):
            # 台风事件同样需要保留报次演进历史（路径点），走多报合并分支。
            return EventRecordMerger._merge_typhoon_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
            )

        if isinstance(event.event, TsunamiEvent):
            # 海啸按事件 ID 折叠多报，维护 history + update_count。
            return EventRecordMerger._merge_tsunami_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
            )

        if isinstance(event.event, WeatherEvent):
            # 气象事件通常按唯一标识覆盖最新摘要，不维护报次历史。
            return EventRecordMerger._merge_non_earthquake_record(
                target_list,
                event,
                source_id=source_id,
                event_unique_id=event_unique_id,
                current_time=current_time,
                description=description,
                record_weather_description=record_weather_description,
            )

        return None

    @staticmethod
    def _normalize_tsunami_event_key(value: Any) -> str:
        """规范化海啸事件键：支持 source|id / 裸 id。"""
        text = str(value or "").strip()
        if not text:
            return ""
        if "|" in text:
            return text.split("|", 1)[-1].strip()
        return text

    @staticmethod
    def _resolve_tsunami_event_keys(
        event: EventEnvelope,
        *,
        event_unique_id: str,
    ) -> tuple[str, str]:
        """返回 (real_event_id, bare_unique_id)。"""
        domain_event = event.event
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        event_meta = (
            domain_event.metadata
            if isinstance(domain_event, TsunamiEvent)
            and isinstance(domain_event.metadata, dict)
            else {}
        )
        real_event_id = str(
            event.id
            or metadata.get("event_id")
            or metadata.get("code")
            or event_meta.get("event_id")
            or event_meta.get("code")
            or ""
        ).strip()
        bare_unique = EventRecordMerger._normalize_tsunami_event_key(event_unique_id)
        if not bare_unique:
            bare_unique = EventRecordMerger._normalize_tsunami_event_key(real_event_id)
        if not real_event_id:
            real_event_id = bare_unique
        return real_event_id, bare_unique

    @staticmethod
    def _merge_tsunami_record(
        target_list: list[dict[str, Any]],
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
    ) -> dict[str, Any] | None:
        """海啸多报合并：同事件 ID 折叠，保留更新历史。"""
        if not isinstance(event.event, TsunamiEvent):
            return None

        real_event_id, bare_unique = EventRecordMerger._resolve_tsunami_event_keys(
            event, event_unique_id=event_unique_id
        )
        if not real_event_id and not bare_unique:
            return None

        cn_aliases = {"fan_studio_tsunami", "china_tsunami_fanstudio"}
        for i, record in enumerate(target_list):
            rec_source = str(
                record.get("source") or record.get("source_id") or ""
            ).strip()
            if rec_source and source_id and rec_source != source_id:
                if not (rec_source in cn_aliases and source_id in cn_aliases):
                    continue

            rec_real = str(record.get("real_event_id") or "").strip()
            rec_unique_bare = EventRecordMerger._normalize_tsunami_event_key(
                record.get("unique_id")
            )
            rec_event_id = str(record.get("event_id") or "").strip()

            is_match = False
            if real_event_id and rec_real and real_event_id == rec_real:
                is_match = True
            elif bare_unique and rec_unique_bare and bare_unique == rec_unique_bare:
                is_match = True
            elif real_event_id and rec_event_id and real_event_id == rec_event_id:
                is_match = True
            elif bare_unique and rec_event_id:
                if bare_unique == EventRecordMerger._normalize_tsunami_event_key(
                    rec_event_id
                ):
                    is_match = True

            if not is_match:
                continue

            old_record = record.copy()
            old_record.pop("history", None)
            if "history" not in record:
                record["history"] = []
            record["history"].insert(0, old_record)
            if len(record["history"]) > 50:
                record["history"] = record["history"][:50]

            next_count = int(record.get("update_count", 1) or 1) + 1
            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=next_count,
            )
            # 先清掉旧 report_num，避免无 batch 的日本海啸沿用上一报业务号。
            # apply_tsunami_fields 仅在解析到业务 batch 时写入 report_num。
            record.pop("report_num", None)
            EventRecordFactory.apply_tsunami_fields(record, event)
            if real_event_id:
                record["real_event_id"] = real_event_id
            # 无业务 batch：用系统更新序号区分各报（日本海啸常见）
            if record.get("report_num") in (None, "", 0):
                record["report_num"] = next_count or 1

            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None

    @staticmethod
    def _merge_earthquake_record(
        target_list: list[dict[str, Any]],
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
        earthquake_level: float | None,
    ) -> dict[str, Any] | None:
        domain_event = event.event
        if not isinstance(domain_event, EarthquakeEvent):
            return None

        identity = getattr(event, "identity", None)
        real_event_id = str(getattr(identity, "event_id", "") or "").strip()
        if not real_event_id:
            return None

        for i, record in enumerate(target_list):
            rec_source = record.get("source")
            rec_real_id = record.get("real_event_id")
            rec_legacy_id = record.get("event_id")
            # 同一来源下才尝试合并，避免不同数据源恰好 event_id 相同造成误命中。
            if rec_source != source_id:
                continue

            rec_unique_id = record.get("unique_id")
            is_match = False
            if rec_real_id and rec_real_id == real_event_id:
                is_match = True
            elif not rec_real_id and rec_legacy_id == real_event_id:
                is_match = True
            elif rec_unique_id and rec_unique_id == event_unique_id:
                is_match = True

            if not is_match:
                continue

            # 命中旧记录时，先把旧摘要压入 history，再用当前事件内容覆盖主记录。
            old_record = record.copy()
            old_record.pop("history", None)
            if "history" not in record:
                record["history"] = []
            record["history"].insert(0, old_record)
            if len(record["history"]) > 50:
                record["history"] = record["history"][:50]

            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=record.get("update_count", 1) + 1,
            )
            record["real_event_id"] = real_event_id
            EventRecordFactory.apply_earthquake_fields(
                record,
                event,
                earthquake_level=earthquake_level,
            )

            # 更新后的记录重新放回列表头部，保证最近一次报文始终排在最前面。
            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None

    @staticmethod
    def _merge_typhoon_record(
        target_list: list[dict[str, Any]],
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
    ) -> dict[str, Any] | None:
        """台风事件多报合并：维护路径点历史链条。

        与地震类似，每次台风观测报文都作为一个路径点保留在 history 中，
        主记录始终展示最新观测，level/wind_speed/pressure 仍存峰值。
        """
        domain_event = event.event
        if not isinstance(domain_event, TyphoonEvent):
            return None

        real_event_id = str(domain_event.typhoon_id or "").strip()
        if not real_event_id:
            return None

        for i, record in enumerate(target_list):
            rec_source = record.get("source")
            rec_real_id = record.get("real_event_id")
            rec_legacy_id = record.get("event_id")
            if rec_source != source_id:
                continue

            rec_unique_id = record.get("unique_id")
            is_match = False
            if rec_real_id and rec_real_id == real_event_id:
                is_match = True
            elif not rec_real_id and rec_legacy_id == real_event_id:
                is_match = True
            elif rec_unique_id and rec_unique_id == event_unique_id:
                is_match = True

            if not is_match:
                continue

            # 命中旧记录时，先把旧摘要压入 history，再用当前事件内容覆盖主记录。
            old_record = record.copy()
            old_record.pop("history", None)
            if "history" not in record:
                record["history"] = []
            record["history"].insert(0, old_record)
            if len(record["history"]) > 50:
                record["history"] = record["history"][:50]

            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=record.get("update_count", 1) + 1,
            )
            record["real_event_id"] = real_event_id
            EventRecordFactory.apply_typhoon_fields(record, event)

            # 更新后的记录重新放回列表头部，保证最近一次报文始终排在最前面。
            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None

    @staticmethod
    def _merge_non_earthquake_record(
        target_list: list[dict[str, Any]],
        event: EventEnvelope,
        *,
        source_id: str,
        event_unique_id: str,
        current_time: str,
        description: str,
        record_weather_description: bool = True,
    ) -> dict[str, Any] | None:
        for i, record in enumerate(target_list):
            rec_source = record.get("source")
            rec_unique_id = record.get("unique_id")
            if rec_source != source_id or rec_unique_id != event_unique_id:
                continue

            # 非地震事件命中后直接覆盖摘要内容，不再额外维护历史链条。
            EventRecordFactory.apply_common_fields(
                record,
                event,
                current_time=current_time,
                event_unique_id=event_unique_id,
                description=description,
                source_id=source_id,
                update_count=1,
            )
            record["subtitle"] = ""
            record["weather_detail"] = ""
            record.pop("history", None)

            if isinstance(event.event, WeatherEvent):
                EventRecordFactory.apply_weather_fields(
                    record,
                    event,
                    record_weather_description=record_weather_description,
                )
            elif isinstance(event.event, TsunamiEvent):
                EventRecordFactory.apply_tsunami_fields(record, event)
            elif isinstance(event.event, TyphoonEvent):
                EventRecordFactory.apply_typhoon_fields(record, event)

            updated_record = target_list.pop(i)
            target_list.insert(0, updated_record)
            return updated_record

        return None
