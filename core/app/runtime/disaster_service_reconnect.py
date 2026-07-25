"""
灾害服务手动重连编排服务。
负责批量检查连接状态、补齐 connection_info 并触发 WebSocket 强制重连，
进一步减少 DisasterWarningService 中的运维编排职责。
"""

from __future__ import annotations

from astrbot.api import logger


class DisasterServiceReconnectService:
    """灾害服务手动重连编排服务。

    该服务主要面向管理端或运维指令场景：
    当用户希望立刻对离线连接发起一次主动重连时，统一由这里完成状态检查与底层调用。
    """

    def __init__(self, service):
        # 主服务中维护了连接计划与连接管理器，本服务只负责在其之上做重连编排。
        self.service = service  # 主服务 DisasterWarningService 实例

    async def reconnect_all_sources(self) -> dict[str, str]:
        """强制重连所有已启用但离线的数据源。"""
        results: dict[str, str] = {}
        # 校验 WebSocket 管理器是否正常就绪
        if not self.service.ws_manager:
            return {"error": "WebSocket管理器未初始化"}

        reconnect_count = 0
        # 遍历已配置的所有数据源连接计划
        for conn_name, conn_config in self.service.connections.items():
            # 已在线连接无需重复触发重连，避免无谓打断。
            if self._is_connected(conn_name):
                results[conn_name] = "已连接 (跳过)"
                continue

            try:
                # 某些连接可能尚未完成首次建连，因此连接管理器内部还没有对应附加信息；
                # 在强制重连前先补齐这些字段，方便底层重连逻辑与状态展示复用。
                self._ensure_connection_info(conn_name, conn_config)
                # 触发底层数据源物理连接重连操作
                triggered = await self._force_reconnect(conn_name)
                if triggered:
                    results[conn_name] = "✅ 已触发重连"
                    reconnect_count += 1
                else:
                    results[conn_name] = "⚠️ 重连未触发"
            except Exception as e:
                # 记录单个连接的失败详情，但不阻断其他连接的重试流程
                results[conn_name] = f"❌ 失败: {e}"
                logger.error(f"[灾害预警] 手动重连 {conn_name} 失败: {e}")

        logger.info(f"[灾害预警] 手动重连操作完成，触发了 {reconnect_count} 个重连任务")
        return results

    def _is_connected(self, conn_name: str) -> bool:
        """
        检查指定连接当前是否已连接。

        Args:
            conn_name (str): 连接的标识名
        """
        # 如果底层 WebSocket 映射表不存在，则未连接
        if conn_name not in self.service.ws_manager.connections:
            return False
        ws = self.service.ws_manager.connections[conn_name]
        return not ws.closed  # 判断底层 socket 连接是否已关闭

    def _ensure_connection_info(self, conn_name: str, conn_config: dict) -> None:
        """确保连接管理器中存在连接的动态属性附加信息。"""
        # 如果已经存在相关信息，跳过不重复覆盖
        if conn_name in self.service.ws_manager.connection_info:
            return

        # 组装连接基础元数据信息
        connection_info = {
            "connection_name": conn_name,
            "handler_type": conn_config["handler"],
            "data_source": conn_config.get("data_source", conn_name),
            "established_time": None,
            "backup_url": conn_config.get("backup_url"),
        }
        # FAN Studio 鉴权字段需一并补齐，否则手动重连后无法发送 auth 包。
        if conn_config.get("fan_app_id"):
            connection_info["fan_app_id"] = conn_config["fan_app_id"]
        if conn_config.get("fan_api_key"):
            connection_info["fan_api_key"] = conn_config["fan_api_key"]
        # 这里的结构与连接管理器常规建连流程保持一致，
        # 这样手动重连与自动重连在读取附加信息时不会出现字段缺失的问题。
        self.service.ws_manager.connection_info[conn_name] = {
            "uri": conn_config["url"],
            "headers": None,
            "connection_type": "websocket",
            "established_time": None,
            "retry_count": 0,
            **connection_info,
        }

    async def _force_reconnect(self, conn_name: str) -> bool:
        """调用 WebSocketManager 执行底层强制物理重连。"""
        # 并非所有连接管理器实现都强制要求提供该接口，
        # 因此这里先做能力检查，再决定是否触发主动重连。
        if not hasattr(self.service.ws_manager, "force_reconnect"):
            return False
        return await self.service.ws_manager.force_reconnect(conn_name)
