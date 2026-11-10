"""
WebSocket 连接管理器。
负责维护连接状态、消息处理器、重连任务、心跳任务与共享会话，
并把生命周期、分发、重连等细节委托给独立服务实现。
"""

import asyncio
import re
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import ClientWebSocketResponse

from astrbot.api import logger

from ...services.telemetry.telemetry_utils import track_error_safely
from .jian_project_connection_policy import (
    is_jian_project_connection,
    jian_project_auth_service,
)
from .websocket_dispatch_service import WebSocketDispatchService
from .websocket_reconnect_service import WebSocketReconnectService
from .websocket_runtime_service import WebSocketRuntimeService

# 记录与上报前统一剥离令牌值，避免写入运行日志、管理端响应或事件元数据（CWE-532）。
_ACCESS_TOKEN_QUERY_RE = re.compile(r"(?i)([?&]key=)[^&\s\"']+")


def _redact_access_token(text: str) -> str:
    """把文本中的 Jian Project 访问令牌查询参数值替换为 ***。"""
    if not text:
        return text
    return _ACCESS_TOKEN_QUERY_RE.sub(r"\1***", text)


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
        # 手动重连追踪表：记录管理员主动触发重连后尚未获得结果的连接。
        # 键为连接名，值为唯一尝试标识 attempt_id（"{request_id}:{seq}"）：
        # - 在首次 await 前登记，消除并发请求的 TOCTOU 覆盖竞态；
        # - 成功/失败/取消路径均按 attempt_id 精确匹配后清理，避免旧 connect
        #   任务误清新请求的标识；同时携带 request_id 供上层路由回执。
        self._manual_reconnect_pending: dict[str, str] = {}
        # 手动重连尝试序号生成器：同一连接每次物理重连尝试递增，保证标识唯一。
        self._manual_reconnect_seq: dict[str, int] = {}
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


        websocket: ClientWebSocketResponse | None = None
        try:
            # 握手地址默认与上报地址一致；Jian Project 需要额外拼入短期访问令牌。
            connect_uri = uri

            # 关键顺序：先落地本次建连的基础元数据并递增重试计数，再做鉴权换票。
            preserved_info = self.connection_info.get(name, {})
            merged_info = {
                **preserved_info,
                **(connection_info or {}),
            }
            # 仅在“非重试”的首次建连时清除离线标记
            # 正常断开后的标记清理由连接成功路径负责，此处无需重复。
            if not is_retry:
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

            # Jian Project：握手前使用登录密钥 (lk_...) 或长期 Token (rt_...) 换取短期 Access Token，
            # 令牌同时通过 ?key= 与 X-API-Key 头携带。
            if is_jian_project_connection(name):
                configured_credential = (connection_info or {}).get("credential") or (
                    self.connection_info.get(name) or {}
                ).get("credential")
                if not configured_credential:
                    data_sources = self.config.get("data_sources")
                    if isinstance(data_sources, dict):
                        jp_cfg = data_sources.get("jian_project")
                        if isinstance(jp_cfg, dict):
                            configured_credential = str(
                                jp_cfg.get("login_key") or ""
                            ).strip()

                try:
                    access_token = await jian_project_auth_service.get_access_token(
                        configured_credential=configured_credential,
                        session=self.session,
                        force_refresh=is_retry,
                        ws_uri=uri,
                    )
                except Exception as auth_err:
                    logger.error(f"[灾害预警] Jian Project 鉴权换票失败: {auth_err}")
                    self._handle_connection_error(name, uri, headers, auth_err)
                    return

                base_url = (connection_info or {}).get("base_url") or uri.split("?")[0]
                # 含令牌的地址只用于本次握手：连接状态、事件元数据与错误日志
                # 一律使用不含令牌的 base_url，避免短期访问令牌被写入
                # 管理端响应、事件 metadata 与日志文件（CWE-532）。
                connect_uri = f"{base_url}?key={access_token}"
                uri = base_url
                headers = dict(headers or {})
                headers["X-API-Key"] = access_token
                if connection_info is not None:
                    connection_info["credential"] = configured_credential
                    connection_info["base_url"] = base_url
                # 短期访问令牌仅随本次握手使用，不常驻 connection_info：
                # 回写最终 uri 与已剥离 X-API-Key 的存储头，避免令牌落盘/外泄。
                stored_headers = {
                    key: value
                    for key, value in (headers or {}).items()
                    if key != "X-API-Key"
                }
                info = self.connection_info.get(name, {})
                info.update(
                    {
                        "uri": uri,
                        "headers": stored_headers,
                        "credential": configured_credential,
                        "base_url": base_url,
                    }
                )
                self.connection_info[name] = info

            # 统一配置建连的超时时间及负载限制
            conn_timeout = self.config.get("connection_timeout", 30)
            connect_kwargs = {
                "url": connect_uri,
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


                # 若该连接处于"手动重连待确认"状态，说明是管理员主动触发的重连：
                # 建连成功后，才清除追踪标记并回调上层
                # 汇报真实成功结果。若鉴权失败，上方错误路径会负责清除标记并汇报失败。
                # 按 attempt_id 精确匹配清理：仅当本次任务携带的尝试标识与当前待确认
                # 标识一致时才 pop 并回报，避免旧 connect 任务误清新请求的标识。
                attempt_id = (self.connection_info.get(name) or {}).get(
                    "manual_reconnect_attempt"
                )
                if attempt_id is not None:
                    current = self._manual_reconnect_pending.get(name)
                    if current == attempt_id:
                        self._manual_reconnect_pending.pop(name, None)
                        self._manual_reconnect_seq.pop(name, None)
                        self.emit_reconnect_result(
                            connection_name=name,
                            success=True,
                            message="重连成功，连接已建立",
                            stage="success",
                            # 回执显式携带原始 request_id 用于上层路由，
                            # attempt_id 仅内部使用，双字段互不覆盖。
                            request_id=attempt_id.rsplit(":", 1)[0],
                            attempt_id=attempt_id,
                        )

                # 重置重连相关的状态变量
                self.connection_retry_counts[name] = 0
                self.fallback_retry_counts[name] = 0
                self.last_heartbeat_time[name] = asyncio.get_running_loop().time()

                # 记录当前活跃服务器类型（基于实际连接的 URI 与原始地址判定）
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
            if is_jian_project_connection(name):
                jian_project_auth_service.invalidate_token()
            # 握手异常文本可能内嵌含令牌的 connect_uri，先脱敏再记录/上抛，
            # 避免短期访问令牌经日志、管理端通知或遥测外泄（CWE-532）。
            safe_error = _redact_access_token(str(e))
            logger.warning(f"[灾害预警] 连接中断或失败 {name}: {safe_error}")
            self._handle_connection_error(name, uri, headers, safe_error)

        except asyncio.CancelledError:
            # 主动关闭或插件卸载引发的任务取消，清理局部资源后正常退出。
            # 若该连接正处于"手动重连待确认"状态，按 attempt_id 精确匹配清理
            # 待确认标记，并汇报取消回执，避免取消后残留标记导致后续重连被误拒。
            attempt_id = (self.connection_info.get(name) or {}).get(
                "manual_reconnect_attempt"
            )
            if attempt_id is not None:
                current = self._manual_reconnect_pending.get(name)
                if current == attempt_id:
                    self._manual_reconnect_pending.pop(name, None)
                    self._manual_reconnect_seq.pop(name, None)
                    self.emit_reconnect_result(
                        connection_name=name,
                        success=False,
                        message="重连被取消（任务终止）",
                        stage="failed",
                        request_id=attempt_id.rsplit(":", 1)[0],
                        attempt_id=attempt_id,
                    )
            logger.debug(f"[灾害预警] WebSocket 连接任务被取消: {name}")
            await self._release_existing_connection(
                name,
                reason="连接任务取消",
                keep_connection_info=False,
                websocket=websocket,
            )
            raise
        except Exception as e:
            # 非预期类型错误，脱敏并上报异常遥测。
            safe_error = _redact_access_token(str(e))
            safe_traceback = _redact_access_token(traceback.format_exc())
            logger.error(
                f"[灾害预警] 未知连接错误 {name}: {type(e).__name__} - {safe_error}"
            )
            logger.debug(f"[灾害预警] 异常堆栈: {safe_traceback}")
            # 统一 best-effort 封装上报连接错误（内部自带启用检查与异常吞噬，
            # 不会中断后续配额策略与重连调度）。
            await track_error_safely(
                self._telemetry,
                e,
                module=f"core.websocket_manager.connect.{name}",
                log_context="连接错误遥测",
            )
            self._handle_connection_error(name, uri, headers, safe_error)
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

    def _handle_connection_error(
        self, name: str, uri: str, headers: dict | None, error: Exception | str
    ):
        """统一分发连接错误处理。"""
        # 若该连接正处于"手动重连待确认"状态，说明管理员主动触发的重连已失败：
        # 按 attempt_id 精确匹配清理（本次任务的尝试标识须与当前待确认标识一致），
        # 再回调上层汇报失败结果（request_id 用于路由、attempt_id 用于匹配），
        # 随后交给重连服务安排重试。
        attempt_id = (self.connection_info.get(name) or {}).get(
            "manual_reconnect_attempt"
        )
        if attempt_id is not None:
            current = self._manual_reconnect_pending.get(name)
            if current == attempt_id:
                self._manual_reconnect_pending.pop(name, None)
                self._manual_reconnect_seq.pop(name, None)
                self.emit_reconnect_result(
                    connection_name=name,
                    success=False,
                    message=f"重连失败: {_redact_access_token(str(error))}",
                    stage="failed",
                    request_id=attempt_id.rsplit(":", 1)[0],
                    attempt_id=attempt_id,
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
        # 说明上一次强制重连尚未产生终态结果。此时若覆盖 attempt_id，旧批次将
        # 永远收不到终态回执，且结果可能被路由到错误批次。
        # 采用串行化策略：拒绝新请求，保留旧请求的完整回执链路。
        if name in self._manual_reconnect_pending:
            logger.debug(
                f"[灾害预警] {name} 已有进行中的手动重连，拒绝重复触发 "
                f"(尝试标识为{self._manual_reconnect_pending.get(name)})"
            )
            return False

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

        # 生成唯一尝试标识并"在首次 await 前"登记：
        # 消除两个并发调用都通过重复检查、后写覆盖先写标识的 TOCTOU 竞态。
        seq = self._manual_reconnect_seq.get(name, 0) + 1
        self._manual_reconnect_seq[name] = seq
        attempt_id = f"{request_id}:{seq}"
        self._manual_reconnect_pending[name] = attempt_id

        # 取消已处于等待计时队列中的待执行重试任务，避免竞争
        if name in self.reconnect_tasks:
            task = self.reconnect_tasks.pop(name, None)
            if task is not None and not task.done():
                task.cancel()
                logger.debug(f"[灾害预警] 取消了 {name} 正在等待的重连任务 (强制重连)")

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

        logger.info(f"[灾害预警] 正在手动重连 {name}...")

        # 把尝试标识写入连接附加信息，供 connect 任务的成功/失败/取消路径
        # 按 attempt_id 精确匹配清理并回报，避免旧任务误清新请求的标识。
        info["manual_reconnect_attempt"] = attempt_id
        self.connection_info[name] = info

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
                    "active_server": info.get("active_server"),
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
        attempt_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """以异步安全方式触发手动重连结果回调。

        Args:
            request_id: 原始请求批次标识，供上层路由回执到对应订阅者。
            attempt_id: 内部唯一尝试标识（"{request_id}:{seq}"），
                用于底层待确认状态匹配，仅内部使用，不参与上层路由。
        """
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
            "attempt_id": attempt_id,
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
        except asyncio.TimeoutError:
            # 超时异常 str(e) 通常为空串，若不单独捕获日志里只会看到 url 后冒号空白
            logger.error(f"[灾害预警] HTTP 请求超时 {url}")
        except aiohttp.ClientError as e:
            logger.error(
                f"[灾害预警] HTTP 请求网络异常 {url}: {type(e).__name__}: {str(e) or repr(e)}"
            )
        except Exception as e:
            # 某些超时/取消异常 str(e) 为空，必须打印类型和 repr
            logger.error(
                f"[灾害预警] HTTP 请求异常 {url}: {type(e).__name__}: {str(e) or repr(e)}"
            )

        return None
