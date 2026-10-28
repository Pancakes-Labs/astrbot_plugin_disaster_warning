"""Fixtures for br_rs_waterq: a fake ExtensionAPI built from the real host classes."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

# Import this first. It avoids an existing circular import in the host.
importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
from astrbot_plugin_disaster_warning.core.extensions.api import MODELS  # noqa: E402
from astrbot_plugin_disaster_warning.core.extensions.i18n import Translator  # noqa: E402

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "br_rs_waterq"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeAPI:
    """Same surface as ExtensionAPI. It records the registrations."""

    models = MODELS

    def __init__(self, lang="zh-CN", config=None):
        self.config = config or {}
        self.t = Translator(EXT_DIR / "locale", lang).t
        self.sources, self.pollers, self.presenters = {}, {}, {}

    def register_source(self, entry):
        """Record the source entry."""
        self.sources[entry.source_id] = entry

    def register_presenter(self, key, cls):
        """Record the presenter class."""
        self.presenters[key] = cls

    def register_poller(self, source_id, fetch, *, interval_seconds):
        """Record the fetch function and the interval."""
        self.pollers[source_id] = (fetch, interval_seconds)


@pytest.fixture(scope="session")
def ext():
    """Load the extension as a standalone package, like the host does."""
    spec = importlib.util.spec_from_file_location(
        "_ext_br_rs_waterq",
        EXT_DIR / "__init__.py",
        submodule_search_locations=[str(EXT_DIR)],
    )
    sys.modules["_ext_br_rs_waterq"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_rs_waterq"])
    return types.SimpleNamespace(
        extension=importlib.import_module("_ext_br_rs_waterq.extension"),
        presenter=importlib.import_module("_ext_br_rs_waterq.presenter"),
    )


@pytest.fixture
def make_api():
    """Return the FakeAPI class."""
    return FakeAPI


@pytest.fixture
def locais_json():
    """Return a fresh copy of the stored /locais fixture."""
    return json.loads((FIXTURES / "balneabilidade_locais.json").read_text("utf-8"))
