"""Integration test of the extension mechanism with two extensions at the same time.

Load br_rs_disaster and br_rs_waterq with the real extension manager and host registry.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
WATERQ_FIXTURE = PLUGIN_DIR / "tests" / "br_rs_waterq" / "fixtures"
DISASTER_FIXTURE = PLUGIN_DIR / "tests" / "br_rs_disaster" / "fixtures"

DISASTER = "br_rs_disaster"
WATERQ = "br_rs_waterq"
DISASTER_SOURCES = {"br_rs_disaster_cemaden_alerts", "br_rs_disaster_cemaden_rain"}
WATERQ_SOURCES = {"br_rs_waterq_balneabilidade"}

BROKEN_SETUP = """
def setup(api):
    m = api.models
    api.register_presenter("broken_weather", type("P", (), {"present": classmethod(lambda c, d, o=None: "")}))
    api.register_source(m.SourceEntry(
        source_id="broken_src", source_enum="broken_src",
        source_type=m.SourceType.WEATHER, provider_family=m.ProviderFamily.EXTENSION,
        config_group="extensions", config_key="broken_src", parser_name="",
        presentation_type="weather", text_presenter_key="broken_weather",
        report_policy="none", intensity_mode="none", priority=1,
        display_name="Broken", description="Broken", default_timezone="UTC",
        fingerprint_prefix="broken_src", dispatch_family="broken_src",
        provider_aliases=("broken_src",), routing_tags=("extension",),
    ))
    raise RuntimeError("setup failed on purpose")
"""


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        """Do nothing. The fake response is always successful."""
        return None

    async def text(self):
        """Return the stored response text."""
        return self._text


class _FakeSession:
    """Return a stored body by URL keyword, one body for each call. No network."""

    def __init__(self, bodies: list[str] | None = None):
        self._bodies = list(bodies or [])

    def get(self, url: str):
        """Return a fake response for the URL."""
        if "wsAlertas2" in url:
            data = json.loads(
                (DISASTER_FIXTURE / "cemaden_alertas.json").read_text("utf-8")
            )
            now = datetime.now(timezone(timedelta(hours=-3)))
            for alert in data["alertas"]:
                alert["ult_atualizacao"] = now.strftime("%Y-%m-%d %H:%M:%S.000")
            return _FakeResponse(json.dumps(data))
        if "oni.ascii" in url:
            return _FakeResponse(
                (DISASTER_FIXTURE / "oni.ascii.txt").read_text("utf-8")
            )
        return _FakeResponse(self._bodies.pop(0))


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


def _config(ids: list[str]) -> dict:
    settings = {i: {"language": "en"} for i in ids}
    return {"extensions": {"enabled_ids": ids, "settings": json.dumps(settings)}}


def _make_manager(tmp_path, ids, roots):
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
    from astrbot_plugin_disaster_warning.core.extensions.host_registry import (
        HostRegistry,
    )
    from astrbot_plugin_disaster_warning.core.extensions.manager import (
        ExtensionManager,
    )

    return ExtensionManager(
        _FakeService(_config(ids)), HostRegistry(), roots=roots, data_dir=tmp_path
    )


def _cleanup(*owners: str) -> None:
    from astrbot_plugin_disaster_warning.core.extensions.host_registry import (
        HostRegistry,
    )

    registry = HostRegistry()
    for owner in owners:
        registry.unregister_source_entries(owner=owner)
        registry.unregister_presenters(owner=owner)


@pytest.fixture
def two_loaded(tmp_path):
    """Load both extensions from the repository. Unregister them after the test."""
    manager = _make_manager(tmp_path, [DISASTER, WATERQ], [PLUGIN_DIR / "extensions"])
    manager.load_all()
    yield manager
    _cleanup(DISASTER, WATERQ)


def _poller(manager, ext_id, source_id):
    return next(p for p in manager._apis[ext_id].pollers if p.source_id == source_id)


def test_both_loaded_without_collision(two_loaded):
    """Check that both extensions load and that their sources are distinct in the catalog."""
    from astrbot_plugin_disaster_warning.core.sources.source_catalog import (
        SOURCE_CATALOG,
        get_source_owner,
    )

    records = {r["id"]: r for r in two_loaded.status()}
    assert set(records) == {DISASTER, WATERQ}
    for rec in records.values():
        assert rec["state"] == "loaded", rec["error"]
    assert set(records[DISASTER]["sources"]) == DISASTER_SOURCES
    assert set(records[WATERQ]["sources"]) == WATERQ_SOURCES
    assert DISASTER_SOURCES.isdisjoint(WATERQ_SOURCES)
    for sources, owner in ((DISASTER_SOURCES, DISASTER), (WATERQ_SOURCES, WATERQ)):
        for source_id in sources:
            assert source_id in SOURCE_CATALOG
            assert get_source_owner(source_id) == owner


async def test_each_event_uses_its_own_presenter(two_loaded):
    """Check that each event is presented by the presenter of its own extension."""
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )

    alerts = _poller(two_loaded, DISASTER, "br_rs_disaster_cemaden_alerts")
    (alert, *_) = await alerts.fetch(_FakeSession())

    locais = json.loads(
        (WATERQ_FIXTURE / "balneabilidade_locais.json").read_text("utf-8")
    )
    baseline = json.dumps(locais)
    for item in locais:
        if item["codLocal"] == 1026:
            item.update(situacao=3, ultimaColeta="02/03/2026")
    waterq = _poller(two_loaded, WATERQ, "br_rs_waterq_balneabilidade")
    session = _FakeSession([baseline, json.dumps(locais)])
    assert await waterq.fetch(session) == []
    (water,) = await waterq.fetch(session)

    alert_text = present_message(alert, alert.source_id)
    water_text = present_message(water, water.source_id)
    assert "CEMADEN" in alert_text and "FEPAM" not in alert_text
    assert "FEPAM / PROCERGS" in water_text and "CEMADEN" not in water_text
    assert "PRAIA DA PICADA" in water_text


def test_failing_extension_does_not_affect_the_others(tmp_path):
    """Check that a third extension that raises in setup is removed and the others stay."""
    from astrbot_plugin_disaster_warning.core.sources.source_catalog import (
        SOURCE_CATALOG,
    )

    root = tmp_path / "root"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "extension.yaml").write_text(
        "id: broken\nname: Broken\nversion: 0.1.0\napi_version: 1\n"
        "min_host_version: 1.6.5\nentry: extension:setup\n"
        "description: This extension fails on purpose.\n"
        "author: test\npermissions: []\n",
        "utf-8",
    )
    (root / "broken" / "__init__.py").write_text("", "utf-8")
    (root / "broken" / "extension.py").write_text(BROKEN_SETUP, "utf-8")
    manager = _make_manager(
        tmp_path, [DISASTER, "broken", WATERQ], [PLUGIN_DIR / "extensions", root]
    )
    try:
        manager.load_all()
        records = {r["id"]: r for r in manager.status()}
        assert records[DISASTER]["state"] == "loaded"
        assert records[WATERQ]["state"] == "loaded"
        assert records["broken"]["state"] == "error"
        assert "setup failed on purpose" in records["broken"]["error"]
        assert "broken_src" not in SOURCE_CATALOG  # The host removed its registrations
        for source_id in DISASTER_SOURCES | WATERQ_SOURCES:
            assert source_id in SOURCE_CATALOG
    finally:
        _cleanup(DISASTER, WATERQ, "broken")


async def test_unregister_one_leaves_the_other(two_loaded):
    """Check that the removal of one extension keeps the sources and the presenter of the other."""
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.sources.source_catalog import (
        SOURCE_CATALOG,
        get_source_owner,
    )

    locais = json.loads(
        (WATERQ_FIXTURE / "balneabilidade_locais.json").read_text("utf-8")
    )
    baseline = json.dumps(locais)
    for item in locais:
        if item["codLocal"] == 56:
            item.update(ultimaColeta="02/03/2026")
    waterq = _poller(two_loaded, WATERQ, "br_rs_waterq_balneabilidade")
    session = _FakeSession([baseline, json.dumps(locais)])
    await waterq.fetch(session)
    (water,) = await waterq.fetch(session)

    _cleanup(DISASTER)
    for source_id in DISASTER_SOURCES:
        assert source_id not in SOURCE_CATALOG
    for source_id in WATERQ_SOURCES:
        assert get_source_owner(source_id) == WATERQ
    assert "FEPAM / PROCERGS" in present_message(water, water.source_id)

    _cleanup(WATERQ)
    for source_id in WATERQ_SOURCES:
        assert source_id not in SOURCE_CATALOG
