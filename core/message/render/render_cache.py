"""
渲染产物缓存器。
负责渲染结果的缓存、过期清理与并发去重，减少 MessagePushManager 中的基础设施职责。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable

from astrbot.api import logger


class RenderImageCache:
    """渲染图片缓存器。"""

    def __init__(self, ttl_seconds: int = 180):
        # _image_cache 保存已完成渲染结果，_inflight_tasks 用于并发去重。
        self._ttl_seconds = ttl_seconds  # 缓存过期时间 (秒)
        self._image_cache: dict[
            str, tuple[float, str]
        ] = {}  # 缓存表 (缓存键 -> 元组(缓存生成时刻, 本地图片路径))
        self._inflight_tasks: dict[
            str, asyncio.Task[str | None]
        ] = {}  # 处于截图中/渲染中的活跃并发任务表
        self._lock = asyncio.Lock()  # 并发控制锁

    def cleanup(self) -> None:
        """清理过期或失效的缓存记录。"""
        now = time.time()
        expired_keys: list[str] = []
        for key, (cache_time, image_path) in self._image_cache.items():
            # 只要 TTL 超时或目标文件已被外部删除，都需要让缓存失效。
            if now - cache_time > self._ttl_seconds:
                expired_keys.append(key)
                continue
            if not os.path.exists(image_path):
                expired_keys.append(key)

        for key in expired_keys:
            self._image_cache.pop(key, None)

    async def render(
        self,
        cache_key: str,
        renderer: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        """执行带缓存与并发去重的渲染。"""
        render_task: asyncio.Task[str | None] | None = None

        async with self._lock:
            self.cleanup()  # 触发一次过期清理

            cached_item = self._image_cache.get(cache_key)
            if cached_item:
                _, image_path = cached_item
                # 若文件还存在于磁盘上，则直接复用
                if os.path.exists(image_path):
                    return image_path

            # 同一 cache_key 若已有渲染中的任务，后续调用直接等待该任务，
            # 这样可以显著减少并发截图造成的浏览器资源竞争。
            render_task = self._inflight_tasks.get(cache_key)
            if render_task is None:
                # 启动新的异步渲染/截图协程任务
                render_task = asyncio.create_task(renderer())
                self._inflight_tasks[cache_key] = render_task

        try:
            # 异步等待渲染/截图动作完成，获取文件物理路径
            result_path = await render_task
            if result_path and os.path.exists(result_path):
                async with self._lock:
                    # 载入缓存以便下次复用
                    self._image_cache[cache_key] = (time.time(), result_path)
            return result_path
        finally:
            # 确保在当前任务完成 (成功或抛出异常) 时从活跃并发表中弹出
            async with self._lock:
                if self._inflight_tasks.get(cache_key) is render_task:
                    self._inflight_tasks.pop(cache_key, None)
