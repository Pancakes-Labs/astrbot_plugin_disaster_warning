from __future__ import annotations

import asyncio
import copy
import json
import types

from conftest import EXT_DIR

# ------------------------------------------------------------------ alerts


def test_parse_alerts_filters_rs_and_maps_levels(ext, alerts_json):
    ex = ext.extension
    alerts = ex.parse_alerts(alerts_json)
    assert alerts and all(a["raw"]["uf"] == "RS" for a in alerts)
    by_cod = {a["cod_alerta"]: a for a in alerts}
    assert by_cod[3141]["level"] == 3 and by_cod[3141]["hazard"] == "flood"
    assert by_cod[3159]["level"] == 2
    assert len(ex.parse_alerts(alerts_json, uf="SC")) != len(alerts)


def test_levels_and_hazard_variants(ext):
    ex = ext.extension
    base = {"uf": "RS", "datahoracriacao": "2026-10-25 11:20:44.159"}
    data = {
        "alertas": [
            {
                **base,
                "cod_alerta": 1,
                "nivel": "Muito Alto",
                "evento": "Movimentos de Massa - Muito Alto",
            },
            {**base, "cod_alerta": 2, "nivel": "Desconhecido", "evento": "x"},
        ]
    }
    (a,) = ex.parse_alerts(data)
    assert (a["level"], a["hazard"]) == (4, "landslide")
    assert a["issued_at"].utcoffset().total_seconds() == -3 * 3600


def test_alert_dedup_and_update(ext, alerts_json):
    ex = ext.extension
    state = ex.AlertState()
    first = state.fresh(ex.parse_alerts(alerts_json))
    assert first
    assert state.fresh(ex.parse_alerts(alerts_json)) == []
    changed = copy.deepcopy(alerts_json)
    for a in changed["alertas"]:
        if a["cod_alerta"] == 3141:
            a["ult_atualizacao"] = "2026-10-28 09:00:00.000"
    again = state.fresh(ex.parse_alerts(changed))
    assert [a["cod_alerta"] for a in again] == [3141]


def test_alert_envelope(ext, make_api, alerts_json):
    ex = ext.extension
    api = make_api("pt-BR")
    enso = ext.enso.build_enso("JAS 2026 29.12 2.16\n")
    alert = next(a for a in ex.parse_alerts(alerts_json) if a["cod_alerta"] == 3141)
    env = ex.build_alert_envelope(api, alert, enso)
    assert env.identity.event_id == "br_rs_cemaden_3141"
    assert env.identity.event_type == "weather_alarm"
    assert env.identity.provider_family == "extension"
    assert "2026-10-25 11:20:44.159" in env.identity.attributes["fingerprint"]
    md = env.event.metadata
    assert md["severity_level"] == 3 and md["hazard"] == "flood" and md["uf"] == "RS"
    assert md["municipio"] == "SÃO BORJA" and md["codibge"] == 4318002
    assert md["enso"]["phase"] == "el_nino"
    assert md["issue_time"].endswith("-03:00")
    assert "SÃO BORJA" in env.event.headline


# ------------------------------------------------------------------ rain


def _gauges(rain_json, **overrides):
    g = copy.deepcopy(rain_json)
    g[0].update(overrides)
    return g


def test_rain_fixture_has_no_warning_and_dash_is_handled(ext, rain_json):
    # Real fixture: only "-" and 0.2 mm -> nothing, and no crash on "-".
    assert ext.extension.parse_rain(rain_json) == []


def test_rain_levels(ext, rain_json):
    ex = ext.extension
    cases = [
        ({"acc1hr": 20}, 2),
        ({"acc24hr": 100}, 3),
        ({"acc1hr": 10, "acc24hr": 150}, 4),
        ({"acc1hr": 5, "acc24hr": 49.9}, 0),
        ({"acc1hr": "-", "acc24hr": 60}, 2),
    ]
    for over, level in cases:
        res = ex.parse_rain(_gauges(rain_json, **over))
        assert (res[0]["level"] if res else 0) == level, over


def test_rain_worst_gauge_per_municipality_and_custom_thresholds(ext, rain_json):
    ex = ext.extension
    g = copy.deepcopy(rain_json)
    g[0].update(acc1hr=25, acc24hr=10)  # PAROBÉ yellow
    g[4].update(acc1hr=65, acc24hr=10)  # PAROBÉ (second gauge) red
    res = ex.parse_rain(g)
    assert len(res) == 1 and res[0]["level"] == 4 and res[0]["codibge"] == 4314050
    th = ex.load_thresholds({"rain_thresholds": {"yellow": {"acc1hr": 10}}})
    assert th[2] == (10.0, 50.0) and th[3] == ex.DEFAULT_THRESHOLDS[3]


def test_rain_escalation_only(ext, rain_json):
    ex = ext.extension
    state = ex.RainState()
    day = "20261028"
    poll = lambda **o: state.escalations(ex.parse_rain(_gauges(rain_json, **o)), day)  # noqa: E731
    assert [i["level"] for i in poll(acc1hr=25)] == [2]
    assert poll(acc1hr=25) == []  # same level
    assert [i["level"] for i in poll(acc1hr=35)] == [3]  # escalates
    assert poll(acc1hr=25) == []  # de-escalation is silent
    nxt = state.escalations(ex.parse_rain(_gauges(rain_json, acc1hr=25)), "20261029")
    assert [i["level"] for i in nxt] == [2]  # new Brasília day resets


def test_rain_envelope(ext, make_api, rain_json):
    ex = ext.extension
    api = make_api("pt-BR")
    item = ex.parse_rain(_gauges(rain_json, acc1hr=31, acc24hr=40))[0]
    env = ex.build_rain_envelope(api, item, "20261028", None)
    assert env.identity.event_id == "br_rs_rain_4314050_20261028_3"
    assert env.event.metadata["hazard"] == "rain"
    assert env.event.metadata["severity_level"] == 3
    assert env.event.metadata["acc1hr"] == 31


# ------------------------------------------------------------------ ENSO


def test_oni_parse_and_classify(ext, oni_text):
    e = ext.enso
    row = e.parse_oni(oni_text)
    assert (row["season"], row["year"], row["anom"]) == ("JAS", 2026, 2.16)
    got = e.build_enso(oni_text)
    assert (got["phase"], got["strength"]) == ("el_nino", "very_strong")
    table = {
        0.49: ("neutral", ""),
        0.5: ("el_nino", "weak"),
        1.0: ("el_nino", "moderate"),
        1.5: ("el_nino", "strong"),
        -0.5: ("la_nina", "weak"),
        -1.7: ("la_nina", "strong"),
        -2.0: ("la_nina", "very_strong"),
    }
    for anom, expect in table.items():
        assert e.classify(anom) == expect
    assert e.parse_oni("garbage") is None


def test_enso_cache(ext, oni_text):
    class Resp:
        def __init__(self, ok):
            self.ok = ok

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("boom")

        async def text(self):
            return oni_text

    class Session:
        calls = 0
        ok = True

        def get(self, url):
            Session.calls += 1
            return Resp(Session.ok)

    cache, s = ext.enso.EnsoCache(), Session()
    assert asyncio.run(cache.get(s))["anom"] == 2.16
    assert asyncio.run(cache.get(s))["anom"] == 2.16
    assert Session.calls == 1  # cached
    cache._fetched_at -= ext.enso.CACHE_SECONDS + 1
    Session.ok = False
    assert asyncio.run(cache.get(s))["anom"] == 2.16  # failure keeps last value
    assert Session.calls == 2
    assert asyncio.run(ext.enso.EnsoCache().get(s)) is None


# ------------------------------------------------------------------ presenter


def _present(ext, make_api, lang, alerts_json, enso_text):
    ex = ext.extension
    api = make_api(lang)
    alert = next(a for a in ex.parse_alerts(alerts_json) if a["cod_alerta"] == 3141)
    env = ex.build_alert_envelope(api, alert, ext.enso.build_enso(enso_text))
    cls = ext.presenter.make_presenter(api)
    ctx = types.SimpleNamespace(
        title=env.event.title,
        headline=env.event.headline,
        metadata=env.event.metadata,
        source_id=env.source_id,
    )
    return cls.present(ctx, None)


def test_presenter_pt_br(ext, make_api, alerts_json, oni_text):
    out = _present(ext, make_api, "pt-BR", alerts_json, oni_text)
    assert out.startswith("🟠 Alerta CEMADEN: risco hidrológico")
    assert "SÃO BORJA/RS" in out
    assert "25/10/2026 11:20 (Brasília)" in out
    assert "El Niño muito forte" in out and "+2.16" in out and "Não é causa" in out
    assert out.endswith("Fonte: CEMADEN / NOAA CPC")


def test_presenter_zh_cn_and_neutral(ext, make_api, alerts_json, oni_text):
    out = _present(ext, make_api, "zh-CN", alerts_json, oni_text)
    assert (
        "厄尔尼诺" in out
        and "并非本次预警的成因" in out
        and "来源：CEMADEN / NOAA CPC" in out
    )
    neutral = _present(ext, make_api, "pt-BR", alerts_json, "JAS 2026 29.12 0.10\n")
    assert "ONI" not in neutral and "NOAA" not in neutral
    assert neutral.endswith("Fonte: CEMADEN")


def test_presenter_rain(ext, make_api, rain_json):
    ex = ext.extension
    api = make_api("pt-BR")
    item = ex.parse_rain(_gauges(rain_json, acc1hr=31, acc24hr="-"))[0]
    env = ex.build_rain_envelope(api, item, "20261028", None)
    ctx = types.SimpleNamespace(
        title=env.event.title, headline=env.event.headline, metadata=env.event.metadata
    )
    out = ext.presenter.make_presenter(api).present(ctx, None)
    assert "Chuva: 31 mm/1h | - mm/24h" in out and "Chuva intensa" in out


# ------------------------------------------------------------------ setup / locale


def test_setup_registrations(ext, make_api):
    api = make_api("pt-BR", {"alerts_interval": 600})
    ext.extension.setup(api)
    assert set(api.sources) == {"br_rs_cemaden_alerts", "br_rs_cemaden_rain"}
    assert set(api.presenters) == {"br_rs_weather"}
    m = api.models
    for sid, entry in api.sources.items():
        assert entry.source_type == m.SourceType.WEATHER
        assert entry.provider_family == m.ProviderFamily.EXTENSION
        assert (entry.config_group, entry.config_key) == ("extensions", sid)
        assert entry.parser_name == ""
        assert entry.presentation_type == "weather"
        assert entry.text_presenter_key == "br_rs_weather"
        assert entry.default_timezone == "America/Sao_Paulo"
        assert entry.display_name and entry.display_name != "source.alerts"
        assert sid in api.pollers
    assert api.pollers["br_rs_cemaden_alerts"][1] == 600
    assert api.pollers["br_rs_cemaden_rain"][1] == 300
    assert hasattr(api.presenters["br_rs_weather"], "present")


def test_pollers_end_to_end_with_fake_session(ext, make_api, alerts_json, rain_json):
    api = make_api("pt-BR")
    ext.extension.setup(api)
    rain = _gauges(rain_json, acc1hr=61)
    bodies = {
        ext.extension.ALERTS_URL: json.dumps(alerts_json),
        ext.extension.RAIN_URL: json.dumps(rain),
        ext.enso.ONI_URL: "JAS 2026 29.12 2.16\n",
    }

    class Resp:
        def __init__(self, body):
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def text(self):
            return self.body

    class Session:
        def get(self, url):
            return Resp(bodies[url])

    fetch_alerts = api.pollers["br_rs_cemaden_alerts"][0]
    fetch_rain = api.pollers["br_rs_cemaden_rain"][0]
    first = asyncio.run(fetch_alerts(Session()))
    assert len(first) == 3 and first[0].event.metadata["enso"]["phase"] == "el_nino"
    assert asyncio.run(fetch_alerts(Session())) == []
    r1 = asyncio.run(fetch_rain(Session()))
    assert len(r1) == 1 and r1[0].event.metadata["severity_level"] == 4
    assert asyncio.run(fetch_rain(Session())) == []


def test_locale_zh_cn_has_every_key(ext):
    loc = {
        p.stem: json.loads(p.read_text("utf-8"))
        for p in (EXT_DIR / "locale").glob("*.json")
    }
    assert {"zh-CN", "en", "pt-BR"} <= set(loc)
    union = set().union(*loc.values())
    assert union <= set(loc["zh-CN"])
    for name, d in loc.items():
        assert set(d) == union, name
