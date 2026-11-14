"""Public/synthetic CMA regressions using the real parser and delivery dedup.

Private package skeletons avoid plugin startup. Only logger/telemetry sinks are
stubbed; parsing, identities, clocks and delivery comparison are actual modules.
No network, framework lifecycle, databases or outgoing messages are used.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1]
PREFIX = "_weather_delivery_regression"
BODY = (
    "长丰县气象台2026年11月14日19时08分发布大雾黄色预警信号。"
    "今天夜里到明天早晨我县朱巷、陶楼、岗集等镇（区）将出现能见度小于500米的大雾，"
    "局地不足200米，出行请注意交通安全。"
)
NOTE = "（预警信息来源：国家预警信息发布中心）"
CONFIG = {"event_deduplication": {"cross_source_enabled": True, "retention_minutes": 60}}
SESSION = "synthetic:GroupMessage:weather"


def load_modules():
    for suffix in ("", "core", "core.domain", "core.domain.tsunami", "core.parsers",
                   "core.services", "core.services.identity", "core.services.telemetry",
                   "core.sources", "core.message", "core.message.push", "utils"):
        name = PREFIX + ("." + suffix if suffix else "")
        package = ModuleType(name)
        package.__path__ = [str(SOURCE.joinpath(*suffix.split("."))) if suffix else str(SOURCE)]
        sys.modules[name] = package
    logger = ModuleType(PREFIX + ".utils.plugin_logger")
    logger.plugin_logger = SimpleNamespace(**{
        level: lambda *args, **kwargs: None for level in ("info", "debug", "warning", "error")
    })
    sys.modules[logger.__name__] = logger
    telemetry = ModuleType(PREFIX + ".core.services.telemetry.telemetry_utils")

    async def track_error_safely(*args, **kwargs):
        return False

    telemetry.track_error_safely = track_error_safely
    sys.modules[telemetry.__name__] = telemetry

    def load(suffix):
        name = PREFIX + "." + suffix
        spec = importlib.util.spec_from_file_location(
            name, SOURCE.joinpath(*suffix.split(".")).with_suffix(".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return SimpleNamespace(parser=load("core.parsers.weather_parser"),
                           dedup=load("core.message.push.cross_source_dedup"))


def payload(*, packaged=False, **changes):
    value = {
        "id": "34012141600000_20261114190900",
        "title": "安徽省合肥市长丰县发布大雾黄色预警" + ("信号" if packaged else ""),
        "description": BODY + (NOTE if packaged else ""),
        "headline": "" if packaged else "长丰县气象台发布大雾黄色预警[III级/较重]",
        "originTime": "2026-11-14T19:09:00" if packaged else "2026-11-14T19:08:00",
        "type": "p0005003",
    }
    value.update(changes)
    return value


class WeatherDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_modules()

    def setUp(self):
        self.dedup = self.modules.dedup.CrossSourceDeduplicator(clock=lambda: 1000)

    def parser(self, source="jian"):
        if source == "jian":
            return self.modules.parser.WeatherAlarmJianProjectParser()
        return self.modules.parser.WeatherAlarmParser()

    def event(self, data=None, *, source="jian"):
        event = self.parser(source).parse_message(payload() if data is None else data)
        self.assertIsNotNone(event)
        return event

    def duplicate(self, event, *, pending=()):
        return self.dedup.is_duplicate(SESSION, event, CONFIG, pending=pending)

    def remember(self, event):
        self.dedup.remember(SESSION, event, CONFIG)

    def test_public_same_id_packaging_versions_deliver_once_in_both_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp()
                first = self.event(payload(packaged=not reverse))
                second = self.event(payload(packaged=reverse))
                self.assertFalse(self.duplicate(first))
                self.remember(first)
                for event in (second, first, second):
                    self.assertTrue(self.duplicate(event))

    def test_jian_displays_station_clock_and_keeps_full_original_payload(self):
        data = payload(packaged=True)
        original = deepcopy(data)
        event = self.event(data)
        expected = datetime(2026, 11, 14, 11, 8, tzinfo=timezone.utc)
        self.assertEqual(event.event.effective_at, expected)
        self.assertEqual(event.identity.published_at, expected)
        self.assertEqual(event.metadata["issue_time"], expected)
        self.assertEqual(event.payload.raw, original)
        self.assertEqual(data, original)
        self.assertEqual(event.event.title, original["title"])
        self.assertEqual(event.metadata["description"], original["description"])

    def test_parser_suppresses_alternating_packaging_without_new_deliveries(self):
        for source in ("jian", "fan"):
            for reverse in (False, True):
                with self.subTest(source=source, reverse=reverse):
                    parser = self.parser(source)
                    self.assertIsNotNone(parser.parse_message(payload(packaged=reverse)))
                    for packaged in (not reverse, reverse, not reverse):
                        self.assertIsNone(parser.parse_message(payload(packaged=packaged)))

    def test_parser_and_delivery_keep_cancellation_and_explicit_restoration(self):
        for source in ("jian", "fan"):
            with self.subTest(source=source):
                self.setUp()
                parser = self.parser(source)
                active = payload(is_cancel=False, status="active")
                cancelled = payload(is_cancel=True, status="cancelled", originTime="2026-11-14T19:10:00")
                for data in (active, cancelled, active):
                    event = parser.parse_message(data)
                    self.assertIsNotNone(event)
                    self.assertFalse(self.duplicate(event))
                    self.remember(event)
                    self.assertIsNone(parser.parse_message(data))

    def test_cancellation_with_old_body_keeps_current_structured_clock(self):
        event = self.event(payload(is_cancel=True, originTime="2026-11-14T20:10:00"))
        self.assertEqual(event.event.effective_at, datetime(2026, 11, 14, 20, 10))

    def test_real_business_changes_survive_both_parser_and_delivery_dedup(self):
        changes = [
            {"description": BODY.replace("500米", "300米")},
            {"description": BODY + "新增影响地区。"},
            {"description": BODY.replace("朱巷", "吴山")},
            {"description": BODY.replace("19时08分", "19时09分"), "originTime": "2026-11-14T19:09:00"},
            {"title": "安徽省合肥市长丰县发布大雾橙色预警"},
            {"level": "橙色"}, {"type": "p0005002"},
            {"headline": "长丰县气象台补充说明：高速交通风险增大"},
            {"headline": "长丰县气象台发布大雾黄色预警[II级/严重]"},
            {"effective": "2026-11-14T19:09:00"},
            {"effectiveTime": "2026-11-14T19:10:00"},
            {"onset": "2026-11-14T19:10:00"},
            {"relieveTime": "2026-11-15T08:00:00"},
            {"expires": "2026-11-15T08:00:00"},
            {"expiry": "2026-11-15T08:00:00"},
            {"province": "安徽省"}, {"city": "合肥市"}, {"district": "长丰县"},
            {"adcode": "340121"}, {"latitude": 32.123}, {"longitude": 117.123},
            {"status": "test"}, {"is_cancel": True},
        ]
        for source in ("jian", "fan"):
            for change in changes:
                with self.subTest(source=source, change=change):
                    self.setUp()
                    parser = self.parser(source)
                    first = parser.parse_message(payload())
                    self.assertIsNotNone(first)
                    self.remember(first)
                    updated = parser.parse_message(payload(**change))
                    self.assertIsNotNone(updated)
                    self.assertFalse(self.duplicate(updated))

    def test_explicit_effective_and_expiry_changes_never_use_issue_clock_tolerance(self):
        for field in ("effective", "effectiveTime", "onset", "expires", "relieveTime", "expiry"):
            with self.subTest(field=field):
                self.setUp()
                self.remember(self.event(payload(**{field: "2026-11-14T19:09:00"})))
                self.assertFalse(self.duplicate(self.event(payload(**{field: "2026-11-14T19:10:00"}))))

    def test_unknown_source_note_is_not_stripped(self):
        self.remember(self.event())
        for note in ("（预警信息来源：其他来源）", "（预警信息来源：国家预警信息发布中心，内容更新）"):
            with self.subTest(note=note):
                self.assertFalse(self.duplicate(self.event(payload(description=BODY + note))))

    def test_unknown_headline_changes_remain_significant(self):
        self.remember(self.event())
        for headline in ("风险提示：道路交通影响增大", "长丰县气象台解除大雾黄色预警"):
            with self.subTest(headline=headline):
                self.assertFalse(self.duplicate(self.event(payload(headline=headline))))

    def test_untrusted_body_time_preserves_origin_clock_behavior(self):
        cases = [
            "转述：" + BODY,
            BODY.replace("2026年11月14日", "11月14日"),
            BODY.replace("2026年11月14日", "2026年11月31日"),
            BODY.replace("长丰县气象台", "其他县气象台"),
            BODY.replace("黄色预警", "橙色预警"),
            BODY.replace("发布", "讨论"),
            "预计今夜有大雾，请注意安全。",
        ]
        for body in cases:
            with self.subTest(body=body):
                self.setUp()
                data = payload(description=body, originTime="2026-11-14T19:09:00")
                first = self.event(data)
                self.assertEqual(first.event.effective_at, datetime(2026, 11, 14, 19, 9))
                self.remember(first)
                self.assertFalse(self.duplicate(self.event({**data, "originTime": "2026-11-14T19:10:00"})))

    def test_valid_body_with_unrelated_structured_clock_is_not_trusted(self):
        event = self.event(payload(originTime="2026-11-15T19:09:00"))
        self.assertEqual(event.event.effective_at, datetime(2026, 11, 15, 19, 9))

    def test_missing_issue_clock_does_not_enable_normalization(self):
        business = self.modules.parser._weather_business_fields(payload(originTime=""), "china_weather_jianproject")
        normalized = self.modules.parser.canonical_weather_report(business, effective_is_issued=True)
        self.assertIsNone(normalized)

    def test_different_ids_do_not_get_packaging_tolerance(self):
        self.remember(self.event(payload(packaged=True)))
        self.assertFalse(self.duplicate(self.event(payload(id="other-warning-id"))))

    def test_different_ids_keep_original_origin_clock_comparison(self):
        # Display clocks may now agree; distinct IDs still need the original
        # strict structured timestamp evidence before they can be aliased.
        first = self.event(payload(originTime="2026-11-14T19:09:00"))
        second = self.event(payload(id="other-warning-id", originTime="2026-11-14T19:08:00"))
        self.assertEqual(first.event.effective_at, second.event.effective_at)
        self.remember(first)
        self.assertFalse(self.duplicate(second))

    def test_distinct_releases_with_generic_title_and_new_body_are_not_merged(self):
        self.remember(self.event())
        self.assertFalse(self.duplicate(self.event(payload(
            id="other-warning-id", description=BODY.replace("19时08分", "19时10分"),
            originTime="2026-11-14T19:10:00",
        ))))

    def test_pending_packaging_duplicates_suppress_without_committing_delivery(self):
        first = self.event(payload(packaged=True))
        second = self.event()
        self.assertTrue(self.duplicate(second, pending=[first]))
        self.assertFalse(self.duplicate(second))
        self.assertEqual(self.dedup._records[SESSION], [])

    def test_delivery_state_is_per_session(self):
        self.remember(self.event(payload(packaged=True)))
        self.assertFalse(self.dedup.is_duplicate("synthetic:GroupMessage:other", self.event(), CONFIG))

    def test_disabled_delivery_dedup_keeps_previous_policy(self):
        disabled = {"event_deduplication": {"cross_source_enabled": False}}
        event = self.event()
        self.dedup.remember(SESSION, event, disabled)
        self.assertFalse(self.dedup.is_duplicate(SESSION, event, disabled))


if __name__ == "__main__":
    unittest.main()
