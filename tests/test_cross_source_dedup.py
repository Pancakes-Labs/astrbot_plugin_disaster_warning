"""Delivery-aware cross-source matching, using only synthetic events and a fake clock."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import sys
from types import SimpleNamespace
import unittest


from _source_loader import SOURCE as ROOT
SOURCE = ROOT / "core/message/push/cross_source_dedup.py"

SESSION = "test:GroupMessage:synthetic-a"
OTHER_SESSION = "test:GroupMessage:synthetic-b"
ORIGIN = datetime(2026, 11, 11, 10, 0, 59, tzinfo=timezone.utc)


def quake(
    source,
    event_id,
    *,
    occurred_at=ORIGIN,
    latitude=31.981,
    longitude=117.30,
    magnitude=4.5,
    depth=10.0,
    report=1,
    final=False,
    event_type="earthquake_warning",
    **flags,
):
    metadata = {"report_num": report, "is_final": final, **flags}
    return SimpleNamespace(
        identity=SimpleNamespace(
            event_id=event_id,
            source_id=source,
            event_type=event_type,
            report_num=report,
            is_final=final,
        ),
        event=SimpleNamespace(
            occurred_at=occurred_at,
            latitude=latitude,
            longitude=longitude,
            magnitude=magnitude,
            depth=depth,
            place_name="synthetic epicentre",
            headline="synthetic",
            metadata=dict(metadata),
        ),
        metadata=metadata,
    )


def weather(
    source,
    event_id,
    *,
    title="合肥市暴雨黄色预警",
    headline="synthetic weather details",
):
    return SimpleNamespace(
        identity=SimpleNamespace(
            event_id=event_id,
            source_id=source,
            event_type="weather_alarm",
            report_num=1,
            is_final=False,
        ),
        event=SimpleNamespace(
            title=title,
            headline=headline,
            effective_at=ORIGIN,
            metadata={"city": "合肥市"},
        ),
        metadata={"city": "合肥市", "issue_time": ORIGIN},
    )


class CrossSourceDedupTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        name = "_disaster_cross_source_dedup_test"
        spec = importlib.util.spec_from_file_location(name, SOURCE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        cls.deduplicator_class = module.CrossSourceDeduplicator

    def setUp(self):
        self.now = 1000.0
        self.config = {
            "event_deduplication": {
                "cross_source_enabled": True,
                "time_window_minutes": 1,
                "location_tolerance_km": 20,
                "magnitude_tolerance": 0.5,
                "retention_minutes": 60,
            }
        }
        self.dedup = self.deduplicator_class(clock=lambda: self.now)

    def duplicate(self, event, *, session=SESSION, pending=()):
        return self.dedup.is_duplicate(session, event, self.config, pending=pending)

    def remember(self, event, *, session=SESSION):
        self.dedup.remember(session, event, self.config)

    def test_enabled_config_gate_does_not_suppress_when_disabled(self):
        self.assertTrue(self.dedup.enabled(self.config))
        self.assertFalse(self.dedup.enabled({}))
        first = quake("jma_wolfx", "wolfx-a")
        second = quake("jma_p2p", "p2p-a")
        self.remember(first)
        self.config["event_deduplication"]["cross_source_enabled"] = False
        self.assertFalse(self.dedup.enabled(self.config))
        self.assertFalse(self.duplicate(second))

    def test_checks_and_pending_do_not_commit_unsent_or_failed_deliveries(self):
        first = quake("jma_wolfx", "wolfx-a")
        second = quake("jma_p2p", "p2p-a")
        self.assertFalse(self.duplicate(first))
        self.assertFalse(self.duplicate(second))
        self.assertTrue(self.duplicate(second, pending=(first,)))
        # A checked/pending message that never succeeds must remain retryable.
        self.assertFalse(self.duplicate(second))
        self.remember(first)
        self.assertTrue(self.duplicate(second))

    def test_jma_cross_channel_ids_match_across_minute_and_grid_boundaries(self):
        first = quake("jma_wolfx", "wolfx-a", info_type="警报")
        self.remember(first)
        second = quake(
            "jma_p2p",
            "p2p-unrelated-id",
            occurred_at=ORIGIN + timedelta(seconds=2),
            latitude=31.983,
            info_type="警报",
        )
        self.assertTrue(self.duplicate(second))

    def test_same_report_material_parameter_revisions_are_preserved(self):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        for values in ({"latitude": 32.011}, {"magnitude": 4.6}, {"depth": 12.0}):
            with self.subTest(values=values):
                self.assertFalse(self.duplicate(quake("jma_p2p", "p2p-a", **values)))

    def test_cenc_and_usgs_matching_do_not_depend_on_transport_ids(self):
        cases = [
            ("cenc_wolfx", "cenc_jianproject", {"info_type": "正式测定"}),
            ("usgs_pancakes", "usgs_jianproject", {"info_type": "reviewed"}),
        ]
        for first_source, second_source, flags in cases:
            with self.subTest(source=first_source):
                first = quake(
                    first_source, "first-channel-id", event_type="earthquake", **flags
                )
                second = quake(
                    second_source,
                    "different-channel-id",
                    event_type="earthquake",
                    **flags,
                )
                self.remember(first)
                self.assertTrue(self.duplicate(second))

    def test_delivered_records_are_isolated_by_session(self):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        second = quake("jma_p2p", "p2p-a")
        self.assertTrue(self.duplicate(second))
        self.assertFalse(self.duplicate(second, session=OTHER_SESSION))

    def test_global_quake_cannot_swallow_first_official_measurement(self):
        self.remember(quake("global_quake", "gq-a"))
        official = quake(
            "cenc_wolfx", "official-a", event_type="earthquake", info_type="正式测定"
        )
        self.assertFalse(self.duplicate(official))
        self.assertFalse(self.duplicate(quake("jma_wolfx", "jma-a")))

    def test_eew_report_and_intensity_report_are_separate_products(self):
        self.remember(quake("jma_wolfx", "jma-a"))
        self.assertFalse(
            self.duplicate(
                quake("jma_p2p_info", "jma-report-a", event_type="earthquake")
            )
        )
        self.remember(quake("cenc_wolfx", "cenc-a", event_type="earthquake"))
        self.assertFalse(self.duplicate(quake("cea_jianproject", "cea-a")))
        self.assertFalse(
            self.duplicate(
                quake("cenc_ir_eqsc", "intensity-a", event_type="earthquake")
            )
        )

    def test_new_report_final_cancel_and_warning_upgrade_are_preserved(self):
        self.remember(quake("jma_wolfx", "wolfx-a", info_type="予报", is_warn=False))
        updates = [
            {"report": 2, "info_type": "予报", "is_warn": False},
            {"final": True, "info_type": "予报", "is_warn": False},
            {"is_cancel": True, "info_type": "予报", "is_warn": False},
            {"info_type": "警报", "is_warn": True},
        ]
        for flags in updates:
            with self.subTest(flags=flags):
                self.assertFalse(self.duplicate(quake("jma_p2p", "p2p-a", **flags)))

    def test_official_status_and_jma_information_upgrades_are_preserved(self):
        self.remember(
            quake(
                "usgs_pancakes",
                "usgs-a",
                event_type="earthquake",
                info_type="automatic",
            )
        )
        self.assertFalse(
            self.duplicate(
                quake(
                    "usgs_jianproject",
                    "usgs-b",
                    event_type="earthquake",
                    info_type="reviewed",
                )
            )
        )
        self.remember(
            quake(
                "jma_p2p_info",
                "jma-a",
                event_type="earthquake",
                info_type="ScalePrompt",
            )
        )
        self.assertFalse(
            self.duplicate(
                quake(
                    "jma_wolfx_info",
                    "jma-b",
                    event_type="earthquake",
                    info_type="DetailScale",
                )
            )
        )

    def test_cancellation_then_restoration_uses_latest_delivered_state(self):
        self.remember(quake("jma_wolfx", "wolfx-a", is_cancel=False))
        self.remember(quake("jma_p2p", "p2p-a", is_cancel=True))
        self.assertFalse(self.duplicate(quake("jma_wolfx", "wolfx-a", is_cancel=False)))

    def test_weather_same_id_new_business_time_level_or_location_survives(self):
        first = weather("china_weather_fanstudio", "weather-a")
        first.metadata["weather_business_fields"] = {
            "issued": "2026-11-11T01:00:00Z",
            "level": "黄色",
        }
        self.remember(first)
        for changed in (
            {"issued": "2026-11-11T02:00:00Z", "level": "黄色"},
            {"issued": "2026-11-11T01:00:00Z", "level": "橙色"},
        ):
            with self.subTest(changed=changed):
                update = weather("china_weather_jianproject", "weather-a")
                update.metadata["weather_business_fields"] = changed
                self.assertFalse(self.duplicate(update))
        update = weather("china_weather_jianproject", "weather-a")
        update.metadata["weather_business_fields"] = first.metadata[
            "weather_business_fields"
        ]
        update.metadata["city"] = "synthetic other city"
        self.assertFalse(self.duplicate(update))
        update = weather("china_weather_jianproject", "weather-a")
        update.metadata["weather_business_fields"] = first.metadata[
            "weather_business_fields"
        ]
        update.event.effective_at += timedelta(hours=1)
        self.assertFalse(self.duplicate(update))

    def test_incomplete_or_nonfinite_quake_data_does_not_fuzzy_match(self):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        cases = [
            {"occurred_at": None},
            {"latitude": None},
            {"longitude": None},
            {"magnitude": None},
            {"latitude": float("nan")},
            {"magnitude": float("inf")},
        ]
        for values in cases:
            with self.subTest(values=values):
                self.assertFalse(self.duplicate(quake("jma_p2p", "p2p-a", **values)))

    def test_nearby_distinct_events_from_same_channel_are_not_merged(self):
        self.remember(quake("jma_wolfx", "distinct-a"))
        self.assertFalse(
            self.duplicate(
                quake(
                    "jma_wolfx", "distinct-b", occurred_at=ORIGIN + timedelta(seconds=5)
                )
            )
        )

    def test_ambiguous_cross_channel_match_is_allowed(self):
        self.remember(quake("jma_wolfx", "distinct-a"))
        self.remember(
            quake("jma_wolfx", "distinct-b", occurred_at=ORIGIN + timedelta(seconds=10))
        )
        candidate = quake(
            "jma_p2p",
            "unknown-correspondence",
            occurred_at=ORIGIN + timedelta(seconds=5),
        )
        self.assertFalse(self.duplicate(candidate))

    def test_weather_different_ids_match_same_content_but_changes_survive(self):
        self.remember(weather("china_weather_fanstudio", "fan-a"))
        self.assertTrue(self.duplicate(weather("china_weather_jianproject", "jian-a")))
        self.assertFalse(
            self.duplicate(
                weather(
                    "china_weather_jianproject",
                    "jian-a",
                    headline="synthetic weather details plus an update",
                )
            )
        )
        self.assertFalse(
            self.duplicate(
                weather(
                    "china_weather_jianproject", "jian-a", title="合肥市暴雨橙色预警"
                )
            )
        )

    def test_unrelated_weather_is_not_merged_by_city_alone(self):
        self.remember(weather("china_weather_fanstudio", "rain-a"))
        candidate = weather(
            "china_weather_jianproject", "wind-a", title="合肥市大风黄色预警"
        )
        self.assertFalse(self.duplicate(candidate))

    def test_delivered_match_expires_after_retention(self):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        self.now += 60 * 60 + 1
        self.assertFalse(self.duplicate(quake("jma_p2p", "p2p-a")))

    def test_same_source_same_report_business_corrections_are_preserved(self):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        for field, value in (
            ("occurred_at", ORIGIN + timedelta(seconds=20)),
            ("headline", "synthetic corrected bulletin"),
            ("place_name", "synthetic corrected location"),
            ("intensity", 4.0),
            ("scale", 45),
        ):
            with self.subTest(field=field):
                update = quake("jma_wolfx", "wolfx-a")
                setattr(update.event, field, value)
                self.assertFalse(self.duplicate(update))
        update = quake("jma_wolfx", "wolfx-a", jma_warning_areas=["synthetic new area"])
        self.assertFalse(self.duplicate(update))
        self.remember(update)
        self.assertTrue(self.duplicate(update))

    def test_source_specific_wording_does_not_create_cross_relay_revisions(self):
        first = quake("jma_wolfx", "wolfx-a", info_type="警报")
        self.remember(first)
        candidate = quake("jma_p2p", "p2p-a", info_type="警报")
        candidate.event.headline = "synthetic alternative relay wording"
        candidate.event.place_name = "synthetic translated location"
        self.assertTrue(self.duplicate(candidate))

    def test_missing_parameter_completion_is_preserved_with_stable_id(self):
        for source in ("jma_wolfx", "jma_p2p"):
            for missing in ("depth", "magnitude", "latitude", "occurred_at"):
                with self.subTest(source=source, missing=missing):
                    self.dedup = self.deduplicator_class(clock=lambda: self.now)
                    first = quake("jma_wolfx", "shared-stable-id")
                    setattr(first.event, missing, None)
                    self.remember(first)
                    complete = quake(source, "shared-stable-id")
                    self.assertFalse(self.duplicate(complete))
                    self.remember(complete)
                    self.assertTrue(self.duplicate(complete))

    def test_shared_structured_warning_area_correction_survives_other_relay(self):
        self.remember(
            quake("jma_wolfx", "wolfx-a", jma_warning_areas=["synthetic area A"])
        )
        candidate = quake("jma_p2p", "p2p-a", jma_warning_areas=["synthetic area B"])
        self.assertFalse(self.duplicate(candidate))

    def test_duplicate_relay_alias_learning_does_not_change_delivery_version_or_time(
        self,
    ):
        self.remember(quake("jma_wolfx", "wolfx-a"))
        delivered = self.dedup._records[SESSION][0]
        before = {key: value for key, value in delivered.items() if key != "aliases"}
        self.now += 10
        self.assertTrue(self.duplicate(quake("jma_p2p", "p2p-a")))
        self.assertEqual(delivered["aliases"], {"wolfx-a", "p2p-a"})
        self.assertEqual(
            {key: value for key, value in delivered.items() if key != "aliases"}, before
        )
        self.assertEqual(len(self.dedup._records[SESSION]), 1)

    def test_pending_duplicate_never_teaches_an_alias_or_delivery(self):
        first = quake("jma_wolfx", "wolfx-a")
        candidate = quake("jma_p2p", "p2p-a")
        self.assertTrue(self.duplicate(candidate, pending=(first,)))
        self.assertEqual(self.dedup._records[SESSION], [])
        cancelled = quake(
            "jma_p2p",
            "p2p-a",
            report=0,
            is_cancel=True,
            occurred_at=None,
            latitude=None,
            longitude=None,
            magnitude=None,
            depth=None,
        )
        self.assertFalse(self.duplicate(cancelled))

    def test_real_delivery_sequence_duplicate_relay_cancel_and_restore(self):
        normal = quake("jma_wolfx", "wolfx-a", report=5, is_cancel=False)
        self.remember(normal)
        # The production caller suppresses B and does not call remember(B).
        self.assertTrue(
            self.duplicate(quake("jma_p2p", "p2p-a", report=5, is_cancel=False))
        )
        cancelled = quake(
            "jma_p2p",
            "p2p-a",
            report=0,
            is_cancel=True,
            occurred_at=None,
            latitude=None,
            longitude=None,
            magnitude=None,
            depth=None,
        )
        self.assertFalse(self.duplicate(cancelled))
        self.remember(cancelled)
        self.assertEqual(len(self.dedup._records[SESSION]), 1)
        self.assertTrue(self.duplicate(cancelled))
        self.assertFalse(self.duplicate(normal))
        self.remember(normal)
        self.assertEqual(len(self.dedup._records[SESSION]), 1)
        self.assertTrue(self.duplicate(normal))

    def test_cancelled_state_is_not_cleared_by_unspecified_or_lower_old_normal(self):
        self.remember(quake("jma_wolfx", "wolfx-a", report=5, is_cancel=False))
        self.assertTrue(
            self.duplicate(quake("jma_p2p", "p2p-a", report=5, is_cancel=False))
        )
        cancelled = quake(
            "jma_p2p",
            "p2p-a",
            report=0,
            is_cancel=True,
            occurred_at=None,
            latitude=None,
            longitude=None,
            magnitude=None,
            depth=None,
        )
        self.remember(cancelled)
        self.assertTrue(self.duplicate(quake("jma_wolfx", "wolfx-a", report=5)))
        self.assertTrue(
            self.duplicate(quake("jma_wolfx", "wolfx-a", report=4, is_cancel=False))
        )
        self.assertFalse(
            self.duplicate(quake("jma_wolfx", "wolfx-a", report=5, is_cancel=False))
        )

    def test_same_source_transport_metadata_does_not_create_a_revision(self):
        self.remember(quake("jma_wolfx", "wolfx-a", timestamp_ms=1, last_update_ms=1))
        self.assertTrue(
            self.duplicate(
                quake("jma_wolfx", "wolfx-a", timestamp_ms=2, last_update_ms=2)
            )
        )

    def test_later_sparse_report_preserves_final_rank_and_highest_report(self):
        self.remember(
            quake(
                "jma_p2p_info",
                "p2p-a",
                report=5,
                final=True,
                event_type="earthquake",
                info_type="DetailScale",
            )
        )
        newer_sparse = quake(
            "jma_wolfx_info", "wolfx-a", report=6, event_type="earthquake"
        )
        self.assertFalse(self.duplicate(newer_sparse))
        self.remember(newer_sparse)
        record = self.dedup._records[SESSION][0]
        self.assertTrue(record["final"])
        self.assertEqual(record["rank"], 4)
        self.assertEqual(record["highest_report"], 6)
        self.assertTrue(
            self.duplicate(
                quake(
                    "jma_p2p_info",
                    "p2p-a",
                    report=6,
                    final=True,
                    event_type="earthquake",
                    info_type="DetailScale",
                )
            )
        )

    async def test_session_locks_are_shared_within_session_only(self):
        first = self.dedup.lock(SESSION)
        self.assertIs(first, self.dedup.lock(SESSION))
        self.assertIsNot(first, self.dedup.lock(OTHER_SESSION))
        self.assertIsInstance(first, asyncio.Lock)
        async with first:
            self.assertTrue(self.dedup.lock(SESSION).locked())
            self.assertFalse(self.dedup.lock(OTHER_SESSION).locked())


if __name__ == "__main__":
    unittest.main()
