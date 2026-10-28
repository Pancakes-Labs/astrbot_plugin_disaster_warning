"""Live tests for br_south_waterq. They call the real APIs of FEPAM (RS), IMA (SC) and IAT (PR).

Set DW_LIVE_TESTS=1 to run them. They check the feed schema, the parsers, and the full host path.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone
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
STATES = ("RS", "SC", "PR")
BRT = timezone(timedelta(hours=-3))

# Field name -> accepted types. The parsers and the events read exactly these fields.
FIELDS = {
    "RS": {
        "codLocal": (int,),
        "nome": (str,),
        "latitude": (str,),
        "longitude": (str,),
        "situacao": (int,),
        "ibge": (str,),
        "ultimaColeta": (str,),
        "laboratorio": (str,),
        "nomeMunicipio": (str,),
    },
    "SC": {
        "CODIGO": (str,),
        "MUNICIPIO_COD_IBGE": (str,),
        "MUNICIPIO": (str,),
        "PONTO_NOME": (str,),
        "BALNEARIO": (str,),
        "LATITUDE": (str,),
        "LONGITUDE": (str,),
        "CONDICAO": (str,),
    },
    "PR": {
        "cod": (str,),
        "municipio": (str,),
        "praia_rio": (str,),
        "local": (str,),
        "ano1": (str,),
        "cod_ibge": (str,),
        "latitude": (float, int),
        "longitude": (float, int),
        **{f"b{n}": (str, type(None)) for n in range(1, 12)},
        **{f"b{n}_data": (int, type(None)) for n in range(1, 12)},
    },
}
KNOWN_VALUES = {
    "RS": ("situacao", (0, 1, 2, 3)),
    "SC": ("CONDICAO", ("PRÓPRIO", "IMPRÓPRIO", "INDETERMINADO")),
}


def _run(coro):
    return asyncio.run(coro)


def _session():
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=_build_ssl_context()),
        timeout=aiohttp.ClientTimeout(total=60),
        headers={"User-Agent": "Mozilla/5.0 (dw-live-tests)"},
    )


async def _fetch_json(provider):
    """Call the provider URL like the provider does, and return the JSON."""
    from_module = getattr(provider, "URL", None) or provider.MAP_URL
    async with _session() as s:
        return await provider.get_json(s, from_module)


@pytest.fixture(scope="module")
def mods():
    """Load br_south_waterq as a standalone package, like the host does."""
    root = PLUGIN_DIR / "extensions" / "br_south_waterq"
    spec = importlib.util.spec_from_file_location(
        "_ext_br_south_waterq_live",
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    sys.modules["_ext_br_south_waterq_live"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_south_waterq_live"])
    return types.SimpleNamespace(
        extension=importlib.import_module("_ext_br_south_waterq_live.extension"),
        states=importlib.import_module("_ext_br_south_waterq_live.states"),
    )


@pytest.fixture(scope="module", params=STATES)
def uf(request):
    """Return each state of the South, one by one."""
    return request.param


@pytest.fixture(scope="module")
def live(mods, uf):
    """Download the live data of one state."""
    return _run(_fetch_json(mods.states.STATES[uf]))


def _items(uf, data):
    if uf == "PR":
        return [f["attributes"] for f in data["features"]]
    return data


# --- 1. Schema (contract) ----------------------------------------------------


def test_schema(uf, live):
    """Check that every live item has the fields the parser reads, with the expected types."""
    items = _items(uf, live)
    assert isinstance(items, list) and items, f"Schema drift in {uf}: no items"
    for i, item in enumerate(items):
        where = f"{uf}[{i}]"
        for name, types_ in FIELDS[uf].items():
            assert name in item, f"Schema drift in {where}: field '{name}' is missing"
            value = item[name]
            assert isinstance(value, types_) and not isinstance(value, bool), (
                f"Schema drift in {where}: field '{name}' has type "
                f"{type(value).__name__}, expected {[t.__name__ for t in types_]}"
            )
        if uf in KNOWN_VALUES:
            field, values = KNOWN_VALUES[uf]
            assert item[field] in values, f"Unknown {field} in {where}: {item[field]!r}"
    if uf == "PR":
        values = {(item[f"b{n}"] or "").strip() for item in items for n in range(1, 12)}
        assert values <= {"", "Propria", "Impropria"}, f"Unknown PR values: {values}"


# --- 2. Parsing correctness on live data -------------------------------------


def test_parse_live(mods, uf, live):
    """Check that the provider keeps every live point, with a known status and a position."""
    points = mods.states.STATES[uf].parse(live)
    assert len(points) == len(_items(uf, live)), f"Some {uf} points were not parsed"
    assert len({p["key"] for p in points}) == len(points), "The keys are not unique"
    for p in points:
        assert p["uf"] == uf and p["beach"] and p["municipio"]
        assert p["status"] in ("suitable", "unsuitable", None)
        assert isinstance(p["latitude"], float) and isinstance(p["longitude"], float)
        assert p["sample_date"] is None or isinstance(p["sample_date"], date)
        assert p["bulletin"] is not None or uf == "SC"


def test_sc_samples_live(mods):
    """Check the SC samples endpoint: dates, and the E. coli result of the newest sample."""
    sc = mods.states.STATES["SC"]
    points = sc.parse(_run(_fetch_json(sc)))

    async def complete():
        async with _session() as s:
            return await sc.complete(s, points[:1])

    (p,) = _run(complete())
    assert isinstance(p["sample_date"], date), f"No sample date for {p['key']}"
    assert p["sample_date"] <= datetime.now(BRT).date()
    assert p["ecoli"] is None or p["ecoli"] >= 0


# --- 3. End to end through the real host -------------------------------------


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


class _ReplaySession:
    """Return one stored body for GET. Send POST to the real API."""

    def __init__(self, body, real):
        self._body = body
        self._real = real

    def get(self, url):
        """Return a fake response with the stored body."""
        return _ReplayResponse(self._body)

    def post(self, url, data=None):
        """Call the real API."""
        return self._real.post(url, data=data)


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
def loaded(tmp_path, uf):
    """Load br_south_waterq for one state through the real manager and host registry."""
    settings = {"br_south_waterq": {"language": "en", "states": [uf]}}
    service = _FakeService(
        {
            "extensions": {
                "enabled_ids": ["br_south_waterq"],
                "settings": json.dumps(settings),
            }
        }
    )
    manager = ExtensionManager(
        service, HostRegistry(), roots=[PLUGIN_DIR / "extensions"], data_dir=tmp_path
    )
    manager.load_all()
    yield manager
    HostRegistry().unregister_source_entries(owner="br_south_waterq")
    HostRegistry().unregister_presenters(owner="br_south_waterq")


def _flip(uf, raw):
    """Change the status of one raw item to the other status, in place."""
    if uf == "RS":
        raw.update(situacao=3 if raw["situacao"] == 2 else 2, ultimaColeta="01/01/2000")
    elif uf == "SC":
        raw["CONDICAO"] = "IMPRÓPRIO" if raw["CONDICAO"] == "PRÓPRIO" else "PRÓPRIO"
    else:
        n = max(i for i in range(1, 12) if (raw[f"b{i}"] or "").strip())
        raw[f"b{n}"] = "Impropria" if raw[f"b{n}"] == "Propria" else "Propria"


async def test_end_to_end_live(loaded, mods, uf, live):
    """Run the real poller, the rule chain, and the presenter on the live data.

    The baseline has one point with the other status. The live poll then shows a change.
    The poller sends an event only if the live result is active (90 days or newer).
    """
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.message.push.push_policy import (
        evaluate_push_decision_with_components,
    )

    (record,) = loaded.status()
    assert record["state"] == "loaded", record["error"]
    (poller,) = loaded._apis["br_south_waterq"].pollers
    assert poller.interval_seconds == 21600

    provider = mods.states.STATES[uf]
    points = [p for p in provider.parse(live) if p["status"]]
    target = next((p for p in points if p["status"] == "unsuitable"), points[0])
    seeded = json.loads(json.dumps(live))
    for raw in _items(uf, seeded):
        if (
            provider.parse(
                [raw] if uf != "PR" else {"features": [{"attributes": raw}]}
            )[0]["key"]
            == target["key"]
        ):
            _flip(uf, raw)

    async with _session() as real:
        assert await poller.fetch(_ReplaySession(json.dumps(seeded), real)) == []
        envelopes = await poller.fetch(_ReplaySession(json.dumps(live), real))
        (done,) = await provider.complete(real, [dict(target)])

    today = datetime.now(BRT).date()
    expected = 1 if mods.extension.is_active(done["sample_date"], today) else 0
    assert len(envelopes) == expected, (uf, done["sample_date"], len(envelopes))
    if envelopes:
        (envelope,) = envelopes
        md = envelope.event.metadata
        assert md["point"] == target["key"] and md["status"] == target["status"]
    else:
        # Out of season: the result is past. Check the host path with a direct envelope.
        envelope = mods.extension.build_envelope(
            loaded._apis["br_south_waterq"], done, provider.SOURCE_LABEL
        )
    assert envelope.event.metadata["uf"] == uf
    runtime_config = {"weather_config": {"weather_filter": {"enabled": True}}}
    decision = evaluate_push_decision_with_components(
        envelope, runtime_config=runtime_config, policy_state={}
    )
    assert decision.accepted, decision.reason
    text = present_message(envelope, envelope.source_id)
    assert provider.SOURCE_LABEL in text and target["beach"] in text
    print(
        f"\n{uf}: {len(envelopes)} event(s), sample {done['sample_date']}"
    )  # Shown with -s
