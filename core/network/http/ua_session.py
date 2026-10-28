"""Shared lazy aiohttp session for the NMC and FAN Studio HTTP clients."""

from __future__ import annotations

import aiohttp

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class UaSessionMixin:
    """Create one aiohttp session on first use and close it on request.

    The class that uses this mixin must set `_session` and `_timeout` in `__init__`.
    """

    _session: aiohttp.ClientSession | None
    _timeout: aiohttp.ClientTimeout

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
