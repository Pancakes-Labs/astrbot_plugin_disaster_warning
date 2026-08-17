"""
WebSocket 连接管理器。
负责维护连接状态、消息处理器、重连任务、心跳任务与共享会话，
并把生命周期、分发、重连等细节委托给独立服务实现。
"""

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import ClientWebSocketResponse

from astrbot.api import logger

from .fan_studio_connection_policy import (
    is_connection_limit_signal,
    is_fan_primary_connection,
    is_fan_secondary_connection,
    is_fan_studio_connection,
    is_primary_fan_connected,
    send_fan_studio_auth,
    yield_secondary_for_primary,
)
from .websocket_dispatch_service import WebSocketDispatchService
from .websocket_reconnect_service import WebSocketReconnectService
from .websocket_runtime_service import WebSocketRuntimeService


class WebSocketManager:
    """WebSocket 连接管理器。"""

    def __init__(self, config: dict[str, Any], message_logger=None, telemetry=None):
        # 共享配置与日志、遥测工具依赖注入
        self.config = config
        self.message_logger = message_logger
        self._telemetry = telemetry

        # 启动静默期日志抑制回调（由主服务注入 is_silencing）：
        # 静默期间连接成功日志降级为 DEBUG，静默结束后恢复 INFO，
        # 避免启动建连阶段刷屏，同时保留运行期重连成功反馈。
        self._silence_checker = None

        # 共享状态维护
        self.connections: dict[str, ClientWebSocketResponse] = {}
        self.message_handlers: dict[str, Callable] = {}
        self.reconnect_tasks: dict[str, asyncio.Task] = {}
        # 手动重连追踪表：记录管理员主动触发重连后尚未获得结果的连接，
        # 键为连接名，值为请求批次标识 request_id（用于把结果路由回对应订阅者）。
        self._manual_reconnect_pending: dict[str, str] = {}
        # FAN 次要通道静默等待主通道的任务表（无感排队，不走错误重连日志）
        self._fan_secondary_wait_tasks: dict[str, asyncio.Task] = {}
        self.connection_retry_counts: dict[str, int] = {}
        self.fallback_retry_counts: dict[str, int] = {}  # 兜底重试计数
        self.connection_info: dict[str, dict] = {}  # 存放连接 URI, header 等元数据
        self.running = False
        self.session: aiohttp.ClientSession | None = None
        self.heartbeat_tasks: dict[str, asyncio.Task] = {}  # 保活协程任务集合
        self.last_heartbeat_time: dict[str, float] = {}  # 上一次接收到数据的时间戳
        self._stop_lock = asyncio.Lock()
        self._stopping = False
        self._offline_notify_callback: (
            Callable[[dict[str, Any]], Awaitable[None]] | None
        ) = None

        # 手动重连结果回调（由上层业务注册，接收连接名、是否成功及详情载荷）。
        # 与离线通知回调解耦：离线通知描述"被动掉线后的重试过程"，
        # 而此回调专门汇报"管理员主动触发重连后的真实结果"。
        self._reconnect_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = (
            None
        )

        # 实例化委托服务，保持高内聚低耦合
        self._reconnect_service = WebSocketReconnectService(self)
        self._runtime_service = WebSocketRuntimeService(self)
        self._dispatch_service = WebSocketDispatchService(self)

    def register_handler(self, connection_name: str, handler: Callable):
        """注册指定连接前缀对应的消息处理器。"""
        self.message_handlers[connection_name] = handler
        logger.debug(f"[灾害预警] 注册处理器: {connection_name}")

    def set_silence_checker(self, checker) -> None:
        """注入启动静默判定回调。

        静默期间连接成功日志降级为 DEBUG，静默结束后恢复 INFO，
        避免启动建连阶段刷屏，同时保留运行期重连成功反馈。
        """
        self._silence_checker = checker

    def _is_silencing(self) -> bool:
        """当前是否处于启动静默期（委托主服务回调）。"""
        checker = self._silence_checker
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _log_connection_success(self, name: str) -> None:
        """记录连接成功日志：启动静默期降级为 DEBUG，静默结束后恢复 INFO。"""
        if self._is_silencing():
            logger.debug(f"[灾害预警] WebSocket 连接成功: {name}")
        else:
            logger.info(f"[灾害预警] WebSocket 连接成功: {name}")

    async def connect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        is_retry: bool = False,
        connection_info: dict[str, Any] | None = None,
    ):
        """建立 WebSocket 连接并托管整个会话生命周期。"""
        # 复用 aiohttp 会话，保证连接池管理更合理
        if not self.session or self.session.closed:
            logger.warning(f"[灾害预警] WebSocket会话未就绪，正在重新初始化: {name}")
            if self.session and not self.session.closed:
                try:
                    await self.session.close()
                except Exception:
                    pass
            # 引入全局超时配置
            timeout_val = self.config.get("http_timeout", 30)
            timeout = aiohttp.ClientTimeout(total=timeout_val)
            self.session = aiohttp.ClientSession(timeout=timeout)

        # 重连/并发建连前先释放同名旧连接，避免上游按 IP 计数时被僵尸连接占满配额
        await self._release_existing_connection(
            name,
            reason="建连前清理旧连接",
            keep_connection_info=True,
        )

        # FAN Studio：次要独立通道仅在主通道 /all 在线后才允许建连。
        # 这是启动/恢复期的内部排队，不是故障重连，必须静默，避免误导用户。
        if is_fan_secondary_connection(name) and not is_primary_fan_connected(self):
            self._defer_fan_secondary_until_primary(
                name,
                uri=uri,
                headers=headers,
                connection_info=connection_info,
            )
            return

        websocket: ClientWebSocketResponse | None = None
        try:
            # 记录连接参数以便重连或状态上报
            preserved_info = self.connection_info.get(name, {})
            merged_info = {
                **preserved_info,
                **(connection_info or {}),
            }
            # 避免把旧会话的离线标记带进新连接元数据
            merged_info.pop("offline_since", None)
            merged_info.pop("short_retry_notified", None)
            self.connection_info[name] = {
                "uri": uri,
                "headers": headers,
                "connection_type": "websocket",
                "established_time": None,
                "retry_count": 0,
                **merged_info,
            }

            # 递增重试次数
            if is_retry:
                current_retry = self.connection_retry_counts.get(name, 0) + 1
                self.connection_retry_counts[name] = current_retry
            else:
                logger.debug(f"[灾害预警] 正在连接 {name}")
                self.connection_retry_counts[name] = 0

            # 统一配置建连的超时时间及负载限制
            conn_timeout = self.config.get("connection_timeout", 30)
            connect_kwargs = {
                "url": uri,
                "headers": headers or {},
                "heartbeat": self.config.get("heartbeat_interval", 60),
                "timeout": conn_timeout,  # aiohttp 握手超时限制
                "max_msg_size": self.config.get(
                    "max_message_size", 2**20
                ),  # 默认 1MB 限制
            }

            # 按需取消证书校验
            if self.config.get("ssl_verify", True) is False:
                connect_kwargs["ssl"] = False

            # 外挂一层超时保护，防止 DNS 解析或三次握手长久卡死
            websocket = await asyncio.wait_for(
                self.session.ws_connect(**connect_kwargs),
                timeout=conn_timeout + 5,
            )

            # 建连成功，记录状态并注册生命周期及心跳协程
            async with websocket:
                self.connections[name] = websocket
                self.connection_info[name]["established_time"] = (
                    asyncio.get_running_loop().time()
                )
                self.connection_info[name].pop("offline_since", None)
                self.connection_info[name].pop("short_retry_notified", None)
                self.connection_info[name].pop("quota_hit", None)
                self.connection_info[name].pop("quota_deferred", None)
                self._log_connection_success(name)
                on_established = getattr(self, "on_connection_established", None)
                if callable(on_established):
                    try:
                        on_established(name)
                    except Exception as exc:
                        logger.debug(
                            f"[灾害预警] WebSocket 建连回调通知静默协调器失败: {exc}"
                        )

                # FAN Studio：握手成功后立即发送应用层鉴权包。
                # 仅“缺少凭证”返回 False；网络异常会向上抛出并由下方 except 重试。
                if is_fan_studio_connection(name):
                    auth_ok = await send_fan_studio_auth(
                        websocket,
                        connection_name=name,
                        connection_info=self.connection_info.get(name),
                    )
                    if not auth_ok:
                        # 配置缺失：关闭空连接，走统一错误处理以便后续可重连。
                        try:
                            await websocket.close(
                                code=1008,
                                message=b"fan studio auth credentials missing",
                            )
                        except Exception:
                            pass
                        error = RuntimeError(f"FAN Studio 缺少鉴权凭证: {name}")
                        logger.error(f"[灾害预警] {error}")
                        await self._apply_fan_quota_policy_on_error(name, error)
                        self._handle_connection_error(name, uri, headers, error)
                        return

                # 若该连接处于"手动重连待确认"状态，说明是管理员主动触发的重连：
                # 建连成功且（FAN 连接）应用层鉴权通过后，才清除追踪标记并回调上层
                # 汇报真实成功结果。若鉴权失败，上方错误路径会负责清除标记并汇报失败。
                pending_request = self._manual_reconnect_pending.pop(name, None)
                if pending_request is not None:
                    self.emit_reconnect_result(
                        connection_name=name,
                        success=True,
                        message="重连成功，连接已建立",
                        stage="success",
                        request_id=pending_request,
                    )

                # 重置重连相关的状态变量
                self.connection_retry_counts[name] = 0
                self.fallback_retry_counts[name] = 0
                self.last_heartbeat_time[name] = asyncio.get_running_loop().time()

                # 主通道恢复后，尽快唤醒此前因等待 /all 而暂缓的次要通道。
                if is_fan_primary_connection(name):
                    self._kick_deferred_fan_secondary_reconnects()

                # 启动后台应用层心跳保活协程前，确保旧心跳任务不会残留
                await self._cancel_heartbeat_task(name)
                self.heartbeat_tasks[name] = asyncio.create_task(
                    self._heartbeat_loop(name, websocket)
                )

                # 开启并阻塞在消息循环派发服务中，直到断开
                await self._dispatch_service.handle_connection_session(
                    name=name,
                    uri=uri,
                    headers=headers,
                    websocket=websocket,
                )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # 常见网络错误或握手超时，走重试容灾逻辑
            logger.warning(f"[灾害预警] 连接中断或失败 {name}: {e}")
            await self._apply_fan_quota_policy_on_error(name, e)
            self._handle_connection_error(name, uri, headers, e)

        except asyncio.CancelledError:
            # 主动关闭或插件卸载引发的任务取消，清理局部资源后正常退出
            logger.debug(f"[灾害预警] WebSocket 连接任务被取消: {name}")
            await self._release_existing_connection(
                name,
                reason="连接任务取消",
                keep_connection_info=False,
                websocket=websocket,
            )
            raise
        except Exception as e:
            # 非预期类型错误，上报异常遥测
            logger.error(f"[灾害预警] 未知连接错误 {name}: {type(e).__name__} - {e}")
            logger.debug(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            if self._telemetry and self._telemetry.enabled:
                asyncio.create_task(
                    self._telemetry.track_error(
                        e, module=f"core.websocket_manager.connect.{name}"
                    )
                )
            await self._apply_fan_quota_policy_on_error(name, e)
            self._handle_connection_error(name, uri, headers, e)
        finally:
            # 会话退出后做一次幂等清理，防止已关闭 socket / 心跳任务残留占位
            await self._cleanup_closed_connection(name, websocket)

    def _log_message(self, name: str, message: Any, uri: str):
        """记录消息的辅助入口。"""
        self._dispatch_service.log_message(name, message, uri)

    async def _cancel_heartbeat_task(self, name: str) -> None:
        """取消指定连接的心跳任务并等待其退出。"""
        task = self.heartbeat_tasks.pop(name, None)
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

    async def _close_websocket_quietly(
        self,
        websocket: ClientWebSocketResponse | None,
        *,
        reason: str,
    ) -> None:
        """安静关闭底层 WebSocket，避免清理路径抛出二次异常。"""
        if websocket is None:
            return
        try:
            if not websocket.closed:
                await websocket.close(code=1000, message=reason.encode("utf-8")[:120])
        except Exception as e:
            logger.debug(f"[灾害预警] 关闭 WebSocket 时忽略异常: {e}")

    async def _release_existing_connection(
        self,
        name: str,
        *,
        reason: str,
        keep_connection_info: bool = True,
        websocket: ClientWebSocketResponse | None = None,
    ) -> None:
        """释放同名旧连接与心跳任务，降低上游连接配额被占满的风险。"""
        existing = self.connections.pop(name, None)
        target = existing if existing is not None else websocket
        if target is not None:
            logger.debug(f"[灾害预警] {name} {reason}，正在释放旧 WebSocket 句柄")
            await self._close_websocket_quietly(target, reason=reason)

        await self._cancel_heartbeat_task(name)
        self.last_heartbeat_time.pop(name, None)

        if not keep_connection_info:
            self.connection_info.pop(name, None)

    async def _cleanup_closed_connection(
        self,
        name: str,
        websocket: ClientWebSocketResponse | None,
    ) -> None:
        """会话结束后清理已关闭连接对应的本地状态。"""
        current = self.connections.get(name)
        # 仅清理当前会话对应句柄，避免误删并发重建出的新连接
        if current is not None and (websocket is None or current is websocket):
            if current.closed:
                self.connections.pop(name, None)
                await self._cancel_heartbeat_task(name)
                self.last_heartbeat_time.pop(name, None)
            return

        if websocket is not None and websocket.closed:
            await self._cancel_heartbeat_task(name)
            if self.connections.get(name) is websocket:
                self.connections.pop(name, None)
            self.last_heartbeat_time.pop(name, None)

    async def _apply_fan_quota_policy_on_error(
        self,
        name: str,
        error: Exception,
    ) -> None:
        """在 FAN Studio 连接错误时应用配额优先级策略。"""
        if not is_connection_limit_signal(error):
            if is_fan_secondary_connection(name) and not is_primary_fan_connected(self):
                info = self.connection_info.get(name, {})
                info["quota_deferred"] = True
                self.connection_info[name] = info
            return

        info = self.connection_info.get(name, {})
        info["quota_hit"] = True
        self.connection_info[name] = info

        if is_fan_primary_connection(name):
            released = await yield_secondary_for_primary(
                self,
                reason="主通道遇连接上限，释放次要 FAN 连接以保活 /all",
            )
            if released:
                logger.warning(
                    f"[灾害预警] {name} 命中连接上限，已释放次要 FAN 连接: {', '.join(released)}"
                )
            return

        if is_fan_secondary_connection(name):
            logger.warning(
                f"[灾害预警] {name} 命中 FAN Studio 连接上限，将拉长退避并优先保活 /all"
            )

    def _defer_fan_secondary_until_primary(
        self,
        name: str,
        *,
        uri: str,
        headers: dict | None,
        connection_info: dict[str, Any] | None,
    ) -> None:
        """静默暂缓次要 FAN 通道，等待主通道 /all 在线后再建连。

        不走错误重连链路，避免出现“将在 N 秒后重连”这类误导日志。
        """
        info = {
            **(self.connection_info.get(name, {}) or {}),
            **(connection_info or {}),
            "uri": uri,
            "headers": headers,
            "quota_deferred": True,
        }
        # 暂缓不是故障，清掉离线告警相关标记，避免误发离线通知。
        info.pop("offline_since", None)
        info.pop("short_retry_notified", None)
        self.connection_info[name] = info

        # 取消可能已存在的重连任务，防止旧路径继续打 INFO。
        existing_task = self.reconnect_tasks.pop(name, None)
        if existing_task is not None and not existing_task.done():
            existing_task.cancel()

        # 已有静默等待任务则复用，避免重复排队。
        wait_tasks = self._fan_secondary_wait_tasks
        wait_task = wait_tasks.get(name)
        if wait_task is not None and not wait_task.done():
            return

        wait_tasks[name] = asyncio.create_task(
            self._wait_primary_then_connect_secondary(
                name,
                uri=uri,
                headers=headers,
                connection_info=info,
            ),
            name=f"dw_fan_secondary_wait_{name}",
        )

    async def _wait_primary_then_connect_secondary(
        self,
        name: str,
        *,
        uri: str,
        headers: dict | None,
        connection_info: dict[str, Any] | None,
    ) -> None:
        """静默轮询主通道状态，就绪后建立次要连接。"""
        try:
            while self.running:
                if is_primary_fan_connected(self):
                    break
                # 短间隔静默等待，不输出用户可见日志。
                await asyncio.sleep(1)
            if not self.running or not is_primary_fan_connected(self):
                return

            info = dict(connection_info or {})
            info.pop("quota_deferred", None)
            self.connection_info[name] = {
                **(self.connection_info.get(name, {}) or {}),
                **info,
                "uri": uri,
                "headers": headers,
            }
            # 这是首次真正建连，不算失败重试。
            await self.connect(
                name,
                uri,
                headers,
                is_retry=False,
                connection_info=self.connection_info.get(name),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 静默等待路径保持无感；真实建连失败会由 connect 内部重连处理。
            return
        finally:
            current = self._fan_secondary_wait_tasks.get(name)
            if current is not None and current.done():
                self._fan_secondary_wait_tasks.pop(name, None)

    def _kick_deferred_fan_secondary_reconnects(self) -> None:
        """主通道在线后，尽快静默唤醒此前暂缓的次要通道。"""
        for name, info in list((self.connection_info or {}).items()):
            if not is_fan_secondary_connection(name):
                continue
            if not isinstance(info, dict):
                continue
            if not info.get("quota_deferred") and not info.get("quota_hit"):
                continue
            existing = self.connections.get(name)
            if existing is not None and not getattr(existing, "closed", True):
                continue
            uri = str(info.get("uri") or "").strip()
            if not uri:
                continue
            headers = info.get("headers")
            if not isinstance(headers, dict):
                headers = None

            was_deferred = bool(info.get("quota_deferred"))
            info.pop("quota_deferred", None)
            self.connection_info[name] = info

            # 取消静默等待任务与可能残留的重连任务，避免双开。
            wait_task = self._fan_secondary_wait_tasks.pop(name, None)
            if wait_task is not None and not wait_task.done():
                wait_task.cancel()
            task = self.reconnect_tasks.pop(name, None)
            if task is not None and not task.done():
                task.cancel()

            asyncio.create_task(
                self.connect(
                    name,
                    uri,
                    headers,
                    # 纯暂缓唤醒按首次建连处理；配额命中后的恢复仍按重试。
                    is_retry=not was_deferred,
                    connection_info=info,
                ),
                name=f"dw_fan_secondary_kick_{name}",
            )

    def _handle_connection_error(
        self, name: str, uri: str, headers: dict | None, error: Exception
    ):
        """统一分发连接错误处理。"""
        # 若该连接正处于"手动重连待确认"状态，说明管理员主动触发的重连已失败：
        # 先清除追踪标记，并回调上层汇报失败结果（携带请求批次标识以路由回执），
        # 随后再交给重连服务安排重试。
        pending_request = self._manual_reconnect_pending.pop(name, None)
        if pending_request is not None:
            self.emit_reconnect_result(
                connection_name=name,
                success=False,
                message=f"重连失败: {error}",
                stage="failed",
                request_id=pending_request,
            )
        self._reconnect_service.handle_connection_error(name, uri, headers, error)

    def _is_critical_error(self, error: Exception) -> bool:
        """判断是否属于需要立即切换兜底策略的关键错误。"""
        return self._reconnect_service.is_critical_error(error)

    def _get_handler_name_for_connection(self, connection_name: str) -> str:
        """获取连接名对应的处理器名称。"""
        return self._dispatch_service.get_handler_name_for_connection(connection_name)

    async def _schedule_reconnect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        connection_info: dict[str, Any] | None = None,
        force_fallback: bool = False,
    ):
        """按统一重连策略安排一次后续重连。"""
        await self._reconnect_service.schedule_reconnect(
            name,
            uri,
            headers,
            connection_info,
            force_fallback=force_fallback,
        )

    async def _heartbeat_loop(self, name: str, websocket: ClientWebSocketResponse):
        """运行应用层心跳循环。"""
        await self._runtime_service.heartbeat_loop(name, websocket)

    async def force_reconnect(self, name: str, *, request_id: str = "") -> bool:
        """强制立即重连指定连接，跳过原有等待队列。

        Args:
            name: 连接标识
            request_id: 请求批次标识，用于把重连结果路由回对应订阅者。
        """
        # 若已有健康连接，没必要强行断开
        existing = self.connections.get(name)
        if existing is not None and not existing.closed:
            return False

        # 拒绝同一连接的重复手动重连请求：若该连接仍处于"手动重连待确认"状态，
        # 说明上一次强制重连尚未产生终态结果。此时若覆盖 request_id，旧批次将
        # 永远收不到终态回执，且结果可能被路由到错误批次。
        # 采用串行化策略：拒绝新请求，保留旧请求的完整回执链路。
        if name in self._manual_reconnect_pending:
            logger.debug(
                f"[灾害预警] {name} 已有进行中的手动重连，拒绝重复触发 "
                f"(请求 ID 为{self._manual_reconnect_pending.get(name)})"
            )
            return False

        # 取消已处于等待计时队列中的待执行重试任务，避免竞争
        if name in self.reconnect_tasks:
            task = self.reconnect_tasks.pop(name, None)
            if task is not None and not task.done():
                task.cancel()
                logger.debug(f"[灾害预警] 取消了 {name} 正在等待的重连任务 (强制重连)")

        # 检查是否保留了该连接的配置元信息
        info = self.connection_info.get(name)
        if not info:
            logger.warning(f"[灾害预警] 无法重连 {name}: 找不到连接信息")
            # 手动重连失败也应反馈到上层，避免命令侧只看到"已触发"却没有下文。
            self.emit_reconnect_result(
                connection_name=name,
                success=False,
                message="找不到连接信息，无法重连",
                stage="failed",
                request_id=request_id,
            )
            return False

        # 强制重连前先释放可能残留的半开/已关闭句柄，避免上游连接配额被占
        await self._release_existing_connection(
            name,
            reason="强制重连前清理旧连接",
            keep_connection_info=True,
        )

        uri = info.get("uri")
        headers = info.get("headers")

        # 手动重连开始，所有失败统计和时间标记必须置空
        self.connection_retry_counts[name] = 0
        self.fallback_retry_counts[name] = 0
        info.pop("offline_since", None)
        info.pop("short_retry_notified", None)

        # 登记手动重连追踪标记，供建连成功/失败回调做结果归因；
        # 值记录请求批次标识，供上层把结果路由回对应订阅者。
        self._manual_reconnect_pending[name] = request_id

        logger.info(f"[灾害预警] 正在手动重连 {name}...")

        # 异步启动物理连接，不卡死调用线程
        asyncio.create_task(
            self.connect(
                name,
                uri,
                headers,
                is_retry=False,
                connection_info=info,
            )
        )
        return True

    async def disconnect(self, name: str):
        """断开指定连接。"""
        await self._runtime_service.disconnect(name)

    async def send_message(self, name: str, message: str):
        """向指定连接发送文本消息。"""
        if name in self.connections:
            try:
                await self.connections[name].send_str(message)
            except Exception as e:
                logger.error(
                    f"[灾害预警] WebSocket 管理器向 {name} 发送消息失败，错误为 {e}"
                )
        else:
            logger.warning(f"[灾害预警] WebSocket 管理器尝试向未连接的 {name} 发送消息")

    def get_connection_status(self, name: str) -> dict[str, Any]:
        """获取单个连接的状态摘要。"""
        status = {
            "connected": name in self.connections and not self.connections[name].closed,
            "retry_count": self.connection_retry_counts.get(name, 0),
            "has_handler": name in self.message_handlers,
        }

        if name in self.connection_info:
            info = self.connection_info[name]
            status.update(
                {
                    "uri": info.get("uri"),
                    "established_time": info.get("established_time"),
                    "connection_type": info.get("connection_type"),
                }
            )

        # 最近活跃时间可辅助管理端判断连接是否假活跃。
        if name in self.last_heartbeat_time:
            status["last_active"] = self.last_heartbeat_time[name]

        return status

    def get_all_connections_status(self) -> dict[str, dict[str, Any]]:
        """获取全部连接的状态摘要。"""
        return {
            name: self.get_connection_status(name)
            for name in self.connection_info.keys()
        }

    async def start(self):
        """启动管理器。"""
        await self._runtime_service.start()

    async def _cancel_and_wait(self, tasks: list[asyncio.Task]) -> None:
        """取消并等待任务结束。"""
        await self._runtime_service.cancel_and_wait(tasks)

    async def stop(self):
        """停止管理器。"""
        await self._runtime_service.stop()

    def set_offline_notify_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """设置离线通知回调。"""
        self._offline_notify_callback = callback

    def set_reconnect_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]] | None
    ) -> None:
        """设置手动重连结果回调。

        与离线通知回调解耦：该回调只在管理员通过强制重连主动触发
        重连后汇报真实结果（成功 / 失败 / 进入重连流程）。
        """
        self._reconnect_callback = callback

    def emit_reconnect_result(
        self,
        connection_name: str,
        success: bool,
        message: str,
        *,
        stage: str = "result",
        request_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """以异步安全方式触发手动重连结果回调。"""
        callback = self._reconnect_callback
        if not callback:
            return
        info = self.connection_info.get(connection_name, {})
        payload: dict[str, Any] = {
            "connection_name": connection_name,
            "data_source": info.get("data_source")
            or info.get("connection_name")
            or connection_name,
            "request_id": request_id,
            "success": success,
            "message": message,
            "stage": stage,
            "detail": detail or {},
        }

        async def _invoke_callback() -> None:
            # 捕获并记录回调异常，避免下游发送失败变成未处理的异步任务异常，
            # 导致回执静默丢失且事件循环持续告警。
            try:
                await callback(payload)
            except Exception as exc:
                logger.error(
                    f"[灾害预警] 手动重连结果回调处理失败 "
                    f"({connection_name}, 请求 ID 为{request_id}): {exc}"
                )

        # 以非阻塞异步任务方式抛出给外层订阅者，避免阻塞建连/重连主流程。
        asyncio.create_task(_invoke_callback())

    def _emit_offline_notification(
        self,
        connection_name: str,
        stage: str,
        reason: str,
        next_retry_in: str | None = None,
        retry_count: int | None = None,
        fallback_count: int | None = None,
    ) -> None:
        """以异步安全方式触发离线通知回调。"""
        self._reconnect_service.emit_offline_notification(
            connection_name=connection_name,
            stage=stage,
            reason=reason,
            next_retry_in=next_retry_in,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )

    def _find_handler_by_prefix(self, connection_name: str) -> str | None:
        """按连接名前缀匹配处理器名称。"""
        return self._dispatch_service.find_handler_by_prefix(connection_name)


class HTTPDataFetcher:
    """HTTP 数据获取器。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        # 采用 session 上下文模式，每次 fetch 使用完自动回收套接字资源，防止连接池泄漏
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.get("http_timeout", 30))
        )
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        await self.close()

    async def close(self):
        """显式关闭会话。"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_json(self, url: str, headers: dict | None = None) -> dict | None:
        """获取 JSON 数据。"""
        if not self.session:
            return None

        try:
            # 异步拉取 HTTP 接口
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"[灾害预警] HTTP 请求失败 {url}: {response.status}")
        except Exception as e:
            logger.error(f"[灾害预警] HTTP 请求异常 {url}: {e}")

        return None
