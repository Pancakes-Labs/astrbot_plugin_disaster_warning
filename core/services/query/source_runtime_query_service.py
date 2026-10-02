"""
统一数据源运行态查询服务。
集中提供基于数据源目录的启用态、分组摘要、连接映射与运行态快照，避免上层继续直接依赖旧配置结构。
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from ...sources.display_registry import (
    CONNECTION_DISPLAY_NAMES,
    CONNECTION_GROUP_ALIAS,
    LEGACY_CONNECTION_GROUP_KEYS,
)
from ...sources.source_catalog import SOURCE_CATALOG, get_legacy_group_names
from ...sources.source_entry import SourceEntry
from ..config.config_service import ConfigAccessor

# 物理连接到连接分组的键映射、物理连接的友好展示名称
# 已统一收编至 core/sources/display_registry.py（CONNECTION_GROUP_ALIAS /
# CONNECTION_DISPLAY_NAMES），此处直接引用事实层常量。
# EQSC / S-Net 等 HTTP 通道展示名同样来自 CONNECTION_DISPLAY_NAMES，
# 与 ConnectionsPayloadBuilder 派生自同一事实源，天然保持一致。


class SourceRuntimeQueryService:
    """基于统一数据源目录的运行态查询服务。"""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        session_config_manager: Any | None = None,
    ):
        # 封装底层配置访问器
        self.config_accessor = ConfigAccessor(config or {})
        # 会话差异配置管理器：用于解析「全局关、会话开」这类子源启用态。
        # 缺省时不立即构造，真正需要会话级判定时再惰性创建，避免高频路径做多余文件 IO。
        self._session_config_manager = session_config_manager
        # 会话感知启用态缓存：{(source_id, 时间桶): bool}。
        # 接入/解析路径调用频繁，而「某会话是否覆写某子源」仅在配置保存时变化，
        # 用短 TTL 缓存把每消息 O(会话数) 的配置解析降为近似 O(1)。
        self._session_active_cache: dict[tuple[str, int], bool] = {}
        self._session_active_ttl = 5.0

    def _data_sources_config(self) -> dict[str, Any]:
        """获取数据源配置总表。"""
        return self.config_accessor.data_sources_config()

    def _get_session_config_manager(self):
        """惰性获取会话差异配置管理器。"""
        manager = self._session_config_manager
        if manager is not None:
            return manager or None
        try:
            # 延迟导入：避免查询层与存储层在模块加载期形成循环依赖。
            from ...storage.session_config_manager import SessionConfigManager

            manager = SessionConfigManager(self.config_accessor.config)
        except Exception:
            # 不可用时标记为 False，避免每条消息反复重试创建。
            manager = False
        self._session_config_manager = manager
        return manager or None

    @staticmethod
    def _resolve_group_config_from(
        data_sources: dict[str, Any], config_group: str
    ) -> dict[str, Any]:
        """从给定数据源配置中合并规范组名与历史别名组名。"""
        merged: dict[str, Any] = {}
        for legacy in get_legacy_group_names(config_group):
            legacy_cfg = data_sources.get(legacy)
            if isinstance(legacy_cfg, dict):
                merged.update(legacy_cfg)
        canonical_cfg = data_sources.get(config_group)
        if isinstance(canonical_cfg, dict):
            merged.update(canonical_cfg)
        return merged

    def _group_config(self, config_group: str) -> dict[str, Any]:
        """获取指定数据源分组的配置，兼容历史组名。"""
        return self._resolve_group_config_from(
            self._data_sources_config(), config_group
        )

    def is_group_enabled(self, source_id: str) -> bool:
        """判断数据源所属分组总闸是否开启（忽略子源级开关）。

        组级开关是「批量闸刀」：关闭后整组的采集与推送一并停止，
        用于一次性停用组内全部子源，而不必逐个关闭子源开关。

        接入层只应依赖本方法做「整组是否放行」判定，
        子源级开关交由推送阶段按会话判定。
        """
        entry = SOURCE_CATALOG.get((source_id or "").strip())
        if entry is None:
            return False
        return bool(self._group_config(entry.config_group).get("enabled", False))

    def is_source_enabled_any_session(self, source_id: str) -> bool:
        """判断子源是否在全局或任一会话生效配置中被启用。

        子源级开关语义为「会话默认值」：全局值决定未覆写会话的默认行为，
        会话可覆写。因此「全局关 + 会话开」应视为有效启用（该会话需要它），
        与 is_source_enabled（只读全局）区分开。
        """
        entry = SOURCE_CATALOG.get((source_id or "").strip())
        if entry is None:
            return False
        # 全局已启用属最常见路径，无需求解会话覆写。
        if self.is_source_enabled(source_id):
            return True

        bucket = int(time.monotonic() / self._session_active_ttl)
        cache_key = (entry.source_id, bucket)
        cached = self._session_active_cache.get(cache_key)
        if cached is not None:
            return cached

        result = False
        manager = self._get_session_config_manager()
        if manager is not None:
            try:
                known_sessions = manager.list_all_known_sessions()
            except Exception:
                # 会话管理器不可用时按「无会话需求」保守降级，避免接入判定抛异常。
                known_sessions = []
            for umo in known_sessions:
                try:
                    effective = manager.get_effective_config(umo)
                except Exception:
                    continue
                if not isinstance(effective, dict):
                    continue
                data_sources = effective.get("data_sources")
                if not isinstance(data_sources, dict):
                    continue
                group_cfg = self._resolve_group_config_from(
                    data_sources, entry.config_group
                )
                # 会话需同时开启组开关与子源开关，才算「该会话需要此子源」。
                if bool(group_cfg.get("enabled", False)) and bool(
                    group_cfg.get(entry.config_key, False)
                ):
                    result = True
                    break

        # 仅保留近期时间桶，避免缓存随运行时长无界增长。
        if len(self._session_active_cache) > 256:
            self._session_active_cache.clear()
        self._session_active_cache[cache_key] = result
        return result

    def is_source_active(self, source_id: str) -> bool:
        """判断子源当前是否需要被接入（建立连接 / 启动轮询）。

        条件 = 组级总闸开启（批量闸刀）且 全局或任一会话需要该子源。
        这样「全局关、会话开」的子源仍会建立连接，不会因会话级意图而漏采集。
        """
        if not self.is_group_enabled(source_id):
            return False
        return self.is_source_enabled_any_session(source_id)

    def get_active_source_ids(self) -> list[str]:
        """获取当前需要接入的具体数据源 ID 列表（建连计划口径）。"""
        return [
            source_id
            for source_id in SOURCE_CATALOG
            if self.is_source_active(source_id)
        ]

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
        return CONNECTION_GROUP_ALIAS.get(
            entry.provider_family.value, entry.provider_family.value
        )

    @staticmethod
    def _canonical_group_key(group_key: str) -> str:
        """把历史连接组 key 折叠为当前规范 key。"""
        normalized = str(group_key or "").strip()
        if not normalized:
            return normalized
        return LEGACY_CONNECTION_GROUP_KEYS.get(normalized, normalized)

    def get_expected_connection_groups(self) -> dict[str, str]:
        """获取理论上应存在的连接分组及其展示名称。"""
        groups: dict[str, str] = {}
        for entry in SOURCE_CATALOG.values():
            group_key = self._canonical_group_key(self.get_connection_group_key(entry))
            groups[group_key] = CONNECTION_DISPLAY_NAMES.get(group_key, group_key)
        return groups

    def get_connection_group_source_map(self) -> dict[str, list[str]]:
        """构建连接分组到数据源标识列表的映射。"""
        grouped: dict[str, list[str]] = defaultdict(list)
        for source_id, entry in SOURCE_CATALOG.items():
            group_key = self._canonical_group_key(self.get_connection_group_key(entry))
            grouped[group_key].append(source_id)
        return {key: sorted(value) for key, value in grouped.items()}

    def build_connection_group_status(self) -> dict[str, dict[str, bool]]:
        """构建连接分组下各数据源的启用状态。"""
        grouped: dict[str, dict[str, bool]] = defaultdict(dict)
        for source_id, entry in SOURCE_CATALOG.items():
            group_key = self._canonical_group_key(self.get_connection_group_key(entry))
            grouped[group_key][source_id] = self.is_source_enabled(source_id)
        return dict(grouped)

    def resolve_active_connection_metrics(
        self,
        service: Any | None,
        actual_connections: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """统一计算活跃连接数与 PancakesAPI 在线标记。

        口径：
        - WebSocket：ws_manager 连接表中 connected=True
        - EQSC HTTP：AccessToken 有效计入活跃
        - S-Net HTTP：配置启用且轮询任务 running 计入活跃
        - total_connections 不在此计算，由 build_runtime_snapshot 按 expected_groups 统计
        """
        actual_connections = actual_connections or {}
        # 按规范连接组 key 去重后统计
        seen_groups: set[str] = set()
        active = 0
        for raw_name, status in actual_connections.items():
            if not (isinstance(status, dict) and bool(status.get("connected"))):
                continue
            canonical = self._canonical_group_key(raw_name)
            if canonical in seen_groups:
                continue
            seen_groups.add(canonical)
            active += 1

        # 延迟导入，避免 query 层与 app 层形成硬循环依赖。
        from ...app.services.eqsc_channel_service import EqscChannelService

        eqsc_active, _eqsc_total = EqscChannelService.resolve_connection_counts(service)
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

        # PancakesAPI 在线标记优先以实际连接状态为准：
        # 建连失败/服务停止后任务名仍可能残留，无法代表真实连通性。
        # actual_connections 由 ws_manager 实时维护 connected 状态，作为首选口径；
        # 任务名检查仅作为连接状态缺失时的兜底。
        pc_status = actual_connections.get("pancakes_api") or actual_connections.get(
            "openquake_api"
        )
        pancakes_connected = bool(
            isinstance(pc_status, dict) and pc_status.get("connected")
        )
        if not pancakes_connected:
            connection_tasks = (
                getattr(service, "connection_tasks", []) if service is not None else []
            )
            pancakes_connected = any(
                (
                    "pancakes_api" in task.get_name()
                    or "openquake_api" in task.get_name()
                )
                if hasattr(task, "get_name")
                else False
                for task in connection_tasks
            )
        return {
            "active_websocket_connections": int(active),
            "pancakes_connected": bool(pancakes_connected),
            "openquake_connected": bool(pancakes_connected),
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
        pancakes_connected: bool | None = None,
        openquake_connected: bool = False,
    ) -> dict[str, Any]:
        """构建统一运行态快照。

        用于同时供给管理端实时面板、状态接口与连接信息展示。
        """
        actual_connections = actual_connections or {}
        latency_cache = latency_cache or {}
        # 把连接表中的历史遗留键折叠为规范键
        merged_actual: dict[str, dict[str, Any]] = {}
        for raw_key, info in actual_connections.items():
            canonical = self._canonical_group_key(raw_key)
            existing = merged_actual.get(canonical)
            if existing is None:
                merged_actual[canonical] = info
                continue
            # 同键冲突时优先保留真实已连接者，避免旧占位记录覆盖新状态。
            if (
                isinstance(info, dict)
                and info.get("connected")
                and not (isinstance(existing, dict) and existing.get("connected"))
            ):
                merged_actual[canonical] = info
        actual_connections = merged_actual
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
        is_connected = bool(
            pancakes_connected
            if pancakes_connected is not None
            else openquake_connected
        )
        return {
            "running": running,
            "uptime": uptime,
            "active_websocket_connections": active_websocket_connections,
            "pancakes_connected": is_connected,
            "openquake_connected": is_connected,
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
