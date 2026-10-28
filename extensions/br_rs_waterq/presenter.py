"""Text presenter for the br_rs_waterq weather events."""

from __future__ import annotations

from datetime import date

EMOJI = {0: "✅", 2: "🟡"}


def make_presenter(api):
    """Return a presenter class that is bound to the translator of the extension."""

    class BrRsWaterqPresenter:
        """Text presenter for the bathing water quality events."""

        @classmethod
        def present(cls, display_context, _options=None) -> str:
            """Return the message text for a weather display context."""
            t = api.t
            md = display_context.metadata or {}
            level = int(md.get("severity_level") or 0)
            lines = [f"{EMOJI.get(level, '⚪')} {display_context.title}"]
            if display_context.headline:
                lines.append(display_context.headline)
            lines.append(t("present.status", status=t(f"status.{md.get('status')}")))
            sample = _fmt_date(md.get("sample_date"), t("format.date"))
            if sample:
                lines.append(t("present.sample", date=sample))
            if md.get("laboratorio"):
                lines.append(t("present.lab", lab=md["laboratorio"]))
            where = _coords(md, t("format.decimal"))
            if where:
                lines.append(t("present.location", coords=where))
            lines.append(t("present.criterion"))
            lines.append(t("present.source"))
            return "\n".join(lines)

    return BrRsWaterqPresenter


def _fmt_date(iso, fmt: str) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso).strftime(fmt)
    except ValueError:
        return str(iso)


def _coords(md: dict, dec: str) -> str:
    lat, lon = md.get("latitude"), md.get("longitude")
    if lat is None or lon is None:
        return ""
    return f"{lat:.4f}; {lon:.4f}".replace(".", dec)
