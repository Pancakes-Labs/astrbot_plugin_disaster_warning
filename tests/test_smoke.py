"""冒烟测试：确认核心注册表与主服务模块可在桩环境下导入。"""

from __future__ import annotations

import importlib


def test_core_registries_import():
    # 与插件真实启动顺序一致：先导入主服务，避免解析器包的循环导入
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
    catalog = importlib.import_module(
        "astrbot_plugin_disaster_warning.core.sources.source_catalog"
    )
    parsers = importlib.import_module(
        "astrbot_plugin_disaster_warning.core.parsers.parser_registry"
    )
    assert "cea_fanstudio" in catalog.SOURCE_CATALOG
    parsers.validate_catalog_parser_names()


def test_disaster_service_module_imports():
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
