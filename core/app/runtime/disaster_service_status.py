"""
灾害服务状态投影服务。
负责聚合 DisasterWarningService 的运行状态、连接概览、子数据源状态、运行时长与活动数据源，
减少主服务类中的状态投影逻辑。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...services.query.source_runtime_query_service import SourceRuntimeQueryService


class DisasterServiceStatusService:
    """灾害服务状态整理服务。

    这里不直接负责连接、推送或存储动作，
    而是把主服务与若干子服务中的运行信息整理成便于展示和查询的结构化快照。
    """

    def __init__(self, service):
        # 主服务提供运行标志、连接任务、统计管理器等原始状态来源。
        self.service = service  # 主服务 DisasterWarningService 实例
        # 优先复用主服务已装配的运行时查询实例，避免重复创建；
        # 仅在主服务尚未注入时再本地兜底构造。
        self._source_runtime_query = getattr(
            service, "source_runtime_query", None
        ) or SourceRuntimeQueryService(service.config)

    def get_service_status(self) -> dict[str, Any]:
        """获取服务状态。"""
        connection_status = (
            self.service.ws_manager.get_all_connections_status()
        )  # 物理 WebSocket 连接活跃表
        # 活跃连接 / Global Quake 标记统一由 SourceRuntimeQueryService 计算，
        # 避免与 RealtimePayloadBuilder 口径漂移。
        metrics = self._source_runtime_query.resolve_active_connection_metrics(
            self.service,
            connection_status,
        )

        snapshot = self._source_runtime_query.build_runtime_snapshot(
            actual_connections=connection_status,
            running=self.service.running,
            start_time=self.service.start_time.isoformat()
            if hasattr(self.service, "start_time")
            else None,
            uptime=self.get_uptime(),
            active_websocket_connections=int(
                metrics.get("active_websocket_connections", 0) or 0
            ),
            message_logger_enabled=self.service.message_logger.enabled
            if self.service.message_logger
            else False,
            global_quake_connected=bool(metrics.get("global_quake_connected")),
        )
        # total_connections 已由 runtime snapshot 按 catalog 期望通道统计
        # （含 EQSC / S-Net / 已停用通道），此处不再二次累加。
        silence_status = None
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None and hasattr(coordinator, "get_status"):
            try:
                silence_status = coordinator.get_status()
            except Exception:
                silence_status = None

        return {
            **snapshot,
            # 统计摘要直接挂在顶层，便于管理端一次请求同时拿到运行状态与统计概览。
            "statistics_summary": self.service.statistics_manager.get_summary(),
            "startup_silence": silence_status,
        }

    def get_sub_source_status(self) -> dict[str, dict[str, bool]]:
        """获取所有子数据源的启用状态。"""
        return self._source_runtime_query.build_sub_source_status()

    def get_uptime(self) -> str:
        """获取服务运行时间。"""
        # 未启动时直接返回说明文本
        if not self.service.running or not hasattr(self.service, "start_time"):
            return "未运行"

        # 计算自启动到当前时间的间隔
        delta = datetime.now(timezone.utc) - self.service.start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        # 输出格式刻意保持中文短文本风格，便于直接显示到命令回复或管理端卡片中。
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分")
        parts.append(f"{seconds}秒")
        return "".join(parts)

    def get_active_data_sources(self) -> list[str]:
        """获取当前配置中已启用的活跃数据源列表。"""
        # 这里返回的是“配置上启用”的数据源标签集合，不等同于实时连接一定在线。
        return self._source_runtime_query.get_enabled_source_labels()
