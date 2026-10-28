"""Shared lazy aiohttp session for the NMC and FAN Studio HTTP clients."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import TypeVar

import aiohttp
from astrbot.api import logger

_T = TypeVar("_T")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class UaSessionMixin:
    """Create one aiohttp session on first use and close it on request.

    The class that uses this mixin must set `_session` and `_timeout` in `__init__`.
    The image helpers also need `_concurrency`.
    """

    _session: aiohttp.ClientSession | None
    _timeout: aiohttp.ClientTimeout
    _concurrency: int

    def _session_headers(self) -> dict[str, str]:
        """Return the default headers for the session."""
        return {"User-Agent": _UA}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers=self._session_headers(),
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话（不清理类级缓存）。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _download_image_bytes(self, url: str, label: str) -> bytes | None:
        """Download one image. Return None on any failure or empty body."""
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] {label}图片下载失败，状态码为{resp.status} URL：{url[:120]}"
                    )
                    return None
                return await resp.read() or None
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(
                f"[灾害预警] {label}图片下载异常: {type(e).__name__}: {e} URL：{url[:120]}"
            )
            return None

    async def _download_pairs(
        self, items: Sequence[_T], url_of: Callable[[_T], str]
    ) -> list[tuple[_T, bytes]]:
        """Download many images with a semaphore. Skip failed items. Keep the input order."""
        if not items:
            return []
        sem = asyncio.Semaphore(self._concurrency)

        async def _one(item: _T) -> tuple[_T, bytes] | None:
            async with sem:
                data = await self.download_image(url_of(item))
                if data is None:
                    return None
                return (item, data)

        # return_exceptions=True：单帧异常只跳过该帧，不让整个 gather 中断，
        # 避免剩余任务成为孤儿协程在已关闭会话上继续运行。
        results = await asyncio.gather(
            *(_one(item) for item in items),
            return_exceptions=True,
        )
        return [
            r for r in results if r is not None and not isinstance(r, BaseException)
        ]
