"""Brazil / Rio Grande do Sul: CEMADEN alerts, CEMADEN rain gauges, ENSO context.

Per-area: a future br_sc is a copy of this package with UF (and ids) changed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .enso import EnsoCache
from .presenter import make_presenter

UF = "RS"
ID = "br_rs"
ALERTS_SOURCE = f"{ID}_cemaden_alerts"
RAIN_SOURCE = f"{ID}_cemaden_rain"
PRESENTER_KEY = f"{ID}_weather"

ALERTS_URL = "https://painelalertas.cemaden.gov.br/wsAlertas2"
RAIN_URL = f"https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf={UF}"

BRT = timezone(timedelta(hours=-3))  # Brasília time; Brazil has no DST since 2019
ALERT_LEVELS = {"Moderado": 2, "Alto": 3, "Muito Alto": 4}
DEFAULT_THRESHOLDS = {  # level -> (acc1hr mm, acc24hr mm), INMET-style
    2: (20.0, 50.0),
    3: (30.0, 100.0),
    4: (60.0, 150.0),
}
LEVEL_KEYS = {2: "yellow", 3: "orange", 4: "red"}


# ---------------------------------------------------------------- parsing (pure)


def _parse_alert_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT)
    except (TypeError, ValueError):
        return None


def parse_alerts(data: dict, uf: str = UF) -> list[dict]:
    """Keep only `uf` alerts with a known level; add severity/hazard/issue time."""
    out = []
    for raw in (data or {}).get("alertas") or []:
        level = ALERT_LEVELS.get(raw.get("nivel"))
        if raw.get("uf") != uf or level is None:
            continue
        evento = raw.get("evento") or ""
        hazard = "landslide" if evento.startswith("Movimentos de Massa") else "flood"
        out.append(
            {
                "raw": raw,
                "cod_alerta": raw.get("cod_alerta"),
                "level": level,
                "hazard": hazard,
                "issued_at": _parse_alert_time(raw.get("datahoracriacao")),
                "updated": raw.get("ult_atualizacao") or "",
            }
        )
    return out


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None  # "-" -> None


def parse_gauge_time(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%d/%m/%y %H:%M").replace(tzinfo=BRT)
    except (TypeError, ValueError):
        return None


def gauge_level(acc1: float | None, acc24: float | None, thresholds) -> int:
    level = 0
    for lvl, (t1, t24) in sorted(thresholds.items()):
        if (acc1 is not None and acc1 >= t1) or (acc24 is not None and acc24 >= t24):
            level = lvl
    return level


def parse_rain(gauges: list, thresholds=None, uf: str = UF) -> list[dict]:
    """Worst gauge per municipality (codibge) with level >= 1 threshold met."""
    thresholds = thresholds or DEFAULT_THRESHOLDS
    worst: dict = {}
    for g in gauges or []:
        if g.get("uf") != uf:
            continue
        acc1, acc24 = _num(g.get("acc1hr")), _num(g.get("acc24hr"))
        level = gauge_level(acc1, acc24, thresholds)
        if not level:
            continue
        rank = (level, acc24 or 0.0, acc1 or 0.0)
        code = g.get("codibge")
        if code not in worst or rank > worst[code]["rank"]:
            worst[code] = {
                "rank": rank,
                "codibge": code,
                "municipio": g.get("cidade") or "",
                "station": g.get("nomeestacao") or "",
                "level": level,
                "acc1hr": acc1,
                "acc24hr": acc24,
                "observed_at": parse_gauge_time(g.get("datahoraUltimovalor")),
            }
    return list(worst.values())


def load_thresholds(config: dict) -> dict:
    """Merge config['rain_thresholds'] = {yellow|orange|red: {acc1hr, acc24hr}}."""
    merged = dict(DEFAULT_THRESHOLDS)
    custom = (config or {}).get("rain_thresholds") or {}
    for lvl, key in LEVEL_KEYS.items():
        item = custom.get(key)
        if isinstance(item, dict):
            t1, t24 = merged[lvl]
            merged[lvl] = (
                float(item.get("acc1hr", t1)),
                float(item.get("acc24hr", t24)),
            )
    return merged


# ---------------------------------------------------------------- dedup state


class AlertState:
    """cod_alerta -> last seen ult_atualizacao; emit only new/updated alerts."""

    def __init__(self) -> None:
        self._seen: dict = {}

    def fresh(self, alerts: list[dict]) -> list[dict]:
        out = []
        for a in alerts:
            if self._seen.get(a["cod_alerta"]) != a["updated"]:
                self._seen[a["cod_alerta"]] = a["updated"]
                out.append(a)
        return out


class RainState:
    """codibge -> (brasilia day, last emitted level); emit only escalations."""

    def __init__(self) -> None:
        self._last: dict = {}

    def escalations(self, items: list[dict], day: str) -> list[dict]:
        out = []
        for it in items:
            prev_day, prev_level = self._last.get(it["codibge"], (None, 0))
            if prev_day != day:
                prev_level = 0
            if it["level"] > prev_level:
                self._last[it["codibge"]] = (day, it["level"])
                out.append(it)
        return out


# ---------------------------------------------------------------- envelopes


def _envelope(api, source_id, event_id, title, headline, issued_at, metadata, raw, fp):
    m = api.models
    identity = m.EventIdentity(
        event_id=event_id,
        source_id=source_id,
        event_type="weather_alarm",
        provider_family="extension",
        source_enum=source_id,
        published_at=issued_at,
        attributes={"fingerprint": fp},
    )
    event = m.WeatherEvent(
        title=title, headline=headline, effective_at=issued_at, metadata=metadata
    )
    payload = m.SourcePayload(source_id=source_id, provider_family="extension", raw=raw)
    return m.EventEnvelope(identity=identity, event=event, payload=payload)


def build_alert_envelope(api, alert: dict, enso: dict | None):
    raw = alert["raw"]
    level, hazard = alert["level"], alert["hazard"]
    issued = alert["issued_at"]
    metadata = {
        "severity_level": level,
        "uf": raw.get("uf"),
        "codibge": raw.get("codibge"),
        "municipio": raw.get("municipio"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "hazard": hazard,
        "issue_time": issued.isoformat() if issued else None,
        "enso": enso,
    }
    title = api.t(f"title.{hazard}")
    headline = api.t(
        "headline.alert",
        level=api.t(f"level.{level}"),
        municipio=raw.get("municipio"),
        uf=raw.get("uf"),
    )
    event_id = f"{ID}_cemaden_{alert['cod_alerta']}"
    fp = f"{event_id}|{alert['updated']}"
    return _envelope(
        api, ALERTS_SOURCE, event_id, title, headline, issued, metadata, raw, fp
    )


def build_rain_envelope(api, item: dict, day: str, enso: dict | None):
    level = item["level"]
    observed = item["observed_at"]
    metadata = {
        "severity_level": level,
        "uf": UF,
        "codibge": item["codibge"],
        "municipio": item["municipio"],
        "hazard": "rain",
        "station": item["station"],
        "acc1hr": item["acc1hr"],
        "acc24hr": item["acc24hr"],
        "issue_time": observed.isoformat() if observed else None,
        "enso": enso,
    }
    title = api.t("title.rain")
    headline = api.t(
        "headline.rain",
        level=api.t(f"level.{level}"),
        municipio=item["municipio"],
        uf=UF,
    )
    event_id = f"{ID}_rain_{item['codibge']}_{day}_{level}"
    raw = {k: v for k, v in item.items() if k not in ("rank", "observed_at")}
    return _envelope(
        api, RAIN_SOURCE, event_id, title, headline, observed, metadata, raw, event_id
    )


# ---------------------------------------------------------------- fetch + setup


async def _get_json(session, url: str):
    async with session.get(url) as resp:
        resp.raise_for_status()
        return json.loads(await resp.text())  # server sends text/html


def _entry(api, source_id: str, name: str, desc: str, priority: int):
    m = api.models
    return m.SourceEntry(
        source_id=source_id,
        source_enum=source_id,
        source_type=m.SourceType.WEATHER,
        provider_family=m.ProviderFamily.EXTENSION,
        config_group="extensions",
        config_key=source_id,
        parser_name="",
        presentation_type="weather",
        text_presenter_key=PRESENTER_KEY,
        report_policy="none",
        intensity_mode="none",
        priority=priority,
        display_name=name,
        description=desc,
        default_timezone="America/Sao_Paulo",
        fingerprint_prefix=source_id,
        dispatch_family=source_id,
        provider_aliases=(source_id,),
        routing_tags=("extension", "brazil", UF.lower(), "http"),
    )


def setup(api) -> None:
    cfg = api.config or {}
    enso = EnsoCache()
    alert_state, rain_state = AlertState(), RainState()
    thresholds = load_thresholds(cfg)

    async def fetch_alerts(session):
        alerts = alert_state.fresh(parse_alerts(await _get_json(session, ALERTS_URL)))
        if not alerts:
            return []
        ctx = await enso.get(session)
        return [build_alert_envelope(api, a, ctx) for a in alerts]

    async def fetch_rain(session):
        items = parse_rain(await _get_json(session, RAIN_URL), thresholds)
        day = datetime.now(BRT).strftime("%Y%m%d")
        items = rain_state.escalations(items, day)
        if not items:
            return []
        ctx = await enso.get(session)
        return [build_rain_envelope(api, i, day, ctx) for i in items]

    api.register_presenter(PRESENTER_KEY, make_presenter(api))
    api.register_source(
        _entry(
            api, ALERTS_SOURCE, api.t("source.alerts"), api.t("source.alerts.desc"), 2
        )
    )
    api.register_source(
        _entry(api, RAIN_SOURCE, api.t("source.rain"), api.t("source.rain.desc"), 1)
    )
    api.register_poller(
        ALERTS_SOURCE,
        fetch_alerts,
        interval_seconds=int(cfg.get("alerts_interval", 300)),
    )
    api.register_poller(
        RAIN_SOURCE, fetch_rain, interval_seconds=int(cfg.get("rain_interval", 300))
    )
