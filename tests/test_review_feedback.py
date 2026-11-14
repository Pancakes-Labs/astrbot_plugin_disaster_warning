"""Reproduce accepted lifecycle reports with real parser and presentation modules."""

import unittest
import json
from datetime import timedelta
from _source_loader import module


class LifecycleReviewFeedbackTests(unittest.TestCase):
    def test_first_global_quake_cancellation_ignores_generated_receipt_clock(self):
        parser = module("core.parsers.global_sources_parser").GlobalQuakeParser()
        raw = {"type": "earthquake", "action": "cancelled",
               "payload": {"id": "synthetic-cancel-without-origin"}}
        first = parser.parse_message(json.dumps(raw))
        second = parser.parse_message(json.dumps(raw))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertTrue(first.metadata["origin_time_generated"])
        second.event.occurred_at = first.event.occurred_at + timedelta(seconds=1)
        dedup = module("core.services.identity.event_deduplication_service").EventDeduplicationService()
        self.assertTrue(dedup.should_push_event(first))
        self.assertFalse(dedup.should_push_event(second))

    def test_full_weather_cancellation_is_visible_in_shared_context_and_text(self):
        parsers = module("core.parsers.weather_parser")
        display = module("core.services.display.service")
        presenter = module("core.message.presenters.weather_presenter").WeatherAlertPresenter
        raw = {"id": "synthetic-weather-lifecycle", "title": "合肥市大雾黄色预警",
               "description": "合成地区天气预警正文", "headline": "合成天气摘要",
               "effective": "2026-11-14T20:00:00+08:00",
               "originTime": "2026-11-14T20:00:00+08:00", "type": "p0005003"}
        for cls in (parsers.WeatherAlarmParser, parsers.WeatherAlarmJianProjectParser):
            for cancelled in (False, True):
                with self.subTest(parser=cls.__name__, cancelled=cancelled):
                    event = cls().parse_payload({**raw, "is_cancel": cancelled})
                    self.assertIsNotNone(event)
                    context = display.build_display_context(event, event.source_id)
                    text = presenter.present(context)
                    self.assertEqual("【预警已解除】" in context.title, cancelled)
                    self.assertEqual("【预警已解除】" in text, cancelled)
                    self.assertEqual(event.event.title, raw["title"])


if __name__ == "__main__":
    unittest.main()
