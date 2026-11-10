"""
Jian Project 城市空气质量指数（AQI）HTTP 客户端。

数据来源：https://api.sismotide.top/get/aqi.php
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

AQI_TTL_SEC = 600.0
CACHE_FAIL_TTL_SEC = 10.0


class JianAqiClient:
    """Jian Project 城市空气质量指数（AQI）客户端。"""

    _aqi_cache: ClassVar[dict[str, tuple[list[dict[str, Any]], str | None, float]]] = {}

    def __init__(
        self,
        *,
        timeout_sec: float = 45.0,
        api_base: str = API_BASE,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._api_base = str(api_base or API_BASE).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._aqi_cache = type(self)._aqi_cache

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

    async def fetch_aqi(
        self, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        """获取全国城市 AQI 快照。"""
        key = self._api_base
        if use_cache:
            item = self._aqi_cache.get(key)
            if item is not None and time.time() < item[2]:
                return item[0], item[1]

        url = f"{self._api_base}/get/aqi.php"
        session = await self._ensure_session()
        payload: Any = None
        error: str | None = None
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    error = f"Jian AQI 数据接口返回状态码 {resp.status}"
                    logger.warning(f"[灾害预警] AQI 数据接口请求失败: {error}")
                else:
                    text = await resp.text(encoding="utf-8-sig", errors="ignore")
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError) as e:
                        error = f"Jian AQI 数据接口响应解析失败: {e}"
                        logger.warning(f"[灾害预警] AQI 数据接口解析失败: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            error = f"Jian AQI 数据接口异常（{type(e).__name__}），请稍后重试"
            logger.warning(f"[灾害预警] AQI 数据接口请求异常: {type(e).__name__}: {e}")

        data: list[dict[str, Any]] = []
        if error is None and isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    data.append(item)
        if error is None and not data:
            error = "Jian AQI 数据接口返回数据为空"

        ttl = AQI_TTL_SEC if data else CACHE_FAIL_TTL_SEC
        now = time.time()
        if len(self._aqi_cache) >= 8:
            oldest_key = next(iter(self._aqi_cache))
            del self._aqi_cache[oldest_key]
        self._aqi_cache[key] = (data, error, now + ttl)
        return data, error


__all__ = [
    "JianAqiClient",
    "API_BASE",
    "AQI_TTL_SEC",
]
