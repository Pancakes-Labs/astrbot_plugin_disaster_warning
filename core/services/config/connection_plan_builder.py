"""
数据源连接配置工厂。
负责基于统一 source catalog 构建灾害服务所需的 WebSocket 连接计划，
避免继续在应用服务层硬编码 provider 子源列表。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

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
    def _resolve_fan_studio_auth(config: dict[str, Any]) -> tuple[str, str]:
        """从全局配置解析 FAN Studio 鉴权字段。"""
        data_sources = config.get("data_sources")
        fan_cfg: dict[str, Any] = {}
        if isinstance(data_sources, dict):
            raw = data_sources.get("fan_studio")
            if isinstance(raw, dict):
                fan_cfg = raw
        app_id = str(fan_cfg.get("app_id") or "").strip()
        api_key = str(fan_cfg.get("api_key") or "").strip()
        return app_id, api_key

    @classmethod
    def build(cls, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """根据统一数据源目录与启用状态构建连接计划。"""
        # 使用运行时查询服务拉取当前的物理数据源启用列表
        runtime_query = SourceRuntimeQueryService(config)
        connections: dict[str, dict[str, Any]] = {}
        fan_app_id, fan_api_key = cls._resolve_fan_studio_auth(config)
        fan_auth_warned = False

        # 只为当前已启用的数据源生成连接计划，避免创建无效连接占位。
        enabled_source_ids = runtime_query.get_enabled_source_ids()
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

            # FAN Studio 连接必须携带 appId + API Key，否则跳过建连计划。
            if group_key.startswith("fan_studio"):
                if not fan_app_id or not fan_api_key:
                    if not fan_auth_warned:
                        logger.warning(
                            "[灾害预警] FAN Studio 相关数据源已启用，但未配置 AppID 或 API Key，已跳过 FAN 连接。"
                            "请到开发者平台申请 Key 后填入配置。"
                        )
                        fan_auth_warned = True
                    continue
                plan["fan_app_id"] = fan_app_id
                plan["fan_api_key"] = fan_api_key

            connections[group_key] = plan
            if group_key == "fan_studio_all":
                logger.info("[灾害预警] 已配置 FAN Studio 全量数据连接")
            elif group_key == "fan_studio_cenc_ir":
                logger.info("[灾害预警] 已配置 FAN Studio 烈度速报独立连接")
            elif group_key == "p2p_main":
                logger.info("[灾害预警] 已配置 P2P 地震情报连接")
            elif group_key == "wolfx_all":
                logger.info("[灾害预警] 已配置 Wolfx 全量数据连接")
            elif group_key == "openquake_api":
                logger.info("[灾害预警] 已配置 OpenQuakeAPI 全量数据连接")
            else:
                logger.info(f"[灾害预警] 已配置数据连接，连接分组为 {group_key}")

        return connections
