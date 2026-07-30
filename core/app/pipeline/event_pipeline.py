"""
事件处理流水线。
负责串联灾害事件的日志记录、推送、统计与 Web 实时通知，减少 DisasterWarningService 中的编排职责。
"""

from __future__ import annotations

from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import EventEnvelope, WeatherEvent


class EventPipeline:
    """灾害事件处理流水线。

    该流水线聚焦"事件进入应用层后的统一后处理"，
    将推送、统计、管理端广播等横切逻辑从主服务中剥离，
    让主服务更专注于依赖装配与总入口编排。
    """

    def __init__(self, service):
        # 这里保存的是主服务实例引用，不复制任何运行时状态，
        # 以确保流水线始终读取到最新的配置、连接状态与消息推送结果。
        self.service = service  # 主服务 DisasterWarningService 的引用
        # 气象预警聚合推送服务，由主服务在装配时注入。
        self._weather_aggregation = None

    def set_weather_aggregation_service(self, service) -> None:
        """注入气象预警聚合推送服务。"""
        self._weather_aggregation = service
        if service is not None:
            service.set_flush_callback(self._flush_weather_buffer)

    async def handle(self, event: EventEnvelope) -> None:
        """
        执行事件主处理流程。

        流水线执行过程：
        1. 获取订阅会话并异步推送事件消息（包含动态渲染、推送过滤等）；
        2. 记录推送统计（包括最终成功订阅的会话）；
        3. 向 Web 管理端异步广播最小化的轻量级事件摘要。
        """
        # 这里保留 envelope 别名，便于后续阅读时明确：
        # 流水线处理的是已经标准化完成的事件对象，而非原始数据源消息。
        envelope = event

        # 第一阶段（在上游已完成）：解析器与主服务负责把原始消息转换为统一事件。
        # 流水线从这里开始只处理“标准化后的应用层事件”。

        # 气象预警聚合：对气象预警事件尝试进入聚合缓冲区。
        # 按会话级配置独立判断：启用聚合的会话进入缓冲区，未启用的会话走常规推送。
        # 若任一会话缓冲了事件，则跳过这些会话的独立推送；
        # 未启用聚合的会话仍需通过常规推送路径发送。
        if self._weather_aggregation is not None and isinstance(
            event.event, WeatherEvent
        ):
            target_sessions = self.service.session_config_manager.list_target_sessions()
            # 按会话分别判断是否聚合
            aggregated_sessions: set[str] = set()
            non_aggregated_sessions: list[str] = []
            for session in target_sessions:
                runtime_config = (
                    self.service.session_config_manager.get_effective_config(session)
                )
                if self._weather_aggregation.should_aggregate(
                    event, session, runtime_config
                ):
                    aggregated_sessions.add(session)
                else:
                    non_aggregated_sessions.append(session)

            if non_aggregated_sessions:
                # 未启用聚合的会话走常规推送路径
                push_result = await self.service.message_manager.push_event(
                    event,
                    target_sessions=non_aggregated_sessions,
                    session_config_getter=self.service.session_config_manager.get_effective_config,
                )
                if not push_result:
                    logger.debug(
                        f"[灾害预警] 事件未产生实际推送（非聚合会话）: {envelope.id}"
                    )

            if aggregated_sessions:
                # 事件已进入聚合缓冲区，跳过这些会话的独立推送
                plugin_logger.debug(
                    f"[灾害预警] 气象预警 {envelope.id} 已进入聚合缓冲区，"
                    f"跳过 {len(aggregated_sessions)} 个会话的独立推送",
                    event_stream="weather_alarm",
                )
        else:
            # 非气象预警事件，走原有推送路径
            await self._push_event_normal(event, envelope)

        # 第三阶段：记录统计结果。
        # 统计记录与实际是否推送成功解耦，这样后续仍可分析规则过滤命中率、会话覆盖情况，以及"收到事件但未推送"的业务原因。
        await self.service.statistics_manager.record_push(
            event,
            pushed_sessions=self.service.message_manager.last_success_sessions,  # 上一次推送成功的会话列表
        )

        # 第四阶段：向管理端广播轻量摘要。
        # 这里只发送最小必要字段，避免把完整事件对象直接传给管理端，
        # 从而降低实时面板负载，并减少内部模型字段外露带来的耦合风险。
        if self.service.web_admin_server:
            try:
                event_summary = {
                    "id": envelope.id,  # 事件唯一标识
                    "type": envelope.event_type,  # 灾害事件类型 (如 earthquake, tsunami)
                    "source": envelope.source_id,  # 数据来源
                    "time": datetime.now().isoformat(),  # 广播到达应用的本地时间
                }
                await self.service.web_admin_server.notify_event(event_summary)
            except Exception as ws_e:
                # 管理端广播失败不影响主链路；用户侧推送与统计已完成，因此这里按可降级的旁路处理。
                logger.debug(f"[灾害预警] WebSocket 通知失败: {ws_e}")

    async def _push_event_normal(
        self, event: EventEnvelope, envelope: EventEnvelope
    ) -> None:
        """执行常规推送路径（非聚合）。"""
        target_sessions = (
            self.service.session_config_manager.list_target_sessions()
        )  # 获取所有目标会话
        push_result = await self.service.message_manager.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=self.service.session_config_manager.get_effective_config,
        )
        if not push_result:
            # 未推送不一定代表异常，常见原因包括规则过滤未命中、会话未订阅，或事件被静默策略抑制。
            logger.debug(f"[灾害预警] 事件未产生实际推送: {envelope.id}")

    async def _flush_weather_buffer(
        self,
        session: str,
        entries: list,
        config: dict,
        *,
        mode: str = "forward",
    ) -> None:
        """聚合缓冲区推送回调。

        为每条气象预警构建含图标的完整消息链后发送。
        每条预警在构建消息前先通过规则链复核，未通过的不发送。
        mode="forward" 时打包为合并转发消息；
        mode="single" 时逐条发送。
        """
        from ...message.push.weather_aggregation_service import WeatherBufferEntry

        if not entries:
            return

        message_manager = self.service.message_manager
        session_config_getter = self.service.session_config_manager.get_effective_config
        runtime_config = session_config_getter(session)

        # 为每条预警构建完整消息链（含图标），先通过规则链复核
        built_messages: list[tuple[WeatherBufferEntry, MessageChain]] = []
        for entry in entries:
            if not isinstance(entry, WeatherBufferEntry):
                continue
            try:
                # 规则链复核：确保聚合推送也遵守过滤规则
                decision = message_manager.evaluate_push_decision(
                    entry.event,
                    runtime_config=runtime_config,
                    session_id=session,
                    emit_filter_log=False,
                    commit_state=False,
                )
                if not decision.accepted:
                    plugin_logger.debug(
                        f"[灾害预警] 聚合推送事件 {entry.event.id} 在 {session} "
                        f"规则链复核未通过: {decision.reason}"
                        + (f"（{decision.detail}）" if decision.detail else ""),
                        event_stream="weather_alarm",
                    )
                    continue

                # 复用消息构建服务构建含图标的完整消息
                message = (
                    await message_manager.message_build_service.build_message_async(
                        entry.event,
                        runtime_config=runtime_config,
                    )
                )
                built_messages.append((entry, message))
            except Exception as e:
                logger.error(
                    f"[灾害预警] 聚合推送构建消息失败: {e}, 事件: {entry.event.id}"
                )

        if not built_messages:
            return

        if mode == "forward":
            # 构建合并转发节点
            bot_id = "0"
            # 尝试从上下文获取 bot_id
            context = getattr(self.service, "context", None)
            if context is not None:
                try:
                    bot_id = str(context.get_self_id() or "0")
                except Exception:
                    pass

            bot_name = "灾害预警"
            nodes = Comp.Nodes([])

            # 添加头部节点
            header = f"📋 气象预警聚合推送（共 {len(built_messages)} 条）"
            nodes.nodes.append(
                Comp.Node(uin=bot_id, name=bot_name, content=[Comp.Plain(header)])
            )

            for entry, message in built_messages:
                # 将每条消息链的组件作为节点内容
                node_content = list(getattr(message, "chain", []))
                if node_content:
                    nodes.nodes.append(
                        Comp.Node(uin=bot_id, name=bot_name, content=node_content)
                    )

            if len(nodes.nodes) <= 1:
                return

            chain = MessageChain([nodes])
            try:
                await message_manager.session_sender.send(session, chain)
                plugin_logger.debug(
                    f"[灾害预警] 气象预警合并转发已发送到 {session}, "
                    f"含 {len(built_messages)} 条预警",
                    event_stream="weather_alarm",
                )
            except Exception as e:
                raise e
        else:
            # 逐条发送
            for entry, message in built_messages:
                try:
                    await message_manager.session_sender.send(session, message)
                except Exception as e:
                    logger.error(
                        f"[灾害预警] 聚合推送逐条发送失败: {e}, 事件: {entry.event.id}"
                    )
                    raise
