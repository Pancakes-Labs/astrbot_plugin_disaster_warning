"""Rio Grande do Sul: FEPAM Programa Balneabilidade.

See docs/extensions/brazil-water-quality-sources.md.
"""

from __future__ import annotations

from .common import SUITABLE, UNSUITABLE, get_json, point, to_date, to_float

UF = "RS"
SOURCE_LABEL = "FEPAM / PROCERGS"
URL = "https://secweb.procergs.com.br/baln/rest/locais"
# situacao. The values 0 and 1 mean "not collected".
STATUS = {2: SUITABLE, 3: UNSUITABLE}


def parse(data) -> list[dict]:
    """Keep the points that have a code and a known sample date."""
    out = []
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("codLocal"), int):
            continue
        sample = to_date(raw.get("ultimaColeta"))
        if sample is None:
            continue
        out.append(
            point(
                raw,
                key=str(raw["codLocal"]),
                uf=UF,
                beach=raw.get("nome"),
                municipio=raw.get("nomeMunicipio"),
                ibge=raw.get("ibge"),
                latitude=to_float(raw.get("latitude")),
                longitude=to_float(raw.get("longitude")),
                laboratorio=raw.get("laboratorio"),
                status=STATUS.get(raw.get("situacao")),
                bulletin=sample.isoformat(),
                sample_date=sample,
            )
        )
    return out


async def fetch(session) -> list[dict]:
    """Get all points with the result of the last bulletin."""
    return parse(await get_json(session, URL))


async def complete(_session, points: list[dict]) -> list[dict]:
    """The points already have the sample dates."""
    return points
