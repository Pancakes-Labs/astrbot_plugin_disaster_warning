"""
消息管理器初始化装配服务。

该服务负责在消息管理器启动时集中创建过滤器、浏览器管理器、消息构建器和远程媒体抓取器等基础组件，
把初始化阶段的大段装配逻辑从主管理器中拆分出来，从而降低主管理器的构造复杂度。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from ...services.config.config_service import ConfigAccessor
from ...services.identity.event_deduplication_service import EventDeduplicationService
from ..builders.card_message_builder import CardMessageBuilder
from ..builders.global_quake_card_builder import GlobalQuakeCardBuilder
from ..builders.map_attachment_builder import MapAttachmentBuilder
from ..builders.text_message_builder import TextMessageBuilder
from ..render.remote_media_fetcher import RemoteMediaFetcher
from ..render.snet_map_renderer import SnetMapRenderer
from ..render.typhoon_map_renderer import TyphoonMapRenderer
from .browser_manager import BrowserManager


class MessageManagerBootstrapService:
    """消息管理器初始化装配服务。"""

    def __init__(self, manager):
        # 该服务只负责初始化期对象装配，本身不持有复杂运行状态。
        self.manager = manager
        self.config_accessor = ConfigAccessor(manager.config)
        self.map_attachment_builder = None
        self.text_message_builder = None
        self.card_message_builder = None
        self.global_quake_card_builder = None
        self.snet_map_renderer = None
        self.typhoon_map_renderer = None
        self.remote_media_fetcher = None

    def setup_filters(self, config: dict[str, Any]) -> None:
        """初始化全局过滤器与去重器。"""
        event_deduplication_config = self.config_accessor.event_deduplication_config()

        # 共享规则组件不再直接零散挂在管理器上，统一由运行时组件工厂按需构建。

        # 去重器属于最前置的入口保护，
        # 目标是在进入更昂贵的渲染、构图、发送逻辑前尽早拦截重复事件。
        self.manager.deduplicator = EventDeduplicationService(
            time_window_minutes=event_deduplication_config.get(
                "time_window_minutes", 1
            ),
            location_tolerance_km=event_deduplication_config.get(
                "location_tolerance_km", 20.0
            ),
            magnitude_tolerance=event_deduplication_config.get(
                "magnitude_tolerance", 0.5
            ),
        )

    def setup_browser(self, config: dict[str, Any], telemetry=None) -> None:
        """初始化浏览器管理器并按需预热。"""
        msg_config = self.config_accessor.message_format_config()
        raw_pool_size = msg_config.get("browser_pool_size", 2)
        # 池大小允许来自配置文件中的字符串值，因此先做稳妥转换。
        try:
            pool_size = int(raw_pool_size)
        except (TypeError, ValueError):
            pool_size = 2
        else:
            if pool_size < 1:
                pool_size = 2

        playwright_mode = msg_config.get("playwright_mode", "local")
        playwright_server_url = msg_config.get("playwright_server_url", "")
        self.manager.browser_manager = BrowserManager(
            pool_size=pool_size,
            telemetry=telemetry,
            mode=playwright_mode,
            server_url=playwright_server_url,
        )

        # 只有本地浏览器模式且确实需要图形渲染时才后台预热，
        # 这样既能缩短首次渲染等待，也能避免无地图场景白白消耗资源。
        # include_map / GQ 卡 / S-Net / 台风路径图任一启用都预热。
        data_sources = (
            self.manager.config.get("data_sources", {})
            if isinstance(self.manager.config, dict)
            else {}
        )
        snet_cfg = (
            data_sources.get("snet", {}) if isinstance(data_sources, dict) else {}
        )
        # 台风源：FAN 遗留开关 + EQSC 独立轮询开关
        fan_studio_cfg = (
            data_sources.get("fan_studio", {}) if isinstance(data_sources, dict) else {}
        )
        eqsc_cfg = (
            data_sources.get("eqsc", {}) if isinstance(data_sources, dict) else {}
        )
        need_browser = (
            msg_config.get("include_map", False)
            or msg_config.get("use_global_quake_card", False)
            or bool(isinstance(snet_cfg, dict) and snet_cfg.get("enabled", False))
            # 台风路径图查询/推送也依赖 Playwright，启用台风源时一并预热。
            or bool(
                isinstance(fan_studio_cfg, dict)
                and fan_studio_cfg.get("china_typhoon", False)
            )
            or bool(
                isinstance(eqsc_cfg, dict)
                and eqsc_cfg.get("enabled", False)
                and eqsc_cfg.get("typhoon_enrichment", False)
            )
        )
        if playwright_mode == "local" and need_browser:
            logger.debug("[灾害预警] 检测到已启用卡片渲染功能，正在后台预热浏览器...")
            asyncio.create_task(self.manager.browser_manager.initialize())

    def setup_message_components(self) -> None:
        """初始化消息构建与远程媒体依赖。"""
        # 文本构建器、卡片构建器、地图附件构建器都属于展示产物生成层，
        # 它们共同复用插件目录、临时目录与浏览器管理器等基础设施。
        self.map_attachment_builder = MapAttachmentBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
            default_config=self.manager.config,
        )
        self.text_message_builder = TextMessageBuilder(
            default_config=self.manager.config
        )
        self.card_message_builder = CardMessageBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
        )
        self.global_quake_card_builder = GlobalQuakeCardBuilder(
            plugin_root=self.manager.plugin_root,
            temp_dir=str(self.manager.temp_dir),
            browser_manager=self.manager.browser_manager,
        )
        self.snet_map_renderer = SnetMapRenderer(
            browser_manager=self.manager.browser_manager,
            plugin_root=self.manager.plugin_root,
        )
        self.typhoon_map_renderer = TyphoonMapRenderer(
            browser_manager=self.manager.browser_manager,
            plugin_root=self.manager.plugin_root,
        )
        self.remote_media_fetcher = RemoteMediaFetcher(
            # 远程媒体抓取器本身不关心底层会话如何创建，
            # 统一通过回调注入获取会话与判定内容类型的能力。
            session_getter=self.manager.get_remote_media_session,
            image_type_checker=self.manager._remote_media_service.is_image_content_type,
            content_type_guesser=self.manager._remote_media_service.guess_image_content_type,
            image_bytes_checker=self.manager._remote_media_service.looks_like_image_bytes,
        )
