"""Rio Grande do Sul (Brazil): CEMADEN alerts, CEMADEN rain gauges, and ENSO data.

To make another state, copy this package and change UF and the ids.
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

BRT = timezone(timedelta(hours=-3))  # Brasília time, no daylight saving time
ALERT_LEVELS = {"Moderado": 2, "Alto": 3, "Muito Alto": 4}
DEFAULT_THRESHOLDS = {  # level -> (acc1hr mm, acc24hr mm)
    2: (20.0, 50.0),
    3: (30.0, 100.0),
    4: (60.0, 150.0),
}
LEVEL_KEYS = {2: "yellow", 3: "orange", 4: "red"}
ALERT_TIME_FMT = "%Y-%m-%d %H:%M:%S"
GAUGE_TIME_FMT = "%d/%m/%y %H:%M"


def _parse_time(text, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=BRT)
    except (TypeError, ValueError):
        return None


def parse_alerts(data: dict) -> list[dict]:
    """Keep the alerts of this UF that have a known level. Add hazard and times."""
    out = []
    for raw in (data or {}).get("alertas") or []:
        level = ALERT_LEVELS.get(raw.get("nivel"))
        if raw.get("uf") != UF or level is None:
            continue
        hazard = (
            "landslide"
            if (raw.get("evento") or "").startswith("Movimentos de Massa")
            else "flood"
        )
        out.append(
            {
                "raw": raw,
                "cod_alerta": raw.get("cod_alerta"),
                "level": level,
                "hazard": hazard,
                "issued_at": _parse_time(
                    str(raw.get("datahoracriacao"))[:19], ALERT_TIME_FMT
                ),
                # An alert stays active for days. The host time rule must use the last update.
                "updated_at": _parse_time(
                    str(raw.get("ult_atualizacao"))[:19], ALERT_TIME_FMT
                ),
                "updated": raw.get("ult_atualizacao") or "",
            }
        )
    return out


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None  # "-" -> None


def parse_rain(gauges: list, thresholds: dict = DEFAULT_THRESHOLDS) -> list[dict]:
    """Keep the worst gauge of each municipality that meets a threshold."""
    worst: dict = {}
    for g in gauges or []:
        if g.get("uf") != UF:
            continue
        acc1, acc24 = _num(g.get("acc1hr")), _num(g.get("acc24hr"))
        level = max(
            (
                lvl
                for lvl, (t1, t24) in thresholds.items()
                if (acc1 is not None and acc1 >= t1)
                or (acc24 is not None and acc24 >= t24)
            ),
            default=0,
        )
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
                "observed_at": _parse_time(
                    g.get("datahoraUltimovalor"), GAUGE_TIME_FMT
                ),
            }
    return list(worst.values())


def load_thresholds(config: dict) -> dict:
    """Replace the default thresholds with the values in config["rain_thresholds"]."""
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


class AlertState:
    """Store the last update of each alert. Return only new or updated alerts."""

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
    """Store the last level of each municipality for the day. Return only higher levels."""

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


def _envelope(api, source_id, event_id, metadata, when, raw, fingerprint=None):
    m = api.models
    hazard = metadata["hazard"]
    headline = api.t(
        "headline.rain" if hazard == "rain" else "headline.alert",
        level=api.t(f"level.{metadata['severity_level']}"),
        municipio=metadata["municipio"],
        uf=metadata["uf"],
    )
    identity = m.EventIdentity(
        event_id=event_id,
        source_id=source_id,
        event_type="weather_alarm",
        provider_family="extension",
        source_enum=source_id,
        published_at=when,
        attributes={"fingerprint": fingerprint or event_id},
    )
    event = m.WeatherEvent(
        title=api.t(f"title.{hazard}"),
        headline=headline,
        effective_at=when,
        metadata=metadata,
    )
    payload = m.SourcePayload(source_id=source_id, provider_family="extension", raw=raw)
    return m.EventEnvelope(identity=identity, event=event, payload=payload)


def build_alert_envelope(api, alert: dict, enso: dict | None):
    raw = alert["raw"]
    issued = alert["issued_at"]
    metadata = {
        "severity_level": alert["level"],
        "uf": raw.get("uf"),
        "codibge": raw.get("codibge"),
        "municipio": raw.get("municipio"),
        "latitude": raw.get("latitude"),
        "longitude": raw.get("longitude"),
        "hazard": alert["hazard"],
        "issue_time": issued.isoformat() if issued else None,
        "enso": enso,
    }
    event_id = f"{ID}_cemaden_{alert['cod_alerta']}"
    return _envelope(
        api,
        ALERTS_SOURCE,
        event_id,
        metadata,
        alert["updated_at"] or issued,
        raw,
        f"{event_id}|{alert['updated']}",
    )


def build_rain_envelope(api, item: dict, day: str, enso: dict | None):
    observed = item["observed_at"]
    metadata = {
        "severity_level": item["level"],
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
    event_id = f"{ID}_rain_{item['codibge']}_{day}_{item['level']}"
    raw = {k: v for k, v in item.items() if k not in ("rank", "observed_at")}
    return _envelope(api, RAIN_SOURCE, event_id, metadata, observed, raw)


async def _get_json(session, url: str):
    async with session.get(url) as resp:
        resp.raise_for_status()
        return json.loads(await resp.text())  # The server sends JSON as text/html.


def _entry(api, source_id: str, kind: str, priority: int):
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
        display_name=api.t(f"source.{kind}"),
        description=api.t(f"source.{kind}.desc"),
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
    api.register_source(_entry(api, ALERTS_SOURCE, "alerts", 2))
    api.register_source(_entry(api, RAIN_SOURCE, "rain", 1))
    api.register_poller(
        ALERTS_SOURCE,
        fetch_alerts,
        interval_seconds=int(cfg.get("alerts_interval", 300)),
    )
    api.register_poller(
        RAIN_SOURCE, fetch_rain, interval_seconds=int(cfg.get("rain_interval", 300))
    )
