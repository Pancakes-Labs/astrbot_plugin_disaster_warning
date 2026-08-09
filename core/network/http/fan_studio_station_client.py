"""
FAN Studio 全国气象站数据 HTTP 客户端。

数据来源：https://api.fanstudio.tech（FAN Studio 数据服务）
接口（参考 docs/FAN Studio GET 数据服务 API 文档.md）：
- GET /we/station_all.php?type={type} -> 全国所有站点单要素实时数据

本客户端核心用途：构建「五位站号 -> 站名」映射表。
NMC 侧站点用「城市随机码」（如 无锡=fvlMy），不认五位站号；
而 FAN station_all 一次返回全国 1.3w+ 站点（stationid + sta_name + lon + lat），
可作为站号 -> 站名映射数据源，再经 NMC 省份城市表映射到 NMC 城市码。

特性：
- 复用 aiohttp 会话
- 全站映射表带 TTL 内存缓存（全国 1.3w 站点，24 小时缓存一次即可）
- 只请求一次 type=temperature 即可拿全量站点（stationid/sta_name/lon/lat）
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from astrbot.api import logger

API_BASE = "https://api.fanstudio.tech"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 全站映射表缓存 TTL（秒）。站点元数据极少变动，缓存 24 小时。
STATION_TTL_SEC = 24 * 3600.0
# 失败结果缓存 TTL。
CACHE_FAIL_TTL_SEC = 10.0


class FanStudioStationClient:
    """FAN Studio 全国气象站（五位站号）客户端。"""

    def __init__(
        self,
        *,
        timeout_sec: float = 45.0,
        api_base: str = API_BASE,
    ) -> None:
        """初始化客户端。

        Args:
            timeout_sec: 单次请求超时（秒）。
            api_base: 基础地址，默认 FAN Studio API。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._api_base = str(api_base or API_BASE).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        # 全站站点映射缓存（类级共享）：api_base -> (stations, expires_at)
        cls = type(self)
        if not hasattr(cls, "_station_cache"):
            cls._station_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._station_cache = cls._station_cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA},
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话（不清理类级缓存）。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch_stations(
        self, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        """获取全国全部气象站（stationid/sta_name/lon/lat）。

        内部只请求一次 type=temperature 即返回全量站点列表（实测 1.3w+）。

        Returns:
            (stations, error)：
            - stations: [{stationid, sta_name, lon, lat}, ...]
            - error: 网络/解析失败时的明确错误描述；成功为 None。
              注意：请求失败时返回 ([], error)，不再静默返回空列表，
              避免上层把「网络失败」误当作「无站点」。
        """
        key = self._api_base
        if use_cache:
            item = self._station_cache.get(key)
            if item is not None and time.time() < item[1]:
                return item[0], None

        url = f"{self._api_base}/we/station_all.php?type=temperature"
        session = await self._ensure_session()
        payload: Any = None
        error: str | None = None
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    error = f"FAN 全国气象站数据接口返回状态码 {resp.status}"
                    logger.warning(f"[灾害预警] 全国气象站数据接口请求失败: {error}")
                else:
                    text = await resp.text(encoding="utf-8", errors="ignore")
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError) as e:
                        error = f"FAN 全国气象站数据接口响应解析失败: {e}"
                        logger.warning(f"[灾害预警] 全国气象站数据接口解析失败: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            error = f"FAN 全国气象站数据接口异常（{type(e).__name__}），请稍后重试"
            logger.warning(
                f"[灾害预警] 全国气象站数据接口请求异常: {type(e).__name__}: {e}"
            )

        stations: list[dict[str, Any]] = []
        if error is None and isinstance(payload, dict):
            raw_data = payload.get("data")
            if isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        continue
                    sid = str(item.get("stationid") or "").strip()
                    name = str(item.get("sta_name") or "").strip()
                    if not sid or not name:
                        continue
                    # 过滤非国家站：站名以 * 开头（街镇级自动站）、
                    # 或含繁体/异常字符的杂站。这些站 NMC 城市表里不存在，
                    # 保留只会污染「站名 -> 站号」映射表。
                    if name.startswith("*"):
                        continue
                    stations.append(
                        {
                            "stationid": sid,
                            "sta_name": name,
                            "lon": str(item.get("lon") or "").strip(),
                            "lat": str(item.get("lat") or "").strip(),
                        }
                    )
        if error is None and not stations:
            error = "FAN 全国气象站数据接口返回数据为空"

        ttl = STATION_TTL_SEC if stations else CACHE_FAIL_TTL_SEC
        now = time.time()
        if len(self._station_cache) >= 8:
            oldest_key = next(iter(self._station_cache))
            del self._station_cache[oldest_key]
        self._station_cache[key] = (stations, now + ttl)
        return stations, error

    async def find_station(self, station_code: str) -> dict[str, Any] | None:
        """按五位站号精确查找站点。

        Args:
            station_code: 五位站号（如 59270）。

        Returns:
            站点 dict 或 None。
        """
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
    "FanStudioStationClient",
    "API_BASE",
]
