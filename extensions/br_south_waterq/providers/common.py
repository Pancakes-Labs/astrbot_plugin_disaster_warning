"""Helpers for the provider modules."""

from __future__ import annotations

import json
from datetime import date, datetime

SUITABLE = "suitable"
UNSUITABLE = "unsuitable"
DATE_FMT = "%d/%m/%Y"


def to_float(value) -> float | None:
    """Return the value as a float, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_date(text, fmt: str = DATE_FMT) -> date | None:
    """Return the text as a date, or None."""
    try:
        return datetime.strptime(text, fmt).date()
    except (TypeError, ValueError):
        return None


def point(raw: dict, **fields) -> dict:
    """Return a common point. Missing fields get None."""
    keys = (
        "key",
        "uf",
        "beach",
        "municipio",
        "ibge",
        "latitude",
        "longitude",
        "laboratorio",
        "status",
        "bulletin",
        "sample_date",
    )
    return {"raw": raw, **{k: fields.get(k) for k in keys}}


async def get_json(session, url: str, data: dict | None = None):
    """Send GET, or POST with form data. Read the body as JSON.

    Some servers send JSON as text/html, or with a byte order mark.
    """
    request = session.post(url, data=data) if data is not None else session.get(url)
    async with request as resp:
        resp.raise_for_status()
        return json.loads((await resp.text()).lstrip("﻿"))
