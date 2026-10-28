"""Pin the lazy session behaviour of the NMC and FAN HTTP clients."""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import patch

import pytest

PKG = "astrbot_plugin_disaster_warning.core.network.http."
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CASES = [
    ("fan_aqi_client", "FanAqiClient"),
    ("fan_studio_station_client", "FanStudioStationClient"),
    ("nmc_radar_client", "NmcRadarClient"),
    ("nmc_realrank_client", "NmcRealRankClient"),
    ("nmc_precipitation_client", "NmcPrecipitationClient"),
    ("nmc_weather_client", "NmcWeatherClient"),
]


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.parametrize(("module", "name"), CASES)
def test_session_is_lazy_reused_and_closed(module, name):
    cls = getattr(importlib.import_module(PKG + module), name)

    async def run():
        client = cls()
        assert client._session is None
        with patch("aiohttp.ClientSession", FakeSession):
            first = await client._ensure_session()
            assert await client._ensure_session() is first
            expected = {"User-Agent": UA}
            if name == "NmcWeatherClient":
                expected["Referer"] = client._page_base + "/"
            assert first.kwargs == {"timeout": client._timeout, "headers": expected}
            await client.close()
            assert first.closed
            assert client._session is None
            await client.close()
            second = await client._ensure_session()
            assert second is not first

    asyncio.run(run())
