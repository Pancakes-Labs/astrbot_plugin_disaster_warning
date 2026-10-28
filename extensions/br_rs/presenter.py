"""Text presenter for br_rs weather events (bound to the extension API for t())."""

from __future__ import annotations

from datetime import datetime

EMOJI = {0: "⚪", 1: "🔵", 2: "🟡", 3: "🟠", 4: "🔴"}


def make_presenter(api):
    class BrRsWeatherPresenter:
        @classmethod
        def present(cls, display_context, options=None) -> str:
            t = api.t
            md = display_context.metadata or {}
            level = int(md.get("severity_level") or 0)
            lines = [f"{EMOJI.get(level, '⚪')} {display_context.title}"]
            if display_context.headline:
                lines.append(display_context.headline)
            if md.get("municipio"):
                lines.append(
                    t(
                        "present.location",
                        municipio=md["municipio"],
                        uf=md.get("uf", ""),
                    )
                )
            if md.get("hazard") == "rain":
                lines.append(
                    t(
                        "present.rain",
                        acc1hr=_mm(md.get("acc1hr")),
                        acc24hr=_mm(md.get("acc24hr")),
                    )
                )
            elif md.get("hazard"):
                lines.append(t("present.hazard", hazard=t(f"hazard.{md['hazard']}")))
            when = _fmt_time(md.get("issue_time"))
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
                        anom=f"{enso.get('anom', 0):+.2f}",
                    )
                )
                sources.append("NOAA CPC")
            lines.append(t("present.source", sources=" / ".join(sources)))
            return "\n".join(lines)

    return BrRsWeatherPresenter


def _mm(value) -> str:
    return "-" if value is None else f"{value:g}"


def _fmt_time(iso) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(iso)
