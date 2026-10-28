"""Integration test. Load br_rs with the real host registries.

A real CEMADEN alert goes through the rule chain and the presenter.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "br_rs" / "fixtures"
PLUGIN_DIR = Path(__file__).resolve().parent.parent


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
    """Return a local fixture by URL keyword. The test does not use the network."""

    def get(self, url: str):
        """Return a fake response for the URL. The alert times are set to the current time."""
        if "wsAlertas2" in url:
            # The fixture is from 2026-10-28. Set the update time to the current
            # Brasilia time to simulate a new alert.
            data = json.loads((FIXTURES / "cemaden_alertas.json").read_text("utf-8"))
            now = datetime.now(timezone(timedelta(hours=-3)))
            for alert in data["alertas"]:
                alert["ult_atualizacao"] = now.strftime("%Y-%m-%d %H:%M:%S.000")
            return _FakeResponse(json.dumps(data))
        if "oni.ascii" in url:
            return _FakeResponse((FIXTURES / "oni.ascii.txt").read_text("utf-8"))
        return _FakeResponse((FIXTURES / "cemaden_rain_RS.json").read_text("utf-8"))


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


@pytest.fixture
def loaded_br_rs(tmp_path):
    """Load br_rs through the real extension manager and host registry. Unregister it after the test."""
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
    from astrbot_plugin_disaster_warning.core.extensions.host_registry import (
        HostRegistry,
    )
    from astrbot_plugin_disaster_warning.core.extensions.manager import (
        ExtensionManager,
    )

    service = _FakeService(
        {
            "extensions": {
                "enabled_ids": ["br_rs"],
                "settings": json.dumps({"br_rs": {"language": "en"}}),
            }
        }
    )
    manager = ExtensionManager(
        service,
        HostRegistry(),
        roots=[PLUGIN_DIR / "extensions"],
        data_dir=tmp_path,
    )
    manager.load_all()
    yield manager
    HostRegistry().unregister_source_entries(owner="br_rs")
    HostRegistry().unregister_presenters(owner="br_rs")


def test_br_rs_loads_through_real_host(loaded_br_rs):
    """Check that br_rs loads and that its two sources are in the host catalog."""
    from astrbot_plugin_disaster_warning.core.sources.source_catalog import (
        SOURCE_CATALOG,
        get_source_owner,
    )

    (record,) = loaded_br_rs.status()
    assert record["state"] == "loaded", record["error"]
    assert set(record["sources"]) == {"br_rs_cemaden_alerts", "br_rs_cemaden_rain"}
    for source_id in record["sources"]:
        assert source_id in SOURCE_CATALOG
        assert get_source_owner(source_id) == "br_rs"


async def test_cemaden_alert_passes_rule_chain_and_renders(loaded_br_rs):
    """Check that a fixture alert passes the rule chain and that the presenter shows CEMADEN."""
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.message.push.push_policy import (
        evaluate_push_decision_with_components,
    )

    api = loaded_br_rs._apis["br_rs"]
    poller = next(p for p in api.pollers if p.source_id == "br_rs_cemaden_alerts")
    envelopes = await poller.fetch(_FakeSession())

    # The fixture has 3 alerts for RS
    assert len(envelopes) == 3
    envelope = envelopes[0]
    assert envelope.event.metadata["uf"] == "RS"
    assert envelope.event.metadata["enso"]["phase"] == "el_nino"

    runtime_config = {"weather_config": {"weather_filter": {"enabled": True}}}
    decision = evaluate_push_decision_with_components(
        envelope, runtime_config=runtime_config, policy_state={}
    )
    assert decision.accepted, decision.reason

    text = present_message(envelope, envelope.source_id)
    assert "CEMADEN" in text
    assert envelope.event.metadata["municipio"].title() in text.title()
