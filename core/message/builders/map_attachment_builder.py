"""
地图附件构建器。
从 MessagePushManager 中拆出通用地图 HTML 渲染与图片生成逻辑。
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from jinja2 import Template

from astrbot.api import logger

from ....utils.map_tile_sources import get_tile_url_js


class MapAttachmentBuilder:
    """地图附件构建器。"""

    def __init__(
        self,
        *,
        plugin_root: str,
        temp_dir: str,
        browser_manager,
        default_config: dict[str, Any],
    ):
        # 地图附件构建依赖模板目录、临时输出目录、浏览器渲染能力与默认消息配置。
        self.plugin_root = plugin_root
        self.temp_dir = temp_dir
        self.browser_manager = browser_manager
        self.default_config = default_config

    async def render_map_image(
        self,
        lat: float,
        lon: float,
        config: dict[str, Any],
        event_caption: str = "",
    ) -> str | None:
        """渲染指定经纬度的地图图片。

        Args:
            lat: 纬度。
            lon: 经度。
            config: 渲染配置（地图源、缩放级别、Playwright 模式等）。
            event_caption: 左上角事件描述文字胶囊文本（时间 震中 震级）。
        """
        try:
            map_source = config.get("map_source", "PetalMap矢量图亮")
            zoom_level = config.get("map_zoom_level", 5)

            resources_dir = os.path.join(self.plugin_root, "resources")
            template_path = os.path.join(
                resources_dir, "card_templates", "Base", "base_map.html"
            )
            if not os.path.exists(template_path):
                logger.error(f"[灾害预警] 找不到通用地图模板: {template_path}")
                return None

            with open(template_path, encoding="utf-8") as f:
                template_content = f.read()

            leaflet_path = os.path.abspath(
                os.path.join(resources_dir, "card_templates", "leaflet.js")
            )
            leaflet_css_path = os.path.abspath(
                os.path.join(resources_dir, "card_templates", "leaflet.css")
            )

            map_helper_path = os.path.abspath(
                os.path.join(resources_dir, "card_templates", "map_render_helper.js")
            )
            with open(map_helper_path, encoding="utf-8") as helper_file:
                map_render_helper_js = helper_file.read()

            # 远程 Playwright 无法直接访问本地 file:// 静态资源时，切换到 CDN 资源。
            playwright_mode = config.get("playwright_mode") or self.default_config.get(
                "message_format", {}
            ).get("playwright_mode", "local")
            if playwright_mode == "remote":
                leaflet_js_url = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
                leaflet_css_url = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
            else:
                leaflet_js_url = f"file://{leaflet_path}"
                leaflet_css_url = f"file://{leaflet_css_path}"

            # 模板上下文只放渲染地图所需的最小参数，避免模板层承担额外业务判断。
            context = {
                "latitude": lat,
                "longitude": lon,
                "zoom_level": zoom_level,
                "map_source": map_source,
                "tile_url": get_tile_url_js(map_source),
                "leaflet_js_url": leaflet_js_url,
                "leaflet_css_url": leaflet_css_url,
                "map_render_helper_js": map_render_helper_js,
                "event_caption": event_caption or "",
            }

            template = Template(template_content)
            html_content = template.render(**context)
            # 文件名加入 event_caption 的稳定哈希：同一坐标同一秒渲染不同事件描述时，
            # 避免后一次渲染覆盖前一次图片（缓存命中可能发送错误的事件描述地图）。
            caption_hash = hashlib.md5(
                str(event_caption or "").encode("utf-8")
            ).hexdigest()[:8]
            image_filename = f"map_{lat}_{lon}_{int(time.time())}_{caption_hash}.png"
            image_path = os.path.join(self.temp_dir, image_filename)
            return await self.browser_manager.render_card(
                html_content,
                image_path,
                selector="#card-wrapper",
                render_label="地震震中地图",
                event_stream="earthquake",
            )
        except Exception as e:
            logger.error(f"[灾害预警] 渲染地图图片时出错: {e}")
            return None
