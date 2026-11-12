"""Exercise the real weather dispatch code without QQ, models or networking."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Nodes
from _source_loader import module

SESSION = "test:GroupMessage:synthetic"


def load_dispatch_modules():
    return SimpleNamespace(
        identity=module("core.domain.event_identity"),
        domain=module("core.domain.event_models"),
        sender=module("core.message.push.session_sender"),
        aggregation=module("core.message.push.weather_aggregation_service"),
        pipeline=module("core.app.pipeline.event_pipeline"),
        execution=module("core.message.push.push_execution_service"),
        rules=module("core.rules.base_rule"),
        weather_rule=module("core.rules.weather_rule"),
    )


class WeatherPipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_dispatch_modules()

    def setUp(self):
        self.now = 1000.0
        self.config = {
            "push_enabled": True,
            "push_frequency_control": {"weather_aggregation": {
                "enabled": True, "fill_nodes": False, "max_batch_size": 20,
                "rate_limit_enabled": True, "rate_limit_max_messages": 3,
                "rate_limit_window_seconds": 900,
            }},
        }
        self.targets = [SESSION]
        self.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        self.sender = self.modules.sender.SessionSender(self.context, clock=lambda: self.now)
        self.builder = AsyncMock(side_effect=lambda *args, **kwargs: MessageChain([Plain("synthetic weather")]))
        self.manager = SimpleNamespace(
            session_sender=self.sender,
            config=self.config,
            evaluate_push_decision=Mock(return_value=SimpleNamespace(accepted=True)),
            message_build_service=SimpleNamespace(build_message_async=self.builder),
            _get_session_log_str=lambda session: "synthetic session",
        )
        self.service = SimpleNamespace(
            message_manager=self.manager,
            session_config_manager=SimpleNamespace(
                get_effective_config=lambda session: self.config,
                list_target_sessions=lambda: list(self.targets),
            ),
            context=SimpleNamespace(get_self_id=lambda: "0"),
        )
        self.pipeline = self.modules.pipeline.EventPipeline(self.service)
        self.aggregation = self.modules.aggregation.WeatherAggregationService(self.config)
        # Observe deadlines without creating timers or sleeping in tests.
        self.aggregation._schedule_flush = Mock()
        self.pipeline.set_weather_aggregation_service(self.aggregation)

    @property
    def settings(self):
        return self.config["push_frequency_control"]["weather_aggregation"]

    def entries(self, count):
        result = []
        for index in range(count):
            event = self.modules.domain.EventEnvelope(
                identity=self.modules.identity.EventIdentity(
                    event_id=f"weather-{index}", source_id="weather_synthetic", event_type="weather",
                ),
                event=self.modules.domain.WeatherEvent(
                    title="合肥黄色预警", headline="synthetic",
                    effective_at=datetime.now(timezone.utc),
                ),
            )
            result.append(self.modules.aggregation.WeatherBufferEntry(event=event))
        return result

    async def flush(self, entries, mode="forward"):
        return await self.pipeline._flush_weather_buffer(SESSION, entries, self.config, mode=mode)

    def queued_ids(self):
        return set(self.aggregation._buffers.get(SESSION, {}))

    async def test_forward_packages_count_once_and_excess_is_buffered(self):
        entries = self.entries(65)
        sent, fill_deferred, failed = await self.flush(entries)
        self.assertEqual((sent, fill_deferred, failed), (60, [], []))
        self.assertEqual(self.context.send_message.await_count, 3)
        self.assertEqual(self.queued_ids(), {entry.event.id for entry in entries[60:]})
        self.assertEqual(len(self.sender._weather_sent[SESSION]), 3)
        self.assertNotIn(SESSION, self.aggregation._flush_retry_counts)
        self.assertNotIn(SESSION, self.aggregation._deferred_stall_counts)
        for call in self.context.send_message.await_args_list:
            nodes = call.args[1].chain[0]
            self.assertIsInstance(nodes, Nodes)
            self.assertEqual(len(nodes.nodes), 21)  # Header plus twenty warnings.

    async def test_forward_failed_package_is_preserved_without_spending_quota(self):
        self.settings["max_batch_size"] = 2
        self.context.send_message.side_effect = [True, RuntimeError("synthetic failure"), True]
        entries = self.entries(5)
        sent, fill_deferred, failed = await self.flush(entries)
        self.assertEqual(sent, 3)
        self.assertEqual(fill_deferred, [])
        self.assertEqual([entry.event.id for entry in failed], [entry.event.id for entry in entries[2:4]])
        self.assertEqual(len(self.sender._weather_sent[SESSION]), 2)
        self.assertEqual(self.queued_ids(), set())

    async def test_disabled_aggregation_replays_single_messages_with_same_limit(self):
        self.settings.update(enabled=False, rate_limit_max_messages=2)
        entries = self.entries(4)
        sent, fill_deferred, failed = await self.flush(entries)
        self.assertEqual((sent, fill_deferred, failed), (2, [], []))
        self.assertEqual(self.context.send_message.await_count, 2)
        self.assertEqual(self.queued_ids(), {entry.event.id for entry in entries[2:]})
        self.assertTrue(all(isinstance(call.args[1].chain[0], Plain)
                            for call in self.context.send_message.await_args_list))

    async def test_single_failure_does_not_drop_later_or_quota_deferred_entries(self):
        self.settings.update(enabled=False, rate_limit_max_messages=2)
        self.context.send_message.side_effect = [RuntimeError("synthetic failure"), True, True]
        entries = self.entries(4)
        sent, fill_deferred, failed = await self.flush(entries)
        self.assertEqual((sent, fill_deferred), (2, []))
        self.assertEqual([entry.event.id for entry in failed], [entries[0].event.id])
        self.assertEqual(self.queued_ids(), {entries[3].event.id})
        self.assertEqual(len(self.sender._weather_sent[SESSION]), 2)

    async def test_unsubscribed_or_disabled_session_is_not_built_or_sent(self):
        for disabled in ("unsubscribed", "push_disabled"):
            with self.subTest(disabled=disabled):
                self.targets[:] = [] if disabled == "unsubscribed" else [SESSION]
                self.config["push_enabled"] = disabled != "push_disabled"
                self.assertEqual(await self.flush(self.entries(1)), (0, [], []))
        self.builder.assert_not_awaited()
        self.context.send_message.assert_not_awaited()

    async def test_unsubscription_during_message_build_stops_delivery(self):
        async def unsubscribe(*args, **kwargs):
            self.targets.clear()
            return MessageChain([Plain("synthetic")])

        self.builder.side_effect = unsubscribe
        await self.flush(self.entries(2))
        self.context.send_message.assert_not_awaited()

    async def test_unsubscription_after_first_package_stops_remaining_packages(self):
        self.settings["max_batch_size"] = 1

        async def unsubscribe(*args, **kwargs):
            self.targets.clear()
            return True

        self.context.send_message.side_effect = unsubscribe
        await self.flush(self.entries(3))
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_concurrent_session_flushes_do_not_duplicate_buffered_events(self):
        entries = self.entries(21)
        self.aggregation._buffers[SESSION] = {entry.event.id: entry for entry in entries}
        await asyncio.gather(self.aggregation._flush_session(SESSION), self.aggregation._flush_session(SESSION))
        self.assertEqual(self.context.send_message.await_count, 2)
        self.assertEqual(self.queued_ids(), set())

    async def test_normal_weather_limit_queues_without_plaintext_fallback(self):
        self.settings.update(enabled=False, rate_limit_max_messages=1)
        await self.sender.send(SESSION, MessageChain([Plain("existing")]), weather_rate_limit=self.settings)
        self.context.send_message.reset_mock()
        execution = self.modules.execution.PushExecutionService(self.manager)
        event = self.entries(1)[0].event
        with patch.object(execution, "_build_plaintext_fallback_message", wraps=execution._build_plaintext_fallback_message) as fallback:
            result = await execution.execute(event, target_sessions=[SESSION])
        self.assertFalse(result["success"])
        self.assertEqual(result["push_success_count"], 0)
        self.assertEqual(self.queued_ids(), {event.id})
        self.context.send_message.assert_not_awaited()
        fallback.assert_not_called()

    async def test_limit_during_plaintext_fallback_queues_without_another_retry(self):
        execution = self.modules.execution.PushExecutionService(self.manager)
        event = self.entries(1)[0].event
        execution._send_event_message = AsyncMock(side_effect=[
            RuntimeError("rich media transfer failed"),
            self.modules.sender.WeatherRateLimitedError(60),
        ])
        result = await execution.execute(event, target_sessions=[SESSION])
        self.assertFalse(result["success"])
        self.assertEqual(execution._send_event_message.await_count, 2)
        self.assertEqual(self.queued_ids(), {event.id})
        self.context.send_message.assert_not_awaited()



if __name__ == "__main__":
    unittest.main()
