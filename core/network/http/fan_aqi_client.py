"""
FAN Studio 城市空气质量指数（AQI）HTTP 客户端。

数据来源：https://api.fanstudio.tech（FAN Studio 数据服务）
接口（参考 docs/FAN Studio GET 数据服务 API 文档.md）：
- GET /we/aqi.php -> 全国主要城市 AQI 全要素实时数据（一次性返回全部记录）

实测接口行为：
- 无需认证、无需参数，一次返回全国 338 个城市的快照数据（约 138KB）。
- 响应体为 UTF-8 带 BOM 的 JSON 数组，json.loads 前需用 utf-8-sig 解码。
- 全量记录 TimePoint 为同一时刻（快照）。
- AQI 字段是字符串数字，存在 "NA" 缺测值（如黄南藏族自治州）；
  AqiLevel=0 同样表示缺测。
- PrimaryPollutant == "—" 表示无首要污染物；缺测为 "NA"。

特性：
- 复用 aiohttp 会话
- 全量数据带 TTL 内存缓存（AQI 按小时更新，10 分钟缓存即可）
- 失败结果同样缓存（短 TTL），避免把「网络失败」误当作「无数据」
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, ClassVar

import aiohttp

from astrbot.api import logger

API_BASE = "https://api.fanstudio.tech"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# AQI 全量数据缓存 TTL（秒）。AQI 数据按小时更新，10 分钟缓存足够。
AQI_TTL_SEC = 600.0
# 失败结果缓存 TTL（秒）。
CACHE_FAIL_TTL_SEC = 10.0


class FanAqiClient:
    """FAN Studio 城市空气质量指数（AQI）客户端。"""

    # 全量 AQI 数据缓存（类级共享）：api_base -> (data, error, expires_at)
    # 失败结果同样缓存（error 一并保存），避免把「网络失败」误当作「无数据」。
    _aqi_cache: ClassVar[dict[str, tuple[list[dict[str, Any]], str | None, float]]] = {}

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
        # AQI 全量数据缓存（类级共享，子类与父类共享同一份）。
        self._aqi_cache = type(self)._aqi_cache

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

    async def fetch_aqi(
        self, use_cache: bool = True
    ) -> tuple[list[dict[str, Any]], str | None]:
        """获取全国全部城市 AQI 快照数据。

        Returns:
            (data, error)：
            - data: [{Id, TimePoint, AQI, COLevel, ..., Area, CityCode, ...}, ...]
            - error: 网络/解析失败时的明确错误描述；成功为 None。
              注意：请求失败时返回 ([], error)，不再静默返回空列表，
              避免上层把「网络失败」误当作「无城市数据」。
        """
        key = self._api_base
        if use_cache:
            item = self._aqi_cache.get(key)
            if item is not None and time.time() < item[2]:
                # 命中时返回缓存的 (data, error)，失败结果不再被误读为「无数据」
                return item[0], item[1]

        url = f"{self._api_base}/we/aqi.php"
        session = await self._ensure_session()
        payload: Any = None
        error: str | None = None
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    error = f"FAN AQI 数据接口返回状态码 {resp.status}"
                    logger.warning(f"[灾害预警] AQI 数据接口请求失败: {error}")
                else:
                    # 实测响应为 UTF-8 带 BOM，json.loads 前需去除 BOM（utf-8-sig）
                    text = await resp.text(encoding="utf-8-sig", errors="ignore")
                    try:
                        payload = json.loads(text)
                    except (ValueError, TypeError) as e:
                        error = f"FAN AQI 数据接口响应解析失败: {e}"
                        logger.warning(f"[灾害预警] AQI 数据接口解析失败: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            error = f"FAN AQI 数据接口异常（{type(e).__name__}），请稍后重试"
            logger.warning(f"[灾害预警] AQI 数据接口请求异常: {type(e).__name__}: {e}")

        data: list[dict[str, Any]] = []
        if error is None and isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    data.append(item)
        if error is None and not data:
            error = "FAN AQI 数据接口返回数据为空"

        ttl = AQI_TTL_SEC if data else CACHE_FAIL_TTL_SEC
        now = time.time()
        if len(self._aqi_cache) >= 8:
            oldest_key = next(iter(self._aqi_cache))
            del self._aqi_cache[oldest_key]
        # 三元组缓存：失败时 ([], error, expires_at)，读取路径一并返回 error
        self._aqi_cache[key] = (data, error, now + ttl)
        return data, error


__all__ = [
    "FanAqiClient",
    "API_BASE",
    "AQI_TTL_SEC",
]
