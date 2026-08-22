"""台风查询路径图辅助。

把 /台风信息查询 的附图逻辑从命令服务中拆出，
避免在 plugin_query_command_service 里堆叠大段渲染代码。
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger

from ...core.message.push.message_build_service import MessageBuildService


async def append_typhoon_track_image(
    *,
    plugin: Any,
    result: dict[str, Any],
    chain_parts: list,
) -> list:
    """若查询结果含可渲染轨迹，则向 chain_parts 追加路径图。

    策略：
    - 候选顺序：优先 result["data"]，再按 items 顺序
    - 遍历候选，渲染首个含有效 history_track 且可以渲染的项
    - 列表多条时只渲一张，避免一次查询打爆浏览器池
    - 渲染失败不抛出，仅打 warning 并返回原 chain_parts
    """
    if not result.get("success"):
        return chain_parts

    candidates: list[dict[str, Any]] = []
    data_item = result.get("data")
    if isinstance(data_item, dict):
        candidates.append(data_item)
    for item in result.get("items") or []:
        if isinstance(item, dict) and item not in candidates:
            candidates.append(item)
    if not candidates:
        return chain_parts

    service = getattr(plugin, "disaster_service", None)
    message_manager = getattr(service, "message_manager", None) if service else None
    renderer = (
        getattr(message_manager, "typhoon_map_renderer", None)
        if message_manager
        else None
    )
    if renderer is None:
        return chain_parts

    msg_cfg: dict[str, Any] = {}
    try:
        cfg = getattr(message_manager, "config", None) or {}
        if isinstance(cfg, dict):
            raw_msg_cfg = cfg.get("message_format", {})
            if isinstance(raw_msg_cfg, dict):
                msg_cfg = raw_msg_cfg
    except Exception:
        msg_cfg = {}
    # 台风路径图专用瓦片源；未配置时回退暗色底图（与台风卡片主题一致）。
    map_source = msg_cfg.get("typhoon_map_source") or "PetalMap矢量图暗"
    if not str(map_source).strip():
        map_source = "PetalMap矢量图暗"
    playwright_mode = msg_cfg.get("playwright_mode", "local")

    for item in candidates:
        history = item.get("history_track") or []
        if not isinstance(history, list) or not history:
            continue
        can_render = getattr(renderer, "can_render", None)
        if callable(can_render) and not can_render(item):
            continue
        try:
            temp_dir = getattr(message_manager, "temp_dir", None)
            safe_id = str(item.get("typhoon_id") or item.get("eqsc_id") or "q").replace(
                "/", "_"
            )
            img_path = os.path.join(
                str(temp_dir or "."),
                f"typhoon_map_query_{safe_id}_{int(time.time())}.png",
            )
            cache_key = MessageBuildService._build_typhoon_map_cache_key(
                item,
                map_source=str(map_source),
                playwright_mode=str(playwright_mode),
            )

            async def _render_typhoon(
                _item: dict[str, Any] = item, _path: str = img_path
            ) -> str | None:
                return await renderer.render(
                    _item,
                    _path,
                    map_source=str(map_source),
                    playwright_mode=str(playwright_mode),
                )

            render_with_cache = getattr(message_manager, "_render_with_cache", None)
            if callable(render_with_cache):
                out = await render_with_cache(cache_key, _render_typhoon)
            else:
                out = await _render_typhoon()
            if out and os.path.exists(out):
                with open(out, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                chain_parts.append(Comp.Image.fromBase64(b64))
                # 只渲首张可渲染路径图，避免列表查询打爆浏览器池。
                break
        except Exception as render_err:
            logger.warning(f"[灾害预警] 台风查询路径图渲染失败: {render_err}")
    return chain_parts


__all__ = ["append_typhoon_track_image"]
