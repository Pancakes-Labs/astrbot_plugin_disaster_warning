"""Pin the ingress side effects of the Wolfx earthquake list messages."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

MOD = "astrbot_plugin_disaster_warning.core.network.source_ingress_side_effect_service"


def _service(with_logger=True):
    return SimpleNamespace(
        earthquake_list_service=MagicMock(),
        message_logger=MagicMock() if with_logger else None,
    )


def _run(svc, message_type, payload):
    cls = importlib.import_module(MOD).SourceIngressSideEffectService
    asyncio.run(
        cls(svc).process_message(
            source_id="s", message_type=message_type, payload_data=payload
        )
    )


@pytest.mark.parametrize(
    ("message_type", "key", "source"),
    [
        ("cenc_eqlist", "cenc", "wolfx_cenc_eqlist"),
        ("jma_eqlist", "jma", "wolfx_jma_eqlist"),
    ],
)
def test_list_message_updates_cache_and_logs(message_type, key, source):
    """Check that a list message updates the cache and writes the log."""
    svc = _service()
    _run(svc, message_type, {"No1": {}})
    svc.earthquake_list_service.update_earthquake_list.assert_called_once_with(
        key, {"No1": {}}
    )
    svc.message_logger.log_earthquake_list_summary.assert_called_once_with(
        source=source, earthquake_list={"No1": {}}
    )


def test_no_logger_still_updates_cache():
    """Check that the cache updates when no logger is set."""
    svc = _service(with_logger=False)
    _run(svc, "jma_eqlist", {"a": 1})
    svc.earthquake_list_service.update_earthquake_list.assert_called_once_with(
        "jma", {"a": 1}
    )


@pytest.mark.parametrize(
    ("message_type", "payload"),
    [("other", {"a": 1}), ("cenc_eqlist", {}), ("cenc_eqlist", None)],
)
def test_other_cases_do_nothing(message_type, payload):
    """Check that other messages change nothing."""
    svc = _service()
    _run(svc, message_type, payload)
    svc.earthquake_list_service.update_earthquake_list.assert_not_called()
    svc.message_logger.log_earthquake_list_summary.assert_not_called()
