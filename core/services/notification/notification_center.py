"""通知中心服务。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from .notification_cache_repository import NotificationCacheRepository
from .notification_normalizer import NotificationNormalizer
from .notification_remote_client import NotificationRemoteClient


class NotificationCenter:
    """负责远端通知同步、本地缓存、已读状态和轮询广播编排。"""

    def __init__(self, disaster_service: Any):
        self.disaster_service = disaster_service
        self.config = disaster_service.config
        # 通知数据仓储管理器
        self.repository = NotificationCacheRepository(disaster_service.storage_dir)
        # 远端网络通信客户端
        self.remote_client = NotificationRemoteClient()
        # 数据规范化转换器
        self.normalizer = NotificationNormalizer()
        # 用于已读映射和缓存区更新的并发排他锁
        self._lock = asyncio.Lock()
        # 网络同步锁，防止多协程并发重复发起远端拉取请求
        self._sync_state_lock = asyncio.Lock()
        self._sync_in_progress = False
        # 定时轮询通知更新的后台 Task 句柄
        self._poll_task: asyncio.Task | None = None
        # 启动时首次远端同步任务句柄（避免 create_task 无强引用被 GC）
        self._initial_sync_task: asyncio.Task | None = None
        # 内存运行态通知缓存字典
        self._cache: dict[str, Any] = NotificationCacheRepository.empty_cache()

    def _get_settings(self) -> dict[str, Any]:
        """读取通知系统轻量配置。"""
        settings = self.config.get("notification_settings", {})
        return dict(settings) if isinstance(settings, dict) else {}

    def is_enabled(self) -> bool:
        """判断通知系统是否启用。"""
        return bool(self._get_settings().get("enabled", True))

    def _get_poll_interval_seconds(self) -> int:
        """获取轮询间隔，并设置安全下限。"""
        value = self._get_settings().get("poll_interval_seconds", 300)
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            seconds = 300
        # 限制轮询间隔最短不得低于 30 秒，防止远端接口过载
        return max(30, seconds)

    async def load_cache(self) -> None:
        """加载通知缓存。"""
        async with self._lock:
            self._cache = await self.repository.load()

    async def save_cache(self) -> None:
        """保存通知缓存。"""
        async with self._lock:
            await self.repository.save(self._cache)

    def _items_signature(self, items: list[dict[str, Any]]) -> str:
        try:
            # 使用有序 key 序列化获得列表签名，便于高效比对通知列表是否发生了实质内容变更
            return json.dumps(items, ensure_ascii=False, sort_keys=True)
        except TypeError as e:
            logger.warning(f"[灾害预警] 创建通知签名时遇到不可序列化项: {e}")
            return str(items)

    def _build_meta_locked(self) -> dict[str, Any]:
        """基于当前缓存构造通知元信息。"""
        unread_count = 0
        read_map = self._cache.get("read_map", {})
        # 循环核算未读条目数
        for item in self._cache.get("items", []):
            if not read_map.get(str(item.get("id")), False):
                unread_count += 1
        return {
            "unread_count": unread_count,
            "last_sync_at": self._cache.get("last_sync_at"),
            "total_count": len(self._cache.get("items", [])),
        }

    async def get_meta(self) -> dict[str, Any]:
        """获取通知元信息。"""
        async with self._lock:
            return self._build_meta_locked()

    async def get_payload(self) -> dict[str, Any]:
        """获取前端通知中心完整载荷。"""
        async with self._lock:
            read_map = self._cache.get("read_map", {})
            # 在返回的每条通知中组装 _read 已读布尔标志，供前台界面渲染使用
            items = [
                {
                    **item,
                    "_read": bool(read_map.get(str(item.get("id")), False)),
                }
                for item in self._cache.get("items", [])
            ]
            return {
                "items": items,
                "meta": self._build_meta_locked(),
            }

    async def mark_as_read(self, notification_id: int) -> dict[str, Any]:
        """标记单条通知为已读。"""
        async with self._lock:
            self._cache.setdefault("read_map", {})[str(notification_id)] = True
            await self.repository.save(self._cache)
            return {
                "ok": True,
                "id": notification_id,
                "meta": self._build_meta_locked(),
            }

    async def mark_all_as_read(self) -> dict[str, Any]:
        """标记全部通知为已读。"""
        async with self._lock:
            read_map = self._cache.setdefault("read_map", {})
            for item in self._cache.get("items", []):
                read_map[str(item.get("id"))] = True
            await self.repository.save(self._cache)
            return {
                "ok": True,
                "meta": self._build_meta_locked(),
            }

    async def refresh(self) -> bool:
        """同步远端通知，返回通知列表是否变化。"""
        async with self._sync_state_lock:
            if self._sync_in_progress:
                return False
            self._sync_in_progress = True

        try:
            # 请求拉取云端原始通知列表
            raw_items = await self.remote_client.fetch()
            # 清洗并结构化通知信息
            remote_items = self.normalizer.normalize_items(raw_items)
            async with self._lock:
                old_signature = self._items_signature(self._cache.get("items", []))
                new_signature = self._items_signature(remote_items)
                changed = old_signature != new_signature

                current_read_map = self._cache.get("read_map", {})
                active_ids = {str(item.get("id")) for item in remote_items}
                # 构建全新缓存，剔除那些由于远端已删除或过期而失效的旧已读标记，控制内存体积
                next_cache = {
                    **self._cache,
                    "read_map": {
                        key: value
                        for key, value in current_read_map.items()
                        if key in active_ids
                    },
                    "items": remote_items,
                    "last_sync_at": datetime.now(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
                await self.repository.save(next_cache)
                self._cache = next_cache
                return changed
        except Exception as e:
            logger.debug(f"[灾害预警] 同步远端通知失败: {e} (可忽略)")
            return False
        finally:
            async with self._sync_state_lock:
                self._sync_in_progress = False

    async def _broadcast_notification_update(self) -> None:
        """通知管理端前端刷新实时数据。"""
        web_admin_server = getattr(self.disaster_service, "web_admin_server", None)
        if not web_admin_server:
            return
        runtime_service = getattr(web_admin_server, "_runtime_service", None)
        if runtime_service:
            # 通过 WebSocket 通知管理端前台刷新面板快照，使最新的通知红点等能即时刷新出来
            await runtime_service.broadcast_data()

    async def start(self) -> None:
        """启动通知中心。"""
        await self.load_cache()
        if not self.is_enabled():
            logger.info("[灾害预警] 通知系统未启用。")
            return

        # 启动时将首次远端同步改为异步非阻塞，避免网络不通时卡住整个启动流程
        async def _initial_sync() -> None:
            try:
                await self.refresh()
                await self._broadcast_notification_update()
            except Exception as e:
                logger.warning(f"[灾害预警] 启动时同步远端通知失败: {e} (可忽略)")

        self._initial_sync_task = asyncio.create_task(_initial_sync())

        async def _poll_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self._get_poll_interval_seconds())
                    await self.refresh()
                    await self._broadcast_notification_update()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"[灾害预警] 通知轮询任务异常: {e} (可忽略)")

        # 挂载后台长轮询定时协程任务
        self._poll_task = asyncio.create_task(_poll_loop())
        logger.debug("[灾害预警] 通知系统已启动")

    async def _cancel_task(self, task: asyncio.Task | None) -> None:
        """取消并等待后台任务结束。"""
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def stop(self) -> None:
        """停止通知中心。"""
        await self._cancel_task(self._initial_sync_task)
        self._initial_sync_task = None
        await self._cancel_task(self._poll_task)
        self._poll_task = None
        # 停止前将当前缓存内容原子落盘保存
        await self.save_cache()
        logger.debug("[灾害预警] 通知系统已停止")
