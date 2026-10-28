"""Paraná: IAT bathing water quality (ArcGIS layer).

See docs/extensions/brazil-water-quality-sources.md.

The layer has one row for each point of the season, and one column for each campaign
(b1 to b11, with the dates in b1_data to b11_data). The last campaign with a result
is the current result. Many campaigns have no date. Then the sample date is the newest
date of this campaign or of an earlier campaign. The real sample is not older than
this date, so the 90-day limit is never too lenient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .common import SUITABLE, UNSUITABLE, get_json, point

UF = "PR"
SOURCE_LABEL = "IAT/PR"
URL = (
    "https://geopr.iat.pr.gov.br/server/rest/services/Geoprocessamento/"
    "balneabilidade/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=false&f=json"
)
CAMPAIGNS = 11
STATUS = {"Propria": SUITABLE, "Impropria": UNSUITABLE}
BRT = timezone(timedelta(hours=-3))


def _epoch_date(ms):
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, BRT).date()


def last_campaign(attrs: dict):
    """Return (campaign number, status, sample date) of the last campaign with a result.

    Return None if no campaign has a result.
    """
    known = None
    last = None
    for n in range(1, CAMPAIGNS + 1):
        known = _epoch_date(attrs.get(f"b{n}_data")) or known
        value = (attrs.get(f"b{n}") or "").strip()
        if value:
            last = (n, STATUS.get(value), known)
    return last


def parse(data) -> list[dict]:
    """Keep the points that have a code and a result."""
    out = []
    for feature in (data or {}).get("features") or []:
        attrs = feature.get("attributes") if isinstance(feature, dict) else None
        if not isinstance(attrs, dict) or not attrs.get("cod"):
            continue
        last = last_campaign(attrs)
        if last is None:
            continue
        number, status, sample = last
        beach = " - ".join(p for p in (attrs.get("praia_rio"), attrs.get("local")) if p)
        out.append(
            point(
                attrs,
                key=str(attrs["cod"]),
                uf=UF,
                beach=beach,
                municipio=attrs.get("municipio"),
                ibge=attrs.get("cod_ibge"),
                latitude=attrs.get("latitude"),
                longitude=attrs.get("longitude"),
                laboratorio="IAT",
                status=status,
                bulletin=f"{attrs.get('ano1')}-b{number}",
                sample_date=sample,
            )
        )
    return out


async def fetch(session) -> list[dict]:
    """Get all points of the season with the last campaign."""
    return parse(await get_json(session, URL))


async def complete(_session, points: list[dict]) -> list[dict]:
    """The points already have a sample date (or None)."""
    return points
