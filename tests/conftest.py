"""
Test bootstrap. Provide minimal stub modules when AstrBot is not installed.

Show the plugin directory on sys.path as the package
astrbot_plugin_disaster_warning.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PACKAGE_NAME = "astrbot_plugin_disaster_warning"


class _StubLogger(logging.LoggerAdapter):
    """Logger stub. It accepts the custom keyword arguments of the plugin, such as is_event_linked."""

    def process(self, msg, kwargs):
        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        return msg, {k: v for k, v in kwargs.items() if k in allowed}


class _StubModule(types.ModuleType):
    """Return a MagicMock for each attribute that is not defined.

    This avoids a separate stub for each AstrBot symbol.
    """

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        value = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


def _install_astrbot_stubs() -> None:
    if "astrbot" in sys.modules and not isinstance(sys.modules["astrbot"], _StubModule):
        return  # The real AstrBot is installed
    for name in (
        "astrbot",
        "astrbot.api",
        "astrbot.api.star",
        "astrbot.api.event",
        "astrbot.api.message_components",
        "astrbot.core",
        "astrbot.core.desktop_runtime",
        "astrbot.core.updator",
        "astrbot.core.process_restart",
        "astrbot.cli",
    ):
        sys.modules.setdefault(name, _StubModule(name))
    api = sys.modules["astrbot.api"]
    api.logger = _StubLogger(logging.getLogger("astrbot"), {})

    class StarTools:
        @staticmethod
        def get_data_dir(name: str) -> Path:
            path = Path(__import__("tempfile").gettempdir()) / "dw-test-data" / name
            path.mkdir(parents=True, exist_ok=True)
            return path

    sys.modules["astrbot.api.star"].StarTools = StarTools


def _expose_plugin_package() -> None:
    parent = str(PLUGIN_DIR.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    if PACKAGE_NAME not in sys.modules and PLUGIN_DIR.name != PACKAGE_NAME:
        # The worktree directory name can be different. Register the package by path.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = module
        spec.loader.exec_module(module)


_install_astrbot_stubs()
_expose_plugin_package()
