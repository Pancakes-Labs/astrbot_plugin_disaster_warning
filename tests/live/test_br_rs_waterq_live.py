"""Live tests for br_rs_waterq. They call the real PROCERGS API of FEPAM.

Set DW_LIVE_TESTS=1 to run them. They check the feed schema, the parser, and the full host path.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

if os.environ.get("DW_LIVE_TESTS") != "1":
    pytest.skip("Set DW_LIVE_TESTS=1 to run the live tests.", allow_module_level=True)

import aiohttp  # noqa: E402

importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
from astrbot_plugin_disaster_warning.core.extensions.host_registry import (  # noqa: E402
    HostRegistry,
)
from astrbot_plugin_disaster_warning.core.extensions.manager import (  # noqa: E402
    ExtensionManager,
    _build_ssl_context,
)

pytestmark = pytest.mark.live

PLUGIN_DIR = Path(__file__).resolve().parents[2]
LOCAIS_URL = "https://secweb.procergs.com.br/baln/rest/locais"

# Field name -> accepted types. The parser and the event read exactly these fields.
LOCAL_FIELDS = {
    "codLocal": (int,),
    "nome": (str,),
    "latitude": (str,),
    "longitude": (str,),
    "situacao": (int,),
    "ibge": (str,),
    "ultimaColeta": (str,),
    "laboratorio": (str,),
    "nomeMunicipio": (str,),
}


def _run(coro):
    return asyncio.run(coro)


def _session():
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_build_ssl_context()),
        timeout=aiohttp.ClientTimeout(total=60),
        headers={"User-Agent": "Mozilla/5.0 (dw-live-tests)"},
    )


async def _fetch_text(url: str) -> str:
    async with _session() as s, s.get(url) as resp:
        resp.raise_for_status()
        return await resp.text()


@pytest.fixture(scope="module")
def ext():
    """Load br_rs_waterq as a standalone package, like the host does."""
    spec = importlib.util.spec_from_file_location(
        "_ext_br_rs_waterq_live",
        PLUGIN_DIR / "extensions" / "br_rs_waterq" / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR / "extensions" / "br_rs_waterq")],
    )
    sys.modules["_ext_br_rs_waterq_live"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_rs_waterq_live"])
    return importlib.import_module("_ext_br_rs_waterq_live.extension")


@pytest.fixture(scope="module")
def locais_json():
    """Download the live /locais list."""
    return json.loads(_run(_fetch_text(LOCAIS_URL)))


# --- 1. Schema (contract) ----------------------------------------------------


def test_locais_schema(locais_json):
    """Check that every live point has the fields the parser reads, with the expected types."""
    assert isinstance(locais_json, list) and locais_json, (
        "Schema drift in /locais: root is not a non-empty list"
    )
    for i, item in enumerate(locais_json):
        where = f"locais[{i}] (codLocal={item.get('codLocal')})"
        for name, types in LOCAL_FIELDS.items():
            assert name in item, f"Schema drift in {where}: field '{name}' is missing"
            value = item[name]
            assert isinstance(value, types) and not isinstance(value, bool), (
                f"Schema drift in {where}: field '{name}' has type "
                f"{type(value).__name__}, expected {[t.__name__ for t in types]}"
            )
        assert item["situacao"] in (0, 1, 2, 3), f"Unknown situacao in {where}"


# --- 2. Parsing correctness on live data -------------------------------------


def test_parse_locais_live(ext, locais_json):
    """Check that parse_locais keeps every live point, with a date and a float position."""
    points = ext.parse_locais(locais_json)
    assert len(points) == len(locais_json), "Some live points were not parsed"
    for p in points:
        assert isinstance(p["sample_date"], date)
        assert p["situacao"] in (0, 1, 2, 3)
        for key in ("latitude", "longitude"):
            assert ext._float(p["raw"][key]) is not None, f"Bad {key}: {p['raw']}"


# --- 3. End to end through the real host -------------------------------------


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


class _ReplaySession:
    """Return the stored bodies one by one, in the order of the calls."""

    def __init__(self, bodies):
        self._bodies = list(bodies)

    def get(self, url):
        """Return a fake response with the next body."""
        assert url == LOCAIS_URL
        return _ReplayResponse(self._bodies.pop(0))


class _ReplayResponse:
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


@pytest.fixture
def loaded(tmp_path):
    """Load br_rs_waterq through the real extension manager and host registry."""
    service = _FakeService(
        {
            "extensions": {
                "enabled_ids": ["br_rs_waterq"],
                "settings": json.dumps({"br_rs_waterq": {"language": "en"}}),
            }
        }
    )
    manager = ExtensionManager(
        service, HostRegistry(), roots=[PLUGIN_DIR / "extensions"], data_dir=tmp_path
    )
    manager.load_all()
    yield manager
    HostRegistry().unregister_source_entries(owner="br_rs_waterq")
    HostRegistry().unregister_presenters(owner="br_rs_waterq")


async def test_end_to_end_live(loaded, locais_json):
    """Run the real poller, the rule chain, and the presenter on the live data."""
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.message.push.push_policy import (
        evaluate_push_decision_with_components,
    )

    (record,) = loaded.status()
    assert record["state"] == "loaded", record["error"]
    (poller,) = loaded._apis["br_rs_waterq"].pollers
    assert poller.source_id == "br_rs_waterq_balneabilidade"
    assert poller.interval_seconds == 21600

    # FEPAM samples from November to February. Out of season, all live results can be past.
    # Then the poller must send nothing.
    ext = sys.modules["_dw_ext_br_rs_waterq.extension"]
    active = [p for p in ext.parse_locais(locais_json) if p["active"]]
    if not active:
        assert await poller.fetch(_ReplaySession([json.dumps(locais_json)])) == []
        async with _session() as live:
            assert await poller.fetch(live) == []  # Past results give no event
        target = next(p for p in ext.parse_locais(locais_json))
        envelope = ext.build_envelope(loaded._apis["br_rs_waterq"], target)
        target = target["raw"]
    else:
        # Seed the baseline: one active live point had a suitable result the day before.
        # The live poll then shows a new unsuitable bulletin for this point.
        point = next((p for p in active if p["situacao"] == 3), active[0])
        target = point["raw"]
        seeded = [dict(p) for p in locais_json]
        day_before = point["sample_date"].toordinal() - 1
        for p in seeded:
            if p["codLocal"] == target["codLocal"]:
                p.update(
                    situacao=2,
                    ultimaColeta=date.fromordinal(day_before).strftime("%d/%m/%Y"),
                )
        assert await poller.fetch(_ReplaySession([json.dumps(seeded)])) == []
        async with _session() as live:
            envelopes = await poller.fetch(live)  # The second poll calls the real API
        if target["situacao"] == 3:
            (envelope,) = (
                e
                for e in envelopes
                if e.event.metadata["cod_local"] == target["codLocal"]
            )
            md = envelope.event.metadata
            assert md["status"] == "unsuitable"
            assert md["uf"] == "RS" and md["hazard"] == "water_quality"
        else:
            # Suitable after suitable is not news. Check the envelope directly.
            envelope = ext.build_envelope(loaded._apis["br_rs_waterq"], point)

    runtime_config = {"weather_config": {"weather_filter": {"enabled": True}}}
    decision = evaluate_push_decision_with_components(
        envelope, runtime_config=runtime_config, policy_state={}
    )
    assert decision.accepted, decision.reason

    text = present_message(envelope, envelope.source_id)
    assert "FEPAM / PROCERGS" in text and target["nome"] in text
