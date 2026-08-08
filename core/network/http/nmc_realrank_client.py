"""
NMC 中央气象台实况排行 HTTP 客户端。

负责：
- 请求 /rest/realrank/{type}/{hour}/{ymdh} 接口获取实况要素排行（Top10）
- 支持气温（maxtemp）、降水（rain）、风速（wind）、最低气温（mintemp）四种要素
- 支持按历史时次（YYYYMMDDHH）查询
- 复用 aiohttp 会话，伪装浏览器 UA 以兼容服务端

数据来源：https://www.nmc.cn 首页「实况排行」模块
接口结构（实测验证）：
    GET /rest/realrank/maxtemp/1/2026080821
    -> {"msg":"success","code":0,"data":{"time":"08月08日21时","format_time":"08月08日21时",
        "data":[{"pname":"新疆","pcode":"AXJ","name":"托克逊","code":"51571",
                 "pinyin":"tuokexun","value":37.8}, ...]}}
- 接口无需 Referer、无需登录，直接 GET 即可
- 无数据/非法时次时 data 为空字符串 ""（而非对象），需防御处理
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from astrbot.api import logger

PAGE_BASE = "https://www.nmc.cn"
_RANK_API = "/rest/realrank/{type}/{hour}/{ymdh}"

# 数据缓存 TTL（秒）。排行每小时更新，缓存 60 秒防抖即可。
CACHE_TTL_SEC = 60.0

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 支持的排行要素类型
RANK_TYPES = ("maxtemp", "mintemp", "rain", "wind")
# 支持的时间跨度
RANK_HOURS = (1, 6, 24)


class NmcRealRankClient:
    """NMC 实况排行数据抓取客户端。"""

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
        # 数据缓存为类级共享：(type, hour, ymdh) -> (payload, expires_at)。
        # 命令侧每次请求都会新建实例，实例级缓存无法跨请求复用；
        # 排行数据是纯数据、不依赖会话，可安全跨实例共享。
        cls = type(self)
        if not hasattr(cls, "_rank_cache"):
            cls._rank_cache: dict[
                tuple[str, int, str], tuple[dict[str, Any], float]
            ] = {}
        self._rank_cache = cls._rank_cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA},
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话。

        注意：不清理类级数据缓存，排行数据不依赖会话可跨请求复用。
        """
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    @staticmethod
    def build_url(
        *,
        rank_type: str,
        hour: int,
        ymdh: str,
        page_base: str = PAGE_BASE,
    ) -> str:
        """构建排行接口 URL。

        Args:
            rank_type: 排行要素类型（maxtemp/mintemp/rain/wind）。
            hour: 时间跨度（1/6/24）。
            ymdh: 时次，格式 YYYYMMDDHH。
            page_base: 基础地址。

        Returns:
            完整接口 URL。
        """
        base = str(page_base or PAGE_BASE).rstrip("/")
        return base + _RANK_API.format(type=rank_type, hour=hour, ymdh=ymdh)

    async def fetch_rank(
        self,
        *,
        rank_type: str,
        hour: int,
        ymdh: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """抓取指定要素、跨度、时次的实况排行数据。

        Args:
            rank_type: 排行要素类型（maxtemp/mintemp/rain/wind）。
            hour: 时间跨度（1/6/24）。
            ymdh: 时次，格式 YYYYMMDDHH。
            use_cache: 是否启用结果缓存（默认开启）。缓存 TTL 见
                CACHE_TTL_SEC，期间重复调用直接返回缓存结果。

        Returns:
            标准结构：
                {"success": True, "time": "...", "format_time": "...", "items": [...]}
            请求失败或解析失败时：
                {"success": False, "error": "错误描述"}
        """
        cache_key = (rank_type, int(hour), ymdh)
        if use_cache:
            cached = self._rank_cache.get(cache_key)
            if cached is not None and time.time() < cached[1]:
                return cached[0]

        url = self.build_url(rank_type=rank_type, hour=hour, ymdh=ymdh)
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] 实况排行接口请求失败。状态码为 {resp.status} URL：{url}"
                    )
                    result: dict[str, Any] = {
                        "success": False,
                        "error": f"接口返回状态码 {resp.status}",
                    }
                    self._cache_result(cache_key, result, use_cache)
                    return result
                text = await resp.text(encoding="utf-8", errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(f"[灾害预警] 实况排行接口请求异常: {type(e).__name__}: {e}")
            result = {"success": False, "error": f"请求异常: {e}"}
            self._cache_result(cache_key, result, use_cache)
            return result

        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as e:
            logger.warning(f"[灾害预警] 实况排行响应解析失败: {e}")
            result = {"success": False, "error": "响应数据解析失败"}
            self._cache_result(cache_key, result, use_cache)
            return result

        result = self._parse_payload(payload)
        self._cache_result(cache_key, result, use_cache)
        return result

    def _cache_result(
        self,
        cache_key: tuple[str, int, str],
        result: dict[str, Any],
        use_cache: bool,
    ) -> None:
        """按 use_cache 标志决定是否写入缓存。"""
        if use_cache:
            self._rank_cache[cache_key] = (result, time.time() + CACHE_TTL_SEC)

    @staticmethod
    def _parse_payload(payload: Any) -> dict[str, Any]:
        """解析接口 JSON，兼容 data 为空字符串等异常形态。

        Args:
            payload: 接口返回的 JSON 对象。

        Returns:
            标准结构（见 fetch_rank 文档）。失败时 success=False。
        """
        if not isinstance(payload, dict):
            return {"success": False, "error": "响应结构异常"}

        msg = payload.get("msg")
        code = payload.get("code")
        if msg != "success" or code != 0:
            return {"success": False, "error": f"接口返回异常: msg={msg}, code={code}"}

        data = payload.get("data")
        # 无数据/非法时次时 data 可能是空字符串 ""，需防御。
        if isinstance(data, str) or data is None:
            return {"success": False, "error": "该时次暂无排行数据"}

        if not isinstance(data, dict):
            return {"success": False, "error": "响应 data 结构异常"}

        raw_items = data.get("data") or []
        if not isinstance(raw_items, list):
            raw_items = []

        items: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            pname = str(raw.get("pname") or "").strip()
            try:
                value = float(raw.get("value"))
            except (TypeError, ValueError):
                value = None
            items.append(
                {
                    "name": name,
                    "pname": pname,
                    "value": value,
                    "code": str(raw.get("code") or "").strip(),
                }
            )

        return {
            "success": True,
            "time": str(data.get("time") or "").strip(),
            "format_time": str(data.get("format_time") or "").strip(),
            "items": items,
        }

    @staticmethod
    def parse_time_text(ymdh: str) -> str:
        """把 YYYYMMDDHH 时次格式化为「YYYY年MM月DD日 HH时」。

        Args:
            ymdh: 时次，格式 YYYYMMDDHH，如 2026080821。

        Returns:
            格式化后的时间文本；解析失败返回原样输入。
        """
        s = str(ymdh or "").strip()
        if len(s) == 10 and s.isdigit():
            try:
                return f"{s[0:4]}年{s[4:6]}月{s[6:8]}日 {s[8:10]}时"
            except (IndexError, ValueError):
                pass
        return s


__all__ = [
    "NmcRealRankClient",
    "RANK_TYPES",
    "RANK_HOURS",
]
