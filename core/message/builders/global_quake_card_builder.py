"""
Global Quake 卡片构建器。
负责生成 Global Quake 专用卡片 HTML 与渲染结果，减少 MessagePushManager 中的专用卡片构建职责。
"""

from __future__ import annotations

import base64
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from jinja2 import Template

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image

from ....utils.map_tile_sources import get_tile_url_js
from ..presenters.global_quake_display_context import GlobalQuakeDisplayContextBuilder


class GlobalQuakeCardBuilder:
    """Global Quake 专用卡片构建器。"""

    def __init__(self, *, plugin_root: str, temp_dir: str, browser_manager):
        # 构建器只持有模板资源定位与浏览器渲染所需依赖。
        self.plugin_root = plugin_root
        self.temp_dir = temp_dir
        self.browser_manager = browser_manager

    async def build(
        self,
        earthquake,
        *,
        active_config: dict[str, Any],
        message_format_config: dict[str, Any],
        cache_key_builder: Callable[[Any, dict[str, Any], str], str],
        render_with_cache: Callable[
            [str, Callable[[], Awaitable[str | None]]], Awaitable[str | None]
        ],
    ) -> MessageChain | None:
        """构建 Global Quake 卡片消息。"""
        try:
            display_timezone = active_config.get("display_timezone", "UTC+8")
            options = {"timezone": display_timezone}
            # 展示上下文由 presenter 子系统提供，builder 只补充模板与地图相关参数。
            context = GlobalQuakeDisplayContextBuilder.build(earthquake, options)

            zoom_level = message_format_config.get("map_zoom_level", 5)
            context["zoom_level"] = zoom_level

            # 地图底图源和缩放级别由消息格式配置控制，方便不同会话按需调整展示风格。
            map_source = message_format_config.get("map_source", "PetalMap矢量图亮")
            context["map_source"] = map_source
            context["tile_url"] = get_tile_url_js(map_source)

            template_name = message_format_config.get("global_quake_template", "Aurora")
            resources_dir = os.path.join(self.plugin_root, "resources")
            template_path = os.path.join(
                resources_dir, "card_templates", template_name, "global_quake.html"
            )

            if not os.path.exists(template_path):
                logger.error(f"[灾害预警] 找不到模板文件: {template_path}")
                return None

            with open(template_path, encoding="utf-8") as f:
                template_content = f.read()

            playwright_mode = active_config.get("message_format", {}).get(
                "playwright_mode", "local"
            )
            # 远程浏览器无法稳定访问本地静态文件时，改用公开静态资源地址。
            if playwright_mode == "remote":
                context["leaflet_js_url"] = (
                    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
                )
                context["leaflet_css_url"] = (
                    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
                )
            else:
                leaflet_path = os.path.abspath(
                    os.path.join(resources_dir, "card_templates", "leaflet.js")
                )
                leaflet_css_path = os.path.abspath(
                    os.path.join(resources_dir, "card_templates", "leaflet.css")
                )
                context["leaflet_js_url"] = f"file://{leaflet_path}"
                context["leaflet_css_url"] = f"file://{leaflet_css_path}"

            map_helper_path = os.path.abspath(
                os.path.join(resources_dir, "card_templates", "map_render_helper.js")
            )
            with open(map_helper_path, encoding="utf-8") as helper_file:
                context["map_render_helper_js"] = helper_file.read()

            template = Template(template_content)
            html_content = template.render(**context)

            card_cache_key = cache_key_builder(
                earthquake,
                message_format_config,
                display_timezone,
            )

            async def render_gq_card() -> str | None:
                # 真实去重依赖外层 cache_key，这里的文件名只要保证落盘时基本唯一即可。
                image_filename = (
                    f"gq_card_{earthquake.id}_{int(datetime.now().timestamp())}.png"
                )
                image_path = os.path.join(self.temp_dir, image_filename)
                return await self.browser_manager.render_card(
                    html_content,
                    image_path,
                    selector="#card-wrapper",
                    render_label="GlobalQuake 卡片",
                )

            result_path = await render_with_cache(card_cache_key, render_gq_card)
            if result_path and os.path.exists(result_path):
                try:
                    with open(result_path, "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode()
                    return MessageChain([Image.fromBase64(b64_data)])
                except Exception as e:
                    logger.error(f"[灾害预警] 读取图片转换为Base64失败: {e}")
                    return None

            logger.warning("[灾害预警] Global Quake 卡片渲染失败")
            return None
        except Exception as e:
            logger.error(f"[灾害预警] Global Quake 卡片构建失败: {e}")
            return None
