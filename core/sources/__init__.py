"""
数据源元数据中心导出。
统一导出数据源目录、来源注册项与路由辅助能力。
"""

from .display_registry import (
    CONNECTION_DISPLAY_NAMES,
    CONNECTION_GROUP_ALIAS,
    CONNECTION_GROUP_ORDER,
    DISPLAY_NAME_ALIASES,
    SOURCE_ALIAS_MAP,
    SOURCE_DISPLAY_MAP,
)
from .source_catalog import (
    SOURCE_CATALOG,
    get_source_entries,
    get_source_entry,
    get_source_ids_by_config_group,
    get_source_ids_by_family,
    get_source_ids_by_provider_message_type,
    get_source_ids_by_provider_source_name,
    get_source_ids_by_routing_tag,
    get_source_ids_by_type,
)
from .source_entry import ProviderFamily, SourceEntry, SourceType
from .source_router import (
    detect_fan_studio_source_entry,
    detect_fan_studio_source_id,
    get_fan_studio_source_id,
    get_openquake_source_id,
    get_provider_source_map,
    get_wolfx_source_id,
    route_fan_studio_message,
)

__all__ = [
    "ProviderFamily",
    "SourceEntry",
    "SourceType",
    "SOURCE_CATALOG",
    "SOURCE_ALIAS_MAP",
    "SOURCE_DISPLAY_MAP",
    "CONNECTION_DISPLAY_NAMES",
    "CONNECTION_GROUP_ALIAS",
    "CONNECTION_GROUP_ORDER",
    "DISPLAY_NAME_ALIASES",
    "get_source_entry",
    "get_source_entries",
    "get_source_ids_by_config_group",
    "get_source_ids_by_family",
    "get_source_ids_by_provider_message_type",
    "get_source_ids_by_provider_source_name",
    "get_source_ids_by_routing_tag",
    "get_source_ids_by_type",
    "detect_fan_studio_source_entry",
    "detect_fan_studio_source_id",
    "get_fan_studio_source_id",
    "get_openquake_source_id",
    "get_provider_source_map",
    "get_wolfx_source_id",
    "route_fan_studio_message",
]
