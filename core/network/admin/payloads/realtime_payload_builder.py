"""
Web 管理端实时数据载荷构建器。
统一组装 WebSocket / 实时视图需要的状态、统计、连接与地震摘要数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .....utils.version import get_plugin_version
from ....services.display import build_admin_statistics_projection
from ....services.query.source_runtime_query_service import SourceRuntimeQueryService
from .connections_payload_builder import ConnectionsPayloadBuilder


class RealtimePayloadBuilder:
    """实时载荷构建器。"""

    def __init__(
        self,
        disaster_service,
        config: dict[str, Any],
        latency_cache: dict[str, float | None] | None = None,
    ):
        """初始化实时载荷构建器及其依赖。"""
        # 实时载荷构建器负责拼装多个子视图，因此长期持有查询器与连接载荷构建器。
        self.disaster_service = disaster_service
        self.config = config
        self.latency_cache = latency_cache if latency_cache is not None else {}
        self.source_runtime_query = (
            disaster_service.source_runtime_query
            if disaster_service
            else SourceRuntimeQueryService(config)
        )
        self._connections_payload_builder = ConnectionsPayloadBuilder(
            disaster_service=disaster_service,
            config=config,
            latency_cache=self.latency_cache,
        )

    async def build(
        self, expected_sources: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """构建实时数据。"""
        result: dict[str, Any] = {"timestamp": datetime.now().isoformat()}

        # 实时面板按“状态、统计、连接、近期地震、通知”五个区块组织数据，便于前端按模块渲染。
        # 只读取内存快照；数据库全量重建由启动加载与统计 API 显式刷新负责，避免阻塞 WebSocket 首包。
        result["status"] = self.build_status_payload()
        result["statistics"] = self.build_statistics_payload()
        result["connections"] = self.build_connections_payload(expected_sources)
        result["earthquakes"] = self._build_recent_earthquakes_payload()
        result["notifications"] = await self._build_notifications_payload()
        return result

    def _build_runtime_snapshot(self) -> dict[str, Any]:
        """构建供多个管理端接口复用的运行时快照。"""
        # 运行时快照是多个管理端接口共享的底层数据来源。
        if not self.disaster_service:
            return {}

        actual_connections = {}
        if getattr(self.disaster_service, "ws_manager", None):
            actual_connections = (
                self.disaster_service.ws_manager.get_all_connections_status()
            )

        # 活跃连接 / 标记统一由 SourceRuntimeQueryService 计算。
        metrics = self.source_runtime_query.resolve_active_connection_metrics(
            self.disaster_service,
            actual_connections,
        )
        snapshot = self.source_runtime_query.build_runtime_snapshot(
            actual_connections=actual_connections,
            latency_cache=self.latency_cache,
            running=bool(getattr(self.disaster_service, "running", False)),
            start_time=self.disaster_service.start_time.isoformat()
            if hasattr(self.disaster_service, "start_time")
            else None,
            uptime=self.disaster_service.get_uptime()
            if hasattr(self.disaster_service, "get_uptime")
            else "未运行",
            active_websocket_connections=int(
                metrics.get("active_websocket_connections", 0) or 0
            ),
            message_logger_enabled=self.disaster_service.message_logger.enabled
            if getattr(self.disaster_service, "message_logger", None)
            else False,
            openquake_connected=bool(metrics.get("openquake_connected")),
        )
        # total_connections 已在 snapshot 内按期望通道统计，避免 EQSC/S-Net 重复累加。
        return snapshot

    def build_status_payload(self) -> dict[str, Any]:
        """构建管理端状态面板所需的状态数据。"""
        snapshot = self._build_runtime_snapshot()
        if not snapshot:
            return {}

        return {
            "running": snapshot.get("running", False),
            "uptime": snapshot.get("uptime", "未知"),
            "active_connections": snapshot.get("active_websocket_connections", 0),
            "total_connections": snapshot.get("total_connections", 0),
            "connection_details": snapshot.get("connection_details", {}),
            "data_sources": snapshot.get("data_sources", []),
            "enabled_source_ids": snapshot.get("enabled_source_ids", []),
            "sub_source_status": snapshot.get("sub_source_status", {}),
            "message_logger_enabled": snapshot.get("message_logger_enabled", False),
            "eew_query_status": self.disaster_service.get_eew_query_status_data(),
            "start_time": snapshot.get("start_time"),
            "version": get_plugin_version(),
        }

    def build_statistics_payload(self) -> dict[str, Any]:
        """构建统计面板所需的数据。"""
        if not self.disaster_service or not self.disaster_service.statistics_manager:
            return {}

        statistics_manager = self.disaster_service.statistics_manager
        payload = build_admin_statistics_projection(
            statistics_manager.stats,
            log_stats=(
                self.disaster_service.message_logger.get_log_summary()
                if self.disaster_service.message_logger
                else {}
            ),
        )
        return self._enrich_session_stats_for_display(payload)

    def _enrich_session_stats_for_display(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """为会话推送统计补充 session_id 与备注名，便于前端短展示。"""
        if not isinstance(payload, dict):
            return payload

        session_stats = payload.get("session_stats")
        if not isinstance(session_stats, dict):
            return payload

        top_sessions = session_stats.get("top_sessions")
        if not isinstance(top_sessions, list) or not top_sessions:
            return payload

        session_config_manager = getattr(
            self.disaster_service, "session_config_manager", None
        )
        enriched_top_sessions: list[dict[str, Any]] = []

        for item in top_sessions:
            if not isinstance(item, dict):
                continue

            session = str(item.get("session") or "").strip()
            session_id = self._extract_session_id(session)
            session_name = ""
            if session and session_config_manager is not None:
                try:
                    session_name = str(
                        session_config_manager.get_session_name(session) or ""
                    ).strip()
                except Exception:
                    session_name = ""

            enriched_item = dict(item)
            enriched_item["session"] = session
            enriched_item["session_id"] = session_id
            enriched_item["session_name"] = session_name
            enriched_top_sessions.append(enriched_item)

        next_session_stats = dict(session_stats)
        next_session_stats["top_sessions"] = enriched_top_sessions
        next_payload = dict(payload)
        next_payload["session_stats"] = next_session_stats
        return next_payload

    @staticmethod
    def _extract_session_id(umo: str) -> str:
        """从完整 UMO 中提取尾部 session_id。"""
        if not isinstance(umo, str) or not umo:
            return ""

        known_types = (
            "FriendMessage",
            "GroupMessage",
            "PrivateMessage",
            "GuildMessage",
        )
        for msg_type in known_types:
            marker = f":{msg_type}:"
            idx = umo.find(marker)
            if idx != -1:
                return umo[idx + len(marker) :].strip() or umo

        parts = umo.split(":")
        if len(parts) >= 3:
            return parts[-1].strip() or umo
        return umo.strip()

    def build_connections_payload(
        self, expected_sources: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """构建连接状态视图。"""
        return self._connections_payload_builder.build(expected_sources)

    def build_status_api_payload(self) -> dict[str, Any]:
        """构建 /api/status 载荷。"""
        payload = self.build_status_payload().copy()
        payload["timestamp"] = datetime.now().isoformat()
        return payload

    def build_statistics_api_payload(self) -> dict[str, Any]:
        """构建 /api/statistics 载荷。"""
        payload = self.build_statistics_payload().copy()
        # 裁剪 recent_pushes。管理端统计页面仅需要基础统计及少量近期推送（通常由 event_summary_views 展现）
        # 避免把近千条原始 recent_pushes 完整数据传给前端，减少序列化及网络传输开销（原本的 recent_pushes 长度可达 500）
        payload["recent_pushes"] = list(payload.get("event_summary_views", []))[:50]
        # 同时为了避免在 statsNormalizer.js 中因为 stats.recent_pushes 未传而降级丢失，
        # 在这里显式清理大块 recent_pushes，并用经过裁剪的列表作为 recent_pushes
        if "recent_pushes" in payload:
            payload["recent_pushes"] = list(payload["recent_pushes"])[:50]
        payload["timestamp"] = datetime.now().isoformat()
        return payload

    async def _build_notifications_payload(self) -> dict[str, Any]:
        """构建通知中心实时载荷。"""
        notification_center = getattr(
            self.disaster_service, "notification_center", None
        )
        if not notification_center:
            return {
                "items": [],
                "meta": {
                    "unread_count": 0,
                    "last_sync_at": None,
                    "total_count": 0,
                },
            }
        return await notification_center.get_payload()

    def _build_recent_earthquakes_payload(self) -> list[dict[str, Any]]:
        """构建近期地震摘要列表。"""
        # 近期地震列表来自统计投影视图，避免重复维护另一套摘要拼装逻辑。
        if not self.disaster_service or not self.disaster_service.statistics_manager:
            return []

        payload = self.build_statistics_payload()
        return list(payload.get("earthquake_views", []))
