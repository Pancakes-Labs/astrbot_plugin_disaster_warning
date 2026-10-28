"""Fixtures: real host model classes (loaded by path) + a fake ExtensionAPI."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from enum import Enum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXT_DIR = ROOT / "extensions" / "br_rs"
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str, path: Path, pkg: bool = False):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
        submodule_search_locations=[str(path.parent)] if pkg else None,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def build_models() -> types.SimpleNamespace:
    # Load only the dependency-free host modules; avoids the service import cycle.
    _load("_hostdom", ROOT / "core" / "domain" / "__init__.py", pkg=True)
    em = importlib.import_module("_hostdom.event_models")
    se = _load("_hostsrc", ROOT / "core" / "sources" / "source_entry.py")
    rr = _load("_hostrule", ROOT / "core" / "rules" / "rule_result.py")
    pf = se.ProviderFamily
    if not hasattr(pf, "EXTENSION"):  # added by the host refactor; shim until merged
        members = {m.name: m.value for m in pf}
        members["EXTENSION"] = "extension"
        pf = Enum("ProviderFamily", members)
    return types.SimpleNamespace(
        SourceEntry=se.SourceEntry,
        SourceType=se.SourceType,
        ProviderFamily=pf,
        FusionRole=se.FusionRole,
        WeatherEvent=em.WeatherEvent,
        EventEnvelope=em.EventEnvelope,
        EventIdentity=em.EventIdentity,
        SourcePayload=em.SourcePayload,
        RuleDecision=rr.RuleDecision,
    )


_MODELS = None


class FakeAPI:
    def __init__(self, lang="zh-CN", config=None):
        global _MODELS
        _MODELS = _MODELS or build_models()
        self.id = "br_rs"
        self.config = config or {}
        self.models = _MODELS
        self.lang = lang
        self.permissions = ["network"]
        self.sources, self.pollers, self.presenters = {}, {}, {}

    def _locale(self, lang):
        p = EXT_DIR / "locale" / f"{lang}.json"
        return json.loads(p.read_text("utf-8")) if p.exists() else {}

    def t(self, key, **fmt):
        text = self._locale(self.lang).get(key) or self._locale("zh-CN").get(key, key)
        try:
            return text.format(**fmt)
        except Exception:
            return text

    def register_source(self, entry):
        if entry.config_group != "extensions" or (
            entry.provider_family != self.models.ProviderFamily.EXTENSION
        ):
            raise ValueError("bad extension source")
        self.sources[entry.source_id] = entry

    def register_presenter(self, key, cls):
        self.presenters[key] = cls

    def register_poller(
        self, source_id, fetch, *, interval_seconds, timeout_seconds=30
    ):
        assert "network" in self.permissions and source_id in self.sources
        self.pollers[source_id] = (fetch, max(60, interval_seconds), timeout_seconds)


@pytest.fixture(scope="session")
def ext():
    pkg = _load("_ext_br_rs", EXT_DIR / "__init__.py", pkg=True)
    return types.SimpleNamespace(
        extension=importlib.import_module("_ext_br_rs.extension"),
        enso=importlib.import_module("_ext_br_rs.enso"),
        presenter=importlib.import_module("_ext_br_rs.presenter"),
        pkg=pkg,
    )


@pytest.fixture
def make_api():
    return FakeAPI


@pytest.fixture(scope="session")
def alerts_json():
    return json.loads((FIXTURES / "cemaden_alertas.json").read_text("utf-8"))


@pytest.fixture(scope="session")
def rain_json():
    return json.loads((FIXTURES / "cemaden_rain_RS.json").read_text("utf-8"))


@pytest.fixture(scope="session")
def oni_text():
    return (FIXTURES / "oni.ascii.txt").read_text("utf-8")
