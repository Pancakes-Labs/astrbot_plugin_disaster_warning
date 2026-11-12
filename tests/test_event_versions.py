"""Exercise the real parser/deduplicator with synthetic reports, offline.

Private namespaces avoid plugin initialization; parser, identity, domain,
time conversion, aggregation code and framework imports stay real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest


from _source_loader import module

dedup_module = module("core.services.identity.event_deduplication_service")
weather_module = module("core.parsers.weather_parser")
aggregation_module = module("core.message.push.weather_aggregation_service")
models = module("core.domain.event_models")
identity_module = module("core.domain.event_identity")

class EarthquakeVersionTests(unittest.TestCase):
    def setUp(self):
        self.dedup = dedup_module.EventDeduplicationService()
        self.now = datetime.now(timezone.utc)

    def event(
        self,
        *,
        source="jma_wolfx",
        event_id="synthetic-event",
        report=3,
        final=False,
        metadata=None,
        domain_metadata=None,
        **fields,
    ):
        domain = models.EarthquakeEvent(
            occurred_at=self.now,
            latitude=35.0,
            longitude=140.0,
            place_name="synthetic test area",
            magnitude=5.0,
            depth=10.0,
            metadata=domain_metadata or {},
        )
        for key, value in fields.items():
            setattr(domain, key, value)
        identity = identity_module.EventIdentity(
            event_id,
            source,
            "earthquake",
            report_num=report,
            is_final=final,
        )
        return models.EventEnvelope(identity, domain, metadata=metadata or {})

    def assert_update_once(self, first, update):
        self.assertTrue(self.dedup.should_push_event(first))
        self.assertTrue(self.dedup.should_push_event(update))
        self.assertFalse(self.dedup.should_push_event(update))

    def test_identity_only_final_is_an_update_once(self):
        self.assert_update_once(self.event(), self.event(final=True))

    def test_final_can_be_on_either_metadata_layer(self):
        for field in ("metadata", "domain_metadata"):
            with self.subTest(field=field):
                self.dedup = dedup_module.EventDeduplicationService()
                self.assert_update_once(
                    self.event(), self.event(**{field: {"is_final": "true"}})
                )

    def test_cancellation_on_either_layer_is_an_update_once(self):
        for layer in ("metadata", "domain_metadata"):
            for flag in ("is_cancel", "isCancel", "cancelled"):
                with self.subTest(layer=layer, flag=flag):
                    self.dedup = dedup_module.EventDeduplicationService()
                    self.assert_update_once(
                        self.event(), self.event(**{layer: {flag: True}})
                    )

    def test_false_strings_do_not_mean_final_or_cancellation(self):
        self.assertTrue(self.dedup.should_push_event(self.event()))
        self.assertFalse(
            self.dedup.should_push_event(
                self.event(
                    metadata={"is_cancel": "false", "is_final": "false"},
                )
            )
        )

    def test_same_report_can_explicitly_restore_cancelled_state(self):
        self.assert_update_once(
            self.event(metadata={"is_cancel": True}),
            self.event(metadata={"is_cancel": False}),
        )

    def test_cancel_without_origin_or_coordinates_is_not_repeated(self):
        self.assert_update_once(
            self.event(source="global_quake", report=7),
            self.event(
                source="global_quake",
                report=0,
                occurred_at=None,
                latitude=None,
                longitude=None,
                magnitude=None,
                metadata={"is_cancel": True},
            ),
        )

    def test_report_number_must_advance_without_a_new_state(self):
        self.assertTrue(self.dedup.should_push_event(self.event(report=3)))
        self.assertFalse(self.dedup.should_push_event(self.event(report=2)))
        self.assertFalse(
            self.dedup.should_push_event(self.event(report=2, magnitude=4.0))
        )
        self.assertTrue(self.dedup.should_push_event(self.event(report=4)))
        self.assertFalse(self.dedup.should_push_event(self.event(report=4)))

    def test_late_lower_report_still_delivers_new_cancel_or_final(self):
        for change in ({"metadata": {"is_cancel": True}}, {"final": True}):
            with self.subTest(change=change):
                self.dedup = dedup_module.EventDeduplicationService()
                self.assert_update_once(
                    self.event(report=5), self.event(report=1, **change)
                )

    def test_old_active_report_cannot_restore_cancelled_state(self):
        self.assertTrue(
            self.dedup.should_push_event(
                self.event(report=5, metadata={"is_cancel": True})
            )
        )
        self.assertFalse(
            self.dedup.should_push_event(
                self.event(report=4, metadata={"is_cancel": False})
            )
        )

    def test_same_id_and_report_corrected_body_or_parameters_pass_once(self):
        for fields in (
            {"magnitude": 5.1},
            {"depth": 15.0},
            {"headline": "synthetic correction"},
            {"latitude": 35.1},
            {"domain_metadata": {"jma_warning_areas": ["synthetic new area"]}},
        ):
            with self.subTest(fields=fields):
                self.dedup = dedup_module.EventDeduplicationService()
                self.assert_update_once(self.event(), self.event(**fields))

    def test_corrected_origin_time_uses_same_stable_identity(self):
        first = self.event()
        update = self.event(occurred_at=self.now + timedelta(minutes=2))
        self.assert_update_once(first, update)

    def test_usgs_reviewed_upgrade_is_supported_on_all_relays(self):
        for source in ("usgs_fanstudio", "usgs_pancakes", "usgs_jianproject"):
            with self.subTest(source=source):
                self.dedup = dedup_module.EventDeduplicationService()
                self.assert_update_once(
                    self.event(source=source, domain_metadata={"status": "automatic"}),
                    self.event(source=source, domain_metadata={"status": "reviewed"}),
                )
                self.assertFalse(
                    self.dedup.should_push_event(
                        self.event(
                            source=source, domain_metadata={"status": "automatic"}
                        ),
                    )
                )

    def test_chinese_measurement_upgrade_with_fixed_report_one(self):
        self.assert_update_once(
            self.event(
                source="cenc_wolfx", report=1, metadata={"info_type": "自动测定"}
            ),
            self.event(
                source="cenc_wolfx", report=1, metadata={"info_type": "正式测定"}
            ),
        )

    def test_jma_information_upgrade_does_not_regress(self):
        self.assert_update_once(
            self.event(metadata={"info_type": "ScalePrompt"}),
            self.event(metadata={"info_type": "ScaleAndDestination"}),
        )
        self.assertFalse(
            self.dedup.should_push_event(
                self.event(metadata={"info_type": "ScalePrompt"})
            )
        )

    def test_transport_clock_and_receive_time_are_not_business_changes(self):
        first = self.event(metadata={"timestamp_ms": 100, "last_update_ms": 100})
        self.assertTrue(self.dedup.should_push_event(first))
        duplicate = self.event(metadata={"timestamp_ms": 200, "last_update_ms": 200})
        duplicate.received_at = self.now + timedelta(minutes=5)
        self.assertFalse(self.dedup.should_push_event(duplicate))

    def test_seed_and_snapshot_restore_include_versions_and_lifecycle(self):
        first = self.event(final=True, metadata={"is_cancel": True})
        self.assertTrue(self.dedup.seed_event(first))
        self.assertFalse(self.dedup.should_push_event(first))
        snapshot = self.dedup.snapshot_state()
        update = self.event(
            report=4, final=True, metadata={"is_cancel": True}, depth=20.0
        )
        self.assertTrue(self.dedup.should_push_event(update))
        self.dedup.restore_state(snapshot)
        self.assertTrue(self.dedup.should_push_event(update))
        self.assertFalse(self.dedup.should_push_event(update))


class WeatherParserVersionTests(unittest.TestCase):
    parser_classes = (
        weather_module.WeatherAlarmParser,
        weather_module.WeatherAlarmJianProjectParser,
    )

    @staticmethod
    def raw(**changes):
        value = {
            "id": "synthetic-weather",
            "title": "synthetic weather warning",
            "headline": "synthetic headline",
            "description": "synthetic warning body",
            "effective": "2026-11-11T12:00:00+00:00",
            "originTime": "2026-11-11T12:00:00+00:00",
            "type": "synthetic-code",
            "level": "蓝色",
            "province": "synthetic province",
            "adcode": "000000",
            "latitude": 35.0,
            "longitude": 140.0,
        }
        value.update(changes)
        return value

    def test_same_id_business_changes_pass_both_real_parsers(self):
        revisions = (
            {"description": "synthetic corrected body"},
            {"level": "红色"},
            {"effective": "2026-11-11T12:01:00+00:00"},
            {"originTime": "2026-11-11T12:01:00+00:00"},
            {"relieveTime": "2026-11-11T18:00:00+00:00"},
            {"adcode": "000001"},
            {"is_cancel": True},
        )
        for parser_class in self.parser_classes:
            for revision in revisions:
                with self.subTest(parser=parser_class.__name__, revision=revision):
                    parser = parser_class()
                    self.assertIsNotNone(parser.parse_payload(self.raw()))
                    changed = self.raw(**revision)
                    self.assertIsNotNone(parser.parse_payload(changed))
                    self.assertIsNone(parser.parse_payload(changed))

    def test_transport_fields_key_order_and_scalar_formats_do_not_create_revisions(
        self,
    ):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                parser = parser_class()
                self.assertIsNotNone(parser.parse_payload(self.raw()))
                duplicate = dict(
                    reversed(
                        list(
                            self.raw(
                                timestampMs=123,
                                retrievedAt="synthetic later receipt",
                                heartbeatTime=123,
                                latitude="35.0",
                                longitude="140.0",
                                effective="2026-11-11T20:00:00+08:00",
                                originTime="2026-11-11T20:00:00+08:00",
                            ).items()
                        )
                    )
                )
                self.assertIsNone(parser.parse_payload(duplicate))

    def test_missing_origin_clock_does_not_turn_replays_into_new_versions(self):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                parser = parser_class()
                payload = self.raw(effective="", originTime="")
                self.assertIsNotNone(parser.parse_payload(payload))
                self.assertIsNone(parser.parse_payload(payload))

    def test_code_aliases_are_normalized(self):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                parser = parser_class()
                first = self.raw()
                self.assertIsNotNone(parser.parse_payload(first))
                alias = dict(first)
                alias["weather_type"] = alias.pop("type")
                self.assertIsNone(parser.parse_payload(alias))

    def test_lifecycle_and_level_are_preserved_in_both_metadata_layers(self):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                event = parser_class().parse_payload(
                    self.raw(level="红色", is_cancel=True)
                )
                self.assertIsNotNone(event)
                for metadata in (
                    event.metadata,
                    event.event.metadata,
                    event.payload.attributes,
                ):
                    self.assertTrue(metadata["is_cancel"])
                    self.assertEqual(metadata["level"], "红色")
                    business = metadata["weather_business_fields"]
                    self.assertEqual(business["level"], "红色")
                    self.assertTrue(business["is_cancel"])
                    self.assertIn("effective", business)
                    self.assertNotIn("id", business)
                    self.assertNotIn("timestampMs", business)

    def test_realtime_update_with_changed_body_and_cancellation_pass(self):
        parser = weather_module.WeatherAlarmParser()

        def wrap(payload, action):
            return {
                "source": "synthetic",
                "type": "weather",
                "action": action,
                "timestampMs": 123,
                "payload": payload,
            }

        self.assertIsNotNone(parser.parse_payload(wrap(self.raw(), "new")))
        self.assertIsNone(parser.parse_payload(wrap(self.raw(), "update")))
        self.assertIsNotNone(
            parser.parse_payload(
                wrap(self.raw(description="synthetic correction"), "update")
            )
        )
        cancelled = parser.parse_payload(wrap(self.raw(), "cancelled"))
        self.assertIsNotNone(cancelled)
        self.assertTrue(cancelled.metadata["is_cancel"])
        self.assertIsNone(parser.parse_payload(wrap(self.raw(), "cancelled")))
        self.assertIsNone(
            parser.parse_payload(wrap(self.raw(id="other-synthetic-id"), "remove"))
        )

    def test_failed_empty_message_does_not_poison_id(self):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                parser = parser_class()
                self.assertIsNone(
                    parser.parse_payload(
                        self.raw(title="", headline="", description="")
                    )
                )
                self.assertIsNotNone(parser.parse_payload(self.raw()))

    def test_old_helper_signatures_and_window_expiry_still_work(self):
        for parser_class in self.parser_classes:
            with self.subTest(parser=parser_class.__name__):
                parser = parser_class()
                parser._remember_weather_id("synthetic-old-helper")
                self.assertTrue(parser._is_weather_duplicate("synthetic-old-helper"))
                self.assertIsNotNone(parser.parse_payload(self.raw()))
                parser._processed_weather_ids = {
                    key: value - 601
                    for key, value in parser._processed_weather_ids.items()
                }
                self.assertIsNotNone(parser.parse_payload(self.raw()))


class WeatherBufferVersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_id_new_business_version_replaces_old_buffer_entry(self):
        config = {
            "push_frequency_control": {
                "weather_aggregation": {
                    "enabled": True,
                    "time_window_seconds": 900,
                    "flush_on_red": False,
                }
            }
        }
        aggregation = aggregation_module.WeatherAggregationService(config)
        now = datetime.now(timezone.utc).isoformat()
        parser = weather_module.WeatherAlarmParser()
        first = parser.parse_payload(
            WeatherParserVersionTests.raw(effective=now, originTime=now)
        )
        changed = parser.parse_payload(
            WeatherParserVersionTests.raw(
                effective=now,
                originTime=now,
                description="synthetic newest body",
                level="红色",
            )
        )
        session = "synthetic:GroupMessage:10001"
        try:
            self.assertTrue(aggregation.should_aggregate(first, session, config))
            self.assertTrue(aggregation.should_aggregate(changed, session, config))
            self.assertEqual(len(aggregation._buffers[session]), 1)
            self.assertIs(aggregation._buffers[session][changed.id].event, changed)
        finally:
            for timer in aggregation._flush_timers.values():
                timer.cancel()


if __name__ == "__main__":
    unittest.main()
