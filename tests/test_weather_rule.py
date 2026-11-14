"""Offline weather-region regressions against the actual rule/domain modules.

Only synthetic/public warning text is used. Package skeletons avoid executing
the plugin startup and the rules package's service imports.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1]
PREFIX = "_weather_filter_regression"


def load_modules():
    """Load real classes without plugin main, service initialization or IO."""
    for suffix in ("", "core", "core.domain", "core.rules", "utils"):
        name = PREFIX + ("." + suffix if suffix else "")
        package = ModuleType(name)
        package.__path__ = [
            str(SOURCE.joinpath(*suffix.split("."))) if suffix else str(SOURCE)
        ]
        sys.modules[name] = package

    def load(suffix, path=None):
        name = PREFIX + "." + suffix
        target = path or SOURCE.joinpath(*suffix.split(".")).with_suffix(".py")
        spec = importlib.util.spec_from_file_location(name, target)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    identity = load("core.domain.event_identity")
    payload = load("core.domain.event_payload")
    domain = load("core.domain.event_models")
    context = load("core.rules.base_rule")
    load("core.rules.rule_result")
    rule = load("core.rules.weather_rule")
    return SimpleNamespace(
        identity=identity,
        payload=payload,
        domain=domain,
        context=context,
        rule=rule,
        load=load,
    )


class WeatherRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_modules()

    def context(
        self,
        *,
        title="安徽省发布大雾黄色预警",
        headline="安徽省气象台发布预警",
        domain_metadata=None,
        metadata=None,
        raw=None,
        weather_filter=None,
    ):
        modules = self.modules
        event = modules.domain.WeatherEvent(
            title=title,
            headline=headline,
            metadata={} if domain_metadata is None else domain_metadata,
        )
        envelope = modules.domain.EventEnvelope(
            identity=modules.identity.EventIdentity(
                event_id="public-synthetic-warning",
                source_id="jian",
                event_type="weather",
            ),
            event=event,
            payload=modules.payload.SourcePayload(
                source_id="jian",
                raw={} if raw is None else raw,
            ),
            metadata={} if metadata is None else metadata,
        )
        policy = (
            {"enabled": True, "keywords": ["合肥"], "min_color_level": "蓝色"}
            if weather_filter is None
            else weather_filter
        )
        return modules.context.RuleContext(
            event=envelope,
            runtime_config={},
            policy_state={"weather_filter": policy},
            session_id="synthetic:GroupMessage:test",
        )

    def evaluate(self, **kwargs):
        return self.modules.rule.WeatherRule().evaluate(self.context(**kwargs))

    def test_province_summary_does_not_hide_real_hefei_warning_body(self):
        decision = self.evaluate(
            title="安徽省发布大雾黄色预警",
            headline="安徽省气象台发布大雾黄色预警",
            metadata={"headline": "安徽省气象台发布大雾黄色预警"},
            raw={
                "title": "安徽省发布大雾黄色预警",
                "headline": "安徽省气象台发布大雾黄色预警",
                "description": (
                    "安徽省气象台2026年11月13日20时10分发布大雾黄色预警。"
                    "预计今天夜里到明天上午，淮河以南大部分地区将出现能见度小于500米的浓雾，"
                    "其中滁州、合肥、淮南、六安、马鞍山、芜湖、铜陵、安庆、池州、宣城、"
                    "黄山市等地部分地区有能见度小于200米的强浓雾。"
                ),
            },
        )
        self.assertTrue(decision.accepted)

    def test_other_anhui_city_is_not_admitted_by_province_title(self):
        decision = self.evaluate(
            title="安徽省芜湖市发布大雾黄色预警",
            headline="安徽省气象台发布大雾黄色预警",
            raw={
                "description": "预计芜湖市部分地区出现强浓雾，请注意防范。",
                "province": "安徽省",
                "city": "芜湖市",
            },
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "气象关键词白名单过滤")

    def test_all_text_fields_are_checked_without_fallback_shadowing(self):
        fields = [("domain", key) for key in ("title", "headline")]
        fields += [
            (source, key)
            for source in ("domain_metadata", "metadata", "raw")
            for key in ("title", "headline", "description")
        ]
        for source, key in fields:
            with self.subTest(source=source, field=key):
                kwargs = {
                    "title": "安徽省发布大雾黄色预警",
                    "headline": "安徽省气象台摘要",
                    "domain_metadata": {
                        "title": "省级预警标题",
                        "headline": "省级摘要",
                        "description": "省级正文",
                    },
                    "metadata": {
                        "title": "安徽省预警",
                        "headline": "安徽省摘要",
                        "description": "安徽省正文",
                    },
                    "raw": {
                        "title": "省级大雾预警",
                        "headline": "非空摘要",
                        "description": "非空正文",
                    },
                }
                text = (
                    "合肥市大雾黄色预警" if key == "title" else "受影响区域包括合肥市"
                )
                if source == "domain":
                    kwargs[key] = text
                else:
                    kwargs[source][key] = text
                self.assertTrue(self.evaluate(**kwargs).accepted)

    def test_non_string_text_fields_are_ignored_without_false_keyword_hits(self):
        for value in (None, 123, False, ["合肥"], {"area": "合肥"}):
            with self.subTest(value=value):
                decision = self.evaluate(
                    headline=value,
                    domain_metadata={
                        "title": value,
                        "headline": value,
                        "description": value,
                    },
                    metadata={"title": value, "headline": value, "description": value},
                    raw={"title": value, "headline": value, "description": value},
                )
                self.assertFalse(decision.accepted)
                self.assertEqual(decision.reason, "气象关键词白名单过滤")

    def test_non_string_title_can_fall_back_to_valid_headline_for_color(self):
        for value in (None, 123, False, ["黄色"], {"color": "黄色"}):
            with self.subTest(value=value):
                self.assertTrue(
                    self.evaluate(title=value, headline="合肥市大雾黄色预警").accepted
                )

    def test_malformed_metadata_does_not_hide_valid_payload_body(self):
        for value in (None, [], 123, "合肥"):
            with self.subTest(value=value):
                self.assertTrue(
                    self.evaluate(
                        domain_metadata=value,
                        metadata=value,
                        raw={"description": "预警覆盖合肥市"},
                    ).accepted
                )

    def test_fields_cannot_join_to_create_city_keyword(self):
        decision = self.evaluate(
            headline="本次预警涉及合",
            raw={"description": "肥东县暂无新增信号"},
        )
        self.assertFalse(decision.accepted)

    def test_blue_threshold_preserves_full_color_order(self):
        for color, expected in (
            ("白色", False),
            ("蓝色", True),
            ("黄色", True),
            ("橙色", True),
            ("红色", True),
        ):
            with self.subTest(color=color):
                decision = self.evaluate(title=f"合肥市大雾{color}预警")
                self.assertEqual(decision.accepted, expected)
                if not expected:
                    self.assertEqual(decision.reason, "气象颜色级别过滤")

    def test_body_color_does_not_override_current_title_color(self):
        decision = self.evaluate(
            title="合肥市大雾蓝色预警",
            raw={"description": "此前发布过红色预警，现调整为蓝色。"},
            weather_filter={
                "enabled": True,
                "keywords": ["合肥"],
                "min_color_level": "黄色",
            },
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.context["detected_color"], "蓝色")

    def test_disabled_filter_does_not_filter_other_city_or_white(self):
        self.assertTrue(
            self.evaluate(
                title="南京市大雾白色预警",
                weather_filter={
                    "enabled": False,
                    "keywords": ["合肥"],
                    "min_color_level": "蓝色",
                },
            ).accepted
        )

    def test_missing_filter_preserves_unconfigured_behavior(self):
        self.assertTrue(
            self.evaluate(
                title="南京市大雾白色预警",
                weather_filter={},
            ).accepted
        )

    def test_empty_keywords_apply_only_color_threshold(self):
        self.assertTrue(
            self.evaluate(
                title="南京市大雾黄色预警",
                weather_filter={
                    "enabled": True,
                    "keywords": [],
                    "min_color_level": "蓝色",
                },
            ).accepted
        )

    def test_legacy_province_keywords_remain_supported(self):
        cases = [
            ({"keywords": [], "provinces": ["安徽"]}, {"province": "安徽省"}),
            ({"keywords": ["合肥"], "provinces": ["安徽"]}, {"province": "安徽省"}),
            ({"keywords": [" ", "合肥", "合肥"], "provinces": []}, {"city": "合肥市"}),
        ]
        for policy, metadata in cases:
            with self.subTest(policy=policy):
                self.assertTrue(
                    self.evaluate(
                        title="肥东县大雾黄色预警",
                        metadata=metadata,
                        weather_filter={
                            "enabled": True,
                            "min_color_level": "蓝色",
                            **policy,
                        },
                    ).accepted
                )

    def test_structured_city_and_district_still_match(self):
        for source in ("metadata", "raw"):
            with self.subTest(source=source):
                self.assertTrue(
                    self.evaluate(
                        title="肥东县大雾黄色预警",
                        **{
                            source: {
                                "province": "安徽省",
                                "city": "合肥市",
                                "district": "肥东县",
                            }
                        },
                    ).accepted
                )

    def test_explicit_region_overrides_incidental_text_mentions(self):
        for source in ("metadata", "raw"):
            for field in ("city", "district"):
                for text_field in ("title", "headline", "description"):
                    with self.subTest(source=source, field=field, text=text_field):
                        kwargs = {source: {field: "芜湖市"}}
                        if text_field == "description":
                            kwargs.setdefault("raw", {})[text_field] = (
                                "合肥气象部门提醒注意防范"
                            )
                        else:
                            kwargs[text_field] = "芜湖大雾黄色预警，合肥部门提供参考"
                        decision = self.evaluate(**kwargs)
                        self.assertFalse(decision.accepted)
                        self.assertTrue(
                            decision.context["structured_region_authoritative"]
                        )
                        kwargs[source][field] = "合肥市"
                        self.assertTrue(self.evaluate(**kwargs).accepted)

    def test_province_only_or_invalid_city_keeps_body_fallback(self):
        for source in ("metadata", "raw"):
            for city in (None, "", "   ", 123, False, ["芜湖"], {"name": "芜湖"}):
                with self.subTest(source=source, city=city):
                    kwargs = {source: {"province": "安徽省", "city": city}}
                    kwargs.setdefault("raw", {})["description"] = "受影响地区包括合肥市"
                    self.assertTrue(self.evaluate(**kwargs).accepted)
                    kwargs["raw"]["description"] = "受影响地区包括芜湖市"
                    self.assertFalse(self.evaluate(**kwargs).accepted)

    def test_parser_stringified_malformed_regions_do_not_disable_fallback(self):
        for field in ("city", "district"):
            for value in (123, True, ["芜湖"], {"name": "芜湖"}):
                with self.subTest(field=field, value=value):
                    kwargs = {
                        "metadata": {field: str(value)},
                        "raw": {field: value, "description": "受影响地区包括合肥市"},
                    }
                    self.assertTrue(self.evaluate(**kwargs).accepted)
                    kwargs["raw"]["description"] = "受影响地区包括芜湖市"
                    self.assertFalse(self.evaluate(**kwargs).accepted)

    def test_valid_payload_region_survives_invalid_metadata(self):
        decision = self.evaluate(
            metadata={"city": ["合肥"]},
            raw={"city": "芜湖市", "description": "合肥气象部门提供参考"},
        )
        self.assertFalse(decision.accepted)
        self.assertTrue(decision.context["structured_region_authoritative"])

    def test_other_event_type_still_skips_weather_rule(self):
        context = self.context()
        context.event.event = self.modules.domain.TsunamiEvent(
            title="synthetic", level="test"
        )
        self.assertTrue(self.modules.rule.WeatherRule().evaluate(context).accepted)


if __name__ == "__main__":
    unittest.main()
