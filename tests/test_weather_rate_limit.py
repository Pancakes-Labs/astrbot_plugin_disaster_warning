"""Weather delivery quotas against real plugin modules and synthetic IO.

The module under test has standard-library dependencies only. Fake Contexts
acknowledge, reject, suspend or cancel in-memory sends; no QQ/API is contacted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


from _source_loader import module

sender_module = module("core.message.push.session_sender")
SessionSender = sender_module.SessionSender
SessionSendFailedError = sender_module.SessionSendFailedError
WeatherRateLimitedError = sender_module.WeatherRateLimitedError
GROUP_A = "synthetic:GroupMessage:10001"
GROUP_B = "synthetic:GroupMessage:10002"
aggregation_module = module("core.message.push.weather_aggregation_service")
event_models = module("core.domain.event_models")
event_identity = module("core.domain.event_identity")

class FakeClock:
    def __init__(self, value=1000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeContext:
    def __init__(self, behavior=None):
        self.behavior = behavior
        self.attempts = []
        self.delivered = []

    async def send_message(self, session, message):
        self.attempts.append((session, message))
        # Yield inside delivery to expose quota checks made without a lock.
        await asyncio.sleep(0)
        result = True if self.behavior is None else self.behavior(session, message)
        if inspect.isawaitable(result):
            result = await result
        if result is not False:
            self.delivered.append((session, message))
        return result


class WeatherSenderRateLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.context = FakeContext()
        self.sender = SessionSender(self.context, clock=self.clock)

    @staticmethod
    def policy(**changes):
        result = {
            "rate_limit_enabled": True,
            "rate_limit_max_messages": 3,
            "rate_limit_window_seconds": 900,
        }
        result.update(changes)
        return result

    async def weather(self, message, session=GROUP_A, **policy_changes):
        return await self.sender.send(session, message, weather_rate_limit=self.policy(**policy_changes))

    async def fill_quota(self, session=GROUP_A):
        for index in range(3):
            await self.weather(f"synthetic weather {index}", session)

    async def test_first_three_successful_weather_messages_are_delivered(self):
        await self.fill_quota()
        self.assertEqual(len(self.context.delivered), 3)
        self.assertEqual(self.context.attempts, self.context.delivered)

    async def test_fourth_is_rejected_before_delivery_with_remaining_wait(self):
        await self.weather("first")
        self.clock.advance(10)
        await self.weather("second")
        self.clock.advance(10)
        await self.weather("third")
        self.clock.advance(80)
        with self.assertRaises(WeatherRateLimitedError) as caught:
            await self.weather("must not reach Context")
        self.assertAlmostEqual(caught.exception.retry_after, 800.0)
        self.assertEqual(len(self.context.attempts), 3)

    async def test_sliding_window_expires_individual_successes_at_boundary(self):
        await self.weather("at 1000")
        self.clock.advance(100)
        await self.weather("at 1100")
        self.clock.advance(100)
        await self.weather("at 1200")
        self.clock.advance(100)
        with self.assertRaises(WeatherRateLimitedError) as caught:
            await self.weather("blocked at 1300")
        self.assertAlmostEqual(caught.exception.retry_after, 600.0)
        self.clock.advance(600)  # Exactly one successful message expires.
        await self.weather("allowed at 1900")
        with self.assertRaises(WeatherRateLimitedError) as caught:
            await self.weather("only one slot expired")
        self.assertAlmostEqual(caught.exception.retry_after, 100.0)
        self.clock.advance(100)
        await self.weather("allowed at 2000")
        self.assertEqual(len(self.context.delivered), 5)

    async def test_two_groups_have_independent_quotas(self):
        await self.fill_quota(GROUP_A)
        await self.fill_quota(GROUP_B)
        for session in (GROUP_A, GROUP_B):
            with self.assertRaises(WeatherRateLimitedError):
                await self.weather("fourth", session)
        self.assertEqual([session for session, _ in self.context.delivered].count(GROUP_A), 3)
        self.assertEqual([session for session, _ in self.context.delivered].count(GROUP_B), 3)

    async def test_explicit_false_delivery_does_not_consume_a_slot(self):
        results = iter((False, True))
        self.context.behavior = lambda session, message: next(results)
        with self.assertRaises(SessionSendFailedError):
            await self.weather("rejected by Context", rate_limit_max_messages=1)
        await self.weather("accepted after failure", rate_limit_max_messages=1)
        with self.assertRaises(WeatherRateLimitedError):
            await self.weather("quota now full", rate_limit_max_messages=1)
        self.assertEqual(len(self.context.attempts), 2)
        self.assertEqual(len(self.context.delivered), 1)

    async def test_raised_delivery_error_does_not_consume_a_slot(self):
        failure = OSError("synthetic transport failure")

        def delivery(session, message):
            if message == "failed":
                raise failure
            return True

        self.context.behavior = delivery
        with self.assertRaises(OSError) as caught:
            await self.weather("failed", rate_limit_max_messages=1)
        self.assertIs(caught.exception, failure)
        await self.weather("accepted", rate_limit_max_messages=1)
        with self.assertRaises(WeatherRateLimitedError):
            await self.weather("full", rate_limit_max_messages=1)
        self.assertEqual(len(self.context.delivered), 1)

    async def test_cancellation_does_not_charge_quota_or_leave_group_locked(self):
        started = asyncio.Event()
        never_finish = asyncio.Event()

        async def delivery(session, message):
            if message == "cancel while sending":
                started.set()
                await never_finish.wait()
            return True

        self.context.behavior = delivery
        task = asyncio.create_task(self.weather("cancel while sending", rate_limit_max_messages=1))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(self.weather("accepted after cancellation", rate_limit_max_messages=1), timeout=1)
            with self.assertRaises(WeatherRateLimitedError):
                await self.weather("full", rate_limit_max_messages=1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(self.context.delivered), 1)

    async def test_concurrent_same_group_sends_never_exceed_three(self):
        results = await asyncio.gather(
            *(self.weather(f"concurrent {index}") for index in range(12)),
            return_exceptions=True,
        )
        rejected = [result for result in results if isinstance(result, WeatherRateLimitedError)]
        unexpected = [result for result in results if isinstance(result, BaseException) and not isinstance(result, WeatherRateLimitedError)]
        self.assertEqual(unexpected, [])
        self.assertEqual(len(rejected), 9)
        self.assertTrue(all(error.retry_after == 900 for error in rejected))
        self.assertEqual(len(self.context.attempts), 3)
        self.assertEqual(len(self.context.delivered), 3)

    async def test_concurrent_failed_deliveries_leave_three_success_slots(self):
        def delivery(session, message):
            if message == 0:
                return False
            if message in (2, 4):
                raise OSError("synthetic concurrent transport failure")
            return True

        self.context.behavior = delivery
        results = await asyncio.gather(*(self.weather(index) for index in range(12)), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, WeatherRateLimitedError) for result in results), 6)
        self.assertEqual(sum(isinstance(result, SessionSendFailedError) for result in results), 1)
        self.assertEqual(sum(isinstance(result, OSError) for result in results), 2)
        self.assertEqual(sum(not isinstance(result, BaseException) for result in results), 3)
        self.assertEqual(len(self.context.attempts), 6)
        self.assertEqual(len(self.context.delivered), 3)

    async def test_slow_group_does_not_block_a_different_group(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def delivery(session, message):
            if session == GROUP_A:
                started.set()
                await release.wait()
            return True

        self.context.behavior = delivery
        blocked = asyncio.create_task(self.weather("slow group", GROUP_A))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.wait_for(self.weather("independent group", GROUP_B), timeout=1)
            self.assertFalse(blocked.done())
            self.assertEqual(self.context.delivered, [(GROUP_B, "independent group")])
        finally:
            release.set()
            await asyncio.gather(blocked, return_exceptions=True)
        self.assertEqual(len(self.context.delivered), 2)

    async def test_disabled_weather_limit_allows_a_burst(self):
        await asyncio.gather(*(self.weather(f"disabled {index}", rate_limit_enabled=False) for index in range(12)))
        self.assertEqual(len(self.context.delivered), 12)

    async def test_ordinary_earthquake_messages_ignore_full_weather_quota(self):
        await self.fill_quota()
        for index in range(6):
            await self.sender.send(GROUP_A, f"synthetic ordinary earthquake {index}")
        with self.assertRaises(WeatherRateLimitedError):
            await self.weather("ordinary messages must not reset the weather quota")
        self.assertEqual(len(self.context.delivered), 9)

    async def test_unlimited_messages_do_not_spend_future_weather_allowance(self):
        for index in range(4):
            await self.sender.send(GROUP_A, f"ordinary before weather {index}")
            await self.weather(f"disabled before weather {index}", rate_limit_enabled=False)
        await self.fill_quota()
        with self.assertRaises(WeatherRateLimitedError):
            await self.weather("fourth enabled weather")
        self.assertEqual(len(self.context.delivered), 11)


class WeatherAggregationQuotaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.config = {"push_frequency_control": {"weather_aggregation": {
            "enabled": True, "fill_nodes": True, "max_batch_size": 20,
            "time_window_seconds": 900,
        }}}
        # Patch this module's time binding, not global time.monotonic (the
        # unittest event loop must retain its actual scheduling clock).
        clock_patch = patch.object(aggregation_module, "time", SimpleNamespace(monotonic=self.clock))
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.service = aggregation_module.WeatherAggregationService(self.config)
        self.addAsyncCleanup(self.stop_synthetic_tasks)

    async def stop_synthetic_tasks(self):
        for timer in self.service._flush_timers.values():
            timer.cancel()
        self.service._flush_timers.clear()
        tasks = list(self.service._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def entry(event_id="synthetic-warning", received_at=1.0, title="合成黄色预警"):
        event = event_models.EventEnvelope(
            identity=event_identity.EventIdentity(event_id=event_id, source_id="synthetic_weather", event_type="weather_alarm"),
            event=event_models.WeatherEvent(title=title, headline="离线测试，无真实灾害", effective_at=datetime.now(timezone.utc)),
        )
        return aggregation_module.WeatherBufferEntry(event=event, color_level=2, color_name="黄色", title=title, received_at=received_at)

    def assert_timer_waits(self, session, seconds):
        delay = self.service._flush_timers[session].when() - asyncio.get_running_loop().time()
        self.assertGreaterEqual(delay, seconds - 0.1)
        self.assertLessEqual(delay, seconds + 0.1)

    async def test_quota_deferral_preserves_existing_failure_and_fill_counters(self):
        entry = self.entry()
        self.service._flush_retry_counts[GROUP_A] = 2
        self.service._deferred_stall_counts[GROUP_A] = 4
        self.service.defer_rate_limited_entries(GROUP_A, [entry], 600)
        self.assertIs(self.service._buffers[GROUP_A][entry.event.id], entry)
        self.assertEqual(self.service._flush_retry_counts[GROUP_A], 2)
        self.assertEqual(self.service._deferred_stall_counts[GROUP_A], 4)
        self.assertFalse(self.service.should_force_send_all(GROUP_A))
        self.assert_timer_waits(GROUP_A, 600)

    async def test_more_than_five_quota_deferrals_never_become_fill_node_force(self):
        entry = self.entry()
        for _ in range(8):
            self.service.defer_rate_limited_entries(GROUP_A, [entry], 600)
        self.assertNotIn(GROUP_A, self.service._flush_retry_counts)
        self.assertNotIn(GROUP_A, self.service._deferred_stall_counts)
        self.assertFalse(self.service.should_force_send_all(GROUP_A))
        self.assertEqual(len(self.service._buffers[GROUP_A]), 1)
        self.assertEqual(self.service._background_tasks, set())

    async def test_quota_gate_blocks_forced_and_shutdown_flush_until_expiry(self):
        entry = self.entry()
        callback = AsyncMock(return_value=(1, [], []))
        self.service.set_flush_callback(callback)
        self.service.defer_rate_limited_entries(GROUP_A, [entry], 300)
        self.service._force_send_all_sessions.add(GROUP_A)
        self.service._in_flush_batch = True
        await self.service._flush_session(GROUP_A)
        self.clock.advance(299)
        await self.service._flush_session(GROUP_A)
        callback.assert_not_awaited()
        self.assertIs(self.service._buffers[GROUP_A][entry.event.id], entry)
        self.assertNotIn(GROUP_A, self.service._flush_retry_counts)
        self.assertNotIn(GROUP_A, self.service._deferred_stall_counts)
        self.assert_timer_waits(GROUP_A, 1)
        self.clock.advance(1)
        await self.service._flush_session(GROUP_A)
        callback.assert_awaited_once_with(GROUP_A, [entry], self.config, mode="forward")
        self.assertNotIn(GROUP_A, self.service._buffers)
        self.assertNotIn(GROUP_A, self.service._rate_limit_deadlines)

    async def test_one_groups_deadline_does_not_block_another_groups_flush(self):
        a, b = self.entry("group-a"), self.entry("group-b")
        callback = AsyncMock(return_value=(1, [], []))
        self.service.set_flush_callback(callback)
        self.service.defer_rate_limited_entries(GROUP_A, [a], 900)
        self.service.defer_rate_limited_entries(GROUP_B, [b], 10)
        self.clock.advance(10)
        await asyncio.gather(self.service._flush_session(GROUP_A), self.service._flush_session(GROUP_B))
        callback.assert_awaited_once_with(GROUP_B, [b], self.config, mode="forward")
        self.assertIs(self.service._buffers[GROUP_A][a.event.id], a)
        self.assertNotIn(GROUP_B, self.service._buffers)

    async def test_concurrent_flushes_cannot_send_one_buffer_twice(self):
        entry = self.entry()
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def callback(session, entries, config, *, mode):
            calls.append((session, list(entries)))
            started.set()
            await release.wait()
            return len(entries), [], []

        self.service.set_flush_callback(callback)
        self.service._buffers[GROUP_A] = {entry.event.id: entry}
        tasks = [asyncio.create_task(self.service._flush_session(GROUP_A)) for _ in range(3)]
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(calls, [(GROUP_A, [entry])])
        finally:
            release.set()
            await asyncio.gather(*tasks)
        self.assertEqual(calls, [(GROUP_A, [entry])])

    async def test_newer_same_id_replaces_older_but_old_recovery_never_overwrites_new(self):
        old = self.entry(received_at=100, title="旧报黄色预警")
        new = self.entry(received_at=200, title="新报黄色预警")
        self.service.defer_rate_limited_entries(GROUP_A, [old], 600)
        self.service.defer_rate_limited_entries(GROUP_A, [new], 600)
        self.assertIs(self.service._buffers[GROUP_A][old.event.id], new)
        self.service.defer_rate_limited_entries(GROUP_A, [old], 600)
        self.assertIs(self.service._buffers[GROUP_A][old.event.id], new)

    async def test_normal_quota_deferral_keeps_new_report_for_an_existing_id(self):
        old = self.entry(received_at=1, title="旧报黄色预警")
        new = self.entry(received_at=2, title="新报黄色预警")
        self.service._buffers[GROUP_A] = {old.event.id: old}
        self.service.defer_rate_limited(new.event, GROUP_A, self.config, 600)
        buffered = self.service._buffers[GROUP_A][old.event.id]
        self.assertIs(buffered.event, new.event)
        self.assertEqual((buffered.color_level, buffered.color_name), (2, "黄色"))

    async def test_real_sender_quota_requeues_then_delivers_once_window_recovers(self):
        context = FakeContext()
        sender = SessionSender(context, clock=self.clock)
        policy = WeatherSenderRateLimitTests.policy(rate_limit_max_messages=1)
        await sender.send(GROUP_A, "already delivered weather", weather_rate_limit=policy)
        entry = self.entry()
        self.service._buffers[GROUP_A] = {entry.event.id: entry}
        attempts = []

        async def callback(session, entries, config, *, mode):
            attempts.append(list(entries))
            try:
                await sender.send(session, "synthetic aggregated forward", weather_rate_limit=policy)
            except WeatherRateLimitedError as exc:
                self.service.defer_rate_limited_entries(session, entries, exc.retry_after)
                return 0, [], []
            return len(entries), [], []

        self.service.set_flush_callback(callback)
        await self.service._flush_session(GROUP_A)
        for _ in range(7):
            await self.service._flush_session(GROUP_A)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(context.delivered), 1)
        self.assertNotIn(GROUP_A, self.service._flush_retry_counts)
        self.assertNotIn(GROUP_A, self.service._deferred_stall_counts)
        self.assertFalse(self.service.should_force_send_all(GROUP_A))
        self.clock.advance(900)
        await self.service._flush_session(GROUP_A)
        self.assertEqual(attempts, [[entry], [entry]])
        self.assertEqual(len(context.delivered), 2)
        self.assertNotIn(GROUP_A, self.service._buffers)


if __name__ == "__main__":
    unittest.main()
