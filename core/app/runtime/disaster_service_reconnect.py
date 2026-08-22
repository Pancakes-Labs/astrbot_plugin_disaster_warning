"""
灾害服务手动重连编排服务。
负责批量检查连接状态、补齐 connection_info 并触发 WebSocket 强制重连，
并把底层连接管理器产生的"手动重连结果"转接为上层业务回执（命令侧异步推送），
进一步减少 DisasterWarningService 中的运维编排职责。
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from ...network.websocket.fan_studio_connection_policy import attach_fan_auth_from_plan
from ...sources.display_registry import CONNECTION_DISPLAY_NAMES


class _ReconnectBatch:
    """单个手动重连请求批次的状态容器。

    每次管理员触发一次重连命令都会创建一个独立批次，用于隔离不同请求：
    - callbacks:  该批次订阅的回执回调（通常为触发指令的会话发送器）
    - awaiting:   该批次正在等待底层结果的连接名集合
    - registered: 该批次是否已完成全部连接登记（防止回调在登记完成前触发清理）
    """

    __slots__ = ("request_id", "callbacks", "awaiting", "registered")

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.callbacks: list[Callable[[dict[str, Any]], Awaitable[None]]] = []
        self.awaiting: set[str] = set()
        self.registered = False


class DisasterServiceReconnectService:
    """灾害服务手动重连编排服务。

    该服务主要面向管理端或运维指令场景：
    当用户希望立刻对离线连接发起一次主动重连时，统一由这里完成状态检查与底层调用，
    并把底层 WebSocket 管理器的"手动重连结果回调"翻译成上层业务可消费的回执事件。

    职责划分：
    - 底层 WebSocketManager 只负责物理建连与成功/失败回调的原始广播；
    - 本服务负责把结果按连接映射为友好展示名，并按"请求批次"路由回执，
      最终由命令侧把回执异步推送到触发指令的会话。

    批次隔离：每次命令触发都会生成唯一 request_id，回执只路由到同一请求的订阅者，
    避免并发触发指令时结果串扰；无等待连接或全部消费完毕时立即清理批次，防止陈旧回调残留。
    """

    # 手动重连结果等待超时（秒）：超过该时长仍未收到底层成功/失败回调时，
    # 视为"仍在重试中"，由本服务主动发起一轮超时回执，避免命令侧永久等待。
    RECONNECT_RESULT_TIMEOUT = 30.0

    def __init__(self, service):
        # 主服务中维护了连接计划与连接管理器，本服务只负责在其之上做重连编排。
        self.service = service  # 主服务 DisasterWarningService 实例
        # 请求批次表：request_id -> _ReconnectBatch。
        # 引入批次是为了隔离不同命令请求，避免并发触发时回执串扰或陈旧回调残留。
        self._batches: dict[str, _ReconnectBatch] = {}
        self._request_seq = itertools.count(1)
        # 回执发送任务集合：持有 asyncio.create_task 返回任务的强引用，
        # 避免回调在等待 session_sender.send() 挂起时被垃圾回收导致回执中断。
        self._receipt_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # 批次注册与底层桥接
    # ------------------------------------------------------------------

    def _create_request_id(self) -> str:
        """生成唯一请求批次标识。"""
        return f"reconnect-{next(self._request_seq)}"

    def register_reconnect_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> tuple[str, Callable[[], None]]:
        """注册一次手动重连请求的回执回调。

        回调接收统一载荷字典，至少包含:
        - connection_name: 底层连接标识
        - display_name: 用户可读的通道展示名
        - success: 是否成功
        - message: 人类可读的结果描述
        - stage: success / failed / timeout

        返回 (request_id, 注销函数)：
        - request_id 用于把本次触发的重连结果关联回该批次；
        - 注销函数供调用方在异常或提前结束时移除订阅。
        """
        request_id = self._create_request_id()
        batch = self._batches.setdefault(request_id, _ReconnectBatch(request_id))
        if callback not in batch.callbacks:
            batch.callbacks.append(callback)
        # 把底层管理器的手动重连结果回调桥接到本服务的统一回执处理入口。
        # 桥接是单例的（WebSocketManager 只保存一个回调），
        # 具体路由交由 _handle_ws_reconnect_result 按 request_id 分发到对应批次。
        ws_manager = getattr(self.service, "ws_manager", None)
        if ws_manager is not None and hasattr(ws_manager, "set_reconnect_callback"):
            ws_manager.set_reconnect_callback(self._handle_ws_reconnect_result)

        def _unregister() -> None:
            if callback in batch.callbacks:
                batch.callbacks.remove(callback)
            if not batch.callbacks:
                self._remove_batch(request_id)

        return request_id, _unregister

    def _remove_batch(self, request_id: str) -> None:
        """移除批次并清理其等待状态，避免陈旧状态残留。"""
        batch = self._batches.pop(request_id, None)
        if batch is None:
            return
        # 兜底：把底层待确认表中仍属于本批次的连接标记清除，
        # 防止本批次被清理后，底层后续结果因 request_id 无法路由而悬空。
        # 底层待确认值现在是 attempt_id（"{request_id}:{seq}"），按前缀匹配。
        ws_manager = getattr(self.service, "ws_manager", None)
        pending = getattr(ws_manager, "_manual_reconnect_pending", None)
        if pending is not None:
            prefix = f"{request_id}:"
            for conn_name in list(batch.awaiting):
                attempt = pending.get(conn_name)
                if attempt is not None and attempt.startswith(prefix):
                    pending.pop(conn_name, None)
                # 顺带清理同连接的尝试序号计数，避免无限增长。
                seq_map = getattr(ws_manager, "_manual_reconnect_seq", None)
                if seq_map is not None:
                    seq_map.pop(conn_name, None)
                # 若该连接正被 FAN 主通道暂缓（cleanup 任务在等超时），
                # 批次清理后 cleanup 不应再发回执，一并取消。
                cleanup_map = getattr(ws_manager, "_fan_secondary_cleanup_tasks", None)
                if cleanup_map is not None:
                    cleanup_task = cleanup_map.pop(conn_name, None)
                    if cleanup_task is not None and not cleanup_task.done():
                        cleanup_task.cancel()

    async def _handle_ws_reconnect_result(self, payload: dict[str, Any]) -> None:
        """接收底层 WebSocketManager 手动重连结果并做上层转接（异步）。

        幂等保护：同一批次中同一连接只会消费一次结果（成功/失败/超时任一先到者生效）。
        批次登记完成（registered）后，若所有等待连接均已出结果，自动清理批次订阅者。

        说明：底层回调显式携带原始 request_id（用于路由）与 attempt_id（内部匹配），
        这里直接使用 request_id 精确命中批次表，不再依赖字符串解析约定。
        """
        conn_name = str(payload.get("connection_name") or "")
        request_id = str(payload.get("request_id") or "")
        if not conn_name or not request_id:
            return
        batch = self._batches.get(request_id)
        # 若非本批次等待的连接或批次已清理，说明是历史残留/跨批次回调，忽略。
        if batch is None or conn_name not in batch.awaiting:
            return
        batch.awaiting.discard(conn_name)

        display_name = self.resolve_display_name(conn_name)
        result_payload = {
            "connection_name": conn_name,
            "display_name": display_name,
            "success": bool(payload.get("success", False)),
            "message": str(payload.get("message") or ""),
            "stage": str(payload.get("stage") or "result"),
        }
        self._broadcast_reconnect_result(batch, result_payload)
        # 登记完成后若已全部出结果，清理批次，避免订阅者长期残留。
        if batch.registered and not batch.awaiting:
            self._remove_batch(request_id)

    def _broadcast_reconnect_result(
        self, batch: _ReconnectBatch, payload: dict[str, Any]
    ) -> None:
        """把统一结果载荷广播给指定批次内的回执回调。"""
        for callback in list(batch.callbacks):
            try:
                task = asyncio.create_task(callback(payload))
            except Exception as exc:
                logger.error(f"[灾害预警] 手动重连回执回调调度失败: {exc}")
                continue
            # 持有任务强引用，避免回调挂起（如等待会话发送）时被 GC 回收，
            # 导致回执发送中断；任务完成后自动从集合移除。
            self._receipt_tasks.add(task)
            task.add_done_callback(self._receipt_tasks.discard)

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

    async def reconnect_all_sources(self, request_id: str = "") -> dict[str, str]:
        """强制重连所有已启用但离线的数据源。

        Args:
            request_id: 由 register_reconnect_callback 生成的请求批次标识，
                用于把本次触发的结果关联回对应批次的订阅者。
        """
        results: dict[str, str] = {}
        # 校验 WebSocket 管理器是否正常就绪
        if not self.service.ws_manager:
            return {"error": "WebSocket管理器未初始化"}

        batch = self._batches.get(request_id) if request_id else None
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
                # 触发底层数据源物理连接重连操作，携带请求批次标识以路由回执。
                triggered = await self._force_reconnect(
                    conn_name, request_id=request_id
                )
                if triggered:
                    # 登记等待结果，供底层回调做幂等消费与超时兜底。
                    if batch is not None:
                        batch.awaiting.add(conn_name)
                    results[conn_name] = "✅ 已触发重连"
                    reconnect_count += 1
                else:
                    results[conn_name] = "⚠️ 重连未触发"
            except Exception as e:
                # 记录单个连接的失败详情，但不阻断其他连接的重试流程
                results[conn_name] = f"❌ 失败: {e}"
                logger.error(f"[灾害预警] 手动重连 {conn_name} 失败: {e}")

        logger.info(f"[灾害预警] 手动重连操作完成，触发了 {reconnect_count} 个重连任务")
        if batch is not None:
            # 关键时序：全部连接登记完成后再允许批次清理，
            # 避免循环中较早出结果的连接触发过早清理而丢失后续连接的回执。
            batch.registered = True
            if reconnect_count > 0 and batch.callbacks:
                self._schedule_timeout_guard(batch)
            elif not batch.awaiting:
                # 无任何等待中的连接（全部未触发/跳过），立即清理批次避免陈旧回调残留。
                self._remove_batch(request_id)
        return results

    def _schedule_timeout_guard(self, batch: _ReconnectBatch) -> None:
        """为批次内等待结果的连接安排超时兜底回执（即发即弃，不阻塞调用方）。

        若连接在超时后仍未从批次 awaiting 中消费掉，说明底层既未成功也未失败
        （可能处于长重试等待），主动推送"仍在尝试中"回执。
        """
        for conn_name in list(batch.awaiting):
            display_name = self.resolve_display_name(conn_name)

            async def _guard(conn_name: str = conn_name, display: str = display_name):
                try:
                    await asyncio.sleep(self.RECONNECT_RESULT_TIMEOUT)
                except asyncio.CancelledError:
                    return
                # 超时后仍未被消费，说明重连仍在进行中，发送超时回执。
                if conn_name not in batch.awaiting:
                    return
                batch.awaiting.discard(conn_name)
                payload = {
                    "connection_name": conn_name,
                    "display_name": display,
                    "success": False,
                    "message": "重连仍在尝试中，可稍后使用状态指令确认",
                    "stage": "timeout",
                }
                # 超时回执同样广播给本批次订阅者。
                for callback in list(batch.callbacks):
                    try:
                        await callback(payload)
                    except Exception as exc:
                        logger.error(
                            f"[灾害预警] 手动重连超时回执发送失败 {conn_name}: {exc}"
                        )
                # 全部连接已出结果，清理批次订阅者。
                if batch.registered and not batch.awaiting:
                    self._remove_batch(batch.request_id)

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
            "connection_config": dict(conn_config),
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

    async def _force_reconnect(self, conn_name: str, *, request_id: str = "") -> bool:
        """调用 WebSocketManager 执行底层强制物理重连。

        Args:
            conn_name: 连接标识
            request_id: 请求批次标识，透传给底层用于结果归因路由。
        """
        # 并非所有连接管理器实现都强制要求提供该接口，
        # 因此这里先做能力检查，再决定是否触发主动重连。
        if not hasattr(self.service.ws_manager, "force_reconnect"):
            return False
        return await self.service.ws_manager.force_reconnect(
            conn_name, request_id=request_id
        )
