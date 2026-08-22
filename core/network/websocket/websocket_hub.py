"""
管理端 WebSocket 广播中心。
负责维护前端连接集合，并统一提供完整快照广播、常规更新广播、事件推送与批量关闭能力。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger


class WebSocketHub:
    """WebSocket 连接与广播中心。"""

    def __init__(self):
        """初始化连接集合。"""
        # 前端连入管理后台的活跃 WebSocket 客户端容器
        self.connections: list[Any] = []

    def add(self, websocket) -> None:
        """注册连接。"""
        # 添加连入的前端 websocket 客户端句柄到连接池
        self.connections.append(websocket)

    def remove(self, websocket) -> None:
        """移除连接。"""
        # 从活跃连接池中安全移除已断开的前端 websocket 客户端句柄
        if websocket in self.connections:
            self.connections.remove(websocket)

    def count(self) -> int:
        """返回当前连接数。"""
        # 返回当前连接池中保存的活跃客户端数量
        return len(self.connections)

    async def send_full_update(
        self,
        websocket,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> bool:
        """向单个客户端发送完整更新。"""
        try:
            # 动态生成当前系统的完整数据载荷快照
            data = await data_factory()
            # 以 JSON 格式打包并发送全量更新包给前端
            await websocket.send_json({"type": "full_update", "data": data})
            return True
        except Exception as e:
            # 发送失败通常意味着连接已被对端关闭，将其从池中移除
            logger.debug(f"[灾害预警] 发送数据失败: {e}")
            self.remove(websocket)
            return False

    async def broadcast_update(
        self,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        """向所有连接客户端广播常规更新。"""
        # 如果连接池内没有在线客户端，直接退出，不执行耗时的数据构建
        if not self.connections:
            return

        # 统一生成广播数据，避免在循环中对每个客户端重复调用 data_factory
        data = await data_factory()
        message = {"type": "update", "data": data}
        disconnected = []

        # 依次发送给当前连接池内的每一个前端客户端
        for websocket in list(self.connections):
            try:
                await websocket.send_json(message)
            except Exception:
                # 若发生异常，则收集起来准备在后续逻辑中批量剔除
                disconnected.append(websocket)

        # 集中清理发送失败的无效连接
        for websocket in disconnected:
            self.remove(websocket)

    async def broadcast_event(
        self,
        data_factory: Callable[[], Awaitable[dict[str, Any]]],
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """向所有连接客户端广播事件更新。"""
        # 快速短路返回
        if not self.connections:
            return

        # 生成最新的状态数据并拼装成事件推送载荷
        data = await data_factory()
        message: dict[str, Any] = {
            "type": "event",
            "data": data,
        }
        # 如果有新产生的地震或海啸具体数据，也一并塞入包内
        if event_data:
            message["new_event"] = event_data

        disconnected = []
        # 分发给所有在线的前端网页客户端
        for websocket in list(self.connections):
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.append(websocket)

        # 清除失效的连接句柄
        for websocket in disconnected:
            self.remove(websocket)

    async def close_all(self) -> None:
        """关闭并清空所有连接。"""
        # 遍历所有客户端进行握手关闭
        for websocket in list(self.connections):
            try:
                await websocket.close()
            except Exception:
                pass
        # 最终清空容器
        self.connections.clear()
