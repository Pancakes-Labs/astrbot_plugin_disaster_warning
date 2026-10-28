"""Smoke tests. Make sure the core registries and the main service import with the stubs."""

from __future__ import annotations

import importlib


def test_core_registries_import():
    # Import the main service first, as the plugin does. This prevents a
    # circular import in the parser package.
    """Check that the core registries import without error."""
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
    """Check that the disaster service module imports without error."""
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
