"""
灾害服务手动重连编排服务。
负责批量检查连接状态、补齐 connection_info 并触发 WebSocket 强制重连，
并把底层连接管理器产生的"手动重连结果"转接为上层业务回执（命令侧异步推送），
进一步减少 DisasterWarningService 中的运维编排职责。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from ...network.websocket.fan_studio_connection_policy import attach_fan_auth_from_plan
from ...sources.display_registry import CONNECTION_DISPLAY_NAMES


class DisasterServiceReconnectService:
    """灾害服务手动重连编排服务。

    该服务主要面向管理端或运维指令场景：
    当用户希望立刻对离线连接发起一次主动重连时，统一由这里完成状态检查与底层调用，
    并把底层 WebSocket 管理器的"手动重连结果回调"翻译成上层业务可消费的回执事件。

    职责划分：
    - 底层 WebSocketManager 只负责物理建连与成功/失败回调的原始广播；
    - 本服务负责把结果按连接映射为友好展示名，并转交给命令侧注册的回执回调，
      最终由命令侧把回执异步推送到触发指令的会话。
    """

    # 手动重连结果等待超时（秒）：超过该时长仍未收到底层成功/失败回调时，
    # 视为"仍在重试中"，由本服务主动发起一轮超时回执，避免命令侧永久等待。
    RECONNECT_RESULT_TIMEOUT = 30.0

    def __init__(self, service):
        # 主服务中维护了连接计划与连接管理器，本服务只负责在其之上做重连编排。
        self.service = service  # 主服务 DisasterWarningService 实例
        # 命令侧注册的回执回调列表：每个回调接收统一结果载荷
        # （含展示名、成功标志、消息等）。支持多订阅者，避免多次触发指令时互相覆盖。
        self._reconnect_callbacks: list[
            Callable[[dict[str, Any]], Awaitable[None]]
        ] = []
        # 登记"正在等待底层结果"的连接名集合，用于超时兜底与并发保护。
        self._awaiting: set[str] = set()

    # ------------------------------------------------------------------
    # 回调注册与底层桥接
    # ------------------------------------------------------------------

    def register_reconnect_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        """注册手动重连结果回执回调（由命令侧注入）。

        回调接收统一载荷字典，至少包含:
        - connection_name: 底层连接标识
        - display_name: 用户可读的通道展示名
        - success: 是否成功
        - message: 人类可读的结果描述
        - stage: success / failed / timeout

        支持多次调用以注册多个订阅者；返回注销函数，命令侧在回执完成后调用。
        """
        if callback not in self._reconnect_callbacks:
            self._reconnect_callbacks.append(callback)
        # 同时把底层管理器的手动重连结果回调桥接到本服务的统一回执处理入口。
        ws_manager = getattr(self.service, "ws_manager", None)
        if ws_manager is not None and hasattr(ws_manager, "set_reconnect_callback"):
            ws_manager.set_reconnect_callback(self._handle_ws_reconnect_result)

        def _unregister() -> None:
            if callback in self._reconnect_callbacks:
                self._reconnect_callbacks.remove(callback)

        return _unregister

    def _broadcast_reconnect_result(self, payload: dict[str, Any]) -> None:
        """把统一结果载荷广播给所有已注册的回执回调。"""
        for callback in list(self._reconnect_callbacks):
            try:
                asyncio.create_task(callback(payload))
            except Exception as exc:
                logger.error(f"[灾害预警] 手动重连回执回调调度失败: {exc}")

    def _handle_ws_reconnect_result(self, payload: dict[str, Any]) -> None:
        """接收底层 WebSocketManager 手动重连结果并做上层转接。

        幂等保护：同一连接只会消费一次结果（成功/失败/超时任一先到者生效）。
        本轮所有等待结果消费完毕后，自动清空订阅者，避免残留回调占用列表。
        """
        conn_name = str(payload.get("connection_name") or "")
        if not conn_name:
            return
        # 若该连接不在"等待结果"集合中，说明是历史残留回调或非本批次触发，忽略。
        if conn_name not in self._awaiting:
            return
        self._awaiting.discard(conn_name)

        display_name = self.resolve_display_name(conn_name)
        result_payload = {
            "connection_name": conn_name,
            "display_name": display_name,
            "success": bool(payload.get("success", False)),
            "message": str(payload.get("message") or ""),
            "stage": str(payload.get("stage") or "result"),
        }
        self._broadcast_reconnect_result(result_payload)
        # 全部连接已出结果，清空订阅者列表，避免长期占用。
        if not self._awaiting:
            self._clear_reconnect_callbacks()

    def _clear_reconnect_callbacks(self) -> None:
        """清空所有已注册的回执回调订阅者。"""
        if self._reconnect_callbacks:
            self._reconnect_callbacks.clear()

    # ------------------------------------------------------------------
    # 展示名解析
    # ------------------------------------------------------------------

    def resolve_display_name(self, conn_name: str) -> str:
        """把内部连接标识解析为用户可读的通道展示名。

        解析优先级（与离线通知场景同口径）：
        1. 连接配置中的数据源若命中折叠表或连接展示名，直接使用；
        2. 连接名本身若在连接展示名命中，直接使用；
        3. 以上均未命中时回退原始连接标识（防御性兜底）。
        """
        if not conn_name:
            return "未知连接"
        # 优先按连接计划中的 data_source 反查展示名。
        # data_source 通常是"子源混合代号"（如 fan_studio_mixed），
        # 需要先经折叠表映射到连接组 key，再查连接组展示名。
        conn_config = self.service.connections.get(conn_name, {}) or {}
        data_source = str(conn_config.get("data_source") or "")
        if data_source:
            display = self._resolve_data_source_display(data_source)
            if display != data_source:
                return display
        return CONNECTION_DISPLAY_NAMES.get(conn_name, conn_name)

    def _resolve_data_source_display(self, data_source: str) -> str:
        """把子源混合代号解析为连接组展示名（复用离线通知的折叠口径）。

        折叠规则与离线通知保持一致，避免同一份子源代号映射在多处重复维护。
        延迟导入通知服务，避免运行时服务与展示服务在模块加载期耦合。
        """
        from ...app.runtime.disaster_service_notice import DisasterServiceNoticeService

        group_key = DisasterServiceNoticeService._SOURCE_GROUP_KEY_MAP.get(data_source)
        if group_key is not None:
            return CONNECTION_DISPLAY_NAMES.get(group_key, group_key)
        return CONNECTION_DISPLAY_NAMES.get(data_source, data_source)

    # ------------------------------------------------------------------
    # 重连编排主流程
    # ------------------------------------------------------------------

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
                    # 登记等待结果，供底层回调做幂等消费与超时兜底。
                    self._awaiting.add(conn_name)
                    results[conn_name] = "✅ 已触发重连"
                    reconnect_count += 1
                else:
                    results[conn_name] = "⚠️ 重连未触发"
            except Exception as e:
                # 记录单个连接的失败详情，但不阻断其他连接的重试流程
                results[conn_name] = f"❌ 失败: {e}"
                logger.error(f"[灾害预警] 手动重连 {conn_name} 失败: {e}")

        logger.info(f"[灾害预警] 手动重连操作完成，触发了 {reconnect_count} 个重连任务")
        # 为每个已触发重连的连接安排超时兜底检查，防止命令侧长时间无回执。
        if reconnect_count > 0 and self._reconnect_callbacks:
            await self._schedule_timeout_guard(list(self._awaiting))
        return results

    async def _schedule_timeout_guard(self, conn_names: list[str]) -> None:
        """为等待结果的连接安排超时兜底回执。

        若连接仍未从 _awaiting 中消费掉，说明底层既未成功也未失败（可能处于长重试等待），主动推送"仍在尝试中"回执。
        """
        for conn_name in conn_names:
            display_name = self.resolve_display_name(conn_name)

            async def _guard(conn_name: str = conn_name, display: str = display_name):
                try:
                    await asyncio.sleep(self.RECONNECT_RESULT_TIMEOUT)
                except asyncio.CancelledError:
                    return
                # 超时后仍未被消费，说明重连仍在进行中，发送超时回执。
                if conn_name not in self._awaiting:
                    return
                self._awaiting.discard(conn_name)
                payload = {
                    "connection_name": conn_name,
                    "display_name": display,
                    "success": False,
                    "message": "重连仍在尝试中，可稍后使用状态指令确认",
                    "stage": "timeout",
                }
                # 超时回执同样广播给所有订阅者。
                for callback in list(self._reconnect_callbacks):
                    try:
                        await callback(payload)
                    except Exception as exc:
                        logger.error(
                            f"[灾害预警] 手动重连超时回执发送失败 {conn_name}: {exc}"
                        )
                # 全部连接已出结果，清空订阅者列表，避免长期占用。
                if not self._awaiting:
                    self._clear_reconnect_callbacks()

            # 挂载为后台任务，并登记到主服务统一回收，避免停机泄漏。
            task = asyncio.create_task(_guard())
            self.service.register_background_task(task)

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
        attach_fan_auth_from_plan(connection_info, conn_config)
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
