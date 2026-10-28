"""
测试引导：在未安装 AstrBot 的环境中为插件提供最小桩模块，
并把插件目录以包名 astrbot_plugin_disaster_warning 暴露到 sys.path。
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
    """接受插件自定义关键字参数（如 is_event_linked）的日志桩。"""

    def process(self, msg, kwargs):
        allowed = {"exc_info", "stack_info", "stacklevel", "extra"}
        return msg, {k: v for k, v in kwargs.items() if k in allowed}


class _StubModule(types.ModuleType):
    """未显式定义的属性一律返回 MagicMock，避免为每个 AstrBot 符号单独建桩。"""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        value = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


def _install_astrbot_stubs() -> None:
    if "astrbot" in sys.modules and not isinstance(sys.modules["astrbot"], _StubModule):
        return  # 真实 AstrBot 已安装
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
        # worktree 目录名可能不同：按真实路径注册包
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
