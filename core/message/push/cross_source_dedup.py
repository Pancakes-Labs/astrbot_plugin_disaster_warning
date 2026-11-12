"""Per-session delivered versions, shared by ordinary and aggregated pushes.

Relays of one institution share a product group. Independent Global Quake
estimates, EEW, measured reports and intensity products remain distinct.
Only successful delivery commits a version; failed or filtered candidates do not.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import re
import time
import json
import hashlib


GROUPS = {
    **dict.fromkeys(
        ("cea_fanstudio", "cea_pr_fanstudio", "cea_wolfx", "cea_jianproject"), "cea_eew"
    ),
    **dict.fromkeys(
        ("cenc_fanstudio", "cenc_wolfx", "cenc_jianproject"), "cenc_report"
    ),
    **dict.fromkeys(("cenc_ir_fanstudio", "cenc_ir_eqsc"), "cenc_intensity"),
    **dict.fromkeys(
        ("jma_fanstudio", "jma_p2p", "jma_wolfx", "jma_pancakes", "jma_jianproject"),
        "jma_eew",
    ),
    **dict.fromkeys(
        ("jma_p2p_info", "jma_wolfx_info", "jma_eqlist_pancakes"), "jma_report"
    ),
    **dict.fromkeys(("cwa_fanstudio", "cwa_wolfx", "cwa_jianproject"), "cwa_eew"),
    "cwa_fanstudio_report": "cwa_report",
    **dict.fromkeys(
        ("usgs_fanstudio", "usgs_pancakes", "usgs_jianproject"), "usgs_report"
    ),
    **dict.fromkeys(
        ("china_weather_fanstudio", "china_weather_jianproject"), "cma_weather"
    ),
    "global_quake": "global_quake",
}


def _number(value, fallback=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _text(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def _flag(value):
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _business_value(value):
    """Canonical values for actual report fields, never transport metadata."""
    if isinstance(value, dict):
        return {str(key): _business_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_business_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    return value


def _timestamp(value, group):
    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None:
            offset = (
                9
                if group.startswith("jma_")
                else 0
                if group.startswith(("usgs_", "global_"))
                else 8
            )
            value = value.replace(tzinfo=timezone(timedelta(hours=offset)))
        return value.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 12742 * math.asin(min(1.0, math.sqrt(max(0.0, h))))


class CrossSourceDeduplicator:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._records = defaultdict(list)
        self._locks = defaultdict(asyncio.Lock)

    @staticmethod
    def enabled(config):
        return bool(
            (config.get("event_deduplication") or {}).get("cross_source_enabled", False)
        )

    def lock(self, session):
        return self._locks[session]

    @staticmethod
    def _describe(envelope):
        identity = envelope.identity
        source = str(getattr(identity, "source_id", "") or "")
        group = GROUPS.get(source)
        if group is None:
            return None
        event = envelope.event
        metadata = {
            **(getattr(event, "metadata", None) or {}),
            **(getattr(envelope, "metadata", None) or {}),
        }
        final = bool(getattr(identity, "is_final", False)) or _flag(
            metadata.get("is_final")
        )
        cancel_keys = (
            "is_cancel",
            "is_cancelled",
            "cancelled",
            "canceled",
            "isCancel",
            "isCancelled",
            "cancel",
        )
        cancelled = any(_flag(metadata.get(key)) for key in cancel_keys)
        info = _text(
            metadata.get("info_type") or metadata.get("issue_type") or ""
        ).lower()
        issue = _text(metadata.get("issue_type") or info)
        ranks = {
            "scaleprompt": 1,
            "destination": 2,
            "scaleanddestination": 3,
            "detailscale": 4,
        }
        rank = ranks.get(issue.lower(), 0)
        if any(value in info for value in ("reviewed", "正式", "警报", "警報")):
            rank = max(rank, 1)
        common = {
            "group": group,
            "source": source,
            "id": str(getattr(identity, "event_id", "") or ""),
            "report": int(_number(getattr(identity, "report_num", None), 1)),
            "final": final,
            "cancelled": cancelled,
            "rank": rank,
            "cancel_specified": any(
                metadata.get(key) is not None for key in cancel_keys
            ),
        }
        if group == "cma_weather":
            title = _text(getattr(event, "title", ""))
            body = _text(metadata.get("description") or getattr(event, "headline", ""))
            if not title and not body:
                return None
            business = metadata.get("weather_business_fields")
            if not isinstance(business, dict):
                business = {
                    key: metadata.get(key)
                    for key in ("level", "alert_level", "severity_color", "status")
                    if metadata.get(key) not in (None, "")
                }
            return {
                **common,
                "kind": "weather",
                "time": _timestamp(getattr(event, "effective_at", None), group),
                "content": (title, body),
                "business": json.dumps(
                    business, ensure_ascii=False, sort_keys=True, default=str
                ),
                "geo": tuple(
                    _text(metadata.get(key)) for key in ("province", "city", "district")
                ),
            }
        lat = _number(getattr(event, "latitude", None))
        lon = _number(getattr(event, "longitude", None))
        coordinates = (
            (lat, lon)
            if lat is not None
            and lon is not None
            and -90 <= lat <= 90
            and -180 <= lon <= 180
            else None
        )
        report_fields = {
            key: _business_value(metadata[key])
            for key in (
                "info_type",
                "issue_type",
                "status",
                "revision",
                "description",
                "magnitude_type",
                "max_scale",
                "max_intensity",
                "domestic_tsunami",
                "foreign_tsunami",
                "jma_points",
                "jma_comment",
                "jma_warn_area",
                "jma_warning_areas",
                "jma_warning_area_ranges",
                "jma_warning_area_groups",
                "warn_area",
                "is_warn",
                "is_training",
                "is_assumption",
                "is_sea",
                "title",
                "headline",
                "telegram",
                "max_pga",
                "stations",
                "quality",
                "fixed_depth",
            )
            if metadata.get(key) not in (None, "", [], {})
        }
        content = {
            key: _business_value(getattr(event, key, None))
            for key in (
                "place_name",
                "headline",
                "province",
                "magnitude",
                "depth",
                "intensity",
                "scale",
                "latitude",
                "longitude",
            )
        }
        content.update(
            time=_timestamp(getattr(event, "occurred_at", None), group),
            metadata=report_fields,
        )
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        shared = {
            key: report_fields[key]
            for key in (
                "domestic_tsunami",
                "foreign_tsunami",
                "revision",
                "max_scale",
                "max_intensity",
                "jma_warning_areas",
                "jma_warning_area_ranges",
                "jma_warning_area_groups",
                "warn_area",
                "is_warn",
                "is_training",
                "is_assumption",
            )
            if key in report_fields
        }
        shared.update(
            {key: _number(getattr(event, key, None)) for key in ("intensity", "scale")}
        )
        return {
            **common,
            "kind": "quake",
            "time": content["time"],
            "coordinates": coordinates,
            "magnitude": _number(getattr(event, "magnitude", None)),
            "depth": _number(getattr(event, "depth", None)),
            "content_version": hashlib.sha256(encoded.encode()).hexdigest(),
            "shared": shared,
        }

    def _prune(self, session, config):
        options = config.get("event_deduplication") or {}
        retention = max(60, _number(options.get("retention_minutes"), 60) * 60)
        cutoff = self._clock() - retention
        self._records[session] = [
            record
            for record in self._records[session]
            if record["delivered_at"] > cutoff
        ][-2048:]

    @staticmethod
    def _same_event(current, previous, config):
        if current["group"] != previous["group"] or current["kind"] != previous["kind"]:
            return False
        known_ids = previous.get("aliases", {previous["id"]})
        if current["id"] and current["id"] in known_ids:
            return True
        if current["kind"] == "weather":
            # Different releases with the same generic title are not interchangeable.
            if current["time"] is None or previous["time"] is None:
                return bool(current["id"]) and current["id"] == previous["id"]
            return (
                abs(current["time"] - previous["time"]) <= 1
                and current["content"] == previous["content"]
                and current["geo"] == previous["geo"]
            )
        if current["source"] == previous["source"]:
            # A single upstream can issue distinct nearby aftershocks.
            return False
        fields = ("time", "coordinates", "magnitude")
        if any(current[key] is None or previous[key] is None for key in fields):
            return False
        options = config.get("event_deduplication") or {}
        return (
            abs(current["time"] - previous["time"])
            <= min(10, max(0, _number(options.get("time_window_minutes"), 1) * 60))
            and _distance(current["coordinates"], previous["coordinates"])
            <= max(0, _number(options.get("location_tolerance_km"), 20))
            and abs(current["magnitude"] - previous["magnitude"])
            <= max(0, _number(options.get("magnitude_tolerance"), 0.5))
        )

    @staticmethod
    def _covered(current, previous):
        if current["kind"] == "weather":
            return all(
                current[key] == previous[key]
                for key in ("content", "business", "time", "geo", "cancelled", "final")
            )
        highest_report = previous.get("highest_report", previous["report"])
        if current["cancelled"] != previous["cancelled"]:
            # A cancellation may be report zero without origin parameters.
            # Restoration requires an explicit active flag and cannot be an
            # older normal report replayed after that cancellation.
            if current["cancelled"]:
                return False
            return not current["cancel_specified"] or current["report"] < highest_report
        if current["final"] and not previous["final"]:
            return False
        if current["rank"] > previous["rank"] or current["report"] > highest_report:
            return False
        # Older reports do not replace a newer estimate with stale parameters.
        if current["report"] < highest_report or current["rank"] < previous["rank"]:
            return True
        if current["source"] == previous["source"]:
            return current["content_version"] == previous["content_version"]
        # Relay wording and rounding differ. Compare shared business fields;
        # absent -> supplied parameters are useful updates, not duplicates.
        for key in ("time", "coordinates", "magnitude", "depth"):
            if current[key] is not None and previous[key] is None:
                return False
        for key, value in current["shared"].items():
            if value is not None and value != previous["shared"].get(key):
                return False
        if current["time"] is not None and previous["time"] is not None:
            if abs(current["time"] - previous["time"]) > 10:
                return False
        for key, tolerance in (("magnitude", 0.05), ("depth", 1.0)):
            if (
                current[key] is not None
                and previous[key] is not None
                and abs(current[key] - previous[key]) > tolerance
            ):
                return False
        if current["coordinates"] is not None and previous["coordinates"] is not None:
            if _distance(current["coordinates"], previous["coordinates"]) > 2:
                return False
        return True

    def is_duplicate(self, session, event, config, pending=()):
        if not self.enabled(config):
            return False
        current = self._describe(event)
        if current is None:
            return False
        self._prune(session, config)
        previous = list(self._records[session])
        previous.extend(
            item
            for candidate in pending
            if (item := self._describe(candidate)) is not None
        )
        matches = [item for item in previous if self._same_event(current, item, config)]
        if not matches:
            return False
        exact = [
            item
            for item in matches
            if current["id"] and current["id"] in item.get("aliases", {item["id"]})
        ]
        if exact:
            matches = exact
        elif (
            current["kind"] == "quake"
            and len({(item["source"], item["id"]) for item in matches}) > 1
        ):
            # Do not choose between two plausible physical events.
            return False
        # Compare the latest delivered version, not any historical version: a
        # restored warning after cancellation must not match the pre-cancel copy.
        latest = matches[-1]
        covered = self._covered(current, latest)
        if covered and current["id"] and "delivered_at" in latest:
            # Link only to a confirmed delivered record. Pending candidates have
            # no delivery receipt and must not teach aliases or consume state.
            latest.setdefault("aliases", {latest["id"]}).add(current["id"])
        return covered

    def remember(self, session, event, config):
        if not self.enabled(config):
            return
        item = self._describe(event)
        if item is None:
            return
        self._prune(session, config)
        records = self._records[session]
        matches = [
            previous for previous in records if self._same_event(item, previous, config)
        ]
        exact = [
            previous
            for previous in matches
            if item["id"] and item["id"] in previous.get("aliases", {previous["id"]})
        ]
        if exact:
            matches = exact
        aliases = {item["id"]} if item["id"] else set()
        # Store the latest version of an unambiguous logical event and retain
        # all relay IDs, so a later cancellation/restoration uses current state.
        if len(matches) == 1:
            previous = matches[0]
            aliases.update(previous.get("aliases", {previous["id"]}))
            item["highest_report"] = max(
                item["report"], previous.get("highest_report", previous["report"])
            )
            item["final"] = item["final"] or previous["final"]
            item["rank"] = max(item["rank"], previous["rank"])
            records.remove(previous)
        records.append({**item, "aliases": aliases, "delivered_at": self._clock()})
