"""
NMC 中央气象台降水量预报图 HTTP 客户端。

负责：
- 抓取降水量预报页面 HTML，解析 .time 元素的 data-img 图片 URL 序列
- 并发下载 JPG 图片帧（用于单图与动图合成）
- 复用 aiohttp 会话，伪装浏览器 UA 以兼容 CDN
- 页面内容带 TTL 内存缓存（降水预报每日 2 次起报，缓存 300 秒防抖）

数据来源：
    24 小时降水量：https://www.nmc.cn/publish/precipitation/1-day.html
    6 小时降水量：https://www.nmc.cn/publish/precipitation/6hours-6.html

图片 URL 结构（实测验证）：
    https://image.nmc.cn/product/{YYYY}/{MM}/{DD}/STFC/medium/SEVP_NMC_STFC_SFER_{ER产品码}_ACHN_L88_P9_{UTC起报时间}{时效}00.JPG

    - 24 小时累计降水（ER24）：时效 024/048/072/096/120/144/168（7 时次），
      文件名尾部为 {fffmm}00，如 ..._P9_20260813120002400.JPG
    - 6 小时累计降水（ER6T06/12/18/24）：时效 006/012/018/024（4 时次），
      文件名尾部为 {fffmm}06，如 ..._P9_20260813120000606.JPG

    - 起报时间为 UTC 时间（北京 08 时 = UTC 00 时、北京 20 时 = UTC 12 时），每日 2 次起报
    - 历史起报的 024 帧可访问，但早期深帧（如 168 时效）会被 CDN 清理（404），属正常
    - ?v= 参数仅为缓存防抖，去掉也可直接访问（实测返回 image/jpeg 200）

页面中的每个 .time 元素带 data-fffmm（预报时效）与 data-img（图片 URL），
页面播放器 ImagePlayer.init({orderBy:'desc', speed:1000}) 即每秒一帧。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import logger

PAGE_BASE = "https://www.nmc.cn"
IMAGE_BASE = "https://image.nmc.cn"
_IMAGE_HOST = "image.nmc.cn"

# 页面内容缓存 TTL（秒）。降水预报每日 2 次起报（08/20 北京时），
# 缓存 300 秒既降低请求频率，又不会让用户看到明显过期的帧。
PAGE_CACHE_TTL_SEC = 300.0

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 匹配 .time 元素的 data-img（降水产品图片 URL），兼容 data-index 有无引号
_TIME_IMG_RE = re.compile(
    r'class="col-xs-12 time [^"]*" data-time="[^"]*" data-index="?\d+"? data-fffmm=(\d+) data-img="([^"]+)"'
)
# 兜底：直接匹配页面中任意 image.nmc.cn 的 JPG 图片地址
_ANY_IMG_RE = re.compile(r'data-img="([^"]+\.JPG[^"]*)"', re.IGNORECASE)
# 从 URL 提取 ER 产品码（ER24 / ER6T06 ...）
_ER_CODE_RE = re.compile(r"SFER_([A-Z0-9]+)_ACHN")
# 从 URL 提取预报时效尾部（如 02400 / 00606）
_FH_RE = re.compile(r"_P9_\d{12}(\d{3})(\d{2})")
# 从 URL 提取起报时间（UTC，如 202608131200）
_INIT_RE = re.compile(r"_P9_(\d{12})")


@dataclass(frozen=True)
class PrecipitationFrame:
    """降水预报单帧信息。

    Attributes:
        url: 图片完整 URL（含 ?v= 防抖参数）。
        fffmm: 预报时效（小时），如 24h 产品为 24/48/.../168。
        init_time: 起报时间（UTC），格式 YYYYMMDDHHMM。
    """

    url: str
    fffmm: int
    init_time: str


class NmcPrecipitationClient:
    """NMC 降水量预报图抓取客户端。"""

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
        # 页面内容缓存为类级共享：(page_base, page_path) -> (frames, expires_at)。
        # 命令侧每次请求都会新建实例，实例级缓存无法跨请求复用；
        # 帧序列是纯数据、不依赖会话，可安全跨实例共享。
        cls = type(self)
        if not hasattr(cls, "_page_cache"):
            cls._page_cache: dict[
                tuple[str, str], tuple[list[PrecipitationFrame], float]
            ] = {}
        self._page_cache = cls._page_cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用（延迟初始化）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers={"User-Agent": _UA},
            )
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话。"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch_page_frames(
        self,
        page_path: str,
        *,
        use_cache: bool = True,
    ) -> list[PrecipitationFrame]:
        """抓取降水页面并解析 data-img 图片帧列表（按页面顺序，时效递增）。

        Args:
            page_path: 降水页面路径，如 /publish/precipitation/1-day.html。
            use_cache: 是否启用页面内容缓存（默认开启）。

        Returns:
            帧列表，页面顺序（24h：024→168，6h：006→024，从早到晚）；失败或未解析到时返回空列表。
        """
        cache_key = (self._page_base, page_path)
        if use_cache:
            cached = self._page_cache.get(cache_key)
            if cached is not None and time.time() < cached[1]:
                return list(cached[0])

        url = f"{self._page_base}{page_path}"
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] 降水页面请求失败。状态码为 {resp.status} URL：{url}"
                    )
                    return []
                html = await resp.text(encoding="utf-8", errors="ignore")
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(f"[灾害预警] 降水页面请求异常: {type(e).__name__}: {e}")
            return []

        frames = self._parse_frames(html)

        if use_cache:
            self._page_cache[cache_key] = (
                list(frames),
                time.time() + PAGE_CACHE_TTL_SEC,
            )
        return frames

    def _parse_frames(self, html: str) -> list[PrecipitationFrame]:
        """从页面 HTML 解析降水帧列表（保持页面顺序）。

        优先解析 .time 元素的 data-fffmm + data-img 结构化信息；
        若结构化解析失败（页面改版），回退到任意 image.nmc.cn JPG 图片 URL。
        """
        frames: list[PrecipitationFrame] = []
        seen: set[str] = set()

        for m in _TIME_IMG_RE.finditer(html):
            fffmm_raw = m.group(1)
            raw_url = m.group(2)
            try:
                fffmm = int(fffmm_raw)
            except (ValueError, TypeError):
                continue
            u = self._sanitize_url(raw_url)
            if u is None:
                continue
            # 页面内嵌的是 /medium/ 缩略图，去掉该段取原图
            u = self.strip_medium(u)
            if u in seen:
                continue
            seen.add(u)
            frames.append(
                PrecipitationFrame(
                    url=u,
                    fffmm=fffmm,
                    init_time=self._extract_init_time(u),
                )
            )

        # 结构化解析为空时回退到任意 JPG 图片 URL
        if not frames:
            for m in _ANY_IMG_RE.finditer(html):
                u = self._sanitize_url(m.group(1))
                if u is None:
                    continue
                u = self.strip_medium(u)
                if u in seen:
                    continue
                seen.add(u)
                frames.append(
                    PrecipitationFrame(
                        url=u,
                        fffmm=self._extract_fffmm(u),
                        init_time=self._extract_init_time(u),
                    )
                )

        return frames

    @staticmethod
    def strip_medium(url: str) -> str:
        """去掉图片 URL 中的 /medium 路径段（仅替换一次），取原图。"""
        return (url or "").replace("/medium", "", 1)

    @staticmethod
    def _sanitize_url(raw_url: str) -> str | None:
        """清洗图片 URL：只保留 image.nmc.cn 主机下的 https 地址。"""
        u = (raw_url or "").strip()
        if not u:
            return None
        try:
            parts = urlsplit(u)
        except ValueError:
            return None
        if parts.scheme != "https" or (parts.hostname or "").lower() != _IMAGE_HOST:
            return None
        return u

    @staticmethod
    def _extract_fffmm(url: str) -> int:
        """从图片 URL 提取预报时效（小时）；失败返回 0。"""
        m = _FH_RE.search(url or "")
        if not m:
            return 0
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _extract_init_time(url: str) -> str:
        """从图片 URL 提取起报时间（UTC，YYYYMMDDHHMM）；失败返回空串。"""
        m = _INIT_RE.search(url or "")
        return m.group(1) if m else ""

    @staticmethod
    def strip_version(url: str) -> str:
        """去掉图片 URL 中的 ?v= 防抖参数，返回纯净地址。"""
        return (url or "").split("?", 1)[0]

    async def download_image(self, url: str) -> bytes | None:
        """下载单帧降水图片字节。

        Args:
            url: 图片完整 URL（可带 ?v= 参数）。

        Returns:
            图片字节；失败返回 None。
        """
        session = await self._ensure_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] 降水图片下载失败，状态码为{resp.status} URL：{url[:120]}"
                    )
                    return None
                data = await resp.read()
                if not data:
                    return None
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            logger.warning(
                f"[灾害预警] 降水图片下载异常: {type(e).__name__}: {e} URL：{url[:120]}"
            )
            return None

    async def download_frames(
        self,
        frames: list[PrecipitationFrame],
    ) -> list[tuple[PrecipitationFrame, bytes]]:
        """并发下载多帧图片，返回 (frame, bytes) 配对列表。

        使用信号量限制并发，避免一次拉取过多连接打爆 CDN。
        失败帧会被跳过，因此返回列表长度可能小于输入。

        Args:
            frames: 帧列表。

        Returns:
            下载成功的 (frame, bytes) 配对列表，顺序与输入一致。
        """
        if not frames:
            return []
        sem = asyncio.Semaphore(self._concurrency)

        async def _one(
            f: PrecipitationFrame,
        ) -> tuple[PrecipitationFrame, bytes] | None:
            async with sem:
                data = await self.download_image(f.url)
                if data is None:
                    return None
                return (f, data)

        results = await asyncio.gather(
            *(_one(f) for f in frames),
            return_exceptions=True,
        )
        pairs: list[tuple[PrecipitationFrame, bytes]] = []
        for r in results:
            if isinstance(r, BaseException):
                continue
            if r is not None:
                pairs.append(r)
        return pairs


__all__ = [
    "NmcPrecipitationClient",
    "PrecipitationFrame",
]
