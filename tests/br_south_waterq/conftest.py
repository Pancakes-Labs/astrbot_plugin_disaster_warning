"""Fixtures for br_south_waterq: a fake ExtensionAPI built from the real host classes."""

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

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "br_south_waterq"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeAPI:
    """Same surface as ExtensionAPI. It records the registrations."""

    models = MODELS
    logger = logging.getLogger("br_south_waterq.test")

    def __init__(self, lang="zh-CN", config=None):
        self.config = config or {}
        self.t = Translator(EXT_DIR / "locale", lang).t
        self.sources, self.pollers, self.presenters = {}, {}, {}
        self.all_pollers = []  # (source_id, fetch, interval, timeout) for every poller

    def register_source(self, entry):
        """Record the source entry."""
        self.sources[entry.source_id] = entry

    def register_presenter(self, key, cls):
        """Record the presenter class."""
        self.presenters[key] = cls

    def register_poller(
        self, source_id, fetch, *, interval_seconds, timeout_seconds=30
    ):
        """Record the poller. The dict keeps the first poller of each source."""
        self.pollers.setdefault(source_id, (fetch, interval_seconds))
        self.all_pollers.append((source_id, fetch, interval_seconds, timeout_seconds))


@pytest.fixture(scope="session")
def ext():
    """Load the extension as a standalone package, like the host does."""
    spec = importlib.util.spec_from_file_location(
        "_ext_br_south_waterq",
        EXT_DIR / "__init__.py",
        submodule_search_locations=[str(EXT_DIR)],
    )
    sys.modules["_ext_br_south_waterq"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_south_waterq"])
    return types.SimpleNamespace(
        extension=importlib.import_module("_ext_br_south_waterq.extension"),
        presenter=importlib.import_module("_ext_br_south_waterq.presenter"),
        states=importlib.import_module("_ext_br_south_waterq.states"),
        rs=importlib.import_module("_ext_br_south_waterq.providers.rs_fepam"),
        sc=importlib.import_module("_ext_br_south_waterq.providers.sc_ima"),
        pr=importlib.import_module("_ext_br_south_waterq.providers.pr_iat"),
    )


@pytest.fixture
def make_api():
    """Return the FakeAPI class."""
    return FakeAPI


def _fixture_json(name):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.fixture
def rs_json():
    """Return a fresh copy of the stored FEPAM /locais fixture (RS)."""
    return _fixture_json("fepam_rs_locais.json")


@pytest.fixture
def sc_json():
    """Return a fresh copy of the stored IMA /relatorio/mapa fixture (SC)."""
    return _fixture_json("ima_sc_mapa.json")


@pytest.fixture
def sc_samples_json():
    """Return a fresh copy of the stored IMA /relatorio/analisesPonto fixture (SC)."""
    return _fixture_json("ima_sc_analises.json")


@pytest.fixture
def pr_json():
    """Return a fresh copy of the stored IAT ArcGIS layer fixture (PR)."""
    return _fixture_json("iat_pr_layer.json")


class FakeResponse:
    """A fake aiohttp response with a stored body."""

    def __init__(self, body):
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        """Do nothing. The fake response is always successful."""
        return None

    async def text(self):
        """Return the stored body."""
        return self._body


class FakeSession:
    """Return a body for each URL. A tuple of bodies gives one body for each call."""

    def __init__(self, bodies: dict):
        self.bodies = bodies
        self.calls = []

    def _next(self, url):
        body = self.bodies[url]
        if isinstance(body, tuple):
            self.bodies[url] = body[1:] or body  # Keep the last body
            body = body[0]
        return FakeResponse(body if isinstance(body, str) else json.dumps(body))

    def get(self, url):
        """Record a GET call and return the next body."""
        self.calls.append(("GET", url, None))
        return self._next(url)

    def post(self, url, data=None):
        """Record a POST call and return the next body."""
        self.calls.append(("POST", url, data))
        return self._next(url)


@pytest.fixture
def fake_session():
    """Return the FakeSession class."""
    return FakeSession
