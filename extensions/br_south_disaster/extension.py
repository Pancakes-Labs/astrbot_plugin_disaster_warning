"""South of Brazil (RS, SC, PR): CEMADEN alerts, CEMADEN rain gauges, and ENSO data.

The states are in states.py. To add a state, add one line there.
The setting "states" selects some of the states. The default is all states.
An alert is active if CEMADEN gives status 1 and the last update is MAX_AGE_DAYS
old or newer. The extension does not send past alerts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .enso import EnsoCache
from .presenter import make_presenter
from .states import STATES, selected_states

ID = "br_south_disaster"
ALERTS_SOURCE = f"{ID}_cemaden_alerts"
RAIN_SOURCE = f"{ID}_cemaden_rain"
PRESENTER_KEY = f"{ID}_weather"

ALERTS_URL = "https://painelalertas.cemaden.gov.br/wsAlertas2"
RAIN_URL = "https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf={uf}"

BRT = timezone(timedelta(hours=-3))  # Brasília time, no daylight saving time
ALERT_LEVELS = {"Moderado": 2, "Alto": 3, "Muito Alto": 4}
DEFAULT_THRESHOLDS = {  # level -> (acc1hr mm, acc24hr mm)
    2: (20.0, 50.0),
    3: (30.0, 100.0),
    4: (60.0, 150.0),
}
LEVEL_KEYS = {2: "yellow", 3: "orange", 4: "red"}
ALERT_TIME_FMT = "%Y-%m-%d %H:%M:%S"
FEED_TIME_FMT = "%d-%m-%Y %H:%M:%S UTC"  # The atualizado field, in UTC
ACTIVE_STATUS = 1
MAX_AGE_DAYS = 90  # Hard limit. An alert with an older last update is past, not active.
GAUGE_TIME_FMT = "%d/%m/%y %H:%M"


def _parse_time(text, fmt: str) -> datetime | None:
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=BRT)
    except (TypeError, ValueError):
        return None


def _feed_time(data: dict) -> datetime:
    """Return the time of the list (atualizado), or the time now if it is not valid."""
    try:
        return datetime.strptime(data["atualizado"], FEED_TIME_FMT).replace(
            tzinfo=timezone.utc
        )
    except (KeyError, TypeError, ValueError):
        return datetime.now(timezone.utc)


def is_active(raw: dict, updated_at: datetime | None, feed_time: datetime) -> bool:
    """Return True for status 1 and a last update MAX_AGE_DAYS old or newer."""
    return (
        raw.get("status") == ACTIVE_STATUS
        and updated_at is not None
        and feed_time - updated_at <= timedelta(days=MAX_AGE_DAYS)
    )


def parse_alerts(data: dict, ufs=tuple(STATES)) -> list[dict]:
    """Keep the alerts of the states in ufs that have a known level.

    Add the hazard, the times and the active flag.
    """
    feed_time = _feed_time(data or {})
    out = []
    for raw in (data or {}).get("alertas") or []:
        level = ALERT_LEVELS.get(raw.get("nivel"))
        if raw.get("uf") not in ufs or level is None:
            continue
        hazard = (
            "landslide"
            if (raw.get("evento") or "").startswith("Movimentos de Massa")
            else "flood"
        )
        updated_at = _parse_time(str(raw.get("ult_atualizacao"))[:19], ALERT_TIME_FMT)
        out.append(
            {
                "raw": raw,
                "cod_alerta": raw.get("cod_alerta"),
                "level": level,
                "hazard": hazard,
                "issued_at": _parse_time(
                    str(raw.get("datahoracriacao"))[:19], ALERT_TIME_FMT
                ),
                # An alert stays active for days.
                # The host time rule must use the last update.
                "updated_at": updated_at,
                "updated": raw.get("ult_atualizacao") or "",
                "active": is_active(raw, updated_at, feed_time),
            }
        )
    return out


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None  # "-" -> None


def _meets(value: float | None, limit: float) -> bool:
    return value is not None and value >= limit


def rain_level(acc1: float | None, acc24: float | None, thresholds: dict) -> int:
    """Return the highest level that the 1 h or the 24 h total meets, or 0."""
    return max(
        (
            lvl
            for lvl, (t1, t24) in thresholds.items()
            if _meets(acc1, t1) or _meets(acc24, t24)
        ),
        default=0,
    )


def _gauge_item(g: dict, thresholds: dict) -> dict | None:
    """Return the rain item of one gauge, or None if the gauge meets no threshold."""
    acc1, acc24 = _num(g.get("acc1hr")), _num(g.get("acc24hr"))
    level = rain_level(acc1, acc24, thresholds)
    if not level:
        return None
    return {
        "rank": (level, acc24 or 0.0, acc1 or 0.0),
        "uf": g.get("uf"),
        "codibge": g.get("codibge"),
        "municipio": g.get("cidade") or "",
        "station": g.get("nomeestacao") or "",
        "level": level,
        "acc1hr": acc1,
        "acc24hr": acc24,
        "observed_at": _parse_time(g.get("datahoraUltimovalor"), GAUGE_TIME_FMT),
    }


def parse_rain(
    gauges: list, thresholds: dict = DEFAULT_THRESHOLDS, uf: str = "RS"
) -> list[dict]:
    """Keep the worst active gauge of each municipality in the state uf.

    Keep only the gauges that meet a threshold. A gauge is past if its last reading
    is more than MAX_AGE_DAYS older than the newest reading of the list.
    """
    items = [_gauge_item(g, thresholds) for g in gauges or [] if g.get("uf") == uf]
    newest = _newest_reading(gauges)
    worst: dict = {}
    for item in items:
        if item is None or not _recent(item["observed_at"], newest):
            continue
        code = item["codibge"]
        if code not in worst or item["rank"] > worst[code]["rank"]:
            worst[code] = item
    return list(worst.values())


def _newest_reading(gauges) -> datetime | None:
    times = (
        _parse_time(g.get("datahoraUltimovalor"), GAUGE_TIME_FMT) for g in gauges or []
    )
    return max((t for t in times if t), default=None)


def _recent(when: datetime | None, newest: datetime | None) -> bool:
    return when is not None and newest - when <= timedelta(days=MAX_AGE_DAYS)


def int_setting(api, cfg: dict, key: str, default: int) -> int:
    """Return cfg[key] as an int.

    If the value is not a number, log a warning and return the default.
    """
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        api.logger.warning(f"Setting '{key}' is not a number. Use {default}.")
        return default


def load_thresholds(config: dict) -> dict:
    """Replace the default thresholds with the values in config["rain_thresholds"]."""
    merged = dict(DEFAULT_THRESHOLDS)
    custom = (config or {}).get("rain_thresholds") or {}
    for lvl, key in LEVEL_KEYS.items():
        item = custom.get(key)
        if isinstance(item, dict):
            t1, t24 = merged[lvl]
            try:
                merged[lvl] = (
                    float(item.get("acc1hr", t1)),
                    float(item.get("acc24hr", t24)),
                )
            except (TypeError, ValueError):
                continue  # Not a number: keep the default threshold
    return merged


class AlertState:
    """Store the last update of each alert. Return only new or updated alerts."""

    def __init__(self) -> None:
        # ponytail: the dict keeps each alert id forever (a few KB a year).
        # Remove old ids if the memory use becomes a problem.
        self._seen: dict = {}

    def fresh(self, alerts: list[dict]) -> list[dict]:
        """Return the alerts that are new or have a newer update time."""
        out = []
        for a in alerts:
            if self._seen.get(a["cod_alerta"]) != a["updated"]:
                self._seen[a["cod_alerta"]] = a["updated"]
                out.append(a)
        return out


class RainState:
    """Store the last level of each municipality for the day.

    Return only higher levels.
    """

    def __init__(self) -> None:
        self._last: dict = {}

    def escalations(self, items: list[dict], day: str) -> list[dict]:
        """Return the items with a level higher than the last level of the day.

        The comparison uses the last level of the same municipality.
        """
        out = []
        for it in items:
            prev_day, prev_level = self._last.get(it["codibge"], (None, 0))
            if prev_day != day:
                prev_level = 0
            if it["level"] > prev_level:
                self._last[it["codibge"]] = (day, it["level"])
                out.append(it)
        return out


def _envelope(api, event_id, metadata, when, raw):
    m = api.models
    hazard = metadata["hazard"]
    source_id = RAIN_SOURCE if hazard == "rain" else ALERTS_SOURCE
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
    """Build the event envelope for a CEMADEN alert.

    The event time is the last update time.
    """
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
    return _envelope(api, event_id, metadata, alert["updated_at"] or issued, raw)


def build_rain_envelope(api, item: dict, day: str, enso: dict | None):
    """Build the event envelope for a rain gauge item.

    The event time is the time of the gauge reading.
    """
    observed = item["observed_at"]
    metadata = {
        "severity_level": item["level"],
        "uf": item["uf"],
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
    return _envelope(api, event_id, metadata, observed, raw)


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
        routing_tags=("extension", "brazil", "south", "http"),
    )


def setup(api) -> None:
    """Entry point of the extension.

    Register the presenter and the two sources. Register one alert poller,
    and one rain poller for each selected state. An error in one state does not stop
    the pollers of the other states.
    """
    cfg = api.config or {}
    ufs = selected_states(cfg)
    if not ufs:
        api.logger.warning(
            f"No known state in 'states'. Use all states {list(STATES)}."
        )
        ufs = tuple(STATES)
    enso = EnsoCache()
    alert_state, rain_state = AlertState(), RainState()
    thresholds = load_thresholds(cfg)

    async def fetch_alerts(session):
        alerts = parse_alerts(await _get_json(session, ALERTS_URL), ufs)
        alerts = [a for a in alerts if a["active"]]
        if not alerts:
            return []
        # Get ENSO before the state changes. A timeout then does not lose an alert.
        ctx = await enso.get(session)
        alerts = alert_state.fresh(alerts)
        return [build_alert_envelope(api, a, ctx) for a in alerts]

    def rain_fetcher(uf: str):
        async def fetch_rain(session):
            gauges = await _get_json(session, RAIN_URL.format(uf=uf))
            day = datetime.now(BRT).strftime("%Y%m%d")
            items = parse_rain(gauges, thresholds, uf)
            if not items:
                return []
            ctx = await enso.get(session)
            items = rain_state.escalations(items, day)
            return [build_rain_envelope(api, i, day, ctx) for i in items]

        return fetch_rain

    api.register_presenter(PRESENTER_KEY, make_presenter(api))
    api.register_source(_entry(api, ALERTS_SOURCE, "alerts", 2))
    api.register_source(_entry(api, RAIN_SOURCE, "rain", 1))
    api.register_poller(
        ALERTS_SOURCE,
        fetch_alerts,
        interval_seconds=int_setting(api, cfg, "alerts_interval", 300),
    )
    for uf in ufs:
        api.register_poller(
            RAIN_SOURCE,
            rain_fetcher(uf),
            interval_seconds=int_setting(api, cfg, "rain_interval", 300),
        )
