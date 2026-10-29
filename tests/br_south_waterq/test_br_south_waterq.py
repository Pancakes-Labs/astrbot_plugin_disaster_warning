"""Offline tests of br_south_waterq: the providers, the state class, the envelope, the presenter, and the pollers."""

from __future__ import annotations

import asyncio
import json
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "br_south_waterq"
# A sample date of last week. The poller sends only active results (90 days or newer).
RECENT = date.today() - timedelta(days=7)
# The day of the state tests. The RS samples of the recorded data are then active.
TODAY = date(2026, 3, 10)
UNSUITABLE_RS = {5, 56, 1023, 1029}
SC_POINT = (
    "ca409384-a717-4bdc-8a14-b5fd6ed3cea8"  # Unsuitable, newest sample 19/10/2026
)


def _set_rs(locais, cod, **fields):
    for item in locais:
        if item["codLocal"] == cod:
            item.update(fields)


def _rs_point(ext, locais, cod):
    return next(p for p in ext.rs.parse(locais) if p["key"] == str(cod))


def _ready_state(ext, points):
    state = ext.extension.BulletinState()
    assert state.changes(points, TODAY) == []
    return state


# ------------------------------------------------------------------ RS provider


def test_rs_parse(ext, rs_json):
    """Check the point count, the status values, and the sample date (RS)."""
    points = ext.rs.parse(rs_json)
    assert len(points) == 96 and {p["uf"] for p in points} == {"RS"}
    assert {
        int(p["key"]) for p in points if p["status"] == "unsuitable"
    } == UNSUITABLE_RS
    p = _rs_point(ext, rs_json, 1026)
    assert p["status"] == "suitable" and p["sample_date"] == date(2026, 2, 24)
    assert p["bulletin"] == "2026-02-24" and isinstance(p["latitude"], float)


def test_rs_parse_skips_bad_items(ext):
    """Check that a bad item, a bad date, and a bad root do not raise (RS)."""
    ok = {"codLocal": 1, "situacao": 2, "ultimaColeta": "01/02/2026"}
    data = [ok, {"codLocal": 2, "ultimaColeta": None}, {"nome": "x"}, "x", None]
    assert [p["key"] for p in ext.rs.parse(data)] == ["1"]
    assert ext.rs.parse({"a": 1}) == [] and ext.rs.parse(None) == []
    (p,) = ext.rs.parse([{**ok, "situacao": 0}])
    assert p["status"] is None  # Not collected


# ------------------------------------------------------------------ SC provider


def test_sc_parse(ext, sc_json):
    """Check the SC points: status from CONDICAO, no sample date in the map."""
    points = ext.sc.parse(sc_json)
    assert len(points) == 30 and {p["uf"] for p in points} == {"SC"}
    assert sum(p["status"] == "unsuitable" for p in points) == 10
    p = next(p for p in points if p["key"] == SC_POINT)
    assert p["status"] == "unsuitable" and p["sample_date"] is None
    assert p["beach"] == "Praia do Arroio do Silva - Ponto 03"
    assert p["municipio"] == "Balneário Arroio do Silva" and p["ibge"] == "4201950"
    assert p["latitude"] == pytest.approx(-28.988136)


def test_sc_parse_unknown_and_bad_items(ext):
    """Check an unknown CONDICAO and bad items (SC)."""
    data = [{"CODIGO": "a", "CONDICAO": "INDETERMINADO"}, {"x": 1}, None]
    (p,) = ext.sc.parse(data)
    assert p["status"] is None
    assert ext.sc.parse(None) == []


def test_sc_newest_sample(ext, sc_samples_json):
    """Check that newest_sample uses the newest date, not the list order."""
    rows = list(reversed(sc_samples_json))
    assert ext.sc.newest_sample(rows)["DATA"] == "19/10/2026"
    assert ext.sc.newest_sample([{"DATA": "x"}]) is None
    assert ext.sc.newest_sample(None) is None


def test_sc_complete(ext, sc_json, sc_samples_json, fake_session):
    """Check that complete asks for the samples of each point with a POST (SC)."""
    p = next(p for p in ext.sc.parse(sc_json) if p["key"] == SC_POINT)
    session = fake_session({ext.sc.SAMPLES_URL: sc_samples_json})
    (done,) = asyncio.run(ext.sc.complete(session, [p]))
    assert session.calls == [("POST", ext.sc.SAMPLES_URL, {"codigo": SC_POINT})]
    assert done["sample_date"] == date(2026, 10, 19) and done["ecoli"] == 6131.0
    assert done["bulletin"] == "2026-10-19"


# ------------------------------------------------------------------ PR provider


def test_pr_parse(ext, pr_json):
    """Check the PR points: the last campaign is the result."""
    points = ext.pr.parse(pr_json)
    assert len(points) == 25 and {p["uf"] for p in points} == {"PR"}
    assert {p["bulletin"] for p in points} == {"2026-b9"}
    assert sum(p["status"] == "unsuitable" for p in points) == 10
    # Only b1 and b2 have dates. The sample date of b9 is the date of b2.
    assert {p["sample_date"] for p in points} == {date(2025, 12, 26)}


def test_pr_last_campaign(ext):
    """Check the last campaign and the date rule (PR)."""
    lc = ext.pr.last_campaign
    day = int(datetime(2026, 1, 10, 12, tzinfo=timezone.utc).timestamp() * 1000)
    attrs = {"b1": "Propria", "b1_data": day, "b2": "Impropria", "b3": " "}
    assert lc(attrs) == (2, "unsuitable", date(2026, 1, 10))
    assert lc({"b1": "", "b2": None}) is None
    assert lc({"b1": "Propria"}) == (1, "suitable", None)  # No date: past


# ------------------------------------------------------------------ state


def test_baseline_emits_nothing(ext, rs_json):
    """Check that the first two calls with the same data give no event."""
    state = ext.extension.BulletinState()
    points = ext.rs.parse(rs_json)
    assert state.changes(points, TODAY) == []
    assert state.changes(points, TODAY) == []


def test_new_unsuitable_bulletin(ext, rs_json):
    """Check that a new bulletin with an unsuitable result gives one event (RS)."""
    state = _ready_state(ext, ext.rs.parse(rs_json))
    _set_rs(rs_json, 1029, ultimaColeta="02/03/2026")  # Was unsuitable already
    _set_rs(rs_json, 1026, ultimaColeta="02/03/2026", situacao=3)  # Was suitable
    got = state.changes(ext.rs.parse(rs_json), TODAY)
    assert {p["key"] for p in got} == {"1029", "1026"}
    assert state.changes(ext.rs.parse(rs_json), TODAY) == []


def test_back_to_suitable(ext, rs_json):
    """Check that unsuitable to suitable gives an event, and suitable alone gives none."""
    state = _ready_state(ext, ext.rs.parse(rs_json))
    _set_rs(rs_json, 56, situacao=2, ultimaColeta="02/03/2026")
    _set_rs(rs_json, 1026, ultimaColeta="02/03/2026")  # Suitable to suitable
    got = state.changes(ext.rs.parse(rs_json), TODAY)
    assert [(p["key"], p["status"]) for p in got] == [("56", "suitable")]


def test_not_collected_is_ignored(ext, rs_json):
    """Check that "not collected" gives no event and keeps the last known state."""
    state = _ready_state(ext, ext.rs.parse(rs_json))
    _set_rs(rs_json, 56, situacao=0, ultimaColeta="02/03/2026")
    assert state.changes(ext.rs.parse(rs_json), TODAY) == []
    _set_rs(rs_json, 56, situacao=2, ultimaColeta="09/03/2026")
    assert [p["key"] for p in state.changes(ext.rs.parse(rs_json), TODAY)] == ["56"]


def test_sc_status_change_only(ext, sc_json):
    """Check SC: the map has no date, so only a status change is news."""
    state = _ready_state(ext, ext.sc.parse(sc_json))
    assert state.changes(ext.sc.parse(sc_json)) == []
    for raw in sc_json:
        if raw["CODIGO"] == SC_POINT:
            raw["CONDICAO"] = "PRÓPRIO"
    (p,) = state.changes(ext.sc.parse(sc_json))
    assert p["key"] == SC_POINT and p["status"] == "suitable"


def test_active_and_past(ext):
    """Check the hard limit: 90 days old is active, 91 days old and no date are past."""
    act = ext.extension.is_active
    today = date(2026, 5, 24)
    assert act(date(2026, 2, 23), today) is True  # 90 days
    assert act(date(2026, 2, 22), today) is False  # 91 days
    assert act(None, today) is False


# ------------------------------------------------------------------ envelope


def test_envelope_unsuitable(ext, make_api, rs_json):
    """Check the identity, the time, and the metadata of an unsuitable event."""
    api = make_api("en")
    before = datetime.now(timezone.utc)
    env = ext.extension.build_envelope(
        api, _rs_point(ext, rs_json, 56), "FEPAM / PROCERGS"
    )
    ident = env.identity
    assert ident.event_id == "br_south_waterq_rs_56_20260223_unsuitable"
    assert ident.event_type == "weather_alarm" and ident.provider_family == "extension"
    assert ident.source_id == "br_south_waterq_balneabilidade"
    # The event time is the detection time. The sample is old.
    assert before <= ident.published_at <= datetime.now(timezone.utc)
    assert env.event.effective_at == ident.published_at
    md = env.event.metadata
    assert md["severity_level"] == 2 and md["status"] == "unsuitable"
    assert md["hazard"] == "water_quality" and md["uf"] == "RS"
    assert md["point"] == "56" and md["beach"] == "PRAIA DO U"
    assert (
        md["sample_date"] == "2026-02-23" and md["source_label"] == "FEPAM / PROCERGS"
    )
    assert md["laboratorio"] and md["municipio"] and md["ibge"]


def test_envelope_suitable(ext, make_api, rs_json):
    """Check the severity, the status, and the id of a suitable-again event."""
    env = ext.extension.build_envelope(
        make_api("en"), _rs_point(ext, rs_json, 1026), "x"
    )
    assert env.identity.event_id == "br_south_waterq_rs_1026_20260224_suitable"
    assert env.event.metadata["severity_level"] == 0


def test_envelope_without_date_uses_bulletin(ext, make_api):
    """Check the event id of a point with no sample date."""
    p = ext.pr.parse(
        {
            "features": [
                {"attributes": {"cod": "GN4", "ano1": "2026", "b1": "Impropria"}}
            ]
        }
    )[0]
    env = ext.extension.build_envelope(make_api("en"), p, "IAT/PR")
    assert env.identity.event_id == "br_south_waterq_pr_GN4_2026-b1_unsuitable"
    assert env.event.metadata["sample_date"] is None


# ------------------------------------------------------------------ presenter


def _present(ext, make_api, lang, point, label):
    api = make_api(lang)
    env = ext.extension.build_envelope(api, point, label)
    ctx = types.SimpleNamespace(
        title=env.event.title, headline=env.event.headline, metadata=env.event.metadata
    )
    return ext.presenter.make_presenter(api).present(ctx, None)


def test_presenter_en(ext, make_api, rs_json):
    """Check the English text (RS)."""
    out = _present(ext, make_api, "en", _rs_point(ext, rs_json, 56), "FEPAM / PROCERGS")
    assert out.startswith("🟡 Water quality alert: unsuitable for bathing")
    assert "PRAIA DO U - " in out and "/RS" in out
    assert "Status: unsuitable for bathing" in out and "Sample date: 23/02/2026" in out
    assert "Laboratory: " in out and "Criterion: CONAMA 274/2000" in out
    assert "E. coli" not in out
    assert out.endswith("Source: FEPAM / PROCERGS")


def test_presenter_en_sc_with_ecoli(ext, make_api, sc_json):
    """Check the SC text with the E. coli result."""
    p = next(p for p in ext.sc.parse(sc_json) if p["key"] == SC_POINT)
    p.update(sample_date=date(2026, 10, 19), ecoli=6131.0)
    out = _present(ext, make_api, "en", p, "IMA/SC")
    assert "Praia do Arroio do Silva - Ponto 03 - Balneário Arroio do Silva/SC" in out
    assert "E. coli: 6131 MPN/100 mL" in out and out.endswith("Source: IMA/SC")


def test_presenter_en_suitable_again(ext, make_api, rs_json):
    """Check the emoji and the status of a suitable-again event."""
    out = _present(ext, make_api, "en", _rs_point(ext, rs_json, 1026), "x")
    assert out.startswith("✅ Water quality: suitable for bathing again")


def test_presenter_zh_cn(ext, make_api, rs_json):
    """Check the Chinese text."""
    out = _present(
        ext, make_api, "zh-CN", _rs_point(ext, rs_json, 56), "FEPAM / PROCERGS"
    )
    assert out.startswith("🟡 水质预警：不宜游泳")
    assert "状态：不宜游泳" in out and "采样日期：23/02/2026" in out
    assert "检测机构：" in out and out.endswith("来源：FEPAM / PROCERGS")


def test_presenter_pt_br(ext, make_api, rs_json):
    """Check the Brazilian Portuguese text, the decimal comma and the ISO 8601 date."""
    out = _present(
        ext, make_api, "pt-BR", _rs_point(ext, rs_json, 56), "FEPAM / PROCERGS"
    )
    assert out.startswith("🟡 Alerta de balneabilidade: imprópria para banho")
    assert (
        "Situação: imprópria para banho" in out and "Data da coleta: 2026-02-23" in out
    )
    pos = next(ln for ln in out.splitlines() if ln.startswith("Posição: "))
    assert "," in pos and "." not in pos
    assert out.endswith("Fonte: FEPAM / PROCERGS")


def test_presenter_ja_jp(ext, make_api, rs_json):
    """Check the Japanese text and the Japanese date order."""
    out = _present(
        ext, make_api, "ja-JP", _rs_point(ext, rs_json, 56), "FEPAM / PROCERGS"
    )
    assert out.startswith("🟡 水質警報：遊泳不適")
    assert "判定：遊泳不適" in out and "採水日：2026/02/23" in out
    assert out.endswith("出典：FEPAM / PROCERGS")


# ------------------------------------------------------------------ setup / pollers


def test_setup_registrations(ext, make_api):
    """Check the presenter, the source, and one poller for each state."""
    api = make_api("en", {"poll_interval": 600})
    ext.extension.setup(api)
    assert set(api.sources) == {"br_south_waterq_balneabilidade"}
    assert set(api.presenters) == {"br_south_waterq_weather"}
    m = api.models
    entry = api.sources["br_south_waterq_balneabilidade"]
    assert entry.source_type == m.SourceType.WEATHER
    assert entry.provider_family == m.ProviderFamily.EXTENSION
    assert (entry.config_group, entry.config_key) == ("extensions", entry.source_id)
    assert entry.text_presenter_key == "br_south_waterq_weather"
    assert entry.display_name and not entry.display_name.startswith("source.")
    assert [(s, i) for s, _, i, _ in api.all_pollers] == [(entry.source_id, 600)] * 3


def test_default_interval_and_states(ext, make_api):
    """Check the default interval (6 h) and the setting "states"."""
    api = make_api("en", {"states": ["sc"]})
    ext.extension.setup(api)
    assert [i for _, _, i, _ in api.all_pollers] == [21600]
    api = make_api("en", {"states": ["XX"]})
    ext.extension.setup(api)
    assert len(api.all_pollers) == 3  # No known state: use all states
    assert ext.states.selected_states({"states": ["PR", "RS"]}) == ("RS", "PR")


def _poller(api, ext, uf):
    """Return the poll function of one state."""
    ext.extension.setup(api)
    index = list(ext.states.STATES).index(uf)
    return api.all_pollers[index][1]


def test_rs_poller_end_to_end(ext, make_api, rs_json, fake_session):
    """Check the RS poller: baseline first, then one event for a new active bulletin."""
    api = make_api("en")
    fetch = _poller(api, ext, "RS")
    baseline = json.dumps(rs_json)
    _set_rs(rs_json, 1026, situacao=3, ultimaColeta=RECENT.strftime("%d/%m/%Y"))
    session = fake_session({ext.rs.URL: (baseline, json.dumps(rs_json))})
    assert asyncio.run(fetch(session)) == []
    (env,) = asyncio.run(fetch(session))
    assert (
        env.identity.event_id == f"br_south_waterq_rs_1026_{RECENT:%Y%m%d}_unsuitable"
    )
    assert env.event.metadata["source_label"] == "FEPAM / PROCERGS"


def test_rs_poller_ignores_past_results(ext, make_api, rs_json, fake_session):
    """Check that a new bulletin with an old sample gives no event."""
    api = make_api("en")
    fetch = _poller(api, ext, "RS")
    old = date.today() - timedelta(days=ext.extension.MAX_AGE_DAYS + 1)
    baseline = json.dumps(rs_json)
    _set_rs(rs_json, 1026, situacao=3, ultimaColeta=old.strftime("%d/%m/%Y"))
    session = fake_session({ext.rs.URL: (baseline, json.dumps(rs_json))})
    assert asyncio.run(fetch(session)) == []
    assert asyncio.run(fetch(session)) == []


def test_sc_poller_end_to_end(ext, make_api, sc_json, sc_samples_json, fake_session):
    """Check the SC poller: a status change asks for the samples of that point only."""
    api = make_api("en")
    fetch = _poller(api, ext, "SC")
    baseline = json.dumps(sc_json)
    for raw in sc_json:
        if raw["CODIGO"] == SC_POINT:
            raw["CONDICAO"] = "PRÓPRIO"
    samples = [dict(s) for s in sc_samples_json]
    samples[0].update(DATA=RECENT.strftime("%d/%m/%Y"), CONDICAO="PRÓPRIO")
    session = fake_session(
        {ext.sc.MAP_URL: (baseline, json.dumps(sc_json)), ext.sc.SAMPLES_URL: samples}
    )
    assert asyncio.run(fetch(session)) == []
    (env,) = asyncio.run(fetch(session))
    assert [c[0] for c in session.calls] == ["GET", "GET", "POST"]
    md = env.event.metadata
    assert md["status"] == "suitable" and md["uf"] == "SC"
    assert md["sample_date"] == RECENT.isoformat() and md["source_label"] == "IMA/SC"


def test_pr_poller_out_of_season(ext, make_api, pr_json, fake_session):
    """Check the PR poller: the dated samples are from 12/2025, so a change is past."""
    api = make_api("en")
    fetch = _poller(api, ext, "PR")
    baseline = json.dumps(pr_json)
    attrs = pr_json["features"][0]["attributes"]
    attrs["b9"] = "Impropria" if attrs["b9"] == "Propria" else "Propria"
    session = fake_session({ext.pr.URL: (baseline, json.dumps(pr_json))})
    assert asyncio.run(fetch(session)) == []
    assert asyncio.run(fetch(session)) == []  # The change is past (no recent date)
    attrs["b10"], attrs["b10_data"] = (
        "Impropria",
        int(
            datetime.combine(RECENT, datetime.min.time(), timezone.utc).timestamp()
            * 1000
        ),
    )
    session = fake_session({ext.pr.URL: json.dumps(pr_json)})
    (env,) = asyncio.run(fetch(session))
    assert (
        env.event.metadata["uf"] == "PR"
        and env.event.metadata["status"] == "unsuitable"
    )


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
    assert meta["id"] == "br_south_waterq"
    assert meta["maintainer"]["github"] == "h0ffmann"
    assert meta["coverage"]["states"] == ["RS", "SC", "PR"]
    assert [s["state"] for s in meta["data_sources"]] == ["RS", "SC", "PR"]
    assert set(meta["languages"]) == {
        p.stem for p in (EXT_DIR / "locale").glob("*.json")
    }
    icon = meta["icon"]
    assert {"title", "source", "author", "license", "license_url", "changes"} <= set(
        icon
    )
    assert (EXT_DIR / icon["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (EXT_DIR / "extension.png").read_bytes() == (
        EXT_DIR.parent / "br_south_disaster" / "extension.png"
    ).read_bytes()


# ------------------------------------------------------------------ review fixes


def test_failed_complete_keeps_change(ext, make_api, rs_json):
    """Check that a failed sample request keeps the change for the next poll."""
    calls = []

    async def fetch(session):
        return ext.rs.parse(rs_json)

    async def complete(session, news):
        calls.append(len(news))
        if len(calls) == 1:
            raise asyncio.TimeoutError
        return news

    provider = types.SimpleNamespace(
        fetch=fetch, complete=complete, SOURCE_LABEL="FEPAM/RS"
    )
    poll = ext.extension.make_fetch(make_api("en"), provider)
    assert asyncio.run(poll(None)) == []  # Baseline
    _set_rs(rs_json, 1026, situacao=3, ultimaColeta=RECENT.strftime("%d/%m/%Y"))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(poll(None))
    (env,) = asyncio.run(poll(None))
    assert env.event.metadata["point"] == "1026"


def test_past_unsuitable_gives_no_recovery(ext, rs_json):
    """Check that a past unsuitable result gives no "suitable again" event."""
    later = TODAY + timedelta(days=200)  # All recorded RS samples are past
    state = ext.extension.BulletinState()
    assert state.changes(ext.rs.parse(rs_json), later) == []
    _set_rs(rs_json, 56, situacao=2, ultimaColeta=later.strftime("%d/%m/%Y"))
    assert state.changes(ext.rs.parse(rs_json), later) == []


def test_bad_settings_use_defaults(ext, make_api):
    """Check that a bad interval or states value does not stop the setup."""
    api = make_api("en", {"poll_interval": "6 h", "states": 123})
    ext.extension.setup(api)
    assert [i for _, _, i, _ in api.all_pollers] == [21600] * 3


def test_presenter_large_ecoli_value(ext, make_api, sc_json):
    """Check that a 7-digit E. coli result does not use scientific notation."""
    p = next(p for p in ext.sc.parse(sc_json) if p["key"] == SC_POINT)
    p.update(sample_date=date(2026, 10, 19), ecoli=2419600.0)
    assert "E. coli: 2419600 MPN/100 mL" in _present(ext, make_api, "en", p, "IMA/SC")
