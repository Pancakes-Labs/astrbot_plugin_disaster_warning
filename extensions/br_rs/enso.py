"""ENSO context from NOAA CPC's Oceanic Niño Index (ONI)."""

from __future__ import annotations

import time

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
CACHE_SECONDS = 24 * 3600


def parse_oni(text: str) -> dict | None:
    """Return the latest row of oni.ascii.txt as {season, year, anom} or None."""
    latest = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            latest = {
                "season": parts[0],
                "year": int(parts[1]),
                "anom": float(parts[3]),
            }
        except ValueError:
            continue  # header or junk line
    return latest


def classify(anom: float) -> tuple[str, str]:
    """Return (phase, strength); strength is '' for neutral."""
    mag = abs(anom)
    if mag < 0.5:
        return "neutral", ""
    phase = "el_nino" if anom > 0 else "la_nina"
    if mag < 1.0:
        strength = "weak"
    elif mag < 1.5:
        strength = "moderate"
    elif mag < 2.0:
        strength = "strong"
    else:
        strength = "very_strong"
    return phase, strength


def build_enso(text: str) -> dict | None:
    row = parse_oni(text)
    if row is None:
        return None
    phase, strength = classify(row["anom"])
    return {**row, "phase": phase, "strength": strength}


class EnsoCache:
    """Fetches the ONI at most once per 24 h; failures keep the last value."""

    def __init__(self) -> None:
        self.value: dict | None = None
        self._fetched_at: float | None = None

    async def get(self, session) -> dict | None:
        now = time.monotonic()
        if self._fetched_at is not None and now - self._fetched_at < CACHE_SECONDS:
            return self.value
        self._fetched_at = now  # also throttles retries after a failure
        try:
            async with session.get(ONI_URL) as resp:
                resp.raise_for_status()
                parsed = build_enso(await resp.text())
            if parsed:
                self.value = parsed
        except Exception:
            pass
        return self.value
