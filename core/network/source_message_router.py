"""
数据源消息路由器。
负责把网络接入层收到的原始消息，按连接前缀、消息类型与数据源配置
分发到对应解析器与事件接入链，是网络入口统一的路由装配点。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from astrbot.api import logger

from ...utils.plugin_logger import plugin_logger
from ..services.telemetry.telemetry_utils import track_error_safely
from ..sources.source_catalog import get_source_entry, get_source_ids_by_dispatch_family
from ..sources.source_entry import ProviderFamily
from ..sources.source_router import (
    get_openquake_source_id,
    get_provider_source_map,
    get_wolfx_source_id,
    route_fan_studio_message,
)
from .websocket.websocket_manager import WebSocketManager

# 从服务路由器预加载 FAN Studio 数据源名称到系统 ID 的双向映射表
FAN_STUDIO_PROVIDER_SOURCE_MAP = get_provider_source_map(ProviderFamily.FAN_STUDIO)


def _build_connection_metadata(connection_name, connection_info, source_channel=None):
    """整理连接元数据，写入事件附加信息。"""
    if not connection_info:
        return None
    # 抽取当前套接字基本参数，以便回溯来源
    metadata = {
        "connection_name": connection_name,
        "uri": connection_info.get("uri"),
        "connection_type": connection_info.get("connection_type"),
        "established_time": connection_info.get("established_time"),
    }
    if source_channel is not None:
        metadata["source_channel"] = source_channel
    return metadata


def _attach_event_connection_metadata(
    event, connection_name=None, connection_info=None, source_channel=None
):
    """把连接信息挂到事件元数据中，便于后续日志与管理端展示。"""
    metadata = _build_connection_metadata(
        connection_name, connection_info, source_channel
    )
    if not metadata:
        return

    # 若事件支持元数据属性，将当前活跃网络连接信息复制进去
    if hasattr(event, "metadata") and isinstance(event.metadata, dict):
        event.metadata["connection_info"] = dict(metadata)


def _resolve_config_key(source_id: str) -> str:
    """把数据源标识映射为配置项主键，便于输出统一日志。"""
    source_entry = get_source_entry(source_id)
    if source_entry is None:
        return source_id
    return source_entry.config_key


# FAN Studio 自 2026-07 起：未鉴权时 /all 仅放行 fssn / fssn-cmt。
# 建连后常先收到仅含这两路的半量 initial_all，鉴权成功后再推完整快照。
_FAN_PREAUTH_FREE_SOURCE_NAMES = frozenset({"fssn", "fssn-cmt"})


class SourceMessageRouter:
    """WebSocket 消息路由装配器。"""

    def __init__(self, service):
        """初始化路由器并缓存事件分发相关依赖。"""
        self.service = service
        self._parser_map_checked = False
        # 缓存事件分发及副作用服务引用
        self._dispatch_service = service.event_ingress_dispatch_service
        self._side_effect_service = service.source_ingress_side_effect_service
        self._source_runtime_query = service.source_runtime_query
        # Wolfx 未知消息节流：连续未知类型仅首次与每 N 轮各打一次，
        # 避免极端情况下高频未知类型逐条刷屏，同时保留排查价值。
        self._wolfx_unknown_logged = False
        self._wolfx_unknown_rounds = 0
        self._wolfx_unknown_log_interval = 60

    def register_all(self, ws_manager: WebSocketManager):
        """把各连接族处理器注册到 WebSocket 管理器。"""
        # 将各不同协议族的消息接收回调挂载到 WebSocket 连接管理器中
        ws_manager.register_handler("fan_studio", self._create_fan_studio_handler())
        ws_manager.register_handler("p2p", self._create_p2p_handler())
        ws_manager.register_handler("wolfx", self._create_wolfx_handler())
        ws_manager.register_handler("openquake_api", self._create_openquake_handler())

    async def _dispatch_event(
        self,
        event,
        *,
        source_id: str,
        source_label: str,
    ) -> None:
        # 交由分发服务去判断是否启动后台 Task 还是同步处理
        await self._dispatch_service.dispatch_event(
            event,
            source_id=source_id,
            source_label=source_label,
        )

    def _log_received_message(
        self,
        provider_name: str,
        message,
        connection_name=None,
        connection_info=None,
    ) -> None:
        # 原始 WebSocket 消息由 message_logger 负责落盘；这里避免对高频消息逐条输出 DEBUG。
        return

    def _has_parser(self, source_id: str) -> bool:
        """检查 source 是否已装配 parser。"""
        parsers = getattr(self.service, "parsers", {})
        return source_id in parsers and parsers[source_id] is not None

    @staticmethod
    def _resolve_stream_by_source_id(source_id: str) -> str:
        """根据数据源标识解析事件流标签，用于细粒度日志级别控制。"""
        if not source_id:
            return "earthquake"
        if "weather" in source_id:
            return "weather_alarm"
        if "typhoon" in source_id:
            return "typhoon"
        if "tsunami" in source_id:
            return "tsunami"
        if source_id == "global_quake":
            return "global_quake"
        return "earthquake"

    def _is_source_routable(self, source_id: str, source_label: str) -> bool:
        config_key = _resolve_config_key(source_id)
        # 校验：1. 数据源是否在当前配置中被启用
        if not self._source_runtime_query.is_source_enabled(source_id):
            logger.debug(
                f"[灾害预警] 数据源 {config_key} ({source_label}) 未启用，忽略"
            )
            return False
        # 校验：2. 相应的消息解析器是否存在，避免解析抛错
        if not self._has_parser(source_id):
            logger.warning(
                f"[灾害预警] 未找到解析器: {source_id}",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return False
        return True

    async def _parse_and_dispatch(
        self,
        *,
        source_id: str,
        source_label: str,
        parser_input,
        connection_name=None,
        connection_info=None,
        source_channel=None,
        parser_log_label: str | None = None,
    ) -> bool:
        # 只有“已启用且已装配解析器”的数据源才允许进入正式解析链
        if not self._is_source_routable(source_id, source_label):
            return False

        # 将收到的报文或结构化数据送入具体解析器
        events = self.service.parse_event(source_id, parser_input)
        if not events:
            return False

        # 兼容解析器返回单个事件或事件列表
        if not isinstance(events, list):
            events = [events]

        for event in events:
            # 把连接来源补到事件元数据，便于后续展示来源通道与追踪链路
            _attach_event_connection_metadata(
                event,
                connection_name=connection_name,
                connection_info=connection_info,
                source_channel=source_channel,
            )
            if getattr(self.service, "is_silencing", lambda: False)():
                if hasattr(event, "metadata") and isinstance(event.metadata, dict):
                    event.metadata.setdefault("bootstrap", True)
                    if connection_name and not event.metadata.get("bootstrap_kind"):
                        event.metadata["bootstrap_kind"] = "conn_first_wave"
            # 将解析好的事件丢给分发流水线处理
            await self._dispatch_event(
                event,
                source_id=source_id,
                source_label=source_label,
            )
        return True

    async def _track_router_error(self, exception: Exception, module: str) -> None:
        """上报路由层非预期异常，避免解析入口错误只停留在日志中。"""
        telemetry = getattr(self.service, "_telemetry", None)
        await track_error_safely(
            telemetry,
            exception,
            module=module,
            log_context="路由异常遥测",
        )

    async def _parse_candidate_source_ids(
        self,
        *,
        source_ids: list[str],
        parser_input,
        connection_name=None,
        connection_info=None,
        source_channel=None,
        source_label_resolver: Callable[[str], str] | None = None,
    ) -> bool:
        # 依次尝试匹配候选的数据源解析器
        for source_id in source_ids:
            try:
                dispatched = await self._parse_and_dispatch(
                    source_id=source_id,
                    source_label=(
                        source_label_resolver(source_id)
                        if callable(source_label_resolver)
                        else source_id
                    ),
                    parser_input=parser_input,
                    connection_name=connection_name,
                    connection_info=connection_info,
                    source_channel=source_channel,
                    parser_log_label=source_id,
                )
                # 只要有一路解析分发成功，就立即中断候选链并返回 True
                if dispatched:
                    return True
            except Exception as error:
                connection_uri = (
                    connection_info.get("uri") if connection_info else "未知地址"
                )
                plugin_logger.error(
                    f"[灾害预警] {source_id} 解析器处理来自 {connection_name or '未知连接'} 的消息失败，连接地址为 {connection_uri}，错误为 {error}",
                    exc_info=True,
                )
                # 对捕获到的具体解析异常进行遥测跟踪，保证健壮性
                await self._track_router_error(
                    error,
                    module=f"core.source_message_router.parse_candidate.{source_id}",
                )
        return False

    def _ensure_fan_studio_parser_mapping(self) -> None:
        """首次处理 FAN 消息前校验路由表与解析器是否对应齐全。"""
        if self._parser_map_checked:
            return
        # 遍历静态数据源配置中的所有 fan studio 定义，校验其解析器是否存在
        for source_name, source_id in FAN_STUDIO_PROVIDER_SOURCE_MAP.items():
            if source_id and not self._has_parser(source_id):
                # FAN Studio 映射包含气象/台风/海啸等子源，按 source_id 解析事件流标签，
                # 避免固定为 earthquake 绕过对应事件流的日志策略。
                stream_tag = self._resolve_stream_by_source_id(source_id)
                plugin_logger.warning(
                    f"[灾害预警] Source ID '{source_id}' (源: {source_name}) 未注册解析器，"
                    f"请检查 core/app/disaster_service.py 中的初始化。",
                    is_event_linked=True,
                    event_stream=stream_tag,
                )
        self._parser_map_checked = True

    @staticmethod
    def _fan_initial_all_known_source_keys(data: dict) -> list[str]:
        """提取 initial_all 中已注册的 FAN 数据源键。"""
        keys: list[str] = []
        for key, value in data.items():
            if key == "type" or not isinstance(value, dict):
                continue
            # 仅认 FAN provider 源名映射，避免把元数据键算进去
            if key in FAN_STUDIO_PROVIDER_SOURCE_MAP:
                keys.append(key)
        return keys

    @classmethod
    def _is_fan_preauth_partial_initial_all(cls, data: dict) -> bool:
        """判断是否为鉴权前半量 initial_all（仅 fssn / fssn-cmt）。

        FAN Studio 文档：未鉴权时 /all 仅放行这两路；鉴权成功后会再推完整快照。
        若把半量包当正式 bootstrap 解析，会出现 fssn-cmt 解析日志刷两次。
        """
        if str(data.get("type") or "").strip() != "initial_all":
            return False
        source_keys = cls._fan_initial_all_known_source_keys(data)
        if not source_keys:
            return False
        return all(key in _FAN_PREAUTH_FREE_SOURCE_NAMES for key in source_keys)

    def _create_fan_studio_handler(self):
        """创建 FAN Studio 连接的消息处理器。"""

        async def fan_studio_handler(
            message, connection_name=None, connection_info=None
        ):
            self._log_received_message(
                "FAN Studio",
                message,
                connection_name=connection_name,
                connection_info=connection_info,
            )

            try:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as error:
                    # 避免非 JSON 报文引起程序崩溃
                    plugin_logger.error(f"[灾害预警] JSON解析失败: {error}")
                    return None

                msg_type = (
                    str(data.get("type") or "").strip()
                    if isinstance(data, dict)
                    else ""
                )

                # 鉴权成功回执：不进入业务解析
                if msg_type in {"auth_success", "auth_ok", "authenticated"}:
                    return None

                # FAN Studio 会以业务错误包表达限流/策略拒绝；收到后主动关闭，尽快释放上游配额
                if msg_type == "error":
                    error_message = str(
                        data.get("message") or data.get("msg") or ""
                    ).strip()
                    plugin_logger.warning(
                        f"[灾害预警] FAN Studio 返回错误包，连接为 {connection_name or 'unknown'}："
                        f"{error_message or data}"
                    )
                    ws_manager = getattr(self.service, "ws_manager", None)
                    if connection_name and ws_manager is not None:
                        try:
                            apply_policy = getattr(
                                ws_manager, "_apply_fan_quota_policy_on_error", None
                            )
                            if callable(apply_policy):
                                await apply_policy(
                                    connection_name,
                                    RuntimeError(
                                        error_message or "FAN Studio policy error"
                                    ),
                                )
                            websocket = ws_manager.connections.get(connection_name)
                            if websocket is not None and not websocket.closed:
                                close_reason = (
                                    error_message or "FAN Studio policy error"
                                ).encode("utf-8")[:120]
                                await websocket.close(code=1000, message=close_reason)
                        except Exception as close_error:
                            plugin_logger.debug(
                                f"[灾害预警] 关闭 FAN Studio 错误连接失败: {close_error}"
                            )
                    return None

                # 鉴权前半量 initial_all（仅 fssn/fssn-cmt）：直接丢弃等待鉴权后的完整快照，
                # 不再通知静默协调器标记已收到 bootstrap，避免导致门闩提前放行。
                if isinstance(data, dict) and self._is_fan_preauth_partial_initial_all(
                    data
                ):
                    return None

                # 先校验路由映射，再把一条总线消息拆成多个候选数据源消息
                self._ensure_fan_studio_parser_mapping()
                routed_messages = route_fan_studio_message(data)
                messages_to_process = [
                    (item.source_name, item.source_id, item.payload)
                    for item in routed_messages
                ]
                if msg_type == "initial_all":
                    coordinator = getattr(self.service, "startup_silence", None)
                    if coordinator is not None:
                        try:
                            coordinator.note_bootstrap_payload(
                                connection_name=connection_name,
                                kind="fan_initial_all",
                            )
                        except Exception as exc:
                            plugin_logger.debug(
                                f"[灾害预警] FAN initial_all 通知静默协调器失败: {exc}"
                            )
                # 遍历被分配出来的数据源及负载，分别尝试分发
                for source, source_id, payload in messages_to_process:
                    if not self._is_source_routable(source_id, source):
                        continue

                    plugin_logger.info(
                        f"[灾害预警] 处理 {source} 数据 ({_resolve_config_key(source_id)})",
                        is_event_linked=True,
                        event_stream=self._resolve_stream_by_source_id(source_id),
                        is_silent_window=True,
                    )
                    await self._parse_and_dispatch(
                        source_id=source_id,
                        source_label=source,
                        parser_input=json.dumps(payload),
                        connection_name=connection_name,
                        connection_info=connection_info,
                        source_channel=source,
                        parser_log_label=source,
                    )

                # 没有任何子消息被路由：心跳/未知包均属常态，无需逐条打日志，
                # 避免高频未知消息刷屏。真正的异常由上层 error 日志承担。
                return None

            except Exception as error:
                connection_uri = (
                    connection_info.get("uri") if connection_info else "未知地址"
                )
                connection_type = (
                    connection_info.get("connection_type")
                    if connection_info
                    else "未知类型"
                )
                plugin_logger.error(
                    f"[灾害预警] FAN Studio 处理器解析来自 {connection_name or '未知连接'} 的消息失败，连接地址为 {connection_uri}，连接类型为 {connection_type}，错误为 {error}",
                    exc_info=True,
                )
                # 路由异常遥测
                await self._track_router_error(
                    error,
                    module="core.source_message_router.fan_studio_handler",
                )
                raise

        return fan_studio_handler

    def _create_p2p_handler(self):
        """创建 P2P WebSocket 连接的消息处理器。"""

        async def p2p_handler(message, connection_name=None, connection_info=None):
            self._log_received_message(
                "P2P",
                message,
                connection_name=connection_name,
                connection_info=connection_info,
            )

            code = None
            try:
                data = json.loads(message)
                code = str(data.get("code") or "").strip()
                # 556 代表紧急地震速报 EEW
                if code == "556":
                    plugin_logger.info(
                        "[灾害预警] P2P 处理器收到紧急地震速报，业务码为 556，准备解析",
                        is_event_linked=True,
                        event_stream="earthquake",
                        is_silent_window=True,
                    )
            except (json.JSONDecodeError, AttributeError, TypeError):
                data = {}

            # P2P 先按业务码归类，再映射到一组可尝试的解析数据源
            dispatch_family = {
                "556": "p2p_eew",
                "551": "p2p_report",
                "552": "p2p_tsunami",
            }.get(code or "")

            # 识别到有效业务码即视为连接已进入业务流：
            # 提前通知静默协调器记录首包（kind 按派发族区分），
            # 覆盖首包无法解析出事件/无匹配数据源时 PENDING 不写 _pending_primed
            # 导致 arm() 后门闩等不到回调而干等超时的情况。
            if dispatch_family:
                self._note_connection_bootstrap(
                    connection_name, kind=f"p2p_first_payload:{code}"
                )

            # 根据派发族获取所有关联的静态数据源候选 ID
            candidate_source_ids = (
                get_source_ids_by_dispatch_family(dispatch_family)
                if dispatch_family
                else []
            )

            # 启动候选者解析轮询
            await self._parse_candidate_source_ids(
                source_ids=list(candidate_source_ids),
                parser_input=message,
                connection_name=connection_name,
                connection_info=connection_info,
                source_channel=code or None,
            )

        return p2p_handler

    def _note_connection_bootstrap(
        self,
        connection_name: str | None,
        *,
        kind: str,
    ) -> None:
        """通知静默协调器：某连接已收到可用于就绪判定的首包/业务帧。"""
        if not connection_name:
            return
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_bootstrap_payload(
                connection_name=connection_name,
                kind=kind,
            )
        except Exception as exc:
            plugin_logger.debug(
                f"[灾害预警] 连接 {connection_name} 通知静默协调器失败: {exc}"
            )

    def _create_wolfx_handler(self):
        """创建 Wolfx WebSocket 连接的消息处理器。"""

        async def wolfx_handler(message, connection_name=None, connection_info=None):
            self._log_received_message(
                "Wolfx",
                message,
                connection_name=connection_name,
                connection_info=connection_info,
            )

            try:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError as error:
                    plugin_logger.error(f"[灾害预警] Wolfx JSON解析失败: {error}")
                    return None

                msg_type = data.get("type")
                # 心跳直接跳过不作处理
                if msg_type in ["heartbeat", "pong"]:
                    return None

                # 非心跳帧即可视为连接已进入业务流，提前放行静默门闩
                self._note_connection_bootstrap(
                    connection_name, kind="wolfx_first_payload"
                )

                # 获取 Wolfx 当前子报文类型对应的系统内 source_id
                source_id = get_wolfx_source_id(msg_type)
                if source_id is None:
                    # 未知类型属罕见异常态，但为防极端情况下高频未知类型刷屏，
                    # 连续未知仅在首次与每 _wolfx_unknown_log_interval 轮各打一次。
                    if not self._wolfx_unknown_logged:
                        plugin_logger.debug(
                            f"[灾害预警] Wolfx 消息类型 {msg_type} 暂未识别，"
                            f"来源连接为 {connection_name}"
                        )
                        self._wolfx_unknown_logged = True
                    else:
                        self._wolfx_unknown_rounds += 1
                        if (
                            self._wolfx_unknown_rounds
                            >= self._wolfx_unknown_log_interval
                        ):
                            self._wolfx_unknown_rounds = 0
                            plugin_logger.debug(
                                f"[灾害预警] Wolfx 连续 {self._wolfx_unknown_log_interval} 轮 "
                                f"未识别消息类型，最近为 {msg_type}"
                            )
                    return None

                if not self._is_source_routable(source_id, msg_type):
                    return None
                # 某些 Wolfx 消息在正式解析前需要先触发旁路副作用（比如缓存 eqlist）
                await self._side_effect_service.process_message(
                    source_id=source_id,
                    message_type=msg_type,
                    payload_data=data,
                )

                # 解析并分发
                await self._parse_and_dispatch(
                    source_id=source_id,
                    source_label=msg_type,
                    parser_input=message,
                    connection_name=connection_name,
                    connection_info=connection_info,
                    source_channel=msg_type,
                    parser_log_label=source_id,
                )
                return None

            except Exception as error:
                connection_uri = (
                    connection_info.get("uri") if connection_info else "未知地址"
                )
                plugin_logger.error(
                    f"[灾害预警] Wolfx 处理器处理来自 {connection_name or '未知连接'} 的消息失败，连接地址为 {connection_uri}，错误为 {error}",
                    exc_info=True,
                )
                # 遥测路由层异常
                await self._track_router_error(
                    error,
                    module="core.source_message_router.wolfx_handler",
                )
                return None

        return wolfx_handler

    def _create_openquake_handler(self):
        """创建 OpenQuakeAPI 聚合连接的消息处理器。

        连接挂在全量端点后，按 RealtimeEvent.source 分发到已注册子源；
        当前仅接入 Global Quake（gq），其余 source 先忽略以便后续继续接入。
        """

        async def openquake_handler(
            message, connection_name=None, connection_info=None
        ):
            self._log_received_message(
                "OpenQuakeAPI",
                message,
                connection_name=connection_name,
                connection_info=connection_info,
            )

            # 任意入站帧都可推进静默门闩（含状态/心跳类），避免无震时干等 first_payload_timeout
            self._note_connection_bootstrap(
                connection_name, kind="openquake_first_payload"
            )

            try:
                # 历史 protobuf 二进制帧仍按 Global Quake 路径处理
                if isinstance(message, (bytes, bytearray)):
                    if not self._is_source_routable("global_quake", "global_quake"):
                        return
                    await self._parse_and_dispatch(
                        source_id="global_quake",
                        source_label="global_quake",
                        parser_input=message,
                        connection_name=connection_name,
                        connection_info=connection_info,
                        source_channel="gq",
                        parser_log_label="Global Quake",
                    )
                    return

                raw_text = message if isinstance(message, str) else None
                if raw_text is None:
                    # 非文本/非二进制消息为混流常态，不逐一记录
                    return

                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as error:
                    plugin_logger.error(
                        f"[灾害预警] OpenQuakeAPI JSON 解析失败: {error}"
                    )
                    return

                if not isinstance(data, dict):
                    # 非对象 JSON 消息为混流常态，不逐一记录
                    return

                source_name = str(data.get("source") or "").strip()
                msg_type = str(data.get("type") or "").strip().lower()
                action = str(data.get("action") or "").strip().lower()

                # 连接态/心跳类帧仅用于保活与静默门闩，不进入业务解析
                if msg_type in {"status", "heartbeat"} or action in {
                    "connected",
                    "disconnected",
                    "info",
                }:
                    return

                source_id = get_openquake_source_id(source_name)
                if source_id is None:
                    return

                if not self._is_source_routable(source_id, source_name or source_id):
                    return

                await self._parse_and_dispatch(
                    source_id=source_id,
                    source_label=source_name or source_id,
                    parser_input=raw_text,
                    connection_name=connection_name,
                    connection_info=connection_info,
                    source_channel=source_name or source_id,
                    parser_log_label=source_id,
                )
            except Exception as error:
                connection_uri = (
                    connection_info.get("uri") if connection_info else "未知地址"
                )
                plugin_logger.error(
                    f"[灾害预警] OpenQuakeAPI 处理器处理来自 "
                    f"{connection_name or '未知连接'} 的消息失败，"
                    f"连接地址为 {connection_uri}，错误为 {error}",
                    exc_info=True,
                )
                # 异常遥测
                await self._track_router_error(
                    error,
                    module="core.source_message_router.openquake_handler",
                )

        return openquake_handler


__all__ = ["SourceMessageRouter"]
