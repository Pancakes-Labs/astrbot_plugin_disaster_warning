"""Real GQ parsing, manager rules and local presentation, without any sends.

Run with run_checks.py so AstrBot's own import-time state lives in its isolated
temporary root. Only the card browser/cache callbacks are synthetic.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


from _source_loader import SOURCE, module

REAL = SimpleNamespace(
    parser=module("core.parsers.global_sources_parser").GlobalQuakeParser,
    factory=module("core.message.runtime.runtime_component_factory").MessageRuntimeComponentFactory,
    manager=module("core.message.message_manager").MessagePushManager,
    context=module("core.message.presenters.global_quake_display_context").GlobalQuakeDisplayContextBuilder,
    present=module("core.message.presenters.presenter_registry").present_message,
    card=module("core.message.builders.global_quake_card_builder").GlobalQuakeCardBuilder,
    catalog=module("core.sources.source_catalog").SOURCE_CATALOG,
    monitor=module("core.message.runtime.local_monitor").LocalMonitor,
)

def local_config(**changes):
    config = {
        "enabled": True,
        "latitude": 31.8383,
        "longitude": 117.2647,
        "place_name": "合肥（合成验收）",
        "intensity_system": "cenc",
        "filter_insensitive_eew": True,
        "filter_insensitive_info": True,
        "intensity_threshold": 1.0,
        "shindo_threshold": 1.0,
        "info_intensity_threshold": 1.0,
        "info_shindo_threshold": 1.0,
        "strict_mode": False,
    }
    config.update(changes)
    return config


def full_config(local=None):
    sources = {}
    for entry in REAL.catalog.values():
        sources.setdefault(entry.config_group, {"enabled": True})[entry.config_key] = (
            True
        )
    return {
        "data_sources": sources,
        "local_monitoring": local if local is not None else local_config(),
        "earthquake_filters": {
            key: {"enabled": False}
            for key in (
                "global_quake_filter",
                "intensity_filter",
                "scale_filter",
                "magnitude_only_filter",
            )
        },
        "push_frequency_control": {"gq_report_n": 1},
        "message_format": {"global_quake_template": "Aurora"},
    }


def parsed_event(**changes):
    payload = {
        "id": "synthetic-gq-local",
        "revisionId": 1,
        "originTimeIso": datetime.now(timezone.utc).isoformat(),
        "latitude": -7.72157,
        "longitude": 127.85638,
        "magnitude": 4.7,
        "depth": 158.6,
        "intensity": "VI",
        "region": "synthetic offshore example",
    }
    payload.update(changes)
    event = REAL.parser().parse_message(
        json.dumps(
            {
                "source": "gq",
                "type": "earthquake",
                "action": "update",
                "payload": payload,
            }
        )
    )
    if event is None:
        raise AssertionError("Synthetic GQ fixture did not parse")
    return event


def manager(config):
    # Exercise the real manager methods and factory without starting services,
    # browsers, senders, background tasks or network connections.
    result = object.__new__(REAL.manager)
    result.config = config
    result._runtime_component_factory = REAL.factory()
    return result


class GlobalQuakeLocalTests(unittest.TestCase):
    def test_real_parser_factory_manager_chain_rejects_distant_zero_info(self):
        event = parsed_event()
        config = full_config()
        service = manager(config)
        result = service.evaluate_push_decision(
            event,
            runtime_config=config,
            session_id="synthetic-session",
            commit_state=False,
        )
        self.assertEqual(event.event_type, "earthquake")
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "本地烈度规则过滤")
        local = event.metadata["local_estimation"]
        self.assertEqual(local["kind"], "info")
        self.assertTrue(local["filter_enabled"])
        self.assertEqual(local["effective_threshold"], 1.0)
        self.assertEqual(local["intensity"], 0.0)

    def test_real_manager_chain_keeps_nearby_felt_event(self):
        event = parsed_event(latitude=32.0, longitude=117.61, magnitude=3.1, depth=10.0)
        config = full_config()
        result = manager(config).evaluate_push_decision(
            event,
            runtime_config=config,
            session_id="synthetic-session",
            commit_state=False,
        )
        self.assertTrue(result.accepted)
        self.assertGreaterEqual(event.metadata["local_estimation"]["intensity"], 1.0)
        self.assertLess(event.metadata["local_estimation"]["distance"], 50.0)

    def test_card_context_keeps_zero_intensity_and_zero_distance(self):
        local = local_config(latitude=31.0, longitude=117.0)
        event = parsed_event(latitude=31.0, longitude=117.0, magnitude=0.0, depth=10.0)
        result = REAL.context.build(event, {"local_monitoring": local})[
            "local_estimation"
        ]
        self.assertEqual(result["intensity"], 0.0)
        self.assertEqual(result["distance"], 0.0)
        self.assertEqual(result["intensity_text"], "0.0")
        self.assertEqual(result["distance_text"], "0.0")
        self.assertEqual(result["unit"], "烈度")
        self.assertEqual(result["place_name"], local["place_name"])

    def test_missing_epicenter_coordinate_hides_local_estimation(self):
        for latitude, longitude in ((None, 117.0), (31.0, None), (None, None)):
            with self.subTest(latitude=latitude, longitude=longitude):
                event = parsed_event()
                event.event.latitude = latitude
                event.event.longitude = longitude
                options = {"local_monitoring": local_config()}
                self.assertIsNone(
                    REAL.context.build_local_estimation(event.event, options)
                )
                text = REAL.present(event, "global_quake", options=options)
                self.assertNotIn("距离震中", text)

    def test_cancelled_placeholder_has_no_local_estimation(self):
        event = REAL.parser().parse_message(
            json.dumps({
                "source": "gq", "type": "earthquake", "action": "cancelled",
                "payload": {"id": "synthetic-gq-cancelled"},
            })
        )
        self.assertIsNotNone(event)
        self.assertTrue(event.metadata["is_cancel"])
        self.assertEqual((event.event.latitude, event.event.longitude), (0.0, 0.0))
        options = {"local_monitoring": local_config()}
        self.assertIsNone(REAL.context.build(event, options)["local_estimation"])
        self.assertNotIn(
            "距离震中", REAL.present(event, "global_quake", options=options)
        )
        self.assertIsNone(
            REAL.context.build_local_estimation(
                SimpleNamespace(is_cancel=True, latitude=31.0, longitude=117.0), options
            )
        )

    def test_text_shows_current_local_zero_and_distance(self):
        local = local_config(latitude=31.0, longitude=117.0)
        event = parsed_event(latitude=31.0, longitude=117.0, magnitude=0.0, depth=10.0)
        text = REAL.present(event, "global_quake", options={"local_monitoring": local})
        self.assertIn(local["place_name"] + "预估", text)
        self.assertIn("距离震中 0.0 km", text)
        self.assertIn("预估最大烈度 0.0", text)
        self.assertNotIn("预计P波", text)

    def test_disabled_local_monitoring_hides_stale_metadata_in_card_and_text(self):
        event = parsed_event()
        event.metadata["local_estimation"] = {
            "place_name": "synthetic stale private location",
            "intensity": 9.0,
            "distance": 1.0,
            "system": "cenc",
        }
        for options in ({}, {"local_monitoring": local_config(enabled=False)}):
            with self.subTest(options=options):
                self.assertIsNone(
                    REAL.context.build(event, options)["local_estimation"]
                )
                text = REAL.present(event, "global_quake", options=options)
                self.assertNotIn("synthetic stale private location", text)
                self.assertNotIn("距离震中", text)

    def test_card_and_text_use_jma_unit_for_current_local_system(self):
        local = local_config(
            latitude=35.68,
            longitude=139.69,
            place_name="东京（合成验收）",
            intensity_system="jma",
        )
        event = parsed_event(latitude=36.0, longitude=140.0, magnitude=4.0, depth=20.0)
        result = REAL.context.build(event, {"local_monitoring": local})[
            "local_estimation"
        ]
        self.assertEqual(result["system"], "jma")
        self.assertEqual(result["unit"], "震度")
        text = REAL.present(event, "global_quake", options={"local_monitoring": local})
        self.assertIn("预估最大震度", text)
        self.assertIn(local["place_name"], text)

    def test_card_and_text_use_same_jma_intensity_grades(self):
        options = {"local_monitoring": local_config(intensity_system="jma")}
        event = parsed_event()
        for measured, label in (
            (-0.8, "0以下"), (4.5, "5弱"), (5.0, "5强"),
            (5.5, "6弱"), (6.0, "6强"), (6.5, "7"),
        ):
            with self.subTest(measured=measured), patch.object(
                REAL.monitor, "_estimate_intensity", return_value=measured
            ):
                context = REAL.context.build(event, options)
                self.assertEqual(context["local_estimation"]["intensity"], measured)
                self.assertEqual(context["local_estimation"]["intensity_text"], label)
                text = REAL.present(event, "global_quake", options=options)
                self.assertIn(f"预估最大震度 {label} (", text)

    def test_current_session_recomputes_without_reading_or_mutating_last_session_estimate(
        self,
    ):
        event = parsed_event()
        local_a = local_config()
        local_b = local_config(
            latitude=30.0, longitude=120.0, place_name="另一地点（合成验收）"
        )
        config_b = full_config(local_b)
        manager(config_b).evaluate_push_decision(
            event, runtime_config=config_b, commit_state=False
        )
        previous = copy.deepcopy(event.metadata["local_estimation"])
        first = REAL.context.build(event, {"local_monitoring": local_a})[
            "local_estimation"
        ]
        second = REAL.context.build(event, {"local_monitoring": local_b})[
            "local_estimation"
        ]
        self.assertEqual(first["place_name"], local_a["place_name"])
        self.assertEqual(second["place_name"], local_b["place_name"])
        self.assertNotEqual(first["distance"], second["distance"])
        text_a = REAL.present(
            event, "global_quake", options={"local_monitoring": local_a}
        )
        self.assertIn(local_a["place_name"], text_a)
        self.assertNotIn(local_b["place_name"], text_a)
        self.assertEqual(event.metadata["local_estimation"], previous)


class GlobalQuakeCardCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_sessions_have_distinct_card_cache_keys_and_output_files(
        self,
    ):
        clock = patch.object(
            module("core.message.builders.global_quake_card_builder"), "datetime"
        )
        fixed_clock = clock.start()
        self.addCleanup(clock.stop)
        fixed_clock.now.return_value = datetime(2026, 11, 14, 12, 0, 0)
        event = parsed_event()
        rendered = []
        cache = {}
        with tempfile.TemporaryDirectory(prefix="gq-local-render-") as tmp:

            async def render_card(html, path, **kwargs):
                rendered.append((html, path))
                await asyncio.sleep(0)
                Path(path).write_bytes(b"synthetic offline image")
                return path

            async def render_cached(key, callback):
                if key not in cache:
                    cache[key] = await callback()
                return cache[key]

            builder = REAL.card(
                plugin_root=str(SOURCE),
                temp_dir=tmp,
                browser_manager=SimpleNamespace(render_card=render_card),
            )
            config_a = full_config(local_config())
            config_b = full_config(
                local_config(
                    latitude=30.0, longitude=120.0, place_name="另一地点（合成验收）"
                )
            )

            async def build(config):
                return await builder.build(
                    event,
                    active_config=config,
                    message_format_config=config["message_format"],
                    cache_key_builder=lambda *args: "same-upstream-key",
                    render_with_cache=render_cached,
                )

            results = await asyncio.gather(build(config_a), build(config_b))
            self.assertTrue(all(result is not None for result in results))
            self.assertEqual(len(cache), 2)
            self.assertEqual(len({path for _, path in rendered}), 2)
            self.assertTrue(
                any(
                    config_a["local_monitoring"]["place_name"] in html
                    for html, _ in rendered
                )
            )
            self.assertTrue(
                any(
                    config_b["local_monitoring"]["place_name"] in html
                    for html, _ in rendered
                )
            )
            await build(config_a)
            self.assertEqual(len(rendered), 2)
            await build(full_config(local_config(enabled=False)))
            self.assertEqual(len(cache), 3)
            self.assertNotIn(
                config_a["local_monitoring"]["place_name"], rendered[-1][0]
            )
            self.assertNotIn(
                config_b["local_monitoring"]["place_name"], rendered[-1][0]
            )


if __name__ == "__main__":
    unittest.main()
