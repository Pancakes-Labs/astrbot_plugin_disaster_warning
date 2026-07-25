"""
统一数据源运行态查询服务。
集中提供基于数据源目录的启用态、分组摘要、连接映射与运行态快照，避免上层继续直接依赖旧配置结构。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ...sources.source_catalog import SOURCE_CATALOG
from ...sources.source_entry import ProviderFamily, SourceEntry
from ..config.config_service import ConfigAccessor

# 物理连接到连接分组的键映射，统一在此集中配置，消除魔法硬编码字符串
_CONNECTION_GROUP_ALIAS: dict[str, str] = {
    ProviderFamily.FAN_STUDIO.value: "fan_studio_all",
    ProviderFamily.P2P.value: "p2p_main",
    ProviderFamily.WOLFX.value: "wolfx_all",
    ProviderFamily.GLOBAL_QUAKE.value: "global_quake",
    ProviderFamily.DIRECT_HTTP.value: "snet_msil",
}

# 物理连接的友好展示名称，供管理后台和 API 使用
_CONNECTION_DISPLAY_NAME: dict[str, str] = {
    "fan_studio_all": "FAN Studio",
    "fan_studio_cenc_ir": "FAN Studio（烈度速报）",
    "p2p_main": "P2P地震情報",
    "wolfx_all": "Wolfx",
    "global_quake": "Global Quake",
    "snet_msil": "NIED S-Net",
    # EQSC 为 HTTP 通道；展示名必须与 ConnectionsPayloadBuilder.EQSC_DISPLAY_NAME
    # 完全一致，否则 catalog 占位会以原始键 "eqsc" 残留，前端误显示“未连接”。
    "eqsc": "EQSC API",
}


class SourceRuntimeQueryService:
    """基于统一数据源目录的运行态查询服务。"""

    def __init__(self, config: dict[str, Any] | None = None):
        # 封装底层配置访问器
        self.config_accessor = ConfigAccessor(config or {})

    def _data_sources_config(self) -> dict[str, Any]:
        """获取数据源配置总表。"""
        return self.config_accessor.data_sources_config()

    def _group_config(self, config_group: str) -> dict[str, Any]:
        """获取指定数据源分组的配置。"""
        data_sources = self._data_sources_config()
        value = data_sources.get(config_group, {})
        return value if isinstance(value, dict) else {}

    def is_source_enabled(self, source_id: str) -> bool:
        """判断指定数据源是否在当前配置中启用。"""
        entry = SOURCE_CATALOG.get((source_id or "").strip())
        # 若此 source_id 未在统一 catalog 目录中注册，一律判定为未启用
        if entry is None:
            return False
        group_cfg = self._group_config(entry.config_group)
        # 如果所属配置大类的顶级 enabled 总开关为 False，则旗下子源全部失效
        if not group_cfg.get("enabled", False):
            return False
        # 读取子数据源对应键名下的具体布尔值配置
        return bool(group_cfg.get(entry.config_key, False))

    def is_family_enabled(self, provider_family: str) -> bool:
        """判断指定提供方家族下是否存在已启用数据源。"""
        family_value = (provider_family or "").strip()
        if not family_value:
            return False
        # 家族中只要有任意一个数据源开关被用户打开，则整个服务商家族判定为 enabled
        return any(
            self.is_source_enabled(source_id)
            for source_id, entry in SOURCE_CATALOG.items()
            if entry.provider_family.value == family_value
        )

    def get_enabled_source_ids(self) -> list[str]:
        """获取当前配置下所有已被启用的具体数据源 ID 列表。"""
        return [
            source_id
            for source_id in SOURCE_CATALOG
            if self.is_source_enabled(source_id)
        ]

    def get_enabled_source_labels(self) -> list[str]:
        """获取已启用数据源的配置标签定位（如 'wolfx.cenc_eew'）。"""
        labels: list[str] = []
        for source_id in self.get_enabled_source_ids():
            entry = SOURCE_CATALOG[source_id]
            labels.append(f"{entry.config_group}.{entry.config_key}")
        return labels

    def build_sub_source_status(self) -> dict[str, dict[str, bool]]:
        """按配置分组构建子数据源启用状态表。"""
        grouped: dict[str, dict[str, bool]] = defaultdict(dict)
        # 对全局所有注册的数据源按配置组进行状态归纳归类
        for source_id, entry in SOURCE_CATALOG.items():
            grouped[entry.config_group][entry.config_key] = self.is_source_enabled(
                source_id
            )
        return dict(grouped)

    def get_connection_group_key(self, entry: SourceEntry) -> str:
        """解析数据源所属连接分组键。"""
        explicit_group = (entry.connection_group or "").strip()
        # 优先读取源目录中显示指定的分组名称
        if explicit_group:
            return explicit_group
        # 降级使用静态定义的全局家族别名列表
        return _CONNECTION_GROUP_ALIAS.get(
            entry.provider_family.value, entry.provider_family.value
        )

    def get_expected_connection_groups(self) -> dict[str, str]:
        """获取理论上应存在的连接分组及其展示名称。"""
        groups: dict[str, str] = {}
        for entry in SOURCE_CATALOG.values():
            group_key = self.get_connection_group_key(entry)
            groups[group_key] = _CONNECTION_DISPLAY_NAME.get(group_key, group_key)
        return groups

    def get_connection_group_source_map(self) -> dict[str, list[str]]:
        """构建连接分组到数据源标识列表的映射。"""
        grouped: dict[str, list[str]] = defaultdict(list)
        for source_id, entry in SOURCE_CATALOG.items():
            group_key = self.get_connection_group_key(entry)
            grouped[group_key].append(source_id)
        return {key: sorted(value) for key, value in grouped.items()}

    def build_connection_group_status(self) -> dict[str, dict[str, bool]]:
        """构建连接分组下各数据源的启用状态。"""
        grouped: dict[str, dict[str, bool]] = defaultdict(dict)
        for source_id, entry in SOURCE_CATALOG.items():
            group_key = self.get_connection_group_key(entry)
            grouped[group_key][source_id] = self.is_source_enabled(source_id)
        return dict(grouped)

    def resolve_active_connection_metrics(
        self,
        service: Any | None,
        actual_connections: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """统一计算活跃连接数与 Global Quake 在线标记。

        口径：
        - WebSocket：ws_manager 连接表中 connected=True
        - EQSC HTTP：AccessToken 有效计入活跃
        - S-Net HTTP：配置启用且轮询任务 running 计入活跃
        - total_connections 不在此计算，由 build_runtime_snapshot 按 expected_groups 统计
        """
        actual_connections = actual_connections or {}
        active = sum(
            1
            for status in actual_connections.values()
            if isinstance(status, dict) and bool(status.get("connected"))
        )

        # 延迟导入，避免 query 层与 app 层形成硬循环依赖。
        from ...app.services.typhoon_enrichment_service import TyphoonEnrichmentService

        eqsc_active, _eqsc_total = TyphoonEnrichmentService.resolve_connection_counts(
            service
        )
        active += int(eqsc_active or 0)

        snet_poll = getattr(service, "snet_poll_service", None) if service else None
        try:
            snet_enabled = bool(self.is_source_enabled("snet_msil"))
        except Exception:
            snet_enabled = False
        if (
            snet_enabled
            and snet_poll is not None
            and getattr(snet_poll, "running", False)
        ):
            active += 1

        connection_tasks = (
            getattr(service, "connection_tasks", []) if service is not None else []
        )
        global_quake_connected = any(
            "global_quake" in task.get_name() if hasattr(task, "get_name") else False
            for task in connection_tasks
        )
        return {
            "active_websocket_connections": int(active),
            "global_quake_connected": bool(global_quake_connected),
        }

    def build_runtime_snapshot(
        self,
        *,
        actual_connections: dict[str, dict[str, Any]] | None = None,
        latency_cache: dict[str, float | None] | None = None,
        running: bool = False,
        start_time: str | None = None,
        uptime: str = "未运行",
        active_websocket_connections: int = 0,
        message_logger_enabled: bool = False,
        global_quake_connected: bool = False,
    ) -> dict[str, Any]:
        """构建统一运行态快照。

        用于同时供给管理端实时面板、状态接口与连接信息展示。
        """
        actual_connections = actual_connections or {}
        latency_cache = latency_cache or {}
        expected_groups = self.get_expected_connection_groups()
        group_source_map = self.get_connection_group_source_map()
        group_status_map = self.build_connection_group_status()

        connections: dict[str, dict[str, Any]] = {}
        for group_key, display_name in expected_groups.items():
            # 即使某分组当前尚未建立真实连接，也要给前端返回完整的占位状态。
            conn_info = dict(
                actual_connections.get(
                    group_key,
                    {
                        "connected": False,
                        "retry_count": 0,
                        "has_handler": False,
                        "status": "未连接",
                    },
                )
            )
            # 稳定主键：健康采样 / 前端映射优先读 group_key，避免展示名微调后失配。
            conn_info["group_key"] = group_key
            conn_info["display_name"] = display_name
            # 计算该物理连接链路下是否有任何一个子数据源开关被开启
            conn_info["enabled"] = any(group_status_map.get(group_key, {}).values())
            # 写入当前链路的探测网络延时
            conn_info["latency"] = latency_cache.get(group_key)
            conn_info["sub_sources"] = dict(group_status_map.get(group_key, {}))
            conn_info["source_ids"] = list(group_source_map.get(group_key, []))
            connections[display_name] = conn_info

        # 总连接数按 catalog 期望的物理通道口径统计（含已停用但应展示的通道），
        # 避免数据源被临时关闭后从分母消失，出现 6/6 而非 6/7。
        # expected_groups 已包含 WS（FAN/P2P/Wolfx/GQ）与 HTTP（EQSC/S-Net）。
        return {
            "running": running,
            "uptime": uptime,
            "active_websocket_connections": active_websocket_connections,
            "global_quake_connected": global_quake_connected,
            "total_connections": len(expected_groups),
            "connection_details": actual_connections,
            "connections": connections,
            "sub_source_status": self.build_sub_source_status(),
            "data_sources": self.get_enabled_source_labels(),
            "enabled_source_ids": self.get_enabled_source_ids(),
            "message_logger_enabled": message_logger_enabled,
            "start_time": start_time,
        }


__all__ = ["SourceRuntimeQueryService"]
