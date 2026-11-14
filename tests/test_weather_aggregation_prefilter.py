"""Exercise the real intake pipeline, native rules and weather buffers offline.

Only framework message containers/logging and external delivery boundaries are
replaced. No plugin startup, sockets, QQ accounts or real warning data are used.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


SOURCE = Path(__file__).resolve().parents[1]
PREFIX = "_weather_aggregation_prefilter_regression"
GROUP_A = "synthetic:GroupMessage:local"
GROUP_B = "synthetic:GroupMessage:other"
WEATHER_SOURCE = "china_weather_jianproject"


class Plain:
    def __init__(self, text):
        self.text = text


class Nodes:
    def __init__(self, nodes):
        self.nodes = nodes


class MessageChain:
    def __init__(self, chain):
        self.chain = chain


def load_modules():
    """Use private packages to avoid __init__ importing service startup code."""
    for suffix in (
        "", "utils", "core", "core.app", "core.app.pipeline", "core.domain",
        "core.message", "core.message.push", "core.message.presenters",
        "core.rules", "core.sources", "core.storage", "core.services",
        "core.services.identity", "core.services.geo", "core.services.snet",
    ):
        name = PREFIX + ("." + suffix if suffix else "")
        package = ModuleType(name)
        package.__path__ = [str(SOURCE.joinpath(*suffix.split("."))) if suffix else str(SOURCE)]
        sys.modules[name] = package

    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        return result

    api = module("astrbot.api", logger=Mock())
    components = module(
        "astrbot.api.message_components", Plain=Plain, Nodes=Nodes,
        Node=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    framework = {
        "astrbot": module("astrbot", api=api),
        "astrbot.api": api,
        "astrbot.api.event": module("astrbot.api.event", MessageChain=MessageChain),
        "astrbot.api.message_components": components,
    }
    with patch.dict(sys.modules, framework):
        def load(suffix):
            return importlib.import_module(PREFIX + "." + suffix)

        domain = load("core.domain.event_models")
        identity = load("core.domain.event_identity")
        payload = load("core.domain.event_payload")
        rules = load("core.rules.rule_chain")
        base_rule = load("core.rules.base_rule")
        rule_result = load("core.rules.rule_result")
        # Export the actual native classes expected by push_policy, without the
        # rules package's startup import graph.
        package = sys.modules[PREFIX + ".core.rules"]
        package.RuleContext = base_rule.RuleContext
        package.RuleDecision = rule_result.RuleDecision
        package.build_default_rule_chain = rules.build_default_rule_chain
        policy = load("core.message.push.push_policy")
        sender = load("core.message.push.session_sender")
        aggregation = load("core.message.push.weather_aggregation_service")
        pipeline = load("core.app.pipeline.event_pipeline")
    return SimpleNamespace(
        domain=domain, identity=identity, payload=payload, rules=rules,
        policy=policy, sender=sender, aggregation=aggregation, pipeline=pipeline,
    )


class WeatherAggregationPrefilterTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_modules()

    def setUp(self):
        self.global_sources = {"jian_project": {
            "enabled": True, "china_weather_alarm": True,
        }}
        self.config = {
            "push_enabled": True,
            "data_sources": copy.deepcopy(self.global_sources),
            "weather_config": {"weather_filter": {
                "enabled": True, "keywords": ["合肥"], "min_color_level": "蓝色",
            }},
            "push_frequency_control": {"weather_aggregation": {
                "enabled": True, "time_window_seconds": 900,
                "max_batch_size": 20, "fill_nodes": False, "flush_on_red": True,
                "rate_limit_enabled": True, "rate_limit_max_messages": 3,
                "rate_limit_window_seconds": 900,
            }},
        }
        self.targets = [GROUP_A]
        self.configs = {GROUP_A: self.config}
        self.decisions = []
        self.policy_states = []
        self.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        self.sender = self.modules.sender.SessionSender(self.context)
        self.normal_push = AsyncMock(return_value=True)
        self.manager = SimpleNamespace(
            evaluate_push_decision=self.evaluate,
            push_event=self.normal_push,
            # Deliberately stale, to detect accidental statistics carry-over.
            last_success_sessions=["synthetic:GroupMessage:previous-event"],
            session_sender=self.sender,
            message_build_service=SimpleNamespace(build_message_async=AsyncMock(
                side_effect=lambda event, **kwargs: MessageChain([Plain(event.event.title)]),
            )),
            _get_session_log_str=lambda session: "synthetic session",
        )
        self.statistics = SimpleNamespace(record_push=AsyncMock())
        self.web = SimpleNamespace(notify_event=AsyncMock())
        self.service = SimpleNamespace(
            message_manager=self.manager,
            session_config_manager=SimpleNamespace(
                list_target_sessions=lambda: list(self.targets),
                get_effective_config=lambda session: self.configs[session],
            ),
            statistics_manager=self.statistics,
            web_admin_server=self.web,
            context=SimpleNamespace(get_self_id=lambda: "0"),
        )
        self.pipeline = self.modules.pipeline.EventPipeline(self.service)
        self.aggregation = self.modules.aggregation.WeatherAggregationService(self.config)
        self.pipeline.set_weather_aggregation_service(self.aggregation)

    async def asyncTearDown(self):
        for timer in self.aggregation._flush_timers.values():
            timer.cancel()
        tasks = list(self.aggregation._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def evaluate(self, event, **kwargs):
        config = kwargs["runtime_config"]
        # This thin manager adapter uses the unchanged native complete chain;
        # there are no mocked allow/deny decisions.
        state = {
            "global_data_sources": self.global_sources,
            "weather_filter": config["weather_config"]["weather_filter"],
            "pushed_events": {},
        }
        before = copy.deepcopy(state)
        decision = self.modules.policy.evaluate_push_decision_with_components(
            event, policy_state=state, **kwargs,
        )
        self.decisions.append((event.id, kwargs, decision))
        self.policy_states.append((before, state))
        return decision

    def event(self, name, *, city="合肥", color="黄色", age_minutes=0,
              source=WEATHER_SOURCE):
        title = f"{city}市大雾{color}预警"
        return self.modules.domain.EventEnvelope(
            identity=self.modules.identity.EventIdentity(
                event_id="synthetic-warning-" + name, source_id=source,
                event_type="weather_alarm",
            ),
            event=self.modules.domain.WeatherEvent(
                title=title, headline=title,
                effective_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
            ),
            payload=self.modules.payload.SourcePayload(
                source_id=source, raw={"description": f"{city}市有强浓雾，请注意防范。"},
            ),
        )

    async def pending_local(self):
        event = self.event("pending")
        await self.pipeline.handle(event)
        self.assertIn(event.id, self.aggregation._buffers[GROUP_A])
        return event, self.aggregation._flush_timers[GROUP_A]

    async def drain_tasks(self):
        while self.aggregation._background_tasks:
            await asyncio.gather(*list(self.aggregation._background_tasks))

    def assert_unchanged_pending(self, event, timer):
        self.assertEqual(set(self.aggregation._buffers[GROUP_A]), {event.id})
        self.assertIs(self.aggregation._flush_timers[GROUP_A], timer)
        self.assertFalse(timer.cancelled())
        self.assertFalse(self.aggregation._background_tasks)
        self.normal_push.assert_not_awaited()
        self.context.send_message.assert_not_awaited()

    async def test_foreign_red_cannot_flush_or_replace_local_buffer_and_timer(self):
        event, timer = await self.pending_local()
        await self.pipeline.handle(self.event("foreign-red", city="武清", color="红色"))
        self.assert_unchanged_pending(event, timer)
        self.assertEqual(self.decisions[-1][2].reason, "气象关键词白名单过滤")
        self.assertEqual(self.statistics.record_push.await_count, 2)
        self.assertEqual(self.web.notify_event.await_count, 2)
        self.assertEqual(self.statistics.record_push.await_args.kwargs["pushed_sessions"], [])

    async def test_matching_red_with_existing_buffer_immediately_uses_forward_path(self):
        pending, timer = await self.pending_local()
        red = self.event("local-red", color="红色")
        await self.pipeline.handle(red)
        await self.drain_tasks()
        self.assertTrue(timer.cancelled())
        self.assertNotIn(GROUP_A, self.aggregation._buffers)
        self.assertEqual(self.context.send_message.await_count, 1)
        chain = self.context.send_message.await_args.args[1]
        self.assertIsInstance(chain.chain[0], Nodes)
        self.assertEqual(len(chain.chain[0].nodes), 3)
        self.normal_push.assert_not_awaited()
        # Both the intake prefilter and the original final review ran.
        self.assertEqual(sum(item[0] == pending.id for item in self.decisions), 2)
        self.assertEqual(sum(item[0] == red.id for item in self.decisions), 2)
        self.assertEqual(len(self.sender._weather_sent[GROUP_A]), 1)

    async def test_matching_red_without_buffer_preserves_immediate_normal_path(self):
        red = self.event("local-red", color="红色")
        await self.pipeline.handle(red)
        self.normal_push.assert_awaited_once()
        self.assertEqual(self.normal_push.await_args.kwargs["target_sessions"], [GROUP_A])
        self.assertEqual(self.aggregation._buffers.get(GROUP_A), {})
        self.assertNotIn(GROUP_A, self.aggregation._flush_timers)

    async def test_region_overrides_are_applied_independently_to_each_session(self):
        self.targets.append(GROUP_B)
        self.configs[GROUP_B] = copy.deepcopy(self.config)
        self.configs[GROUP_B]["weather_config"]["weather_filter"]["keywords"] = ["武清"]
        pending, timer = await self.pending_local()
        red = self.event("foreign-red", city="武清", color="红色")
        await self.pipeline.handle(red)
        self.assertEqual(set(self.aggregation._buffers[GROUP_A]), {pending.id})
        self.assertIs(self.aggregation._flush_timers[GROUP_A], timer)
        self.assertEqual(self.normal_push.await_count, 1)
        self.assertEqual(self.normal_push.await_args.kwargs["target_sessions"], [GROUP_B])
        self.assertFalse(self.aggregation._background_tasks)

    async def test_push_disabled_session_cannot_trigger_flush_or_evaluate_rules(self):
        pending, timer = await self.pending_local()
        evaluations = len(self.decisions)
        self.config["push_enabled"] = False
        await self.pipeline.handle(self.event("local-red-disabled", color="红色"))
        self.assert_unchanged_pending(pending, timer)
        self.assertEqual(len(self.decisions), evaluations)

    async def test_disabled_source_cannot_trigger_red_flush(self):
        pending, timer = await self.pending_local()
        self.config["data_sources"]["jian_project"]["china_weather_alarm"] = False
        await self.pipeline.handle(self.event("source-disabled", color="红色"))
        self.assert_unchanged_pending(pending, timer)
        self.assertEqual(self.decisions[-1][2].reason, "会话数据源开关关闭")

    async def test_global_source_group_gate_cannot_trigger_red_flush(self):
        pending, timer = await self.pending_local()
        self.global_sources["jian_project"]["enabled"] = False
        await self.pipeline.handle(self.event("global-source-disabled", color="红色"))
        self.assert_unchanged_pending(pending, timer)
        self.assertIn("全局配置已禁用", self.decisions[-1][2].detail)

    async def test_unregistered_source_cannot_trigger_red_flush(self):
        pending, timer = await self.pending_local()
        await self.pipeline.handle(self.event("unknown-source", color="红色", source="unregistered"))
        self.assert_unchanged_pending(pending, timer)

    async def test_stale_red_inside_aggregation_three_hour_window_is_rejected_by_native_rule(self):
        pending, timer = await self.pending_local()
        stale = self.event("stale-red", color="红色", age_minutes=120)
        self.assertTrue(self.aggregation._is_event_fresh(stale))
        await self.pipeline.handle(stale)
        self.assert_unchanged_pending(pending, timer)
        self.assertEqual(self.decisions[-1][2].reason, "事件时间过早")

    async def test_below_threshold_warning_never_enters_buffer(self):
        pending, timer = await self.pending_local()
        await self.pipeline.handle(self.event("white", color="白色"))
        self.assert_unchanged_pending(pending, timer)
        self.assertEqual(self.decisions[-1][2].reason, "气象颜色级别过滤")

    async def test_regular_nine_hundred_second_window_is_not_reset_and_still_flushes_forward(self):
        pending, timer = await self.pending_local()
        delay = timer.when() - asyncio.get_running_loop().time()
        self.assertGreater(delay, 895)
        self.assertLessEqual(delay, 900)
        second = self.event("second")
        await self.pipeline.handle(second)
        self.assertIs(self.aggregation._flush_timers[GROUP_A], timer)
        self.assertEqual(set(self.aggregation._buffers[GROUP_A]), {pending.id, second.id})
        self.context.send_message.assert_not_awaited()
        # Advance the service through its existing timer callback, without sleep.
        await self.aggregation._flush_session(GROUP_A)
        self.assertEqual(self.context.send_message.await_count, 1)
        self.assertIsInstance(self.context.send_message.await_args.args[1].chain[0], Nodes)

    async def test_native_rules_preflight_never_commits_state_or_charges_send_quota(self):
        await self.pipeline.handle(self.event("ordinary"))
        chain = self.modules.rules.build_default_rule_chain()
        self.assertEqual([rule.rule_name for rule in chain.rules], [
            "time_rule", "source_rule", "measurement_type_rule", "weather_rule",
            "typhoon_rule", "tsunami_rule", "keyword_rule", "intensity_rule",
            "report_rule", "local_rule",
        ])
        self.assertEqual(len(self.decisions), 1)
        kwargs = self.decisions[0][1]
        self.assertIs(kwargs["runtime_config"], self.config)
        self.assertEqual(kwargs["session_id"], GROUP_A)
        self.assertIs(kwargs["emit_filter_log"], False)
        self.assertIs(kwargs["commit_state"], False)
        self.assertEqual(self.policy_states[0][0], self.policy_states[0][1])
        self.assertEqual(dict(self.sender._weather_sent), {})
        self.normal_push.assert_not_awaited()

    async def test_original_send_time_review_still_rejects_changed_session_filter(self):
        await self.pending_local()
        self.config["weather_config"]["weather_filter"]["keywords"] = ["武清"]
        await self.aggregation._flush_session(GROUP_A)
        self.assertEqual(len(self.decisions), 2)
        self.assertFalse(self.decisions[-1][2].accepted)
        self.context.send_message.assert_not_awaited()
        self.assertEqual(dict(self.sender._weather_sent), {})

    async def test_disabled_aggregation_preserves_normal_entry_for_accepted_sessions(self):
        self.config["push_frequency_control"]["weather_aggregation"]["enabled"] = False
        await self.pipeline.handle(self.event("normal"))
        self.normal_push.assert_awaited_once()
        self.assertEqual(self.normal_push.await_args.kwargs["target_sessions"], [GROUP_A])
        self.assertEqual(self.aggregation._buffers, {})


if __name__ == "__main__":
    unittest.main()
