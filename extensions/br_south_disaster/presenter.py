"""Text presenter for the br_south_disaster weather events."""

from __future__ import annotations

from datetime import datetime

EMOJI = {0: "⚪", 1: "🔵", 2: "🟡", 3: "🟠", 4: "🔴"}


def make_presenter(api):
    """Return a presenter class that is bound to the translator of the extension."""

    class BrSouthWeatherPresenter:
        """Text presenter for the alerts and the rain events of br_south_disaster."""

        @classmethod
        def present(cls, display_context, _options=None) -> str:
            """Return the message text for a weather display context."""
            t = api.t
            dec = t("format.decimal")
            md = display_context.metadata or {}
            level = int(md.get("severity_level") or 0)
            lines = [f"{EMOJI.get(level, '⚪')} {display_context.title}"]
            if display_context.headline:
                lines.append(display_context.headline)
            if md.get("hazard") == "rain":
                lines.append(
                    t(
                        "present.rain",
                        acc1hr=_mm(md.get("acc1hr"), dec),
                        acc24hr=_mm(md.get("acc24hr"), dec),
                    )
                )
            elif md.get("hazard"):
                lines.append(t("present.hazard", hazard=t(f"hazard.{md['hazard']}")))
            when = _fmt_time(md.get("issue_time"), t("format.time"))
            if when:
                lines.append(t("present.time", time=when))
            sources = ["CEMADEN"]
            enso = md.get("enso") or {}
            if enso.get("phase") in ("el_nino", "la_nina"):
                lines.append(
                    t(
                        "present.enso",
                        phase=t(f"enso.phase.{enso['phase']}"),
                        strength=t(f"enso.strength.{enso['strength']}"),
                        season=enso.get("season", ""),
                        year=enso.get("year", ""),
                        anom=f"{enso.get('anom', 0):+.2f}".replace(".", dec),
                    )
                )
                sources.append("NOAA CPC")
            lines.append(t("present.source", sources=" / ".join(sources)))
            return "\n".join(lines)

    return BrSouthWeatherPresenter


def _mm(value, dec: str) -> str:
    return "-" if value is None else f"{value:g}".replace(".", dec)


def _fmt_time(iso, fmt: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except ValueError:
        return str(iso)
