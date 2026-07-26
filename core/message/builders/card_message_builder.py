"""
卡片消息构建器。
负责渲染地震列表等卡片类消息，减少 MessagePushManager 中的展示渲染职责。
"""

from __future__ import annotations

import os
import time

from jinja2 import Template

from astrbot.api import logger

from ....utils.version import get_plugin_version


class CardMessageBuilder:
    """卡片消息构建器。"""

    def __init__(self, *, plugin_root: str, temp_dir: str, browser_manager):
        # 构建器本身不维护复杂状态，只持有模板目录、临时目录与浏览器渲染能力。
        self.plugin_root = plugin_root
        self.temp_dir = temp_dir
        self.browser_manager = browser_manager

    async def render_earthquake_list_card(
        self, events: list[dict], source_name: str
    ) -> str | None:
        """渲染地震列表卡片。"""
        try:
            # 地震列表卡片使用基础模板目录中的通用列表模板。
            template_path = os.path.join(
                self.plugin_root,
                "resources",
                "card_templates",
                "Base",
                "earthquake_list.html",
            )

            if not os.path.exists(template_path):
                logger.error(f"[灾害预警] 找不到地震列表模板: {template_path}")
                return None

            with open(template_path, encoding="utf-8") as f:
                template_content = f.read()

            version = get_plugin_version()
            footer_text = (
                f"🔧 @DBJD-CR/astrbot_plugin_disaster_warning (灾害预警) {version}"
            )
            # 模板上下文尽量保持扁平，便于 HTML 模板层直接渲染。
            context = {
                "source_name": source_name,
                "events": events,
                "generated_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "footer_text": footer_text,
            }

            template = Template(template_content)
            html_content = template.render(**context)

            # 输出文件名只要求本次渲染基本唯一，真正去重由上层决定是否复用结果。
            image_filename = f"eq_list_{int(time.time())}.png"
            image_path = os.path.join(self.temp_dir, image_filename)
            return await self.browser_manager.render_card(
                html_content,
                image_path,
                selector="#card-wrapper",
                render_label="地震列表卡片",
            )
        except Exception as e:
            logger.error(f"[灾害预警] 渲染地震列表卡片失败: {e}")
            return None
