"""Offline tests of the br_rs_waterq parser, state class, envelope, presenter, and poller."""

from __future__ import annotations

import asyncio
import json
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "br_rs_waterq"
# A sample date of last week. The poller sends only active results (90 days or newer).
RECENT = (date.today() - timedelta(days=7)).strftime("%d/%m/%Y")
UNSUITABLE_CODES = {1029, 5, 56, 1023}


def _point(ex, locais, cod):
    return next(p for p in ex.parse_locais(locais) if p["cod_local"] == cod)


def _set(locais, cod, **fields):
    for item in locais:
        if item["codLocal"] == cod:
            item.update(fields)


def _ready_state(ex, locais):
    state = ex.BulletinState()
    assert state.changes(ex.parse_locais(locais)) == []
    return state


# ------------------------------------------------------------------ parse


def test_parse_locais(ext, locais_json):
    """Check the point count, the situacao values, and the sample date."""
    ex = ext.extension
    points = ex.parse_locais(locais_json)
    assert len(points) == 96
    unsuitable = {p["cod_local"] for p in points if p["situacao"] == ex.UNSUITABLE}
    assert unsuitable == UNSUITABLE_CODES
    assert all(p["situacao"] == ex.SUITABLE for p in points if p["cod_local"] == 1026)
    assert _point(ex, locais_json, 1026)["sample_date"] == date(2026, 2, 24)


def test_parse_locais_skips_bad_items(ext):
    """Check that a bad item, a bad date, and a bad root do not raise."""
    ex = ext.extension
    ok = {"codLocal": 1, "situacao": 2, "ultimaColeta": "01/02/2026"}
    data = [ok, {"codLocal": 2, "ultimaColeta": None}, {"nome": "x"}, "x", None]
    assert [p["cod_local"] for p in ex.parse_locais(data)] == [1]
    assert ex.parse_locais({"a": 1}) == []
    assert ex.parse_locais(None) == []


# ------------------------------------------------------------------ state


def test_baseline_emits_nothing(ext, locais_json):
    """Check that the first poll records the baseline and the second poll is quiet."""
    ex = ext.extension
    state = ex.BulletinState()
    points = ex.parse_locais(locais_json)
    assert state.changes(points) == []
    assert state.changes(points) == []


def test_new_unsuitable_bulletin(ext, locais_json):
    """Check that a new sample date with situacao 3 gives one event."""
    ex = ext.extension
    state = _ready_state(ex, locais_json)
    _set(locais_json, 1029, ultimaColeta="02/03/2026")  # Was unsuitable already
    _set(locais_json, 1026, ultimaColeta="02/03/2026", situacao=3)  # Was suitable
    got = state.changes(ex.parse_locais(locais_json))
    assert {p["cod_local"] for p in got} == {1029, 1026}
    assert state.changes(ex.parse_locais(locais_json)) == []


def test_same_unsuitable_bulletin_is_quiet(ext, locais_json):
    """Check that an unsuitable point with the same sample date gives no event."""
    ex = ext.extension
    state = _ready_state(ex, locais_json)
    assert state.changes(ex.parse_locais(locais_json)) == []


def test_back_to_suitable(ext, locais_json):
    """Check that situacao 3 to 2 gives an event, and a suitable point alone gives none."""
    ex = ext.extension
    state = _ready_state(ex, locais_json)
    _set(locais_json, 56, situacao=2, ultimaColeta="02/03/2026")
    _set(locais_json, 1026, ultimaColeta="02/03/2026")  # Suitable to suitable
    got = state.changes(ex.parse_locais(locais_json))
    assert [(p["cod_local"], p["situacao"]) for p in got] == [(56, 2)]


def test_not_collected_is_ignored(ext, locais_json):
    """Check that situacao 0 and 1 give no event and keep the last known state."""
    ex = ext.extension
    state = _ready_state(ex, locais_json)
    _set(locais_json, 56, situacao=0, ultimaColeta="02/03/2026")
    _set(locais_json, 1026, situacao=1, ultimaColeta="02/03/2026")
    assert state.changes(ex.parse_locais(locais_json)) == []
    # The point 56 was unsuitable before the gap. A suitable bulletin is news.
    _set(locais_json, 56, situacao=2, ultimaColeta="09/03/2026")
    got = state.changes(ex.parse_locais(locais_json))
    assert [p["cod_local"] for p in got] == [56]


# ------------------------------------------------------------------ envelope


def test_envelope_unsuitable(ext, make_api, locais_json):
    """Check the identity, the time, and the metadata of an unsuitable event."""
    ex = ext.extension
    api = make_api("en")
    before = datetime.now(timezone.utc)
    env = ex.build_envelope(api, _point(ex, locais_json, 56))
    ident = env.identity
    assert ident.event_id == "br_rs_waterq_56_20260223_3"
    assert ident.event_type == "weather_alarm"
    assert ident.provider_family == "extension"
    assert ident.source_id == "br_rs_waterq_balneabilidade"
    # The event time is the detection time. The sample is days old.
    assert ident.published_at.tzinfo is not None
    assert before <= ident.published_at <= datetime.now(timezone.utc)
    assert env.event.effective_at == ident.published_at
    md = env.event.metadata
    assert md["severity_level"] == 2 and md["status"] == "unsuitable"
    assert md["hazard"] == "water_quality" and md["uf"] == "RS"
    assert md["cod_local"] == 56 and md["beach"] == "PRAIA DO U"
    assert md["sample_date"] == "2026-02-23"
    assert isinstance(md["latitude"], float) and isinstance(md["longitude"], float)
    assert md["laboratorio"] and md["municipio"] and md["ibge"]


def test_envelope_suitable(ext, make_api, locais_json):
    """Check the severity, the status, and the id of a suitable-again event."""
    ex = ext.extension
    env = ex.build_envelope(make_api("en"), _point(ex, locais_json, 1026))
    assert env.identity.event_id == "br_rs_waterq_1026_20260224_2"
    assert env.event.metadata["severity_level"] == 0
    assert env.event.metadata["status"] == "suitable"


# ------------------------------------------------------------------ presenter


def _present(ext, make_api, lang, cod, locais):
    api = make_api(lang)
    env = ext.extension.build_envelope(api, _point(ext.extension, locais, cod))
    ctx = types.SimpleNamespace(
        title=env.event.title, headline=env.event.headline, metadata=env.event.metadata
    )
    return ext.presenter.make_presenter(api).present(ctx, None)


def test_presenter_en(ext, make_api, locais_json):
    """Check the English text."""
    out = _present(ext, make_api, "en", 56, locais_json)
    assert out.startswith("🟡 Water quality alert: unsuitable for bathing")
    assert "PRAIA DO U - " in out and "/RS" in out
    assert "Status: unsuitable for bathing" in out
    assert "Sample date: 23/02/2026" in out
    assert "Laboratory: " in out
    assert "Criterion: CONAMA 274/2000" in out
    assert out.endswith("Source: FEPAM / PROCERGS")


def test_presenter_en_suitable_again(ext, make_api, locais_json):
    """Check the emoji and the status of a suitable-again event."""
    out = _present(ext, make_api, "en", 1026, locais_json)
    assert out.startswith("✅ Water quality: suitable for bathing again")
    assert "Status: suitable for bathing" in out


def test_presenter_zh_cn(ext, make_api, locais_json):
    """Check the Chinese text."""
    out = _present(ext, make_api, "zh-CN", 56, locais_json)
    assert out.startswith("🟡 水质预警：不宜游泳")
    assert "状态：不宜游泳" in out and "采样日期：23/02/2026" in out
    assert "检测机构：" in out and out.endswith("来源：FEPAM / PROCERGS")


def test_presenter_pt_br(ext, make_api, locais_json):
    """Check the Brazilian Portuguese text, the decimal comma and the ISO 8601 date."""
    out = _present(ext, make_api, "pt-BR", 56, locais_json)
    assert out.startswith("🟡 Alerta de balneabilidade: imprópria para banho")
    assert "Situação: imprópria para banho" in out
    assert "Data da coleta: 2026-02-23" in out
    assert "Laboratório: " in out
    pos = next(ln for ln in out.splitlines() if ln.startswith("Posição: "))
    assert "," in pos and "." not in pos
    assert out.endswith("Fonte: FEPAM / PROCERGS")


def test_presenter_ja_jp(ext, make_api, locais_json):
    """Check the Japanese text and the Japanese date order."""
    out = _present(ext, make_api, "ja-JP", 56, locais_json)
    assert out.startswith("🟡 水質警報：遊泳不適")
    assert "判定：遊泳不適" in out and "採水日：2026/02/23" in out
    assert "検査機関：" in out and out.endswith("出典：FEPAM / PROCERGS")


# ------------------------------------------------------------------ setup / locale


def test_setup_registrations(ext, make_api):
    """Check that setup registers the presenter, the source, and the poller."""
    api = make_api("en", {"poll_interval": 600})
    ext.extension.setup(api)
    assert set(api.sources) == {"br_rs_waterq_balneabilidade"}
    assert set(api.presenters) == {"br_rs_waterq_weather"}
    m = api.models
    entry = api.sources["br_rs_waterq_balneabilidade"]
    assert entry.source_type == m.SourceType.WEATHER
    assert entry.provider_family == m.ProviderFamily.EXTENSION
    assert (entry.config_group, entry.config_key) == ("extensions", entry.source_id)
    assert entry.presentation_type == "weather"
    assert entry.text_presenter_key == "br_rs_waterq_weather"
    assert entry.display_name and not entry.display_name.startswith("source.")
    assert api.pollers[entry.source_id][1] == 600
    assert hasattr(api.presenters["br_rs_waterq_weather"], "present")


def test_default_interval(ext, make_api):
    """Check that the default polling interval is 6 h."""
    api = make_api("en")
    ext.extension.setup(api)
    assert api.pollers["br_rs_waterq_balneabilidade"][1] == 21600


def test_poller_end_to_end_with_fake_session(ext, make_api, locais_json):
    """Check the poller: baseline first, then one event for a new bulletin."""
    api = make_api("en")
    ext.extension.setup(api)
    fetch, _ = api.pollers["br_rs_waterq_balneabilidade"]
    bodies = [json.dumps(locais_json)]
    _set(locais_json, 1026, situacao=3, ultimaColeta=RECENT)
    bodies.append(json.dumps(locais_json))

    class Resp:
        def __init__(self, body):
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        async def text(self):
            return self._body

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url):
            self.calls.append(url)
            return Resp(bodies[len(self.calls) - 1])

    session = Session()
    assert asyncio.run(fetch(session)) == []
    (env,) = asyncio.run(fetch(session))
    assert session.calls == [ext.extension.LOCAIS_URL] * 2
    sample = datetime.strptime(RECENT, "%d/%m/%Y")
    assert env.identity.event_id == f"br_rs_waterq_1026_{sample:%Y%m%d}_3"


def test_active_and_past(ext, locais_json):
    """Check the hard limit: a sample 90 days old is active, 91 days old is past."""
    ex = ext.extension
    today = date(2026, 5, 24)  # 90 days after 23/02/2026
    points = ex.parse_locais(locais_json, today=today)
    by_date = {p["sample_date"]: p["active"] for p in points}
    assert by_date == {date(2026, 2, 23): True, date(2026, 2, 24): True}
    later = ex.parse_locais(locais_json, today=today + timedelta(days=1))
    assert {p["sample_date"]: p["active"] for p in later}[date(2026, 2, 23)] is False
    assert ex.is_active(date(2026, 2, 24), today + timedelta(days=1)) is True


def test_poller_ignores_past_results(ext, make_api, locais_json):
    """Check that the poller sends no event for a new bulletin with an old sample."""
    api = make_api("en")
    ext.extension.setup(api)
    fetch, _ = api.pollers["br_rs_waterq_balneabilidade"]
    old = (date.today() - timedelta(days=ext.extension.MAX_AGE_DAYS + 1)).strftime(
        "%d/%m/%Y"
    )
    baseline = json.dumps(locais_json)
    _set(locais_json, 1026, situacao=3, ultimaColeta=old)

    class Resp:
        def __init__(self, body):
            self._body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        async def text(self):
            return self._body

    bodies = iter([baseline, json.dumps(locais_json)])

    class Session:
        def get(self, url):
            return Resp(next(bodies))

    session = Session()
    assert asyncio.run(fetch(session)) == []
    assert asyncio.run(fetch(session)) == []


def test_locales_have_same_keys():
    """Check that the four locales have the same key set."""
    tables = {
        p.stem: set(json.loads(p.read_text("utf-8")))
        for p in (EXT_DIR / "locale").glob("*.json")
    }
    assert set(tables) == {"zh-CN", "en", "pt-BR", "ja-JP"}
    for lang, keys in tables.items():
        assert keys == tables["en"], lang


def test_meta_json_and_icon():
    """Check that meta.json is valid and that its icon file is a PNG."""
    meta = json.loads((EXT_DIR / "meta.json").read_text("utf-8"))
    assert meta["id"] == "br_rs_waterq"
    assert meta["maintainer"]["github"] == "h0ffmann"
    assert meta["coverage"]["hazards"] == ["water_quality"]
    assert set(meta["languages"]) == {
        p.stem for p in (EXT_DIR / "locale").glob("*.json")
    }
    icon = meta["icon"]
    assert {"title", "source", "author", "license", "license_url", "changes"} <= set(
        icon
    )
    assert (EXT_DIR / icon["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (EXT_DIR / "extension.png").read_bytes() == (
        EXT_DIR.parent / "br_rs_disaster" / "extension.png"
    ).read_bytes()
