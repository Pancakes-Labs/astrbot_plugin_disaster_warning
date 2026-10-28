"""Fixtures for br_south_disaster: a fake ExtensionAPI built from the real host classes."""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

# Import this first. It avoids an existing circular import in the host.
importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
from astrbot_plugin_disaster_warning.core.extensions.api import MODELS  # noqa: E402
from astrbot_plugin_disaster_warning.core.extensions.i18n import Translator  # noqa: E402

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "br_south_disaster"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeAPI:
    """Same surface as ExtensionAPI. It records the registrations."""

    models = MODELS
    logger = logging.getLogger("br_south_disaster.test")

    def __init__(self, lang="zh-CN", config=None):
        self.config = config or {}
        self.t = Translator(EXT_DIR / "locale", lang).t
        self.sources, self.pollers, self.presenters = {}, {}, {}
        self.all_pollers = []  # (source_id, fetch, interval) for every poller

    def register_source(self, entry):
        """Record the source entry."""
        self.sources[entry.source_id] = entry

    def register_presenter(self, key, cls):
        """Record the presenter class."""
        self.presenters[key] = cls

    def register_poller(self, source_id, fetch, *, interval_seconds):
        """Record the fetch function and the interval.

        The dict keeps the first poller of each source. all_pollers keeps all of them.
        """
        self.pollers.setdefault(source_id, (fetch, interval_seconds))
        self.all_pollers.append((source_id, fetch, interval_seconds))


@pytest.fixture(scope="session")
def ext():
    """Load the extension as a standalone package, like the host does."""
    spec = importlib.util.spec_from_file_location(
        "_ext_br_south_disaster",
        EXT_DIR / "__init__.py",
        submodule_search_locations=[str(EXT_DIR)],
    )
    sys.modules["_ext_br_south_disaster"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_south_disaster"])
    return types.SimpleNamespace(
        extension=importlib.import_module("_ext_br_south_disaster.extension"),
        enso=importlib.import_module("_ext_br_south_disaster.enso"),
        presenter=importlib.import_module("_ext_br_south_disaster.presenter"),
        states=importlib.import_module("_ext_br_south_disaster.states"),
    )


@pytest.fixture
def make_api():
    """Return the FakeAPI class."""
    return FakeAPI


def _fixture_json(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.fixture(scope="session")
def alerts_json():
    """Return the stored CEMADEN alerts fixture."""
    return _fixture_json("cemaden_alertas.json")


@pytest.fixture(scope="session")
def rain_json():
    """Return the stored CEMADEN rain gauge fixture."""
    return _fixture_json("cemaden_rain_RS.json")


@pytest.fixture(scope="session")
def rain_by_state():
    """Return the stored CEMADEN rain gauge fixture of each state."""
    return {uf: _fixture_json(f"cemaden_rain_{uf}.json") for uf in ("RS", "SC", "PR")}


@pytest.fixture(scope="session")
def oni_text():
    """Return the stored NOAA ONI text fixture."""
    return (FIXTURES / "oni.ascii.txt").read_text("utf-8")
