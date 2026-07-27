"""
统计管理总入口。
负责装配数据库、统计状态、事件聚合、查询服务与持久化能力，
作为 storage 目录中统计子系统的统一协调层。
"""

from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..domain.event_models import EventEnvelope
from ..services.geo.weather_region_resolver import WeatherRegionResolver
from ..services.identity.event_deduplication_service import EventDeduplicationService
from ..services.identity.event_identity import (
    ensure_utc_datetime,
    resolve_event_unique_key,
    resolve_source_id,
)
from .database_manager import DatabaseManager
from .history_dirty_data_cleanup_service import HistoryDirtyDataCleanupService
from .stats.event_stats_aggregator import EventStatsAggregator
from .stats.stats_event_support_service import StatsEventSupportService
from .stats.stats_load_service import StatsLoadService
from .stats.stats_query_service import StatsQueryService
from .stats.stats_record_service import StatsRecordService
from .stats.stats_repository import StatsRepository
from .stats.stats_rule_service import StatsRuleService
from .stats.stats_session_service import StatsSessionService
from .stats.stats_state_factory import StatsStateFactory


class StatisticsManager:
    """灾害预警统计管理器。

    负责维护统计模块共享状态，并协调数据库、规则、聚合、记录、查询与加载等子服务，
    对外提供统一的统计记录、查询摘要与状态重置入口。
    """

    def __init__(self, config: dict[str, Any] = None):
        """初始化统计管理器与全部协作组件。"""
        # 统计管理器负责持有共享统计状态，并装配查询、规则、聚合与持久化等子服务。
        self.config = config or {}
        self.display_timezone = self.config.get("display_timezone", "UTC+8")
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.stats_file = self.data_dir / "statistics.json"

        # 数据库负责保存历史事件明细与更新轨迹，供恢复与查询流程复用。
        self.db = DatabaseManager(self.data_dir / "events.db")
        self._db_initialized = False

        # 内存统计状态是管理端展示与统计查询的直接数据来源。
        self.stats: dict[str, Any] = StatsStateFactory.build_initial_stats()

        # 运行时去重集合
        self._recorded_event_ids = set()  # 全局去重（用于 total_events）
        self._recorded_source_event_ids = set()  # 源内去重（用于 by_source）
        self._recorded_cenc_official_region_ids = set()  # CENC 正式测定地区统计去重

        # 初始化去重器，用于复用统一事件指纹与判重能力。
        self.deduplicator = EventDeduplicationService()

        # 统计侧独立复用地区解析逻辑，避免继续依赖旧气象筛选逻辑。
        self._weather_region_resolver = WeatherRegionResolver()
        # 子服务按“查询/持久化/规则/辅助/会话/聚合/记录/加载”拆分，
        # 共同支撑统计记录、摘要查询与历史恢复流程。
        self.query_service = StatsQueryService(self.stats, self.display_timezone)
        self.repository = StatsRepository(self.data_dir, self.stats_file)
        self.rule_service = StatsRuleService(self)
        self.event_support_service = StatsEventSupportService(self)
        self.session_service = StatsSessionService(self)
        self.aggregator = EventStatsAggregator(self)
        self.record_service = StatsRecordService(self)
        self.load_service = StatsLoadService(self)
        # S-Net 峰值观测：独立于通用事件流，维护测站历史最大震度档案。
        # 延迟导入，避免 snet ↔ storage 包级循环依赖。
        from ..services.snet.snet_peak_service import SnetPeakService

        self.snet_peak_service = SnetPeakService(self)

    async def initialize(self):
        """异步初始化数据库并加载历史数据"""
        # 统计模块初始化分成“建库”和“恢复内存态”两步，且只执行一次。
        if not self._db_initialized:
            await self.db.initialize()
            self._db_initialized = True
            # 启动时清理历史脏数据（海啸重复折叠 + CWA 报告 id 复用污染）
            try:
                cleanup = HistoryDirtyDataCleanupService(self.db)
                await cleanup.run_once()
            except Exception as exc:
                logger.warning(f"[灾害预警] 历史脏数据清理跳过/失败: {exc}", exc_info=True)
            await self._load_stats()

    async def record_push(
        self,
        event: EventEnvelope,
        pushed_sessions: list[str] | None = None,
    ):
        """记录一次事件处理（无论是否推送）"""
        try:
            # 这里统一串联聚合、近期列表更新、会话统计与持久化流程。
            if not self._db_initialized:
                await self.initialize()

            # S-Net 是连续观测网：峰值已在轮询侧归档，这里只记会话推送统计，
            # 避免与 snet_poll_service._observe_station_peaks 双写 hit_count。
            if self.snet_peak_service.is_snet_event(event):
                pushed_sessions = pushed_sessions or []
                current_time = datetime.now(timezone.utc).isoformat()
                if pushed_sessions:
                    self.session_service.record_session_stats(
                        pushed_sessions, current_time
                    )
                self.save_stats()
                return

            # 先完成聚合，统一拿到本次写入所需的时间、来源与唯一标识。
            aggregate_result = await self.aggregator.aggregate_event(event)
            current_time = str(aggregate_result["current_time"])
            source_for_display = str(aggregate_result["source_for_display"])
            event_unique_id = str(aggregate_result["event_unique_id"])
            is_major = self.rule_service.is_major_event(event)

            # 最近事件列表保存全部近期摘要，是统计面板的主要事件缓存。
            await self.record_service.update_push_list(
                self.stats["recent_pushes"],
                event,
                source_id=source_for_display,
                event_unique_id=event_unique_id,
                current_time=current_time,
                max_len=100,
            )

            if is_major:
                # 重大事件列表只保留重点事件摘要，便于单独展示与查询。
                await self.record_service.update_push_list(
                    self.stats["major_events"],
                    event,
                    source_id=source_for_display,
                    event_unique_id=event_unique_id,
                    current_time=current_time,
                    max_len=50,
                    is_major=True,
                    persist_db=False,
                )

            pushed_sessions = pushed_sessions or []
            # 最后再刷新会话统计并落盘，确保前面的聚合结果已经全部写回内存态。
            self.session_service.record_session_stats(pushed_sessions, current_time)
            self.save_stats()

        except Exception as e:
            logger.error(f"[灾害预警] 记录统计数据失败: {e}")

    def get_unique_event_id(self, event: EventEnvelope) -> str:
        """获取统一事件唯一标识。

        优先复用身份服务解析出的稳定唯一键，若缺失则退回到“来源 + 事件 ID”组合，
        以便统计侧仍能维持基本去重能力。
        """
        resolved_key = resolve_event_unique_key(event)
        if resolved_key:
            return resolved_key
        return f"{resolve_source_id(event)}|{event.id}"

    def normalize_utc_datetime(self, value, source_id: str = ""):
        """统一时间到 UTC aware datetime，空值时回退当前 UTC 时间。"""
        return ensure_utc_datetime(value, source_id=source_id) or ensure_utc_datetime(
            None
        )  # type: ignore[arg-type]

    def _extract_region(self, text: str, strict: bool = False) -> str | None:
        """从文本中提取地区（省份）信息"""
        return self.event_support_service.extract_region(text, strict=strict)

    def _get_event_description(self, event: EventEnvelope) -> str:
        """生成简短的事件描述"""
        return self.event_support_service.get_event_description_from_envelope(event)

    def save_stats(self):
        """保存统计数据"""
        # 序列化与落盘细节统一交由 repository 处理。
        self.repository.save_stats(self.stats)

    async def reset_stats(self):
        """重置统计数据"""
        try:
            # 重置时既要重建内存统计状态，也要清空运行时去重集合和数据库记录。
            self.stats = StatsStateFactory.build_initial_stats()
            self._recorded_event_ids.clear()
            self._recorded_source_event_ids.clear()
            self._recorded_cenc_official_region_ids.clear()

            if self._db_initialized:
                await self.db.clear_all_events()
                await self.snet_peak_service.clear_peaks()

            # 重置后 query_service 仍引用旧 stats 字典，需要重新绑定。
            self.query_service = StatsQueryService(self.stats, self.display_timezone)
            self.save_stats()
            logger.info("[灾害预警] 统计数据已重置")

        except Exception as e:
            logger.error(f"[灾害预警] 重置统计数据失败: {e}")

    async def _load_stats(self):
        """加载统计数据。

        具体加载、迁移和恢复逻辑由专门的加载服务承接，主管理器只保留调度职责。
        """
        await self.load_service.load()

    async def refresh_derived_stats_from_database(self) -> None:
        """从数据库全量刷新前端统计卡片依赖的派生统计。"""
        if not self._db_initialized:
            await self.initialize()
            return
        await self.load_service.refresh_derived_stats_from_database()

    def _merge_stats(self, current: dict, saved: dict):
        """递归合并统计数据。

        该方法是对仓储层合并逻辑的轻量封装，便于加载服务在恢复历史状态时统一调用。
        """
        self.repository.merge_stats(current, saved)

    def get_summary(self) -> str:
        """获取统计摘要文本"""
        return self.query_service.get_summary()

    def get_trend_data(self, hours: int = 24) -> list[dict[str, Any]]:
        """获取趋势数据（最近N小时）"""
        return self.query_service.get_trend_data(hours)

    def get_heatmap_data(
        self, days: int = 180, year: int = None
    ) -> list[dict[str, Any]]:
        """获取日历热力图数据"""
        return self.query_service.get_heatmap_data(days=days, year=year)
