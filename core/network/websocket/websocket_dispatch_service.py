"""
WebSocket 消息分发与关闭处理服务。
负责托管单连接消息循环、原始消息记录、处理器定位、关闭码判定与异常关闭后的策略分流。
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

from aiohttp import ClientWebSocketResponse, WSMsgType

from astrbot.api import logger


class WebSocketDispatchService:
    """WebSocket 消息循环分发服务。"""

    # 正常关闭连接的代码集合
    _NORMAL_CLOSE_CODES = {
        1000,
        1001,
    }

    # 协议级严重错误且通常不可恢复的代码集合，这类情况短时重试意义不大
    # 注意：1008（Policy Violation）不在此列。
    # FAN Studio 等上游会用 1008 表达限流/策略拒绝，连接配额释放后仍可恢复。
    _NO_RECONNECT_CODES = {
        1002,
        1003,
        1007,
        1009,
        1010,
        1011,
    }

    def __init__(self, manager):
        """保存管理器引用，复用其连接状态与处理器注册表。"""
        self.manager = manager

    async def handle_connection_session(
        self,
        name: str,
        uri: str,
        headers: dict | None,
        websocket: ClientWebSocketResponse,
    ) -> None:
        """处理单个连接的消息循环与关闭码策略。"""
        try:
            # 持续监听当前 WebSocket 实例传入的数据帧
            async for msg in websocket:
                # 判断当前数据帧的具体类别并分流处理
                if msg.type == WSMsgType.TEXT:
                    # 文本业务消息处理
                    await self._handle_payload_message(
                        name=name,
                        uri=uri,
                        message=msg.data,
                        error_label="消息处理错误",
                    )
                elif msg.type == WSMsgType.BINARY:
                    # 二进制业务消息处理（如 Global Quake 这种 Protobuf 二进制序列）
                    await self._handle_payload_message(
                        name=name,
                        uri=uri,
                        message=msg.data,
                        error_label="二进制消息处理错误",
                    )
                elif msg.type == WSMsgType.ERROR:
                    # 抛出 socket 传输错误
                    raise msg.data
                elif msg.type == WSMsgType.CLOSED:
                    # 正常关闭码由 handle_close_code 的 INFO 汇总承担，此处仅保留非正常关闭码明细
                    if websocket.close_code not in self._NORMAL_CLOSE_CODES:
                        logger.debug(
                            f"[灾害预警] WebSocket {name} 的连接已收到关闭帧，关闭码为 {websocket.close_code}"
                        )
                    break
                elif msg.type in {WSMsgType.PING, WSMsgType.PONG}:
                    # 保活心跳帧，只需刷新该连接的活跃时间即可
                    self._touch_connection(name)

            # 消息循环正常结束（即连接断开或关闭帧到达），依据关闭码决定重连动作
            await self.handle_close_code(
                name=name,
                uri=uri,
                headers=headers,
                close_code=websocket.close_code,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[灾害预警] WebSocket消息循环异常 {name}: {e}")
            raise

        logger.debug(f"[灾害预警] WebSocket 连接 {name} 的消息循环已结束")

    def log_message(self, name: str, message: Any, uri: str) -> None:
        """记录原始 WebSocket 消息。"""
        if not self.manager.message_logger:
            return

        try:
            # 构造连接信息并向日志记录器写数据
            self.manager.message_logger.log_raw_message(
                source=f"websocket_{name}",
                message_type="websocket_message",
                payload_data=message,
                connection_info={
                    "url": uri,
                    "connection_type": "websocket",
                    "handler": self.get_handler_name_for_connection(name),
                    **self.manager.connection_info.get(name, {}),
                },
            )
        except (TypeError, AttributeError):
            try:
                # 降级处理，兼容旧式接口
                self.manager.message_logger.log_websocket_message(name, message, uri)
            except Exception as e:
                logger.warning(f"[灾害预警] 消息记录失败: {e}")

    def get_handler_name_for_connection(self, connection_name: str) -> str:
        """获取连接对应的处理器名称。"""
        connection_meta = self.manager.connection_info.get(connection_name, {})
        explicit_handler = str(connection_meta.get("handler") or "").strip()
        if explicit_handler:
            return explicit_handler

        handler_name = self.find_handler_by_prefix(connection_name)
        return handler_name or "unknown"

    def find_handler_by_prefix(self, connection_name: str) -> str | None:
        """通过已注册处理器名称查找连接对应处理器。"""
        matched_handler = None

        # 遍历所有已注册的处理器，按最长前缀去寻找最佳匹配者
        for handler_name in self.manager.message_handlers.keys():
            if connection_name.startswith(handler_name):
                if matched_handler is None or len(handler_name) > len(matched_handler):
                    matched_handler = handler_name

        if matched_handler is not None:
            return matched_handler

        # 降级分割匹配，截取下划线前的部分作为处理器名称
        candidate_handler = connection_name.split("_", 1)[0].strip()
        if candidate_handler and candidate_handler not in self.manager.message_handlers:
            logger.warning(
                f"[灾害预警] 前缀匹配找到但处理器不存在: '{connection_name}' -> '{candidate_handler}'"
            )

        return None

    async def handle_close_code(
        self,
        name: str,
        uri: str,
        headers: dict | None,
        close_code: int | None,
    ) -> None:
        """根据关闭码执行关闭后策略。"""
        if close_code is None:
            return

        # 检查是否属于协议级严重错误且通常不可恢复的代码
        if close_code in self._NO_RECONNECT_CODES:
            raise Exception(f"WebSocket协议错误关闭（不重连），代码 {close_code}")

        # 如果管理器正处于关闭中（例如主动 stop），或者该连接已被主动从管理器中移除/断开，则不进行重连
        # 在 websocket_runtime_service.stop/disconnect 时，self.manager._stopping 会被置为 True
        # 且 disconnect 会主动将连接从 connections 中移出。
        if self.manager._stopping or name not in self.manager.connections:
            logger.info(
                f"[灾害预警] WebSocket {name} 属于用户主动断连或管理器停止流程，不触发重连。关闭码为 {close_code}"
            )
            return

        # 判断并路由关闭码行为
        if close_code in self._NORMAL_CLOSE_CODES:
            logger.info(
                f"[灾害预警] WebSocket {name} 的连接正常关闭，关闭码为 {close_code}。准备尝试重新连接..."
            )
            # 即使是正常关闭码（1000, 1001），为了高可用性也触发重连机制，但使用 info 日志以示区别
            self.manager._handle_connection_error(
                name,
                uri,
                headers,
                RuntimeError(f"WebSocket正常断开，尝试恢复连接，代码 {close_code}"),
            )
            return

        # 异常断开（1006 代表未经握手便直接在 TCP 层断开）
        if close_code == 1006:
            logger.warning(
                f"[灾害预警] WebSocket {name} 的连接异常关闭，关闭码为 {close_code}，准备重连"
            )
            # 交由管理器去触发重连策略
            self.manager._handle_connection_error(
                name,
                uri,
                headers,
                RuntimeError(f"WebSocket异常关闭（连接中断），代码 {close_code}"),
            )
            return

        # 策略违规（1008）：常见于上游限流、连接数配额或策略拒绝，释放后通常可恢复
        if close_code == 1008:
            logger.warning(
                f"[灾害预警] WebSocket {name} 因策略违规关闭，关闭码为 {close_code}，"
                "将释放本地连接后尝试重连"
            )
            policy_error = RuntimeError(f"WebSocket策略违规关闭，代码 {close_code}")
            apply_policy = getattr(
                self.manager, "_apply_fan_quota_policy_on_error", None
            )
            if callable(apply_policy):
                await apply_policy(name, policy_error)
            self.manager._handle_connection_error(
                name,
                uri,
                headers,
                policy_error,
            )
            return

        # 兜底：其他未被标记为不可重连的意外关闭码，也尝试进行重连，防止由于其它关闭码导致监控永久失效
        logger.warning(
            f"[灾害预警] WebSocket {name} 意外关闭，关闭码为 {close_code}，准备尝试重连"
        )
        self.manager._handle_connection_error(
            name,
            uri,
            headers,
            RuntimeError(f"WebSocket意外关闭，代码 {close_code}"),
        )

    async def _handle_payload_message(
        self,
        name: str,
        uri: str,
        message: Any,
        error_label: str,
    ) -> None:
        """处理文本或二进制消息。"""
        # 只要收到了网络包，就刷新活跃心跳时间，防止空转超时被主动切断
        self._touch_connection(name)

        try:
            self.log_message(name, message, uri)

            # 定位处理器
            connection_info = self.manager.connection_info[name]
            handler_name = str(connection_info.get("handler") or "").strip()
            if not handler_name:
                handler_name = self.find_handler_by_prefix(name)

            # 调用处理器去解析及分派消息
            if handler_name and handler_name in self.manager.message_handlers:
                await self.manager.message_handlers[handler_name](
                    message,
                    connection_name=name,
                    connection_info=connection_info,
                )
            else:
                logger.warning(f"[灾害预警] 连接 {name} 没有匹配到可用的消息处理器")
        except Exception as e:
            logger.error(f"[灾害预警] {error_label} {name}: {e}")
            logger.debug(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            # 消息在分发到处理器之前的解码/路由异常，只有日志没有遥测，这里补上报。
            telemetry = getattr(self.manager, "_telemetry", None)
            if telemetry and telemetry.enabled:
                try:
                    await telemetry.track_error(
                        e,
                        module="core.websocket_dispatch_service._handle_payload_message",
                    )
                except Exception as telemetry_exc:
                    # 遥测上报失败不得影响消息循环继续运行。
                    logger.debug(
                        f"[灾害预警] 分发错误遥测上报失败（已忽略）: {telemetry_exc}"
                    )

    def _touch_connection(self, name: str) -> None:
        """刷新连接最近活跃时间。"""
        self.manager.last_heartbeat_time[name] = asyncio.get_running_loop().time()
