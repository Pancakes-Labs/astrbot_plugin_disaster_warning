"""
Jian Project 实时台风 HTTP 客户端。

数据来源：https://api.sismotide.top/get/typhoon.php
字段口径以 Jian Project API 说明文档为准。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, ClassVar

import aiohttp

from astrbot.api import logger

API_BASE = "https://api.sismotide.top"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TYPHOON_TTL_SEC = 60.0
CACHE_FAIL_TTL_SEC = 10.0


class JianTyphoonClient:
    """Jian Project 实时活跃台风客户端。"""

    _typhoon_cache: ClassVar[
        dict[str, tuple[list[dict[str, Any]], str | None, float]]
    ] = {}

    def __init__(
        self,
        *,
        timeout_sec: float = 30.0,
        api_base: str = API_BASE,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._api_base = str(api_base or API_BASE).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._typhoon_cache = type(self)._typhoon_cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch_active_typhoons(
        self, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        """获取当前活跃台风数组；无活跃时返回空列表。"""
        key = self._api_base
        if use_cache:
            item = self._typhoon_cache.get(key)
            if item is not None and time.time() < item[2]:
                return item[0], item[1]

        url = f"{self._api_base}/get/typhoon.php"
        session = await self._ensure_session()
        payload: Any = None
        error: str | None = None
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    error = f"Jian 台风接口返回状态码 {resp.status}"
                    logger.warning(f"[灾害预警] 台风接口请求失败: {error}")
                else:
                    text = await resp.text(encoding="utf-8-sig", errors="ignore")
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError) as e:
                        error = f"Jian 台风接口响应解析失败: {e}"
                        logger.warning(f"[灾害预警] 台风接口解析失败: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            error = f"Jian 台风接口异常（{type(e).__name__}），请稍后重试"
            logger.warning(f"[灾害预警] 台风接口请求异常: {type(e).__name__}: {e}")

        typhoons: list[dict[str, Any]] = []
        if error is None and isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and str(item.get("id") or "").strip():
                    typhoons.append(item)
        # 空数组是合法“无活跃台风”，不算错误。

        ttl = TYPHOON_TTL_SEC if error is None else CACHE_FAIL_TTL_SEC
        now = time.time()
        if len(self._typhoon_cache) >= 8:
            oldest_key = next(iter(self._typhoon_cache))
            del self._typhoon_cache[oldest_key]
        self._typhoon_cache[key] = (typhoons, error, now + ttl)
        return typhoons, error


__all__ = [
    "JianTyphoonClient",
    "API_BASE",
    "TYPHOON_TTL_SEC",
]
