"""
Jian Project 全国气象站 HTTP 客户端。

数据来源：https://api.sismotide.top/get/station.php?type=...
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

STATION_TTL_SEC = 24 * 3600.0
CACHE_FAIL_TTL_SEC = 10.0


class JianStationClient:
    """Jian Project 全国气象站客户端。"""

    _station_cache: ClassVar[
        dict[str, tuple[list[dict[str, Any]], str | None, float]]
    ] = {}

    def __init__(
        self,
        *,
        timeout_sec: float = 45.0,
        api_base: str = API_BASE,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._api_base = str(api_base or API_BASE).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._station_cache = type(self)._station_cache

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

    async def fetch_stations(
        self, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        """获取全国气象站列表（默认 type=temperature 拿全站元数据）。"""
        key = self._api_base
        if use_cache:
            item = self._station_cache.get(key)
            if item is not None and time.time() < item[2]:
                return item[0], item[1]

        url = f"{self._api_base}/get/station.php"
        session = await self._ensure_session()
        payload: Any = None
        error: str | None = None
        # 文档约定同 IP 至少间隔 1 秒；遇 429 短暂退避后重试一次
        for attempt in range(2):
            try:
                async with session.get(url, params={"type": "temperature"}) as resp:
                    if resp.status == 429 and attempt == 0:
                        logger.warning(
                            "[灾害预警] 气象站接口触发限流(429)，1.2s 后重试"
                        )
                        await asyncio.sleep(1.2)
                        continue
                    if resp.status != 200:
                        error = f"Jian 气象站接口返回状态码 {resp.status}"
                        logger.warning(f"[灾害预警] 气象站接口请求失败: {error}")
                    else:
                        text = await resp.text(encoding="utf-8-sig", errors="ignore")
                        try:
                            payload = json.loads(text)
                            error = None
                        except (ValueError, TypeError) as e:
                            error = f"Jian 气象站接口响应解析失败: {e}"
                            logger.warning(f"[灾害预警] 气象站接口解析失败: {e}")
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
                error = f"Jian 气象站接口异常（{type(e).__name__}），请稍后重试"
                logger.warning(
                    f"[灾害预警] 气象站接口请求异常: {type(e).__name__}: {e}"
                )
                break

        stations: list[dict[str, Any]] = []
        if error is None and isinstance(payload, dict):
            rows = payload.get("data")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    station_id = str(row.get("stationid") or "").strip()
                    sta_name = str(row.get("sta_name") or "").strip()
                    if not station_id:
                        continue
                    stations.append(
                        {
                            "stationid": station_id,
                            "sta_name": sta_name,
                            "lon": row.get("lon"),
                            "lat": row.get("lat"),
                            "val": row.get("val"),
                        }
                    )
        if error is None and not stations:
            error = "Jian 气象站接口返回数据为空"

        ttl = STATION_TTL_SEC if stations else CACHE_FAIL_TTL_SEC
        now = time.time()
        if len(self._station_cache) >= 8:
            oldest_key = next(iter(self._station_cache))
            del self._station_cache[oldest_key]
        self._station_cache[key] = (stations, error, now + ttl)
        return stations, error

    async def fetch_element(
        self, element_type: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """按要素拉取气象站快照（透传 type 查询参数）。"""
        clean_type = str(element_type or "").strip()
        if not clean_type:
            return None, "气象站查询缺少 type 参数"

        url = f"{self._api_base}/get/station.php"
        session = await self._ensure_session()
        try:
            async with session.get(url, params={"type": clean_type}) as resp:
                if resp.status != 200:
                    return None, f"Jian 气象站接口返回状态码 {resp.status}"
                text = await resp.text(encoding="utf-8-sig", errors="ignore")
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload, None
                return None, "Jian 气象站接口响应格式无效"
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as e:
            return None, f"Jian 气象站接口异常（{type(e).__name__}）"


    async def find_station(self, station_code: str) -> dict[str, Any] | None:
        """按五位站号精确查找站点。"""
        scode = str(station_code or "").strip()
        if not scode:
            return None
        stations, error = await self.fetch_stations()
        if error is not None:
            return None
        for s in stations:
            if s["stationid"] == scode:
                return s
        return None


__all__ = [
    "JianStationClient",
    "API_BASE",
    "STATION_TTL_SEC",
]
