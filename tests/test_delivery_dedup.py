"""Cross-source records are committed by real dispatch only after delivery."""

import asyncio
from datetime import datetime, timezone
import importlib.util
import sys
from types import SimpleNamespace
import unittest

from _source_loader import module as load_real_module
import _delivery_fixtures as fixtures
import test_cross_source_dedup as dedup_fixtures


SESSION = fixtures.SESSION
OTHER_SESSION = "test:GroupMessage:synthetic-other"
ORIGIN = datetime(2026, 11, 11, 10, 0, 59, tzinfo=timezone.utc)


class DeliveryDedupTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = fixtures.load_dispatch_modules()
        cls.deduplicator_class = load_real_module(
            "core.message.push.cross_source_dedup"
        ).CrossSourceDeduplicator

    def setUp(self):
        fixtures.WeatherPipelineTests.setUp(self)
        self.config["event_deduplication"] = {
            "cross_source_enabled": True, "retention_minutes": 60,
            "time_window_minutes": 1, "location_tolerance_km": 20,
            "magnitude_tolerance": 0.5,
        }
        self.dedup = self.deduplicator_class(clock=lambda: self.now)
        self.manager.cross_source_deduplicator = self.dedup

    settings = fixtures.WeatherPipelineTests.settings

    def weather(self, source, event_id):
        return self.modules.domain.EventEnvelope(
            identity=self.modules.identity.EventIdentity(
                event_id=event_id, source_id=source, event_type="weather_alarm",
            ),
            event=self.modules.domain.WeatherEvent(
                title="合肥市暴雨黄色预警", headline="synthetic weather details",
                effective_at=ORIGIN, metadata={"city": "合肥市"},
            ),
            metadata={"city": "合肥市"},
        )

    def quake(self, source, event_id):
        return self.modules.domain.EventEnvelope(
            identity=self.modules.identity.EventIdentity(
                event_id=event_id, source_id=source, event_type="earthquake_warning", report_num=1,
            ),
            event=self.modules.domain.EarthquakeEvent(
                occurred_at=ORIGIN, latitude=31.9, longitude=117.3,
                magnitude=4.5, depth=10.0, place_name="synthetic epicentre",
            ),
        )

    def entry(self, event):
        return self.modules.aggregation.WeatherBufferEntry(event=event)

    async def flush(self, events, session=SESSION):
        return await self.pipeline._flush_weather_buffer(
            session, [self.entry(event) for event in events], self.config,
        )

    async def execute(self, event, sessions=None):
        return await self.modules.execution.PushExecutionService(self.manager).execute(
            event, target_sessions=[SESSION] if sessions is None else sessions,
            session_config_getter=self.service.session_config_manager.get_effective_config,
        )

    def duplicate(self, event, session=SESSION):
        return self.dedup.is_duplicate(session, event, self.config)

    async def test_batch_weather_duplicates_commit_once_and_later_paths_skip(self):
        first = self.weather("china_weather_fanstudio", "fan-id")
        relay = self.weather("china_weather_jianproject", "jian-id")
        self.assertEqual(await self.flush([first, relay]), (1, [], []))
        self.assertEqual(self.context.send_message.await_count, 1)
        self.assertEqual(len(self.context.send_message.await_args.args[1].chain[0].nodes), 2)
        self.assertEqual(await self.flush([relay]), (0, [], []))
        self.assertFalse((await self.execute(relay))["success"])
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_aggregate_send_failure_leaves_relay_retryable(self):
        first = self.weather("china_weather_fanstudio", "fan-id")
        relay = self.weather("china_weather_jianproject", "jian-id")
        self.context.send_message.side_effect = RuntimeError("synthetic delivery failure")
        with self.assertRaises(RuntimeError):
            await self.flush([first])
        self.assertFalse(self.duplicate(relay))
        self.context.send_message.side_effect = None
        self.assertEqual(await self.flush([relay]), (1, [], []))
        self.assertTrue(self.duplicate(first))

    async def test_aggregate_filtering_is_isolated_by_session(self):
        self.targets[:] = [SESSION, OTHER_SESSION]
        first = self.weather("china_weather_fanstudio", "fan-id")
        relay = self.weather("china_weather_jianproject", "jian-id")
        self.manager.evaluate_push_decision.side_effect = lambda *args, **kwargs: SimpleNamespace(
            accepted=kwargs["session_id"] == OTHER_SESSION, reason="synthetic filter", detail="",
        )
        self.assertEqual(await self.flush([first], SESSION), (0, [], []))
        self.assertEqual(await self.flush([first], OTHER_SESSION), (1, [], []))
        self.assertFalse(self.duplicate(relay, SESSION))
        self.assertTrue(self.duplicate(relay, OTHER_SESSION))
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_ordinary_delivery_commits_and_aggregate_relay_uses_same_record(self):
        first = self.weather("china_weather_fanstudio", "fan-id")
        relay = self.weather("china_weather_jianproject", "jian-id")
        self.assertTrue((await self.execute(first))["success"])
        self.assertEqual(await self.flush([relay]), (0, [], []))
        self.assertFalse((await self.execute(relay))["success"])
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_ordinary_failure_and_failed_fallback_do_not_commit(self):
        first = self.quake("jma_wolfx", "wolfx-id")
        relay = self.quake("jma_p2p", "p2p-id")
        self.context.send_message.side_effect = RuntimeError("synthetic delivery failure")
        result = await self.execute(first)
        self.assertFalse(result["success"])
        self.assertFalse(self.duplicate(relay))
        self.context.send_message.side_effect = None
        self.assertTrue((await self.execute(relay))["success"])
        self.assertTrue(self.duplicate(first))

    async def test_ordinary_unconfirmed_timeout_does_not_claim_delivered_record(self):
        first = self.quake("jma_wolfx", "wolfx-id")
        relay = self.quake("jma_p2p", "p2p-id")
        self.context.send_message.side_effect = asyncio.TimeoutError("synthetic uncertain timeout")
        await self.execute(first)
        self.assertFalse(self.duplicate(relay))

    async def test_ordinary_group_filter_does_not_mark_another_group_as_delivered(self):
        first = self.quake("jma_wolfx", "wolfx-id")
        relay = self.quake("jma_p2p", "p2p-id")
        self.manager.evaluate_push_decision.side_effect = lambda *args, **kwargs: SimpleNamespace(
            accepted=kwargs["session_id"] == OTHER_SESSION, reason="synthetic filter", detail="",
        )
        result = await self.execute(first, [SESSION, OTHER_SESSION])
        self.assertEqual(result["passed_sessions"], [OTHER_SESSION])
        self.assertFalse(self.duplicate(relay, SESSION))
        self.assertTrue(self.duplicate(relay, OTHER_SESSION))
        self.assertEqual(self.context.send_message.await_count, 1)

    async def test_concurrent_ordinary_relays_deliver_only_once(self):
        first = self.quake("jma_wolfx", "wolfx-id")
        relay = self.quake("jma_p2p", "p2p-id")
        results = await asyncio.gather(self.execute(first), self.execute(relay))
        self.assertEqual(sum(result["push_success_count"] for result in results), 1)
        self.assertEqual(self.context.send_message.await_count, 1)


    async def test_simulation_does_not_consume_real_delivery_record(self):
        simulation = self.quake("jma_wolfx", "wolfx-id")
        simulation.metadata["simulation"] = True
        self.assertTrue((await self.execute(simulation))["success"])
        self.assertFalse(self.duplicate(self.quake("jma_p2p", "p2p-id")))

    async def test_noncommitting_send_does_not_consume_real_delivery_record(self):
        first = self.quake("jma_wolfx", "wolfx-id")
        result = await self.modules.execution.PushExecutionService(self.manager).execute(
            first, target_sessions=[SESSION],
            session_config_getter=self.service.session_config_manager.get_effective_config,
            commit_state=False,
        )
        self.assertTrue(result["success"])
        self.assertFalse(self.duplicate(self.quake("jma_p2p", "p2p-id")))

    async def test_empty_chain_in_mixed_batch_is_never_marked_delivered(self):
        empty = self.weather("china_weather_fanstudio", "empty-id")
        sent = self.weather("china_weather_fanstudio", "sent-id")
        sent.event.title = "合肥市大雾黄色预警"
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain
        self.builder.side_effect = lambda event, **kwargs: MessageChain(
            [] if event.id == empty.id else [Plain("synthetic delivered warning")]
        )
        self.assertEqual(await self.flush([empty, sent]), (1, [], []))
        self.assertFalse(self.duplicate(empty))
        self.assertTrue(self.duplicate(sent))
        self.assertEqual(self.context.send_message.await_count, 1)


if __name__ == "__main__":
    unittest.main()
