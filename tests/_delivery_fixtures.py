"""Exercise the real weather dispatch code without QQ, models or networking."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
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
                "rate_limit_enabled": False, "rate_limit_max_messages": 3,
                "rate_limit_window_seconds": 900,
            }},
        }
        self.targets = [SESSION]
        self.context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        self.sender = self.modules.sender.SessionSender(self.context)
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
