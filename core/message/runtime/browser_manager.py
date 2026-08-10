"""
浏览器管理器。

负责管理浏览器实例、页面池、并发渲染与远程渲染模式切换，
为卡片、地图等图片渲染场景提供统一的浏览器基础设施。
"""

import asyncio
import json
import os
import tempfile
import time

import aiohttp
from playwright.async_api import Browser, Page, async_playwright

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger


class BrowserManager:
    """浏览器管理器。"""

    # 页面池默认视口；大尺寸卡片（如 S-Net 1400×1000）可在 render_card 临时覆盖
    DEFAULT_VIEWPORT: dict[str, int] = {"width": 800, "height": 800}

    def __init__(
        self,
        pool_size: int = 2,
        telemetry=None,
        mode: str = "local",
        server_url: str = "",
        ignore_https_errors: bool = False,
    ):
        """初始化浏览器管理器。

        Args:
            pool_size: 页面池大小。
            telemetry: 遥测服务实例。
            mode: 运行模式，local 或 remote。
            server_url: 远程浏览器服务地址。
            ignore_https_errors: 是否忽略 HTTPS 证书错误（仅本地模式生效）。
                默认关闭。某些地图瓦片源（如 FAN Studio ）证书过期时，
                开启后底图可继续加载；但会信任自签/过期证书，请谨慎使用。
        """
        self.pool_size = pool_size
        self._browser: Browser | None = None
        self._playwright = None
        # 远程连接场景下可能需要保留上下文对象引用。
        self._context = None
        # 共享 context 是否已关闭（由 _cleanup 联动维护，不依赖属性探测）。
        self._context_closed = False
        # 共享 context 创建/关闭互斥锁：_create_local_page 可能在
        # 补池、应急建页等多协程路径并发调用，不加锁会重复创建 context 造成泄漏。
        self._context_lock = asyncio.Lock()
        self._ignore_https_errors = bool(ignore_https_errors)
        self._page_pool: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
        # 信号量用于限制同时渲染数量，页面创建锁与初始化锁用于避免并发竞争。
        self._semaphore = asyncio.Semaphore(pool_size)
        self._page_creation_lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False
        self._closed = False
        self._telemetry = telemetry
        self._mode = mode
        self._server_url = server_url

    @staticmethod
    def _normalize_viewport(
        viewport: dict | None,
    ) -> dict[str, int] | None:
        """把可选 viewport 规整为整数宽高；非法时返回 None。"""
        if not viewport or not isinstance(viewport, dict):
            return None
        try:
            width = int(viewport.get("width", 0))
            height = int(viewport.get("height", 0))
        except (TypeError, ValueError):
            return None
        if width < 1 or height < 1:
            return None
        return {"width": width, "height": height}

    @staticmethod
    def _normalize_render_label(render_label: str | None) -> str:
        """规范化渲染来源标签，供成功/失败日志使用。"""
        text = str(render_label or "").strip()
        return text or "卡片"

    @staticmethod
    def _is_target_closed_error(error: Exception | str | None) -> bool:
        """判断是否为 Playwright 目标/浏览器已关闭类错误。"""
        message = str(error or "").lower()
        return any(
            marker in message
            for marker in (
                "target page, context or browser has been closed",
                "browser has been closed",
                "target closed",
                "page has been closed",
                "context has been closed",
            )
        )

    @staticmethod
    def _is_map_tile_url(url: str | None) -> bool:
        """判断 URL 是否为地图瓦片资源。"""
        text = str(url or "").lower()
        if not text:
            return False
        if "tilemap.fanstudio.tech" in text:
            return True
        if "appmaptile" in text or "webrd0" in text:
            return True
        if "/petaldark/" in text or "/petallight/" in text:
            return True
        return any(
            marker in text
            for marker in (
                "/arcwi/",
                "/arcwob/",
                "/arcwh/",
                "/geovis/",
                "tile.openstreetmap",
                "tiles.",
                "/tiles/",
            )
        )

    @classmethod
    def _is_benign_request_failure(
        cls,
        *,
        url: str | None,
        failure_text: str | None,
        resource_type: str | None = None,
    ) -> bool:
        """识别渲染过程中可忽略的资源失败。

        Leaflet 在瓦片重试、视野更新、截图收尾时会主动中止旧请求，
        Playwright 常以 net::ERR_ABORTED 上报；出图成功时这些属于噪声。
        """
        failure = str(failure_text or "").lower()
        is_aborted = (
            "err_aborted" in failure
            or "net::err_aborted" in failure
            or failure.strip() in {"aborted", "abort", "canceled", "cancelled"}
        )
        if not is_aborted:
            return False
        return cls._is_map_tile_url(url)

    @staticmethod
    def _dedupe_failures(entries: list[str]) -> list[str]:
        """按条目内容去重并保持首次出现顺序，供汇总日志使用。

        Args:
            entries: 原始失败条目列表（如 "GET https://... -> net::ERR_CERT_DATE_INVALID"）。

        Returns:
            去重后的条目列表；若输入为空则返回空列表。
        """
        if not entries:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for entry in entries:
            key = str(entry).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(entry)
        return result

    def _truncate_debug_text(self, value, limit: int = 240) -> str:
        """截断浏览器侧日志文本，避免单条日志过长。"""
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    async def _is_page_usable(self, page: Page | None) -> bool:
        """检查页面是否仍可用于渲染。"""
        if page is None:
            return False
        try:
            if page.is_closed():
                return False
            # 轻量探测：页面所属浏览器/上下文若已失效，这里会抛出。
            _ = page.context
            # 给探测加超时，避免页面卡死时把归还/取用流程一起拖死。
            await asyncio.wait_for(page.evaluate("() => true"), timeout=1.0)
            return True
        except Exception:
            return False

    async def _is_browser_alive(self) -> bool:
        """检查本地浏览器进程是否仍可用。"""
        if self._closed or self._browser is None:
            return False
        try:
            # Playwright Browser.is_connected() 在进程崩溃后会返回 False。
            is_connected = getattr(self._browser, "is_connected", None)
            if callable(is_connected):
                return bool(is_connected())
            return True
        except Exception:
            return False

    async def _ensure_local_browser_ready(self) -> bool:
        """确保本地浏览器与页面池可用；必要时自动重建。"""
        if self._mode != "local":
            return self._initialized and not self._closed

        if self._closed:
            return False

        # 快路径：已初始化且浏览器仍连接时直接复用。
        if self._initialized and await self._is_browser_alive():
            return True

        # 慢路径：加初始化锁，避免并发渲染同时触发多次重建。
        async with self._init_lock:
            if self._closed:
                return False
            if self._initialized and await self._is_browser_alive():
                return True

            logger.warning("[灾害预警] 检测到浏览器不可用，尝试重新初始化...")
            try:
                # 注意：这里不能调用 initialize() 内部的 _init_lock（同协程重入会卡死），
                # 因此在已持有锁的情况下直接执行重建步骤。
                await self._cleanup()
                self._closed = False

                logger.info(f"[灾害预警] 正在启动浏览器（模式：{self._mode}）...")
                start_time = time.time()
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                logger.info("[灾害预警] 本地浏览器启动成功")
                await self._initialize_local_page_pool()
                elapsed = time.time() - start_time
                self._initialized = True
                logger.info(
                    f"[灾害预警] 浏览器重建完成，耗时 {elapsed:.2f}秒，页面池大小: {self.pool_size}"
                )
                return await self._is_browser_alive()
            except Exception as reinit_err:
                logger.error(f"[灾害预警] 浏览器重新初始化失败: {reinit_err}")
                await self._cleanup()
                return False

    async def _ensure_local_context(self):
        """确保本地浏览器共享上下文就绪。

        并发安全：context 的重新创建受 _context_lock 保护，避免
        补池/应急建页等多协程路径并发进入时各自创建 context 造成泄漏。
        存活性以 _context_closed 显式标志为准（由 _cleanup 联动维护），
        不依赖 .browser 属性探测——该属性在 close() 后仍可访问，无法可靠识别已关闭的 context。
        """
        if not self._browser:
            raise RuntimeError("浏览器未就绪，无法创建页面")
        async with self._context_lock:
            # double-check：锁内再次确认，避免重复创建。
            if self._context is not None and not self._context_closed:
                return self._context
            self._context = await self._browser.new_context(
                viewport=dict(self.DEFAULT_VIEWPORT),
                device_scale_factor=2,
                ignore_https_errors=self._ignore_https_errors,
            )
            self._context_closed = False
            if self._ignore_https_errors:
                logger.debug(
                    "[灾害预警] 已启用忽略 HTTPS 证书错误（仅对瓦片等资源加载生效）"
                )
            return self._context

    async def _create_local_page(self) -> Page:
        """创建本地页面池中的新页面。"""
        context = await self._ensure_local_context()
        return await context.new_page()

    async def _put_page_into_pool(self, page: Page) -> bool:
        """非阻塞入池；池满时关闭多余页面，避免 put 永久阻塞。"""
        try:
            self._page_pool.put_nowait(page)
            return True
        except asyncio.QueueFull:
            logger.debug("[灾害预警] 页面池已满，关闭多余页面")
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
            return False
        except Exception as put_err:
            logger.debug(f"[灾害预警] 页面入池失败: {put_err}")
            try:
                if not page.is_closed():
                    await page.close()
            except Exception:
                pass
            return False

    async def _return_page_to_pool(self, page: Page | None) -> None:
        """仅在页面仍可用时归还页面池，避免把坏页重新投入复用。"""
        if page is None:
            return
        try:
            if await self._is_page_usable(page):
                if await self._put_page_into_pool(page):
                    return
                # 入池失败（满/异常）时页面已关闭，仍尝试补齐容量
                await self._replenish_page_pool()
                return
        except Exception as return_err:
            logger.debug(f"[灾害预警] 归还页面到池失败: {return_err}")

        # 页面已不可用：尽量关闭，并补一个新页面维持池容量。
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass
        await self._replenish_page_pool()

    async def _acquire_usable_page(self, timeout: float = 5.0) -> Page | None:
        """从页面池获取可用页面；遇到坏页时丢弃并补充，必要时直接新建。"""
        if not await self._ensure_local_browser_ready():
            return None

        deadline = time.time() + max(timeout, 0.1)
        discarded = 0

        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            page: Page | None = None
            try:
                page = await asyncio.wait_for(self._page_pool.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break

            if await self._is_page_usable(page):
                return page

            discarded += 1
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass

            # 坏页不回池，异步补齐容量后继续取下一个。
            await self._replenish_page_pool()

        if discarded:
            logger.warning(
                f"[灾害预警] 页面池中发现 {discarded} 个不可用页面，已丢弃并尝试补充"
            )

        # 池中暂时没有可用页时，直接新建一个应急页面。
        try:
            if await self._ensure_local_browser_ready():
                page = await self._create_local_page()
                logger.debug("[灾害预警] 页面池暂无可用页，已直接创建应急页面")
                return page
        except Exception as create_err:
            logger.error(f"[灾害预警] 创建应急页面失败: {create_err}")

        return None

    async def _replenish_page_pool(self) -> None:
        """在页面损坏或关闭后，尽量把页面池补回到目标容量。"""
        if self._mode != "local" or self._closed:
            return

        async with self._page_creation_lock:
            if not await self._ensure_local_browser_ready():
                return

            while self._page_pool.qsize() < self.pool_size:
                try:
                    new_page = await self._create_local_page()
                    if not await self._put_page_into_pool(new_page):
                        # 池已满：停止补充
                        break
                    logger.debug(
                        f"[灾害预警] 已补充页面，当前池大小: {self._page_pool.qsize()}/{self.pool_size}"
                    )
                except Exception as recover_err:
                    logger.error(f"[灾害预警] 页面恢复失败: {recover_err}")
                    break

    async def _wait_for_fonts_ready(self, page: Page, timeout_ms: int = 1500) -> None:
        """等待网页字体就绪，超时后继续截图，避免卡死在 fonts.ready。"""
        # JS 侧有 timeoutMs 竞速；Python 侧再加 wait_for，防止连接挂死时永久占用页面。
        python_timeout = max(1.0, (timeout_ms / 1000.0) + 1.0)
        try:
            await asyncio.wait_for(
                page.evaluate(
                    """
                    async (timeoutMs) => {
                        if (!document.fonts || !document.fonts.ready) {
                            return "unsupported";
                        }
                        const timeoutPromise = new Promise((resolve) => {
                            setTimeout(() => resolve("timeout"), timeoutMs);
                        });
                        const readyPromise = document.fonts.ready
                            .then(() => "ready")
                            .catch(() => "error");
                        return await Promise.race([readyPromise, timeoutPromise]);
                    }
                    """,
                    timeout_ms,
                ),
                timeout=python_timeout,
            )
        except Exception as font_err:
            # 字体等待失败不应阻断截图；Playwright 截图本身仍会再做一次字体检查。
            logger.debug(f"[灾害预警] 等待字体就绪失败，继续截图: {font_err}")

    async def _screenshot_card(
        self,
        page: Page,
        selector: str,
        output_path: str,
        *,
        timeout_ms: int = 10000,
    ) -> None:
        """对卡片元素截图，并在字体等待超时场景下做一次降级重试。"""
        await self._wait_for_fonts_ready(page, timeout_ms=1500)
        card = page.locator(selector)
        try:
            await card.screenshot(
                path=output_path,
                omit_background=True,
                timeout=timeout_ms,
            )
            return
        except Exception as first_err:
            # Playwright 默认截图会等待字体加载；若仍超时，强制结束字体加载后重试一次。
            message = str(first_err).lower()
            if "font" not in message and "timeout" not in message:
                raise

            logger.warning(
                f"[灾害预警] 卡片截图超时，尝试强制结束字体加载后重试: {first_err}"
            )
            try:
                await page.evaluate(
                    """
                    async () => {
                        try {
                            if (document.fonts && document.fonts.ready) {
                                // 主动触发一次 ready 竞争，尽量让后续截图不再长时间阻塞。
                                await Promise.race([
                                    document.fonts.ready,
                                    new Promise((resolve) => setTimeout(resolve, 50)),
                                ]);
                            }
                        } catch (e) {}
                        return true;
                    }
                    """
                )
            except Exception:
                pass

            await card.screenshot(
                path=output_path,
                omit_background=True,
                timeout=min(timeout_ms, 5000),
            )

    async def _log_page_diagnostics(self, page: Page, *, reason: str) -> None:
        """输出页面级诊断信息，辅助定位资源加载、脚本执行与选择器状态问题。"""
        try:
            diagnostics = await page.evaluate(
                """
                () => {
                    const mapEl = document.querySelector('#map-container') || document.querySelector('#map-area') || document.querySelector('#map');
                    const cardEl = document.querySelector('#card-wrapper') || document.querySelector('.quake-card');
                    const html = document.documentElement;
                    const body = document.body;
                    return {
                        title: document.title || '',
                        readyState: document.readyState,
                        bodyClasses: body ? body.className || '' : '',
                        mapReady: !!document.querySelector('.map-ready') || (body && body.classList.contains('map-ready')),
                        mapContainerExists: !!mapEl,
                        mapContainerSize: mapEl ? {
                            width: mapEl.clientWidth,
                            height: mapEl.clientHeight,
                        } : null,
                        cardExists: !!cardEl,
                        cardSize: cardEl ? {
                            width: cardEl.clientWidth,
                            height: cardEl.clientHeight,
                        } : null,
                        htmlLength: html ? (html.outerHTML || '').length : 0,
                    };
                }
                """
            )
            logger.warning(
                f"[灾害预警] 页面诊断（{reason}）：当前文档状态为 {diagnostics.get('readyState')}，"
                f"地图就绪标记{'已出现' if diagnostics.get('mapReady') else '尚未出现'}，"
                f"地图容器{'已找到' if diagnostics.get('mapContainerExists') else '未找到'}，"
                f"地图区域尺寸为 {diagnostics.get('mapContainerSize')}，"
                f"卡片容器{'已找到' if diagnostics.get('cardExists') else '未找到'}，"
                f"卡片尺寸为 {diagnostics.get('cardSize')}，"
                f"页面 body 类名为“{self._truncate_debug_text(diagnostics.get('bodyClasses'))}”，"
                f"页面 HTML 长度约为 {diagnostics.get('htmlLength')} 个字符。"
            )
        except Exception as diag_err:
            logger.warning(f"[灾害预警] 页面诊断({reason})失败: {diag_err}")

    async def initialize(self):
        """初始化浏览器和页面池"""
        async with self._init_lock:
            if self._initialized:
                logger.debug("[灾害预警] 浏览器已初始化，跳过")
                return

            try:
                # 远程模式使用 HTTP API，不需要初始化 Playwright
                if self._mode == "remote":
                    logger.info(
                        f"[灾害预警] 远程模式：使用 browserless HTTP API ({self._server_url})"
                    )
                    self._initialized = True
                    return

                logger.debug(f"[灾害预警] 正在启动浏览器（模式：{self._mode}）...")
                start_time = time.time()

                # 启动 Playwright
                self._playwright = await async_playwright().start()

                # 本地模式：启动本地浏览器
                self._browser = await self._playwright.chromium.launch(
                    args=["--no-sandbox", "--disable-setuid-sandbox"]
                )
                logger.debug("[灾害预警] 本地浏览器启动成功")

                # 本地模式：直接创建页面池
                await self._initialize_local_page_pool()

                elapsed = time.time() - start_time
                self._initialized = True
                logger.debug(
                    f"[灾害预警] 浏览器启动完成，耗时 {elapsed:.2f}秒，页面池大小: {self.pool_size}"
                )

            except Exception as e:
                logger.error(f"[灾害预警] 浏览器初始化失败: {e}")
                # 上报浏览器初始化错误到遥测
                if self._telemetry and self._telemetry.enabled:
                    await self._telemetry.track_error(
                        e, module="core.browser_manager.initialize"
                    )
                # 清理已创建的资源
                await self._cleanup()
                raise

    async def _initialize_local_page_pool(self):
        """初始化本地浏览器的页面池"""
        # 统一走共享 context 创建页面，以支持 ignore_https_errors 等上下文级配置。
        context = await self._ensure_local_context()
        for i in range(self.pool_size):
            try:
                page = await asyncio.wait_for(context.new_page(), timeout=10.0)
                if not await self._put_page_into_pool(page):
                    break
                logger.debug(f"[灾害预警] 页面 {i + 1}/{self.pool_size} 已创建")
            except asyncio.TimeoutError:
                logger.error(f"[灾害预警] 创建页面 {i + 1} 超时")
                if i == 0:
                    raise  # 如果第一个页面就失败，抛出异常
                break  # 部分页面创建成功，继续使用
            except Exception as e:
                logger.error(f"[灾害预警] 创建页面 {i + 1} 失败: {e}")
                if i == 0:
                    raise
                break

    async def _initialize_remote_page_pool(self):
        """初始化远程浏览器的页面池（兼容 browserless CDP）"""
        try:
            # browserless CDP：必须使用默认 context
            contexts = self._browser.contexts
            logger.debug(f"[灾害预警] 发现 {len(contexts)} 个现有 context")

            if contexts:
                # 使用第一个 context（browserless 的默认 context）
                self._context = contexts[0]
                logger.debug("[灾害预警] 使用现有 context")
            else:
                # 没有现有 context，创建新的
                logger.debug("[灾害预警] 创建新 context")
                self._context = await asyncio.wait_for(
                    self._browser.new_context(
                        viewport=dict(self.DEFAULT_VIEWPORT),
                        device_scale_factor=2,
                    ),
                    timeout=15.0,
                )

            # 从 context 创建页面
            for i in range(self.pool_size):
                try:
                    page = await asyncio.wait_for(
                        self._context.new_page(), timeout=10.0
                    )
                    if not await self._put_page_into_pool(page):
                        break
                    logger.debug(f"[灾害预警] 页面 {i + 1}/{self.pool_size} 已创建")
                except asyncio.TimeoutError:
                    logger.error(f"[灾害预警] 创建页面 {i + 1} 超时")
                    if i == 0:
                        raise
                    break
                except Exception as e:
                    logger.error(f"[灾害预警] 创建页面 {i + 1} 失败: {e}")
                    if i == 0:
                        raise
                    break

            # 检查是否至少有一个页面可用
            if self._page_pool.qsize() == 0:
                raise RuntimeError("无法创建任何可用页面")

            logger.info(
                f"[灾害预警] 远程浏览器页面池初始化完成，可用页面: {self._page_pool.qsize()}"
            )

        except asyncio.TimeoutError:
            logger.error("[灾害预警] 远程浏览器页面池初始化超时")
            raise RuntimeError(
                "远程浏览器页面池初始化超时，请检查网络或增加 browserless 超时设置"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 远程浏览器页面池初始化失败: {e}")
            raise

    async def render_card(
        self,
        html_content: str,
        output_path: str,
        selector: str = "#card-wrapper",
        wait_until: str = "domcontentloaded",
        viewport: dict | None = None,
        render_label: str | None = None,
        event_stream: str | None = None,
    ) -> str | None:
        """把 HTML 内容渲染为图片文件。

        Args:
            html_content: 完整 HTML 字符串。
            output_path: 输出 PNG 路径。
            selector: 截图目标选择器。
            wait_until: 预留参数（本地加载仍用 domcontentloaded + 地图就绪等待）。
            viewport: 可选临时视口 {"width": int, "height": int}。
                用于大尺寸卡片（如 S-Net 1400×1000）；截图后会恢复默认 800×800，
                避免污染页面池中的其它渲染任务。
            render_label: 渲染来源标签，用于成功日志区分场景。
        """
        resolved_viewport = self._normalize_viewport(viewport)
        label = self._normalize_render_label(render_label)
        # 远程模式直接走 HTTP 渲染接口，本地模式则复用页面池执行截图。
        if self._mode == "remote":
            if not self._initialized:
                logger.warning("[灾害预警] 浏览器未初始化，尝试初始化...")
                await self.initialize()
            return await self._render_card_via_http(
                html_content,
                output_path,
                selector,
                viewport=resolved_viewport,
                render_label=label,
                event_stream=event_stream,
            )

        # 本地模式：使用 Playwright
        if not await self._ensure_local_browser_ready():
            logger.error(f"[灾害预警] 浏览器不可用，无法渲染{label}")
            return None

        page: Page | None = None
        page_returned = False
        render_succeeded = False
        start_time = time.time()

        acquired_semaphore = False
        console_messages: list[str] = []
        page_errors: list[str] = []
        request_failures: list[str] = []
        # 瓦片类资源失败：方案A降级为 debug 收集，末尾统一去重汇总。
        tile_request_failures: list[str] = []
        benign_request_failures: list[str] = []
        listeners_attached = False
        _record_console = None
        _record_page_error = None
        _record_request_failed = None
        try:
            # 并发控制 - 限制同时渲染的数量
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=20.0)
                acquired_semaphore = True
            except asyncio.TimeoutError:
                logger.error("[灾害预警] 等待渲染信号量超时，系统负载过高")
                return None

            try:
                # 本地模式：从池中获取可用页面；坏页直接丢弃并补充。
                page = await self._acquire_usable_page()
                if page is None:
                    logger.error("[灾害预警] 无法获取可用页面对象")
                    return None

                try:

                    def _record_console(msg):
                        try:
                            location = msg.location or {}
                            entry = (
                                f"[{msg.type}] {self._truncate_debug_text(msg.text)}"
                                f" @ {location.get('url', '')}:{location.get('lineNumber', '')}:{location.get('columnNumber', '')}"
                            )
                            console_messages.append(entry)
                            if msg.type not in {"error", "warning"}:
                                return
                            text_lower = str(msg.text or "").lower()
                            # 方案A：瓦片资源加载失败时，requestfailed 事件已精确记录
                            # URL 与错误码，此处浏览器侧 console 的 "Failed to load resource"
                            # 是同一事件的重复上报。降级为 debug，避免每条瓦片刷两条 WARN。
                            is_dup_resource_log = (
                                "failed to load resource" in text_lower
                                and any(
                                    marker in text_lower
                                    for marker in (
                                        "tilemap.fanstudio.tech",
                                        "/petallight/",
                                        "/petaldark/",
                                        "/arcwi/",
                                        "/arcwob/",
                                        "/arcwh/",
                                        "/geovis/",
                                        "tile.openstreetmap",
                                        "tiles.",
                                        "/tiles/",
                                    )
                                )
                            )
                            if is_dup_resource_log:
                                logger.debug(
                                    f"[灾害预警] 忽略与资源请求失败重复的控制台日志: {entry}"
                                )
                            else:
                                logger.warning(f"[灾害预警] 页面控制台{entry}")
                        except Exception as hook_err:
                            logger.debug(f"[灾害预警] 记录控制台日志失败: {hook_err}")

                    def _record_page_error(exc):
                        try:
                            text = self._truncate_debug_text(exc)
                            page_errors.append(text)
                            logger.warning(f"[灾害预警] 页面脚本异常: {text}")
                        except Exception as hook_err:
                            logger.debug(f"[灾害预警] 记录页面脚本异常失败: {hook_err}")

                    def _record_request_failed(req):
                        try:
                            failure = req.failure
                            failure_text = ""
                            if failure:
                                if isinstance(failure, dict):
                                    failure_text = failure.get("errorText", "")
                                else:
                                    failure_text = str(failure)
                            resource_type = ""
                            try:
                                resource_type = str(
                                    getattr(req, "resource_type", "") or ""
                                )
                            except Exception:
                                resource_type = ""
                            entry = (
                                f"{req.method} {req.url} -> "
                                f"{self._truncate_debug_text(failure_text or 'unknown failure')}"
                            )
                            is_tile = self._is_map_tile_url(getattr(req, "url", None))
                            # 方案A：瓦片类失败（含证书过期/中止等）一律降级为 debug，
                            # 仅在末尾统一汇总一条，避免每张瓦片刷屏。
                            if is_tile:
                                tile_request_failures.append(entry)
                                logger.debug(
                                    f"[灾害预警] 瓦片资源请求失败(已降级为debug): {entry}"
                                )
                                return
                            if self._is_benign_request_failure(
                                url=getattr(req, "url", None),
                                failure_text=failure_text,
                                resource_type=resource_type,
                            ):
                                # 非瓦片但属于重试/视野更新导致的中止请求：仅 debug。
                                benign_request_failures.append(entry)
                                logger.debug(
                                    f"[灾害预警] 忽略可预期的资源中止: {entry}"
                                )
                                return
                            request_failures.append(entry)
                            logger.warning(f"[灾害预警] 页面资源请求失败: {entry}")
                        except Exception as hook_err:
                            logger.debug(f"[灾害预警] 记录请求失败失败: {hook_err}")

                    page.on("console", _record_console)
                    page.on("pageerror", _record_page_error)
                    page.on("requestfailed", _record_request_failed)
                    listeners_attached = True

                    # 大尺寸卡片（如 S-Net）临时放大视口，截图后在 finally 中恢复默认
                    if resolved_viewport:
                        await page.set_viewport_size(resolved_viewport)
                        logger.debug(
                            f"[灾害预警] 临时视口已设为 "
                            f"{resolved_viewport['width']}x{resolved_viewport['height']}"
                        )

                    # 本地模式：使用 file:// 协议（支持相对路径资源）
                    temp_html = None
                    try:
                        # 创建临时 HTML 文件
                        with tempfile.NamedTemporaryFile(
                            mode="w", suffix=".html", delete=False, encoding="utf-8"
                        ) as f:
                            temp_html = f.name
                            f.write(html_content)

                        # 使用 file:// 协议加载，支持相对路径
                        file_url = f"file://{temp_html}"
                        await page.goto(
                            file_url,
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                    finally:
                        # 清理临时 HTML 文件
                        if temp_html and os.path.exists(temp_html):
                            try:
                                os.unlink(temp_html)
                            except Exception:
                                pass

                    # 仅在页面实际包含地图区域时等待地图渲染完成标记。
                    # 地震列表等纯卡片模板并不包含地图容器，如果统一等待 .map-ready，
                    # 会在正常场景下产生误导性的“地图超时”诊断日志。
                    has_map_related_nodes = await page.evaluate("""
                        () => {
                            const selectors = [
                                '.map-ready',
                                '#map',
                                '#map-area',
                                '.map-container',
                                '#map-container',
                                '.leaflet-container'
                            ];
                            return selectors.some((selector) => document.querySelector(selector));
                        }
                    """)
                    if has_map_related_nodes:
                        try:
                            await page.wait_for_selector(
                                ".map-ready", state="attached", timeout=10000
                            )
                            logger.debug("[灾害预警] 地图渲染标记已就绪")
                        except Exception:
                            logger.warning(
                                f"[灾害预警] {label}等待 .map-ready 标记超时，地图可能未完全加载"
                            )
                            if request_failures:
                                logger.warning(
                                    f"[灾害预警] {label}地图等待超时期间捕获到资源失败: "
                                    f"{' | '.join(request_failures[-5:])}"
                                )
                            if page_errors:
                                logger.warning(
                                    f"[灾害预警] {label}地图等待超时期间捕获到脚本异常: "
                                    f"{' | '.join(page_errors[-3:])}"
                                )
                            await self._log_page_diagnostics(
                                page, reason="map-ready-timeout"
                            )
                            # 兜底等待，确保至少能看到部分内容
                            await asyncio.sleep(0.2)

                    # 等待卡片元素可见
                    try:
                        await page.wait_for_selector(
                            selector, state="visible", timeout=2000
                        )
                    except Exception:
                        # 兜底：尝试找常见的类名。该分支在部分模板中属于正常兼容路径，不额外输出诊断日志。
                        logger.debug(
                            f"[灾害预警] 选择器 {selector} 未找到，尝试备用选择器"
                        )
                        selector = ".quake-card"
                        await page.wait_for_selector(
                            selector, state="visible", timeout=1000
                        )

                    # 截图：只截取元素，背景透明；字体等待超时会自动降级重试。
                    await self._screenshot_card(
                        page,
                        selector,
                        output_path,
                        timeout_ms=10000,
                    )

                    elapsed = time.time() - start_time

                    if os.path.exists(output_path):
                        # 方案B：瓦片类失败按 URL->错误码 去重后汇总输出，避免逐条刷屏。
                        tile_summary = self._dedupe_failures(tile_request_failures)
                        if request_failures:
                            logger.warning(
                                f"[灾害预警] {label}渲染虽成功，但捕获到资源请求失败: "
                                f"{' | '.join(request_failures[-5:])}"
                            )
                        if tile_summary:
                            logger.warning(
                                f"[灾害预警] {label}渲染虽成功，但有 {len(tile_request_failures)} 个瓦片请求失败"
                                f"（去重后 {len(tile_summary)} 项）: {' | '.join(tile_summary[-5:])}"
                            )
                        if (
                            not request_failures
                            and not tile_summary
                            and benign_request_failures
                        ):
                            logger.debug(
                                f"[灾害预警] {label}渲染成功，已忽略 "
                                f"{len(benign_request_failures)} 条可预期资源中止"
                            )
                        if page_errors:
                            logger.warning(
                                f"[灾害预警] {label}渲染虽成功，但捕获到页面脚本异常: "
                                f"{' | '.join(page_errors[-3:])}"
                            )
                        plugin_logger.info(
                            f"[灾害预警] {label}渲染成功，耗时 {elapsed:.3f}秒",
                            is_event_linked=True,
                            event_stream=event_stream,
                            is_silent_window=True,
                        )
                        render_succeeded = True
                        return output_path
                    else:
                        logger.warning(f"[灾害预警] {label}截图未生成文件")
                        await self._log_page_diagnostics(
                            page, reason="screenshot-missing"
                        )
                        return None

                finally:
                    # 页面池复用前必须卸掉本次监听器，否则 requestfailed 会随复用次数叠加刷屏。
                    if page and listeners_attached:
                        for event_name, handler in (
                            ("console", _record_console),
                            ("pageerror", _record_page_error),
                            ("requestfailed", _record_request_failed),
                        ):
                            if handler is None:
                                continue
                            try:
                                page.remove_listener(event_name, handler)
                            except Exception:
                                try:
                                    page.off(event_name, handler)
                                except Exception:
                                    pass
                        listeners_attached = False

                    # 成功：恢复视口后归还；失败：直接丢弃坏页并补池，避免污染后续渲染。
                    if page and not page_returned:
                        if render_succeeded:
                            if resolved_viewport:
                                try:
                                    if await self._is_page_usable(page):
                                        await page.set_viewport_size(
                                            dict(self.DEFAULT_VIEWPORT)
                                        )
                                except Exception as restore_err:
                                    logger.debug(
                                        f"[灾害预警] 恢复默认视口失败: {restore_err}"
                                    )
                            await self._return_page_to_pool(page)
                        else:
                            try:
                                if not page.is_closed():
                                    await page.close()
                                    logger.debug("[灾害预警] 已关闭损坏的页面")
                            except Exception:
                                pass
                            await self._replenish_page_pool()
                        page_returned = True
            finally:
                # 释放信号量
                if acquired_semaphore:
                    self._semaphore.release()

        except Exception as e:
            logger.error(f"[灾害预警] {label}渲染失败: {e}")
            # 上报卡片渲染错误到遥测
            if self._telemetry and self._telemetry.enabled:
                await self._telemetry.track_error(
                    e, module="core.browser_manager.render_card"
                )

            # 内层 finally 通常已处理页面回收；这里再兜底一次，避免异常路径泄漏坏页。
            if page and not page_returned:
                try:
                    if not page.is_closed():
                        await page.close()
                        logger.debug("[灾害预警] 已关闭损坏的页面")
                except Exception:
                    pass
                page_returned = True
                await self._replenish_page_pool()

            # 目标关闭类错误即使页面已回收，也主动确认浏览器可恢复。
            if self._is_target_closed_error(e):
                await self._ensure_local_browser_ready()

            return None

    async def _render_card_via_http(
        self,
        html_content: str,
        output_path: str,
        selector: str,
        viewport: dict[str, int] | None = None,
        render_label: str | None = None,
        event_stream: str | None = None,
    ) -> str | None:
        """使用 browserless HTTP API 渲染卡片。

        viewport 可选；未提供时使用默认 800×800。
        """
        label = self._normalize_render_label(render_label)
        start_time = time.time()

        # 对注入的 selector 进行 JSON 编码，规避转义和 JS 语法截断安全风险。
        js_encoded_selector = json.dumps(selector)

        # 注入大小修正脚本，将 html 和 body 的大小强制与卡片对齐。
        # 远程模式不传递 selector 给 browserless，从而完美避开 browserless 本身在 float 宽高时的 setViewportSize Bug。
        # 代之以在 JS 中锁死 html 与 body 为卡片的 Math.ceil() 精准整数长宽，并使用 fullPage 截图以获得完美卡片大小和透明背景。
        if selector:
            inject_js = f"""
<script>
(function() {{
    function fixSize() {{
        try {{
            var selectorStr = {js_encoded_selector};
            var el = document.querySelector(selectorStr) || document.querySelector('.quake-card') || document.querySelector('.container');
            if (el) {{
                document.documentElement.style.margin = '0';
                document.documentElement.style.padding = '0';
                document.body.style.margin = '0';
                document.body.style.padding = '0';

                var rect = el.getBoundingClientRect();
                var w = Math.ceil(rect.width);
                var h = Math.ceil(rect.height);
                if (w > 0 && h > 0) {{
                    document.documentElement.style.width = w + 'px';
                    document.documentElement.style.height = h + 'px';
                    document.documentElement.style.overflow = 'hidden';
                    document.body.style.width = w + 'px';
                    document.body.style.height = h + 'px';
                    document.body.style.overflow = 'hidden';
                }}
            }}
        }} catch (e) {{
            console.error('Error fixing size:', e);
        }}
    }}
    if (document.readyState !== 'loading') {{
        fixSize();
    }} else {{
        document.addEventListener('DOMContentLoaded', fixSize);
    }}
    window.addEventListener('load', fixSize);
    // 延迟多次调用，以应对地图或瓦片等异步资源载入撑开或调整卡片高度
    setTimeout(fixSize, 200);
    setTimeout(fixSize, 600);
    setTimeout(fixSize, 1200);
    setTimeout(fixSize, 2000);
    setTimeout(fixSize, 3000);
    // 对一些极其缓慢的渲染兜底再次修剪
    setTimeout(fixSize, 5000);
}})();
</script>
"""
            if "</body>" in html_content:
                html_content = html_content.replace("</body>", f"{inject_js}</body>")
            else:
                html_content = html_content + inject_js

        # 构建请求 URL
        api_url = self._server_url
        if not api_url.endswith("/"):
            api_url += "/"
        api_url += "screenshot"

        try:
            # 构建请求体 - 使用 browserless screenshot API
            # 注意：此处全量使用 fullPage: True 截图，绝对不传任何顶级 selector，以彻底规避底层 setViewportSize 错误
            # gotoOptions 的 waitUntil 使用兼容 Puppeteer/Playwright 的 networkidle2 (或 networkidle0) 选项，
            # 避免直接传递仅由 Playwright 独占的 networkidle 属性从而引发远程服务报错。
            payload = {
                "html": html_content,
                "options": {
                    "type": "png",
                    "omitBackground": True,
                    "fullPage": True,  # 使用 fullPage 配合被修剪成卡片大小的 html 与 body
                },
                "gotoOptions": {
                    "waitUntil": "networkidle2",  # 确保地图瓦片等网络连接稳定(Puppeteer 格式)
                    "timeout": 60000,
                },
                "viewport": {
                    "width": (
                        viewport["width"]
                        if viewport
                        else self.DEFAULT_VIEWPORT["width"]
                    ),
                    "height": (
                        viewport["height"]
                        if viewport
                        else self.DEFAULT_VIEWPORT["height"]
                    ),
                    "deviceScaleFactor": 2,
                },
                "waitForTimeout": 3000,  # 额外等待 3 秒，确保地图瓦片完全展现并让 JS 执行完锁定尺寸
            }

            # 我们不传 options 里的 "selector"，但在 waitForSelector 选项中，我们可以只“等待”选择器出现而不触发裁剪视口
            if selector:
                payload["waitForSelector"] = {
                    "selector": selector,
                    "visible": True,
                    "timeout": 15000,
                }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=90),  # 90 秒超时保护
                ) as response:
                    if response.status == 200:
                        # 保存截图
                        image_data = await response.read()
                        with open(output_path, "wb") as f:
                            f.write(image_data)

                        elapsed = time.time() - start_time
                        plugin_logger.info(
                            f"[灾害预警] {label}渲染成功（HTTP API），耗时 {elapsed:.3f}秒",
                            is_event_linked=True,
                            event_stream=event_stream,
                            is_silent_window=True,
                        )
                        return output_path
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"[灾害预警] browserless API 返回错误: {response.status} - {error_text}"
                        )
                        # 降级策略：如果由于浮点数 viewportSize 报错，自动关闭 fullPage 进行降级截图重试
                        if (
                            "expected integer" in error_text
                            or "viewportSize" in error_text
                        ):
                            logger.warning(
                                "[灾害预警] 检测到 browserless viewport 浮点数限制错误，尝试禁用 fullPage 并以固定视口大小降级重试..."
                            )
                            fallback_payload = dict(payload)
                            fallback_payload["options"] = {
                                "type": "png",
                                "omitBackground": True,
                                "fullPage": False,  # 禁用 fullPage 以避免 browserless 底层自动计算 float scrollHeight 并设置 viewportSize
                            }
                            try:
                                async with session.post(
                                    api_url,
                                    json=fallback_payload,
                                    timeout=aiohttp.ClientTimeout(total=60),
                                ) as fallback_response:
                                    if fallback_response.status == 200:
                                        image_data = await fallback_response.read()
                                        with open(output_path, "wb") as f:
                                            f.write(image_data)
                                        elapsed = time.time() - start_time
                                        plugin_logger.info(
                                            f"[灾害预警] {label}渲染通过降级重试成功（HTTP API），耗时 {elapsed:.3f}秒",
                                            is_event_linked=True,
                                            event_stream=event_stream,
                                            is_silent_window=True,
                                        )
                                        return output_path
                                    else:
                                        fallback_error = await fallback_response.text()
                                        logger.error(
                                            f"[灾害预警] browserless API 降级重试依然返回错误: {fallback_response.status} - {fallback_error}"
                                        )
                            except Exception as fallback_ex:
                                logger.error(
                                    f"[灾害预警] browserless API 降级重试请求失败: {fallback_ex}"
                                )
                        return None

        except asyncio.TimeoutError:
            logger.error(f"[灾害预警] {label} browserless API 请求超时")
            return None
        except Exception as e:
            logger.error(f"[灾害预警] {label} browserless API 请求失败: {e}")
            if self._telemetry and self._telemetry.enabled:
                await self._telemetry.track_error(
                    e, module="core.browser_manager._render_card_via_http"
                )
            return None

    async def close(self):
        """关闭浏览器管理器。

        与 _ensure_local_browser_ready 的重建路径共用 init_lock，
        避免 close 与 auto-recover 并发导致状态撕裂。
        """
        async with self._init_lock:
            if self._closed:
                logger.debug("[灾害预警] 浏览器已关闭，跳过")
                return

            logger.debug("[灾害预警] 正在关闭浏览器...")
            self._closed = True
            await self._cleanup()
            logger.debug("[灾害预警] 浏览器已关闭")

    async def _cleanup(self):
        """清理资源，确保前一步失败也不影响后续步骤继续执行。"""
        cleanup_errors = []

        # 步骤 1: 关闭页面池中的所有页面
        try:
            while not self._page_pool.empty():
                try:
                    page = self._page_pool.get_nowait()
                    await page.close()
                except Exception as e:
                    cleanup_errors.append(f"关闭页面失败: {e}")
                    logger.debug(f"[灾害预警] 关闭页面失败: {e}")
        except Exception as e:
            cleanup_errors.append(f"清理页面池失败: {e}")
            logger.warning(f"[灾害预警] 清理页面池时发生异常: {e}")

        # 步骤 2: 关闭共享 context（与页面池同生命周期，先于浏览器关闭）
        # 加锁防止与 _ensure_local_context 的并发创建交错；并联动维护
        # _context_closed 标志，供存活判断使用。
        async with self._context_lock:
            if self._context is not None:
                try:
                    await self._context.close()
                except Exception as e:
                    cleanup_errors.append(f"关闭浏览器上下文失败: {e}")
                    logger.debug(f"[灾害预警] 关闭浏览器上下文失败: {e}")
                finally:
                    # 无论成败都置空，避免重建路径残留旧 context 引用。
                    self._context = None
            # 显式标记已关闭：.browser 属性在 close() 后仍可访问，
            # 不能依赖属性探测判断存活，改由标志与清理路径联动。
            self._context_closed = True

        # 步骤 3: 关闭浏览器
        try:
            if self._browser:
                await self._browser.close()
                self._browser = None
        except Exception as e:
            cleanup_errors.append(f"关闭浏览器失败: {e}")
            logger.warning(f"[灾害预警] 关闭浏览器失败: {e}")
            # 即使关闭失败,也强制置空引用,防止后续误用
            self._browser = None

        # 步骤 4: 停止 Playwright
        try:
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            cleanup_errors.append(f"停止 Playwright 失败: {e}")
            logger.warning(f"[灾害预警] 停止 Playwright 失败: {e}")
            # 即使停止失败,也强制置空引用
            self._playwright = None

        # 标记为未初始化
        self._initialized = False

        # 如果有清理错误,记录汇总日志
        if cleanup_errors:
            logger.warning(
                f"[灾害预警] 资源清理过程中遇到 {len(cleanup_errors)} 个错误"
            )

    def __del__(self):
        """析构函数 - 确保资源释放"""
        if self._browser or self._playwright:
            logger.warning(
                "[灾害预警] 检测到未正常关闭的浏览器资源，这可能导致进程泄漏"
            )
