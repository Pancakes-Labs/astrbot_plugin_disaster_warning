"""
数据源连接配置工厂。
负责基于统一 source catalog 构建灾害服务所需的 WebSocket 连接计划，
避免继续在应用服务层硬编码 provider 子源列表。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...network.websocket.jian_project_connection_policy import (
    jian_project_auth_service,
)
from ...sources.source_catalog import SOURCE_CATALOG
from ...sources.source_entry import SourceEntry
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class ConnectionPlanBuilder:
    """数据源连接配置工厂。

    负责把已启用的数据源目录项转换为运行时可直接消费的连接计划。
    """

    @staticmethod
    def _resolve_connection_plan(
        entry: SourceEntry,
    ) -> tuple[str, dict[str, Any]] | None:
        """从单个数据源目录项解析连接分组键与连接参数。"""
        # 构建物理连接配置计划（例如 WebSocket URL、Headers、类型等）
        plan = entry.build_connection_plan()
        group_key = str(plan.get("group_key") or "").strip()
        # 若分组键为空，则说明此数据源无需直接建立常驻 WebSocket 物理连接
        if not group_key:
            return None
        # 清理过滤掉空值或不需要的参数，同时剥离内部逻辑使用的 group_key
        return group_key, {
            key: value
            for key, value in plan.items()
            if key != "group_key" and value not in (None, "")
        }

    @staticmethod
    def _resolve_jian_project_auth(config: dict[str, Any]) -> str:
        """从全局配置解析 Jian Project 鉴权凭证（登录密钥 lk_ 或长期 Token rt_）。"""
        data_sources = config.get("data_sources")
        jp_cfg: dict[str, Any] = {}
        if isinstance(data_sources, dict):
            raw = data_sources.get("jian_project")
            if isinstance(raw, dict):
                jp_cfg = raw
        return str(jp_cfg.get("login_key") or "").strip()

    @classmethod
    def build(
        cls,
        config: dict[str, Any],
        fan_server_pref_override: str | None = None,
        session_config_manager: Any | None = None,
    ) -> dict[str, dict[str, Any]]:
        """根据统一数据源目录与启用状态构建连接计划。

        Args:
            config: 全局配置字典。
            fan_server_pref_override: 兼容旧调用签名，已忽略（FSSN API 无主备切换）。
            session_config_manager: 可选的会话配置管理器；传入时复用它解析
                「全局关、会话开」的子源，避免新建重复实例造成快照陈旧与并发覆盖。
        """
        del fan_server_pref_override  # 兼容旧签名
        if session_config_manager is not None:
            cls._session_config_manager = session_config_manager
        # 使用运行时查询服务拉取当前的物理数据源启用列表。
        # 优先复用主服务注入的会话配置管理器，避免每次构建都新建独立实例。
        runtime_query = SourceRuntimeQueryService(
            config,
            session_config_manager=getattr(cls, "_session_config_manager", None),
        )
        connections: dict[str, dict[str, Any]] = {}
        jp_login_key = cls._resolve_jian_project_auth(config)
        jp_auth_warned = False

        # 只为「组级总闸开启且全局或任一会话需要」的子源生成连接计划，
        # 避免为完全无会话需要的数据源创建无效连接占位；
        # 同时保证「全局关、会话开」的子源仍会建立连接，不漏采集。
        enabled_source_ids = runtime_query.get_active_source_ids()
        enabled_entries = [
            SOURCE_CATALOG[source_id]
            for source_id in enabled_source_ids
            if source_id in SOURCE_CATALOG
        ]

        for entry in enabled_entries:
            resolved = cls._resolve_connection_plan(entry)
            if resolved is None:
                continue
            group_key, plan = resolved
            # 同一连接分组只保留一份计划，避免多个子源重复覆盖/创建同一连接。
            if group_key in connections:
                continue

            # FSSN（原 fan_studio 连接组）公开路径，无需 API Key。
            # Jian Project 台风 HTTP 轮询组不建 WebSocket。
            if group_key.startswith("jian_project") and group_key != "jian_project_all":
                # 非 /all 的 Jian 分组（如台风 HTTP）不占用 WebSocket 计划
                continue

            # Jian Project 连接必须携带凭证或本地有持久化凭证，否则跳过建连计划。
            if group_key == "jian_project_all":
                has_stored_token = jian_project_auth_service.has_valid_token()
                if not jp_login_key and not has_stored_token:
                    if not jp_auth_warned:
                        logger.warning(
                            "[灾害预警] Jian Project 相关数据源已启用，但未配置登录密钥 (lk_...) 或长期 Token (rt_...)，已跳过 Jian Project 连接。"
                            "请前往 https://auth.sismotide.top/ 申请。"
                        )
                        jp_auth_warned = True
                    continue
                if jp_login_key:
                    plan["credential"] = jp_login_key

            connections[group_key] = plan

        if connections:
            group_list = "、".join(sorted(connections.keys()))
            logger.debug(f"[灾害预警] 已配置数据连接分组: {group_list}")

        return connections
