"""Rio Grande do Sul (Brazil): FEPAM bathing water quality (Programa Balneabilidade).

The API lists the monitoring points with the status of the last bulletin.
The extension sends an event when a point becomes unsuitable or suitable again.
A result with a sample older than MAX_AGE_DAYS is past. The extension does not send it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from .presenter import make_presenter

UF = "RS"
ID = "br_rs_waterq"
SOURCE = f"{ID}_balneabilidade"
PRESENTER_KEY = f"{ID}_weather"

LOCAIS_URL = "https://secweb.procergs.com.br/baln/rest/locais"
DEFAULT_INTERVAL = 21600  # 6 h. FEPAM publishes one bulletin each week.

SUITABLE = 2  # situacao 2: própria
UNSUITABLE = 3  # situacao 3: imprópria. The values 0 and 1 mean "not collected".
SEVERITY = {SUITABLE: 0, UNSUITABLE: 2}  # white and yellow
STATUS = {SUITABLE: "suitable", UNSUITABLE: "unsuitable"}
DATE_FMT = "%d/%m/%Y"
BRT = timezone(timedelta(hours=-3))  # Brasília time, no daylight saving time
MAX_AGE_DAYS = 90  # Hard limit. An older sample is past, not active.


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(text):
    try:
        return datetime.strptime(text, DATE_FMT).date()
    except (TypeError, ValueError):
        return None


def is_active(sample: date, today: date) -> bool:
    """Return True if the sample is MAX_AGE_DAYS old or newer."""
    return (today - sample).days <= MAX_AGE_DAYS


def parse_locais(data, today: date | None = None) -> list[dict]:
    """Keep the points that have a code and a known sample date.

    Mark each point as active or past. The default for today is the date in Brasília.
    """
    today = today or datetime.now(BRT).date()
    out = []
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("codLocal"), int):
            continue
        sample = _date(raw.get("ultimaColeta"))
        if sample is None:
            continue
        out.append(
            {
                "raw": raw,
                "cod_local": raw["codLocal"],
                "situacao": raw.get("situacao"),
                "sample_date": sample,
                "active": is_active(sample, today),
            }
        )
    return out


def _is_news(prev, key) -> bool:
    if key[1] == UNSUITABLE:
        return prev != key  # A new unsuitable bulletin
    return prev is not None and prev[1] == UNSUITABLE  # Suitable again


class BulletinState:
    """Store the last (sample date, situacao) of each point.

    The first call only records the baseline. A restart does not send old bulletins.
    """

    def __init__(self) -> None:
        self._seen: dict = {}
        self._ready = False

    def changes(self, points: list[dict]) -> list[dict]:
        """Return the points with a new unsuitable bulletin or a recovery."""
        out = []
        for p in points:
            situacao = p["situacao"]
            if situacao not in STATUS:
                continue  # Not collected: keep the last known state
            key = (p["sample_date"], situacao)
            prev = self._seen.get(p["cod_local"])
            self._seen[p["cod_local"]] = key
            if self._ready and _is_news(prev, key):
                out.append(p)
        self._ready = True
        return out


def build_envelope(api, point: dict, now: datetime | None = None):
    """Build the event envelope for a point.

    The event time is the detection time, not the sample date.
    The host time rule drops events older than 1 h.
    """
    m = api.models
    now = now or datetime.now(timezone.utc)
    raw, situacao = point["raw"], point["situacao"]
    sample = point["sample_date"]
    status = STATUS[situacao]
    metadata = {
        "severity_level": SEVERITY[situacao],
        "hazard": "water_quality",
        "status": status,
        "cod_local": point["cod_local"],
        "beach": raw.get("nome"),
        "municipio": raw.get("nomeMunicipio"),
        "ibge": raw.get("ibge"),
        "uf": UF,
        "latitude": _float(raw.get("latitude")),
        "longitude": _float(raw.get("longitude")),
        "laboratorio": raw.get("laboratorio"),
        "sample_date": sample.isoformat(),
    }
    identity = m.EventIdentity(
        event_id=f"{ID}_{point['cod_local']}_{sample:%Y%m%d}_{situacao}",
        source_id=SOURCE,
        event_type="weather_alarm",
        provider_family="extension",
        source_enum=SOURCE,
        published_at=now,
    )
    event = m.WeatherEvent(
        title=api.t(f"title.{status}"),
        headline=api.t(
            "headline",
            beach=metadata["beach"],
            municipio=metadata["municipio"],
            uf=UF,
        ),
        effective_at=now,
        metadata=metadata,
    )
    payload = m.SourcePayload(source_id=SOURCE, provider_family="extension", raw=raw)
    return m.EventEnvelope(identity=identity, event=event, payload=payload)


async def _get_json(session, url: str):
    async with session.get(url) as resp:
        resp.raise_for_status()
        return json.loads(await resp.text())


def _entry(api):
    m = api.models
    return m.SourceEntry(
        source_id=SOURCE,
        source_enum=SOURCE,
        source_type=m.SourceType.WEATHER,
        provider_family=m.ProviderFamily.EXTENSION,
        config_group="extensions",
        config_key=SOURCE,
        parser_name="",
        presentation_type="weather",
        text_presenter_key=PRESENTER_KEY,
        report_policy="none",
        intensity_mode="none",
        priority=1,
        display_name=api.t("source.balneabilidade"),
        description=api.t("source.balneabilidade.desc"),
        default_timezone="America/Sao_Paulo",
        fingerprint_prefix=SOURCE,
        dispatch_family=SOURCE,
        provider_aliases=(SOURCE,),
        routing_tags=("extension", "brazil", UF.lower(), "http"),
    )


def setup(api) -> None:
    """Entry point of the extension.

    Register the presenter, the source, and the poller.
    """
    cfg = api.config or {}
    state = BulletinState()

    async def fetch(session):
        points = parse_locais(await _get_json(session, LOCAIS_URL))
        active = [p for p in points if p["active"]]  # Past results give no event
        return [build_envelope(api, p) for p in state.changes(active)]

    api.register_presenter(PRESENTER_KEY, make_presenter(api))
    api.register_source(_entry(api))
    api.register_poller(
        SOURCE,
        fetch,
        interval_seconds=int(cfg.get("poll_interval", DEFAULT_INTERVAL)),
    )
