"""
事件统计聚合器。
负责处理 StatisticsManager 中的写模型聚合逻辑，
包括接收计数、唯一事件识别、按源统计、类型统计与时间序列更新。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TyphoonEvent,
    WeatherEvent,
)
from ..source_compat import build_source_stats_key, is_earthquake_supplement_product


class EventStatsAggregator:
    """事件统计聚合器。"""

    def __init__(self, manager):
        self.manager = manager

    async def aggregate_event(self, event: EventEnvelope) -> dict[str, object]:
        """聚合一次事件写入前的统计状态。"""
        current_time = datetime.now(timezone.utc).isoformat()
        stats = self.manager.stats
        # 每次接收到事件都刷新最后更新时间，便于外部观察统计状态的新鲜度。
        stats["last_updated"] = current_time

        if "total_received" not in stats:
            # 兼容旧版本字段名，首次运行时把旧计数字段平滑迁移过来。
            stats["total_received"] = stats.get("total_pushes", 0)
        stats["total_received"] += 1

        envelope = event
        source_id = envelope.source_id or "unknown"
        # 贡献统计键：台风 fan/enriched 合并；eqsc_rebuild 单独计数
        source_stats_key = self._resolve_source_stats_key(envelope)
        source_for_display = source_stats_key
        # 兼容补充去重逻辑，将 CENC 烈度速报和 CMT 均视为补充产品
        intensity_report = self._is_supplement_product(envelope)

        event_unique_id = self.manager.get_unique_event_id(event)
        # 源内唯一键用于统计同一来源下是否重复收到同一事件。
        # 使用贡献统计键，使历史重建与实时台风可分别计入 by_source。
        source_event_unique_id = f"{source_stats_key}:{event_unique_id}"

        if source_event_unique_id not in self.manager._recorded_source_event_ids:
            # 只有来源内首次出现时，才累加来源维度统计。
            stats["by_source"][source_stats_key] += 1
            self.manager._recorded_source_event_ids.add(source_event_unique_id)
            stats["recent_source_event_ids"].append(source_event_unique_id)
            if len(stats["recent_source_event_ids"]) > 2000:
                stats["recent_source_event_ids"] = stats["recent_source_event_ids"][
                    -2000:
                ]

        is_new_event = event_unique_id not in self.manager._recorded_event_ids
        if not is_new_event and isinstance(envelope.event, EarthquakeEvent):
            # 国内地区分布只统计 CENC 正式测定，但不应被其他来源先到造成的全局去重挡掉。
            self.manager.rule_service.record_cenc_official_region_stats(event)

        if is_new_event:
            # 无论是否计入全局事件数，都先登记唯一键，避免同源重复报文反复进入写路径。
            self.manager._recorded_event_ids.add(event_unique_id)
            stats["recent_event_ids"].append(event_unique_id)
            if len(stats["recent_event_ids"]) > 500:
                stats["recent_event_ids"] = stats["recent_event_ids"][-500:]

            # 烈度速报与 CMT 等是同一物理地震的补充产品：保留 by_source / 事件列表，
            # 但不计入 total_events、by_type、震级分布与时间序列，避免与正式测定双计。
            if not intensity_report:
                stats["total_events"] += 1

                event_type = envelope.event_type or "unknown"
                stats["by_type"][event_type] += 1

                if isinstance(envelope.event, EarthquakeEvent):
                    # 地震事件直接进入地震统计分桶与最大震级更新流程。
                    self.manager.rule_service.record_earthquake_stats(event)
                elif isinstance(envelope.event, WeatherEvent):
                    # 气象事件需要先完成地区解析，成功后才写入详细气象统计。
                    weather_stats_recorded = (
                        await self.manager.rule_service.record_weather_stats(
                            envelope.event
                        )
                    )
                    if weather_stats_recorded is False:
                        # 附带上下文供日志排障（事件ID/来源/标题/头条/提取地名）。
                        self.manager.rule_service.log_weather_stats_skip(
                            event_id=event_unique_id,
                            source_id=source_id,
                            title_text=str(getattr(envelope.event, "title", "") or ""),
                            headline_text=str(
                                getattr(envelope.event, "headline", "") or ""
                            ),
                        )
                    elif weather_stats_recorded is not True:
                        # record_weather_stats 返回 (False, context) 时带上解析细节。
                        self.manager.rule_service.log_weather_stats_skip(
                            event_id=event_unique_id,
                            source_id=source_id,
                            **weather_stats_recorded[1],
                        )

                self.manager.rule_service.record_time_series(event)

        # 台风统计在 is_new_event 分支外调用，使 by_level 统计每次推送频次，
        # by_max_level 跟踪台风发展过程中的最高等级变化，max_wind_typhoons 更新最大风速。
        if isinstance(envelope.event, TyphoonEvent):
            self.manager.rule_service.record_typhoon_stats(event)

        return {
            "current_time": current_time,
            "source_id": source_id,
            "source_for_display": source_for_display,
            "event_unique_id": event_unique_id,
            "is_new_event": is_new_event,
            "is_intensity_report": intensity_report,
        }

    @staticmethod
    def _resolve_info_type(envelope: EventEnvelope) -> str:
        """从 envelope / 领域事件元数据中提取 info_type。"""
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        for key in ("typhoon_data_mode", "info_type", "data_source"):
            raw = metadata.get(key)
            if raw:
                return str(raw).strip()

        event_metadata = getattr(envelope.event, "metadata", None)
        if isinstance(event_metadata, dict):
            for key in ("typhoon_data_mode", "info_type", "data_source"):
                raw = event_metadata.get(key)
                if raw:
                    return str(raw).strip()
        return ""

    @classmethod
    def _is_supplement_product(cls, envelope: EventEnvelope) -> bool:
        """判断当前事件是否为补充产品（CENC 烈度速报或 FSSN CMT）。"""
        return is_earthquake_supplement_product(
            envelope.source_id or "",
            info_type=cls._resolve_info_type(envelope),
        )

    @classmethod
    def _resolve_source_stats_key(cls, envelope: EventEnvelope) -> str:
        """解析写入 by_source 的贡献统计键。"""
        source_id = envelope.source_id or "unknown"
        event_type = envelope.event_type or ""
        info_type = cls._resolve_info_type(envelope)
        return build_source_stats_key(
            source_id,
            event_type=event_type,
            info_type=info_type,
        )
