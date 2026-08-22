"""
Web 管理端配置摘要载荷构建器。
统一组装简化版配置响应，避免在运行时服务中内联拼装配置视图。
"""

from __future__ import annotations

from typing import Any

from ....services.config.config_service import ConfigAccessor
from ....services.query.source_runtime_query_service import SourceRuntimeQueryService


class ConfigPayloadBuilder:
    """配置摘要载荷构建器。"""

    def __init__(self, config: dict[str, Any]):
        """保存原始配置，并初始化管理端所需的查询工具。"""
        self.config = config
        self.config_accessor = ConfigAccessor(config)
        self.source_runtime_query = SourceRuntimeQueryService(config)

    def _build_source_summary(self) -> dict[str, Any]:
        """构建数据源启用状态与连接分组摘要。"""
        return {
            "enabled_source_ids": self.source_runtime_query.get_enabled_source_ids(),
            "enabled_source_labels": self.source_runtime_query.get_enabled_source_labels(),
            "sub_source_status": self.source_runtime_query.build_sub_source_status(),
            "connection_groups": self.source_runtime_query.get_expected_connection_groups(),
            "connection_group_sources": self.source_runtime_query.get_connection_group_source_map(),
        }

    def build_summary(self) -> dict[str, Any]:
        """构建管理端使用的配置摘要。"""
        # 这里只返回管理端概览所需的摘要字段，而非完整配置，减少敏感信息暴露与前端负载。
        return {
            "enabled": self.config.get("enabled", True),
            "target_sessions_count": len(self.config_accessor.target_sessions()),
            "data_sources": self.config_accessor.data_sources_config(),
            "source_summary": self._build_source_summary(),
            "earthquake_filters": self.config.get("earthquake_filters", {}),
            "local_monitoring": {
                "enabled": self.config_accessor.local_monitoring_config().get(
                    "enabled", False
                ),
                "place_name": self.config_accessor.local_monitoring_config().get(
                    "place_name", ""
                ),
            },
            "typhoon_config": self.config_accessor.typhoon_config(),
            "display_timezone": self.config.get("display_timezone", "UTC+8"),
            "web_admin": {
                # password 明确从摘要中剔除，避免任何管理端展示接口泄露敏感字段。
                k: v
                for k, v in self.config_accessor.web_admin_config().items()
                if k != "password"
            },
        }
