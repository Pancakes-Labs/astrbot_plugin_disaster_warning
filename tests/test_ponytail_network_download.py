"""Pin the image download behaviour of the NMC radar and precipitation clients."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib

import aiohttp
import pytest

PKG = "astrbot_plugin_disaster_warning.core.network.http."


class FakeResp:
    """Fake HTTP response with a status and a body."""

    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def read(self):
        """Return the stored body bytes."""
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeGetSession:
    """Fake session that returns a list of fake responses in order."""

    closed = False

    def __init__(self, outcome):
        self.outcome = outcome

    def get(self, url):
        """Return the next fake response as a context manager."""
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("nmc_radar_client", "NmcRadarClient"),
        ("nmc_precipitation_client", "NmcPrecipitationClient"),
    ],
)
def test_download_image_outcomes(module, name):
    """Check the result of the image download for each response status."""
    cls = getattr(importlib.import_module(PKG + module), name)
    cases = [
        (FakeResp(200, b"abc"), b"abc"),
        (FakeResp(200, b""), None),
        (FakeResp(500, b"abc"), None),
        (aiohttp.ClientError("x"), None),
        (asyncio.TimeoutError(), None),
        (RuntimeError("closed"), None),
    ]

    async def run():
        for outcome, expected in cases:
            client = cls()
            client._session = FakeGetSession(outcome)
            assert await client.download_image("https://x/y") == expected

    asyncio.run(run())


def test_download_frames_keep_order_and_skip_failures():
    """Check that the frame download keeps the order and skips the failed frames."""
    radar = importlib.import_module(PKG + "nmc_radar_client").NmcRadarClient
    precip = importlib.import_module(PKG + "nmc_precipitation_client")

    async def fake(url):
        if url == "none":
            return None
        if url == "boom":
            raise ValueError("x")
        return url.encode()

    def frame(url):
        kwargs = {
            f.name: ("" if f.name == "url" else 0)
            for f in dataclasses.fields(precip.PrecipitationFrame)
        }
        kwargs["url"] = url
        return precip.PrecipitationFrame(**kwargs)

    async def run():
        urls = ["a", "none", "boom", "b"]
        client = radar()
        client.download_image = fake
        assert await client.download_frames([]) == []
        assert await client.download_frames(urls) == [("a", b"a"), ("b", b"b")]

        pc = precip.NmcPrecipitationClient()
        pc.download_image = fake
        assert await pc.download_frames([]) == []
        out = await pc.download_frames([frame(u) for u in urls])
        assert [(f.url, d) for f, d in out] == [("a", b"a"), ("b", b"b")]

    asyncio.run(run())
