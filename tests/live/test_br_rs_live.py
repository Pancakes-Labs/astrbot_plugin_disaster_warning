"""Live tests for br_rs. They call the real CEMADEN and NOAA CPC services.

Set DW_LIVE_TESTS=1 to run them. They check the feed schemas, the parsers, and the full host path.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
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
ALERTS_URL = "https://painelalertas.cemaden.gov.br/wsAlertas2"
RAIN_URL = "https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
BRT = timezone(timedelta(hours=-3))
SEASONS = "DJF JFM FMA MAM AMJ MJJ JJA JAS ASO SON OND NDJ".split()

# Field name -> accepted types. The parsers read exactly these fields.
ALERT_FIELDS = {
    "cod_alerta": (int,),
    "datahoracriacao": (str,),
    "ult_atualizacao": (str,),
    "codibge": (int,),
    "evento": (str,),
    "nivel": (str,),
    "uf": (str,),
    "municipio": (str,),
    "latitude": (int, float),
    "longitude": (int, float),
}
GAUGE_FIELDS = {
    "uf": (str,),
    "codibge": (int,),
    "cidade": (str,),
    "nomeestacao": (str,),
    "datahoraUltimovalor": (str, type(None)),  # Null for a gauge with no reading
}
GAUGE_ACC_FIELDS = ("acc1hr", "acc24hr")  # A number or the string "-"


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


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_fields(item: dict, fields: dict, where: str) -> None:
    for name, types in fields.items():
        assert name in item, f"Schema drift in {where}: field '{name}' is missing"
        value = item[name]
        assert isinstance(value, types) and not isinstance(value, bool), (
            f"Schema drift in {where}: field '{name}' has type "
            f"{type(value).__name__}, expected {[t.__name__ for t in types]}"
        )


@pytest.fixture(scope="module")
def ext():
    """Load br_rs as a standalone package, like the host does."""
    spec = importlib.util.spec_from_file_location(
        "_ext_br_rs_live",
        PLUGIN_DIR / "extensions" / "br_rs" / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR / "extensions" / "br_rs")],
    )
    sys.modules["_ext_br_rs_live"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sys.modules["_ext_br_rs_live"])
    return importlib.import_module("_ext_br_rs_live.extension")


@pytest.fixture(scope="module")
def enso_mod(ext):
    """Return the enso module of the live-loaded br_rs package."""
    return importlib.import_module("_ext_br_rs_live.enso")


@pytest.fixture(scope="module")
def alerts_json():
    """Download the live CEMADEN alerts."""
    return json.loads(_run(_fetch_text(ALERTS_URL)))


@pytest.fixture(scope="module")
def rain_json():
    """Download the live CEMADEN rain gauges of RS."""
    return json.loads(_run(_fetch_text(RAIN_URL)))


@pytest.fixture(scope="module")
def oni_text():
    """Download the live NOAA ONI text."""
    return _run(_fetch_text(ONI_URL))


# --- 1. Schema (contract) of each feed ---------------------------------------


def test_alerts_schema(alerts_json):
    """Check that every live alert has the fields the parser reads, with the expected types."""
    assert isinstance(alerts_json, dict), (
        "Schema drift in alerts: root is not an object"
    )
    assert isinstance(alerts_json.get("alertas"), list), (
        "Schema drift in alerts: field 'alertas' is missing or is not a list"
    )
    for i, alert in enumerate(alerts_json["alertas"]):
        where = f"alerts[{i}] (cod_alerta={alert.get('cod_alerta')})"
        _check_fields(alert, ALERT_FIELDS, where)


def test_rain_schema(rain_json):
    """Check that every live gauge has the fields the parser reads, and that the accumulations are numbers or '-'."""
    assert isinstance(rain_json, list) and rain_json, (
        "Schema drift in rain gauges: root is not a non-empty list"
    )
    for i, gauge in enumerate(rain_json):
        where = f"rain[{i}] (idestacao={gauge.get('idestacao')})"
        _check_fields(gauge, GAUGE_FIELDS, where)
        for name in GAUGE_ACC_FIELDS:
            assert name in gauge, f"Schema drift in {where}: field '{name}' is missing"
            value = gauge[name]
            assert value == "-" or _is_number(value), (
                f"Schema drift in {where}: '{name}' is {value!r}, "
                "expected a number or '-'"
            )


def test_oni_schema(oni_text):
    """Check that the live ONI file has numeric columns and known season names."""
    rows = [p for p in (ln.split() for ln in oni_text.splitlines()) if len(p) == 4]
    assert rows, "Schema drift in ONI: no line with 4 columns"
    data = [r for r in rows if r[0] in SEASONS]
    assert data, (
        f"Schema drift in ONI: no data row with a known season, first row {rows[0]}"
    )
    for parts in data:
        try:
            int(parts[1])
            float(parts[2])
            float(parts[3])
        except ValueError:
            pytest.fail(f"Schema drift in ONI: non-numeric columns in {parts}")


# --- 2. Parsing correctness on live data -------------------------------------


def test_parse_alerts_live(ext, alerts_json):
    """Check that parse_alerts keeps only RS alerts with level 2 to 4 and a flood or landslide hazard."""
    out = ext.parse_alerts(alerts_json)
    expected = [
        a
        for a in alerts_json["alertas"]
        if a["uf"] == "RS" and a["nivel"] in ext.ALERT_LEVELS
    ]
    assert len(out) == len(expected)
    for a in out:
        assert a["raw"]["uf"] == "RS"
        assert a["level"] in (2, 3, 4)
        assert a["hazard"] in ("flood", "landslide")
        assert a["issued_at"] is not None, f"Unparsed datahoracriacao: {a['raw']}"
        assert a["updated_at"] is not None, f"Unparsed ult_atualizacao: {a['raw']}"


def test_parse_rain_live(ext, rain_json):
    """Check that parse_rain returns only gauges above a threshold, with float or None accumulations."""
    out = ext.parse_rain(rain_json)
    assert len({i["codibge"] for i in out}) == len(out)  # One gauge for each city
    for item in out:
        assert item["level"] in (2, 3, 4)
        for key in ("acc1hr", "acc24hr"):
            assert item[key] is None or isinstance(item[key], float)
        t1, t24 = ext.DEFAULT_THRESHOLDS[item["level"]]
        assert (item["acc1hr"] or 0) >= t1 or (item["acc24hr"] or 0) >= t24
        assert item["observed_at"] is not None, f"Unparsed gauge time: {item}"
    # A gauge above a threshold must not be missing from the result.
    over = {
        g["codibge"]
        for g in rain_json
        if g["uf"] == "RS"
        and any(
            (_is_number(g["acc1hr"]) and g["acc1hr"] >= t1)
            or (_is_number(g["acc24hr"]) and g["acc24hr"] >= t24)
            for t1, t24 in ext.DEFAULT_THRESHOLDS.values()
        )
    }
    assert {i["codibge"] for i in out} == over


def test_build_enso_live(enso_mod, oni_text):
    """Check that the live ENSO data is recent and that the phase matches classify."""
    enso = enso_mod.build_enso(oni_text)
    assert enso is not None, "build_enso found no data row in the live ONI file"
    assert enso["season"] in SEASONS
    # The year is the year of the middle month. The last month is one month later.
    last_month = SEASONS.index(enso["season"]) + 2
    year = enso["year"] + (1 if last_month > 12 else 0)
    last_month = (last_month - 1) % 12 + 1
    season_end = datetime(year, last_month, 28)
    age = datetime.now() - season_end
    assert age <= timedelta(days=125), (
        f"ONI data is old: {enso['season']} {enso['year']}"
    )
    assert isinstance(enso["anom"], float)
    assert (enso["phase"], enso["strength"]) == enso_mod.classify(enso["anom"])


# --- 3. End to end through the real host -------------------------------------


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


@pytest.fixture
def loaded(tmp_path):
    """Load br_rs through the real extension manager and host registry."""
    service = _FakeService(
        {
            "extensions": {
                "enabled_ids": ["br_rs"],
                "settings": json.dumps({"br_rs": {"language": "en"}}),
            }
        }
    )
    manager = ExtensionManager(
        service, HostRegistry(), roots=[PLUGIN_DIR / "extensions"], data_dir=tmp_path
    )
    manager.load_all()
    yield manager
    HostRegistry().unregister_source_entries(owner="br_rs")
    HostRegistry().unregister_presenters(owner="br_rs")


async def test_end_to_end_live(loaded, ext):
    """Run the real pollers, the rule chain, and the presenter on the live data."""
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.message.push.push_policy import (
        evaluate_push_decision_with_components,
    )

    (record,) = loaded.status()
    assert record["state"] == "loaded", record["error"]

    fetched = {}
    async with _session() as session:
        for poller in loaded._apis["br_rs"].pollers:
            fetched[poller.source_id] = await poller.fetch(session)
    assert set(fetched) == {"br_rs_cemaden_alerts", "br_rs_cemaden_rain"}
    print("\nlive envelopes:", {k: len(v) for k, v in fetched.items()})  # Shown with -s

    runtime_config = {"weather_config": {"weather_filter": {"enabled": True}}}
    for source_id, envelopes in fetched.items():
        for envelope in envelopes:
            md = envelope.event.metadata
            assert envelope.source_id == source_id
            assert md["uf"] == "RS"
            assert md["severity_level"] in (2, 3, 4)

            decision = evaluate_push_decision_with_components(
                envelope, runtime_config=runtime_config, policy_state={}
            )
            assert isinstance(decision.accepted, bool)

            text = present_message(envelope, envelope.source_id)
            assert "CEMADEN" in text

            if source_id == "br_rs_cemaden_alerts":
                updated = envelope.payload.raw["ult_atualizacao"]
                expected = datetime.strptime(updated[:19], ext.ALERT_TIME_FMT)
                assert envelope.event.effective_at == expected.replace(tzinfo=BRT)
                assert envelope.identity.published_at == envelope.event.effective_at
