"""South of Brazil (RS, SC, PR): bathing water quality of the state agencies.

Each state has a provider module (see providers/ and states.py). The extension sends
an event when a point becomes unsuitable or suitable again. A result with a sample
older than MAX_AGE_DAYS, or with no known sample date, is past. The extension does
not send it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .presenter import make_presenter
from .providers.common import SUITABLE, UNSUITABLE
from .states import STATES, selected_states

ID = "br_south_waterq"
SOURCE = f"{ID}_balneabilidade"
PRESENTER_KEY = f"{ID}_weather"

DEFAULT_INTERVAL = 21600  # 6 h. The agencies publish about one bulletin each week.
SEVERITY = {SUITABLE: 0, UNSUITABLE: 2}  # white and yellow
BRT = timezone(timedelta(hours=-3))  # Brasília time, no daylight saving time
MAX_AGE_DAYS = 90  # Hard limit. An older sample is past, not active.


def is_active(sample: date | None, today: date) -> bool:
    """Return True if the sample date is known and MAX_AGE_DAYS old or newer."""
    return sample is not None and (today - sample).days <= MAX_AGE_DAYS


def _is_news(prev, key) -> bool:
    if key[1] == UNSUITABLE:
        return prev != key  # A new unsuitable bulletin
    return prev is not None and prev[1] == UNSUITABLE  # Suitable again


class BulletinState:
    """Store the last (bulletin, status) of each point of one state.

    The first call only records the baseline. A restart does not send old bulletins.
    """

    def __init__(self) -> None:
        self._seen: dict = {}
        self._ready = False

    def changes(self, points: list[dict]) -> list[dict]:
        """Return the points with a new unsuitable bulletin or a recovery."""
        out = []
        for p in points:
            if p["status"] not in SEVERITY:
                continue  # Not collected or unknown: keep the last known state
            key = (p["bulletin"], p["status"])
            prev = self._seen.get(p["key"])
            self._seen[p["key"]] = key
            if self._ready and _is_news(prev, key):
                out.append(p)
        if points:
            self._ready = True
        return out


def build_envelope(api, point: dict, source_label: str, now: datetime | None = None):
    """Build the event envelope for a common point.

    The event time is the detection time, not the sample date.
    The host time rule drops events older than 1 h.
    """
    m = api.models
    now = now or datetime.now(timezone.utc)
    status, sample = point["status"], point["sample_date"]
    metadata = {
        "severity_level": SEVERITY[status],
        "hazard": "water_quality",
        "status": status,
        "point": point["key"],
        "beach": point["beach"],
        "municipio": point["municipio"],
        "ibge": point["ibge"],
        "uf": point["uf"],
        "latitude": point["latitude"],
        "longitude": point["longitude"],
        "laboratorio": point["laboratorio"],
        "sample_date": sample.isoformat() if sample else None,
        "ecoli": point.get("ecoli"),
        "source_label": source_label,
    }
    tag = f"{sample:%Y%m%d}" if sample else point["bulletin"]
    identity = m.EventIdentity(
        event_id=f"{ID}_{point['uf'].lower()}_{point['key']}_{tag}_{status}",
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
            uf=metadata["uf"],
        ),
        effective_at=now,
        metadata=metadata,
    )
    payload = m.SourcePayload(
        source_id=SOURCE, provider_family="extension", raw=point["raw"]
    )
    return m.EventEnvelope(identity=identity, event=event, payload=payload)


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
        routing_tags=("extension", "brazil", "south", "http"),
    )


def make_fetch(api, provider):
    """Return the poll function of one state. Each state has its own baseline."""
    state = BulletinState()

    async def fetch(session):
        news = state.changes(await provider.fetch(session))
        if not news:
            return []
        news = await provider.complete(session, news)
        today = datetime.now(BRT).date()
        return [
            build_envelope(api, p, provider.SOURCE_LABEL)
            for p in news
            if is_active(p["sample_date"], today)  # Past results give no event
        ]

    return fetch


def setup(api) -> None:
    """Entry point of the extension.

    Register the presenter and the source. Register one poller for each selected
    state. An error in one state does not stop the pollers of the other states.
    """
    cfg = api.config or {}
    ufs = selected_states(cfg)
    if not ufs:
        api.logger.warning(
            f"No known state in 'states'. Use all states {list(STATES)}."
        )
        ufs = tuple(STATES)
    api.register_presenter(PRESENTER_KEY, make_presenter(api))
    api.register_source(_entry(api))
    for uf in ufs:
        api.register_poller(
            SOURCE,
            make_fetch(api, STATES[uf]),
            interval_seconds=int(cfg.get("poll_interval", DEFAULT_INTERVAL)),
            timeout_seconds=60,  # SC asks for the samples of each changed point
        )
