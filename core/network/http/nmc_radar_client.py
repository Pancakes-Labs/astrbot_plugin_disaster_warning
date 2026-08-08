"""
NMC 中央气象台雷达图 HTTP 客户端。

负责：
- 抓取雷达页面 HTML，解析 data-img 属性中的雷达图片 URL 序列
- 并发下载 PNG 图片帧（用于单图与动图合成）
- 复用 aiohttp 会话，伪装浏览器 UA 以兼容 CDN
- 页面内容带 TTL 内存缓存（雷达帧 6 分钟更新，缓存 90 秒防抖）

数据来源：https://www.nmc.cn/publish/radar/chinaall.html 及其分页
图片 URL 结构（实测验证）：
    https://image.nmc.cn/product/{YYYY}/{MM}/{DD}/RDCP/medium/SEVP_AOC_RDCP_SLDAS3_ECREF_{站点代码}_L88_PI_{YYYYMMDDHHMMSS00000}.PNG
每个页面 data-img 属性携带该站点最近 N 帧（6 分钟/帧）：
- 全国拼图约 239 帧
- 区域拼图约 20 帧
- 单站雷达约 6~22 帧
"""

from __future__ import annotations

import asyncio
import re
import time

import aiohttp

from astrbot.api import logger

PAGE_BASE = "https://www.nmc.cn"
IMAGE_BASE = "https://image.nmc.cn"

# 页面内容缓存 TTL（秒）。雷达帧每 6 分钟更新，缓存 90 秒既降低请求频率，
# 又不会让用户看到明显过期的帧。
PAGE_CACHE_TTL_SEC = 90.0

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_DATA_IMG_RE = re.compile(r'data-img="([^"]+)"')
_CODE_RE = re.compile(r"ECREF_([A-Z0-9_]+)_L88")
_TIME_RE = re.compile(r"_PI_(\d{17})")


class NmcRadarClient:
    """NMC 雷达图抓取客户端。"""

    def __init__(
        self,
        *,
        timeout_sec: float = 20.0,
        concurrency: int = 3,
        page_base: str = PAGE_BASE,
    ) -> None:
        """初始化客户端。

        Args:
            timeout_sec: 单次请求超时（秒）。
            concurrency: 并发下载图片帧的信号量上限。
            page_base: 页面基础地址，默认中央气象台官网。
        """
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._page_base = str(page_base or PAGE_BASE).rstrip("/")
        self._concurrency = max(1, int(concurrency or 3))
        self._session: aiohttp.ClientSession | None = None
        # 页面内容缓存：page_path -> (urls, expires_at)
        self._page_cache: dict[str, tuple[list[str], float]] = {}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA},
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话并清空缓存。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._page_cache.clear()

    async def fetch_page_radar_urls(
        self,
        page_path: str,
        *,
        use_cache: bool = True,
    ) -> list[str]:
        """抓取雷达页面并解析 data-img 图片 URL 列表（按时间倒序）。

        Args:
            page_path: 雷达页面路径，如 /publish/radar/chinaall.html。
            use_cache: 是否启用页面内容缓存（默认开启）。缓存 TTL 见
                PAGE_CACHE_TTL_SEC，期间重复调用直接返回缓存结果。

        Returns:
            图片 URL 列表，最新一帧在前；失败或未解析到时返回空列表。
        """
        if use_cache:
            cached = self._page_cache.get(page_path)
            if cached is not None and time.time() < cached[1]:
                return list(cached[0])

        url = f"{self._page_base}{page_path}"
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] 雷达页面请求失败。状态码为 {resp.status} URL：{url}"
                    )
                    return []
                html = await resp.text(encoding="utf-8", errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(f"[灾害预警] 雷达页面请求异常: {type(e).__name__}: {e}")
            return []

        urls = _DATA_IMG_RE.findall(html)
        # 仅保留 image.nmc.cn 主机下的地址，避免页面被篡改后向任意主机发请求
        result: list[str] = []
        seen: set[str] = set()
        for u in urls:
            u = u.strip()
            if not u or not u.startswith(IMAGE_BASE) or u in seen:
                continue
            seen.add(u)
            result.append(u)

        if use_cache:
            self._page_cache[page_path] = (
                list(result),
                time.time() + PAGE_CACHE_TTL_SEC,
            )
        return result

    @staticmethod
    def strip_medium(url: str) -> str:
        """去掉图片 URL 中的 /medium 路径段（仅替换一次），取原图。"""
        return url.replace("/medium", "", 1)

    async def download_image(self, url: str) -> bytes | None:
        """下载单帧雷达图片字节。

        Args:
            url: 图片完整 URL。

        Returns:
            图片字节；失败返回 None。
        """
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] 雷达图片下载失败，状态码为{resp.status} URL：{url[:120]}"
                    )
                    return None
                data = await resp.read()
                if not data:
                    return None
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(
                f"[灾害预警] 雷达图片下载异常: {type(e).__name__}: {e} URL：{url[:120]}"
            )
            return None

    async def download_frames(self, urls: list[str]) -> list[tuple[str, bytes]]:
        """并发下载多帧图片，返回 (url, bytes) 配对列表。

        使用信号量限制并发，避免一次拉取过多连接打爆 CDN。
        失败帧会被跳过，因此返回列表长度可能小于输入；配对保留 URL，
        供上层按实际成功帧提取时间戳，避免时间标签与帧错位。

        Args:
            urls: 图片 URL 列表。

        Returns:
            下载成功的 (url, bytes) 配对列表，顺序与输入一致。
        """
        if not urls:
            return []
        sem = asyncio.Semaphore(self._concurrency)

        async def _one(u: str) -> tuple[str, bytes] | None:
            async with sem:
                data = await self.download_image(u)
                if data is None:
                    return None
                return (u, data)

        results = await asyncio.gather(*(_one(u) for u in urls))
        return [r for r in results if r is not None]

    @staticmethod
    def parse_code_from_url(url: str) -> str | None:
        """从图片 URL 中提取站点代码（如 ACHN / AZ9010）。"""
        m = _CODE_RE.search(url or "")
        return m.group(1) if m else None

    @staticmethod
    def parse_time_from_url(url: str) -> str | None:
        """从图片 URL 中提取时间戳字符串（如 20260808102400000）。"""
        m = _TIME_RE.search(url or "")
        return m.group(1) if m else None


__all__ = ["NmcRadarClient"]
