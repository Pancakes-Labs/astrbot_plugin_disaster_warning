"""ENSO data from the Oceanic Niño Index (ONI) of NOAA CPC."""

from __future__ import annotations

import time

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
CACHE_SECONDS = 24 * 3600
# The lower limit of each strength class, in degrees Celsius of ONI anomaly.
STRENGTH_FLOORS = (
    (2.0, "very_strong"),
    (1.5, "strong"),
    (1.0, "moderate"),
    (0.5, "weak"),
)
# The columns of oni.ascii.txt: SEAS YR TOTAL ANOM.
ONI_COLUMNS = 4


def classify(anom: float) -> tuple[str, str]:
    """Return (phase, strength). The strength is empty for the neutral phase."""
    mag = abs(anom)
    for floor, strength in STRENGTH_FLOORS:
        if mag >= floor:
            return ("el_nino" if anom > 0 else "la_nina"), strength
    return "neutral", ""


def build_enso(text: str) -> dict | None:
    """Return the last row of oni.ascii.txt as an ENSO dict, or None."""
    row = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == ONI_COLUMNS:
            try:
                row = {
                    "season": parts[0],
                    "year": int(parts[1]),
                    "anom": float(parts[3]),
                }
            except ValueError:
                continue  # header line
    if row is None:
        return None
    phase, strength = classify(row["anom"])
    return {**row, "phase": phase, "strength": strength}


class EnsoCache:
    """Get the ONI one time each 24 hours. If a request fails, keep the last value."""

    def __init__(self) -> None:
        self.value: dict | None = None
        self._fetched_at: float | None = None

    async def get(self, session) -> dict | None:
        """Return the cached ENSO dict.

        Download the ONI file again when the cache is older than 24 hours.
        """
        now = time.monotonic()
        if self._fetched_at is not None and now - self._fetched_at < CACHE_SECONDS:
            return self.value
        self._fetched_at = now  # This also limits the retries after a failure.
        try:
            async with session.get(ONI_URL) as resp:
                resp.raise_for_status()
                parsed = build_enso(await resp.text())
            if parsed:
                self.value = parsed
        # ENSO is optional background data. No failure here can stop an alert.
        except Exception:  # noqa: BLE001
            pass
        return self.value
