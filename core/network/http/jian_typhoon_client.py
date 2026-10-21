"""
Jian Project 台风 HTTP 客户端。

GET https://api.sismotide.top/get/typhoon.php
无需令牌；同一 IP 建议间隔 ≥1 秒。
"""

from __future__ import annotations

from typing import Any

import aiohttp
from astrbot.api import logger

DEFAULT_TYPHOON_URL = "https://api.sismotide.top/get/typhoon.php"
LOOKUP_HIT = "hit"
LOOKUP_EMPTY = "empty"
LOOKUP_ERROR = "error"


class JianTyphoonClient:
    """Jian Project 实时活跃台风 HTTP 客户端。"""

    def __init__(
        self,
        *,
        url: str = DEFAULT_TYPHOON_URL,
        timeout_seconds: float = 20.0,
        message_logger: Any | None = None,
    ):
        self._url = str(url or DEFAULT_TYPHOON_URL).strip() or DEFAULT_TYPHOON_URL
        self._timeout = max(5.0, float(timeout_seconds or 20.0))
        self._message_logger = message_logger
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch_typhoon_list(
        self, *, use_cache: bool = False
    ) -> tuple[list[dict[str, Any]], str]:
        """拉取当前活跃台风列表。

        Returns:
            (list, status) 其中 status 为 hit / empty / error。
        """
        del use_cache  # 无短缓存；轮询侧自行控制间隔
        try:
            session = await self._ensure_session()
            async with session.get(self._url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] Jian 台风 HTTP 状态异常: {resp.status}"
                    )
                    return [], LOOKUP_ERROR
                payload = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[灾害预警] Jian 台风 HTTP 请求失败: {exc}")
            return [], LOOKUP_ERROR

        if self._message_logger is not None:
            try:
                self._message_logger.log_raw_message(
                    "jian_typhoon_http",
                    payload if isinstance(payload, (dict, list)) else str(payload),
                )
            except Exception:
                pass

        if not isinstance(payload, list):
            logger.warning("[灾害预警] Jian 台风响应非数组，视为错误")
            return [], LOOKUP_ERROR

        items = [item for item in payload if isinstance(item, dict)]
        if not items:
            return [], LOOKUP_EMPTY
        return items, LOOKUP_HIT


__all__ = [
    "DEFAULT_TYPHOON_URL",
    "LOOKUP_EMPTY",
    "LOOKUP_ERROR",
    "LOOKUP_HIT",
    "JianTyphoonClient",
]
