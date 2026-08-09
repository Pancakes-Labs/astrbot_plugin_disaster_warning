"""
NMC 中央气象台气象站数据 HTTP 客户端。

数据来源：https://www.nmc.cn（中央气象台官网）
接口（实测验证，参考官网 weather.js 源码）：
- GET /rest/province/all            -> 34 个省级行政区 [{code, name, url}]
- GET /rest/province/{pcode}        -> 某省全部城市/县区 [{code, province, city, url}]
- GET /rest/weather?stationid={code}-> 城市实况+预报+近24h逐小时观测（字段极丰富）
- GET /rest/position[?stationid={code}] -> IP 定位城市 / 指定城市信息

特性：
- 复用 aiohttp 会话，伪装浏览器 UA 与 Referer 以兼容服务端
- 省份/城市列表带 TTL 内存缓存（键含 pcode，防缓存污染）
- 实况请求带短 TTL 缓存（实况约 10 分钟更新一次，缓存 60 秒防抖即可）
- 城市码为 NMC 侧随机短码（如 无锡=fvlMy、怀集=EhVeb），短期内稳定
- 缺测值 9999 统一保留，由展示层格式化为「-」
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from astrbot.api import logger

PAGE_BASE = "https://www.nmc.cn"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 省份/城市列表缓存 TTL（秒）。行政区划极少变动，缓存 24 小时。
CITY_TTL_SEC = 24 * 3600.0
# 实况缓存 TTL（秒）。实况约 10 分钟更新，缓存 60 秒防抖。
REAL_TTL_SEC = 60.0
# 失败结果缓存 TTL：缩短以支持上游快速恢复后立即重试。
CACHE_FAIL_TTL_SEC = 5.0
# 缓存最大条目数。
CACHE_MAX_ENTRIES = 256


class NmcWeatherClient:
    """NMC 气象站（城市级）数据抓取客户端。"""

    def __init__(
        self,
        *,
        timeout_sec: float = 20.0,
        page_base: str = PAGE_BASE,
    ) -> None:
        """初始化客户端。

        Args:
            timeout_sec: 单次请求超时（秒）。
            page_base: 基础地址，默认中央气象台官网。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._page_base = str(page_base or PAGE_BASE).rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        # 省份/城市列表缓存（类级共享）：
        #   (page_base, "province_all")        -> (data, expires_at)
        #   (page_base, "province", pcode)     -> (data, expires_at)
        cls = type(self)
        if not hasattr(cls, "_city_cache"):
            cls._city_cache: dict[tuple[str, str, str], tuple[Any, float]] = {}
        self._city_cache = cls._city_cache
        # 实况缓存（类级共享）：
        #   (page_base, stationid) -> (data, expires_at)
        if not hasattr(cls, "_real_cache"):
            cls._real_cache: dict[tuple[str, str], tuple[dict[str, Any], float]] = {}
        self._real_cache = cls._real_cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA, "Referer": self._page_base + "/"},
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话（不清理类级缓存，纯数据可跨请求复用）。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------
    # 通用 GET 与缓存
    # ------------------------------------------------------------------

    async def _get_json(self, path: str) -> Any | None:
        """GET 并解析 JSON；失败返回 None。"""
        url = self._page_base + path
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] NMC 接口请求失败。状态码 {resp.status} URL：{url}"
                    )
                    return None
                text = await resp.text(encoding="utf-8", errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(f"[灾害预警] NMC 接口请求异常: {type(e).__name__}: {e}")
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError) as e:
            logger.warning(f"[灾害预警] NMC 响应解析失败: {e}")
            return None

    def _cache_get(self, cache: dict, key: tuple) -> Any | None:
        item = cache.get(key)
        if item is not None and time.time() < item[1]:
            return item[0]
        return None

    def _cache_set(
        self,
        cache: dict,
        key: tuple,
        value: Any,
        ttl: float,
    ) -> None:
        now = time.time()
        # 淘汰过期条目
        expired = [k for k, (_, exp) in cache.items() if exp <= now]
        for k in expired:
            del cache[k]
        # 超过上限时丢弃最旧条目
        if len(cache) >= CACHE_MAX_ENTRIES:
            oldest_key = next(iter(cache))
            del cache[oldest_key]
        cache[key] = (value, now + ttl)

    # ------------------------------------------------------------------
    # 省份 / 城市
    # ------------------------------------------------------------------

    async def fetch_provinces(self, use_cache: bool = True) -> list[dict[str, Any]]:
        """获取全国省级行政区列表。

        Returns:
            [{code, name, url}, ...]；失败返回空列表。
        """
        key = (self._page_base, "province_all", "")
        if use_cache:
            cached = self._cache_get(self._city_cache, key)
            if cached is not None:
                return cached
        payload = await self._get_json("/rest/province/all")
        data: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("code") and item.get("name"):
                    data.append(
                        {
                            "code": str(item["code"]).strip(),
                            "name": str(item["name"]).strip(),
                            "url": str(item.get("url") or "").strip(),
                        }
                    )
        ttl = CITY_TTL_SEC if data else CACHE_FAIL_TTL_SEC
        self._cache_set(self._city_cache, key, data, ttl)
        return data

    async def fetch_cities(
        self, province_code: str, use_cache: bool = True
    ) -> list[dict[str, Any]]:
        """获取某省全部城市/县区列表。

        Args:
            province_code: 省份码（如 AJS=江苏、AGD=广东）。

        Returns:
            [{code, province, city, url}, ...]；失败返回空列表。
        """
        pcode = str(province_code or "").strip()
        if not pcode:
            return []
        key = (self._page_base, "province", pcode)
        if use_cache:
            cached = self._cache_get(self._city_cache, key)
            if cached is not None:
                return cached
        payload = await self._get_json(f"/rest/province/{pcode}")
        data: list[dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("code") and item.get("city"):
                    data.append(
                        {
                            "code": str(item["code"]).strip(),
                            "province": str(item.get("province") or "").strip(),
                            "city": str(item["city"]).strip(),
                            "url": str(item.get("url") or "").strip(),
                        }
                    )
        ttl = CITY_TTL_SEC if data else CACHE_FAIL_TTL_SEC
        self._cache_set(self._city_cache, key, data, ttl)
        return data

    async def fetch_all_cities(self) -> dict[str, list[dict[str, Any]]]:
        """拉取全国所有省份的城市列表，构建「省份码 -> 城市列表」映射。

        用于按省份过滤 / 全量建映射缓存。逐省串行拉取（约 34 次请求），
        由调用方决定是否落缓存；本方法不写缓存。

        Returns:
            {pcode: [{code, province, city, url}, ...]}；空 dict 表示全部失败。
        """
        result: dict[str, list[dict[str, Any]]] = {}
        provinces = await self.fetch_provinces()
        for p in provinces:
            cities = await self.fetch_cities(p["code"], use_cache=False)
            if cities:
                result[p["code"]] = cities
        return result

    # ------------------------------------------------------------------
    # 实况 + 历史（近 24h）
    # ------------------------------------------------------------------

    async def fetch_weather(
        self, station_code: str, use_cache: bool = True
    ) -> dict[str, Any]:
        """获取指定城市码的天气数据（实况+预报+近24h观测）。

        Args:
            station_code: NMC 城市码（如 fvlMy=无锡、EhVeb=怀集）。
            use_cache: 是否启用实况短缓存（默认开启）。

        Returns:
            标准结构：
                {
                  "success": True,
                  "station": {...}, "publish_time": "...",
                  "real": {...}, "predict": [...],
                  "passedchart": [...],   # 近24h逐小时观测
                  "warn": {...}, "sunriseSunset": {...},
                }
            失败：{"success": False, "error": "..."}
        """
        scode = str(station_code or "").strip()
        if not scode:
            return {"success": False, "error": "站点代码为空"}
        cache_key = (self._page_base, scode)
        if use_cache:
            cached = self._cache_get(self._real_cache, cache_key)
            if cached is not None:
                return cached
        payload = await self._get_json(f"/rest/weather?stationid={scode}")
        result = self._parse_weather_payload(payload)
        ttl = REAL_TTL_SEC if result.get("success") else CACHE_FAIL_TTL_SEC
        self._cache_set(self._real_cache, cache_key, result, ttl)
        return result

    @staticmethod
    def _parse_weather_payload(payload: Any) -> dict[str, Any]:
        """解析 /rest/weather 响应，提取实况、预报与近24h观测。"""
        if not isinstance(payload, dict):
            return {"success": False, "error": "响应结构异常"}
        if payload.get("msg") != "success" or payload.get("code") != 0:
            return {
                "success": False,
                "error": f"接口返回异常: msg={payload.get('msg')}, code={payload.get('code')}",
            }
        data = payload.get("data")
        # 站点不存在/无数据时 data 可能为空字符串 ""；非 dict 一律视为无站点数据
        if not isinstance(data, dict):
            return {"success": False, "error": "未找到该站点数据"}
        real = data.get("real") if isinstance(data.get("real"), dict) else {}
        predict_raw = data.get("predict")
        predict = (
            predict_raw.get("detail")
            if isinstance(predict_raw, dict)
            and isinstance(predict_raw.get("detail"), list)
            else []
        )
        passedchart = data.get("passedchart")
        if not isinstance(passedchart, list):
            passedchart = []
        station = real.get("station") if isinstance(real.get("station"), dict) else {}
        return {
            "success": True,
            "station": station,
            "publish_time": str(real.get("publish_time") or "").strip(),
            "real": real,
            "predict": predict,
            "passedchart": passedchart,
            "warn": real.get("warn") if isinstance(real.get("warn"), dict) else {},
            "sunriseSunset": (
                real.get("sunriseSunset")
                if isinstance(real.get("sunriseSunset"), dict)
                else {}
            ),
        }


__all__ = [
    "NmcWeatherClient",
    "PAGE_BASE",
]
