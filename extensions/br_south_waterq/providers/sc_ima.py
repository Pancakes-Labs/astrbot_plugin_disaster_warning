"""Santa Catarina: IMA bathing water quality.

See docs/extensions/brazil-water-quality-sources.md.

The map endpoint gives the status of all points, but no sample date. The samples
endpoint gives the dates of one point. Thus, the provider asks for the samples only
for the changed points. A new sample with the same status is not news in SC.
"""

from __future__ import annotations

from .common import SUITABLE, UNSUITABLE, get_json, point, to_date, to_float

UF = "SC"
SOURCE_LABEL = "IMA/SC"
BASE = "https://balneabilidade.ima.sc.gov.br"
MAP_URL = f"{BASE}/relatorio/mapa"
SAMPLES_URL = f"{BASE}/relatorio/analisesPonto"
STATUS = {"PRÓPRIO": SUITABLE, "IMPRÓPRIO": UNSUITABLE}  # Other values: unknown


def _beach(raw: dict) -> str:
    return " - ".join(
        part for part in (raw.get("BALNEARIO"), raw.get("PONTO_NOME")) if part
    )


def parse(data) -> list[dict]:
    """Keep the points that have a code. The sample date is None."""
    out = []
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, dict) or not raw.get("CODIGO"):
            continue
        out.append(
            point(
                raw,
                key=raw["CODIGO"],
                uf=UF,
                beach=_beach(raw),
                municipio=raw.get("MUNICIPIO"),
                ibge=raw.get("MUNICIPIO_COD_IBGE"),
                latitude=to_float(raw.get("LATITUDE")),
                longitude=to_float(raw.get("LONGITUDE")),
                laboratorio="IMA",
                status=STATUS.get(raw.get("CONDICAO")),
            )
        )
    return out


def newest_sample(samples) -> dict | None:
    """Return the sample with the newest date, or None."""
    rows = samples if isinstance(samples, list) else []
    dated = [
        (d, s) for s in rows if isinstance(s, dict) and (d := to_date(s.get("DATA")))
    ]
    return max(dated, key=lambda item: item[0])[1] if dated else None


async def fetch(session) -> list[dict]:
    """Get all points with the current status."""
    return parse(await get_json(session, MAP_URL))


async def complete(session, points: list[dict]) -> list[dict]:
    """Add the date and the E. coli result of the newest sample to each point."""
    for p in points:
        sample = newest_sample(
            await get_json(session, SAMPLES_URL, data={"codigo": p["key"]})
        )
        if sample is not None:
            p["sample_date"] = to_date(sample["DATA"])
            p["bulletin"] = p["sample_date"].isoformat()
            p["ecoli"] = to_float(sample.get("RESULTADO"))
    return points
