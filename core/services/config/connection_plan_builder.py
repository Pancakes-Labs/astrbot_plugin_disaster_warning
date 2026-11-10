"""
数据源连接配置工厂。
负责基于统一 source catalog 构建灾害服务所需的 WebSocket 连接计划，
避免继续在应用服务层硬编码 provider 子源列表。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...network.websocket.jian_project_connection_policy import (
    ServerPreference,
    jian_project_auth_service,
    resolve_server_urls,
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
        plan = entry.build_connection_plan()
        group_key = str(plan.get("group_key") or "").strip()
        if not group_key:
            return None
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
    def _resolve_jian_server_preference(cls, config: dict[str, Any]) -> str:
        """从全局配置解析 Jian Project 服务器偏好。"""
        data_sources = config.get("data_sources")
        if not isinstance(data_sources, dict):
            return ServerPreference.PRIMARY_FIRST.value
        jp_cfg = data_sources.get("jian_project", {})
        if not isinstance(jp_cfg, dict):
            return ServerPreference.PRIMARY_FIRST.value
        raw = str(jp_cfg.get("server_preference") or "").strip()
        return ServerPreference.normalize(raw)

    @classmethod
    def build(
        cls,
        config: dict[str, Any],
        fan_server_pref_override: str | None = None,
        jian_server_pref_override: str | None = None,
        session_config_manager: Any | None = None,
    ) -> dict[str, dict[str, Any]]:
        """根据统一数据源目录与启用状态构建连接计划。

        Args:
            config: 全局配置字典。
            fan_server_pref_override: 历史别名，等同 jian_server_pref_override。
            jian_server_pref_override: 可选的 Jian Project 服务器偏好临时覆盖值。
            session_config_manager: 可选的会话配置管理器。
        """
        override = jian_server_pref_override
        if override is None:
            override = fan_server_pref_override
        if session_config_manager is not None:
            cls._session_config_manager = session_config_manager
        runtime_query = SourceRuntimeQueryService(
            config,
            session_config_manager=getattr(cls, "_session_config_manager", None),
        )
        connections: dict[str, dict[str, Any]] = {}
        jp_login_key = cls._resolve_jian_project_auth(config)
        jp_server_pref = (
            ServerPreference.normalize(override)
            if override
            else cls._resolve_jian_server_preference(config)
        )
        jp_auth_warned = False

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
            if group_key in connections:
                continue

            if group_key == "jian_project_all" or group_key.startswith("jian_project"):
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
                plan["server_preference"] = jp_server_pref
                original_url = str(plan.get("url") or "").strip()
                original_backup = str(plan.get("backup_url") or "").strip()
                first_url, second_url = resolve_server_urls(
                    original_url,
                    original_backup,
                    jp_server_pref,
                )
                plan["url"] = first_url
                plan["backup_url"] = second_url
                plan["original_url"] = original_url
                plan["original_backup"] = original_backup

            connections[group_key] = plan

        if connections:
            ordered = sorted(
                connections.keys(),
                key=lambda name: (0 if name == "jian_project_all" else 1, name),
            )
            group_list = "、".join(ordered)
            logger.debug(f"[灾害预警] 已配置数据连接分组: {group_list}")

        return connections
