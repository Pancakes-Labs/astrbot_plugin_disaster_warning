"""Pin the response handling of the EQSC typhoon client."""

from __future__ import annotations

import asyncio
import importlib

MOD = "astrbot_plugin_disaster_warning.core.network.http.eqsc_typhoon_client"


def _client(status, data):
    mod = importlib.import_module(MOD)
    client = mod.EqscTyphoonClient(None, {})

    async def fake_request(**kwargs):
        return status, data, ""

    client._request_json = fake_request
    return mod, client


def test_list_and_detail_response_handling():
    """Check that the typhoon client handles the list and detail responses."""
    for status, data, expected_state, expected_list in [
        (200, {"typhoon": [{"a": 1}]}, "hit", [{"a": 1}]),
        (200, {"typhoon": []}, "empty", []),
        (200, {}, "empty", []),
        (200, {"typhoon": "bad"}, "error", []),
        (200, [1], "error", []),
        (500, {}, "error", []),
    ]:
        mod, client = _client(status, data)
        lst, state = asyncio.run(client.fetch_typhoon_list("tok", use_cache=False))
        assert state == getattr(mod, "LOOKUP_" + expected_state.upper())
        assert lst == expected_list

    mod, client = _client(200, {"typhoon": "bad"})
    data, state = asyncio.run(
        client.fetch_typhoon_by_id("2501", "tok", use_cache=False)
    )
    assert data is None
    assert state == mod.LOOKUP_ERROR
