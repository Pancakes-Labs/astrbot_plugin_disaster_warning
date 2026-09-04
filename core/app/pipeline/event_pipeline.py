"""
事件处理流水线。
负责串联灾害事件的日志记录、推送、统计与 Web 实时通知，减少 DisasterWarningService 中的编排职责。
"""

from __future__ import annotations

import asyncio

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...message.presenters.weather_constants import (
    resolve_weather_color_emoji,
    resolve_weather_emoji,
)
from ...message.push.weather_aggregation_service import WeatherBufferEntry


def _resolve_event_occurred_time(event: EventEnvelope):
    """从领域事件提取事件自身发生/发布时间（与数据库口径一致）。

    数据库事件记录的时间字段使用事件发生时间（如发震时间 / 发布时间），
    而非广播到达时间；管理端跑马灯需按同一时间基准展示与过滤，
    因此广播摘要的 time 必须取事件自身时间，避免与统计投影时间错位。
    """
    domain = getattr(event, "event", None)
    if isinstance(domain, EarthquakeEvent):
        return getattr(domain, "occurred_at", None)
    if isinstance(domain, TsunamiEvent):
        return getattr(domain, "issued_at", None)
    if isinstance(domain, WeatherEvent):
        return getattr(domain, "effective_at", None)
    if isinstance(domain, TyphoonEvent):
        return getattr(domain, "updated_at", None)
    return None


def _build_event_summary_description(event: EventEnvelope) -> str:
    """从领域事件提取适合跑马灯展示的描述文本。

    各灾种字段存在差异，统一在此收敛，避免前端重复判断：
    - 地震：震中位置 / 标题
    - 海啸：事件标题
    - 气象：预警名称 / 标题
    - 台风：强度等级 + 台风名称
    """
    domain = getattr(event, "event", None)
    if isinstance(domain, EarthquakeEvent):
        return str(domain.place_name or domain.headline or "").strip()
    if isinstance(domain, TsunamiEvent):
        return str(domain.title or "").strip()
    if isinstance(domain, WeatherEvent):
        return str(domain.title or domain.headline or "").strip()
    if isinstance(domain, TyphoonEvent):
        name = str(domain.name or domain.name_en or domain.typhoon_id or "").strip()
        typhoon_type = str(domain.typhoon_type or "").strip()
        return f"{typhoon_type} {name}".strip() if typhoon_type else name
    return ""


def _resolve_event_magnitude(event: EventEnvelope):
    """从领域事件提取震级（若有）。"""
    domain = getattr(event, "event", None)
    if isinstance(domain, EarthquakeEvent):
        return getattr(domain, "magnitude", None)
    if isinstance(domain, TsunamiEvent):
        # 海啸领域模型无独立震级字段，metadata 可能携带源震级
        metadata = getattr(domain, "metadata", None)
        if isinstance(metadata, dict):
            return metadata.get("magnitude")
    return None


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
        # 本事件是否实际调用过常规推送：用于统计时避免把上一次推送的会话残留记入本次。
        pushed_this_event = False
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
                # aggregated_session_count 透传到日志汇总，用于在会话筛选结果中
                # 区分"已进入聚合缓冲区"与"被规则链拦截"，避免误导为未产生任何推送。
                push_result = await self.service.message_manager.push_event(
                    event,
                    target_sessions=non_aggregated_sessions,
                    session_config_getter=self.service.session_config_manager.get_effective_config,
                    aggregated_session_count=len(aggregated_sessions),
                )
                pushed_this_event = True
                if not push_result:
                    # 未产生推送有两种可能：
                    # 1) 有会话已进入聚合缓冲（aggregated_sessions 非空）——属正常，预警稍后聚合发出；
                    # 2) 没有任何会话缓冲且非聚合会话全部被过滤——事件整体未产生推送。
                    # 区分信息已并入 _log_filter_summary 的汇总日志，此处仅 debug 兜底。
                    if not aggregated_sessions:
                        logger.debug(
                            f"[灾害预警] 事件未产生实际推送（非聚合会话）: {envelope.id}"
                        )

            if aggregated_sessions:
                # 事件已进入聚合缓冲区，跳过这些会话的独立推送。
                # 汇总日志（会话筛选结果）已包含"已进入聚合缓冲区 N 个会话"，
                # 这里仅保留 debug 级明细，避免与 INFO 汇总重复刷屏。
                plugin_logger.debug(
                    f"[灾害预警] 气象预警 {envelope.id} 已进入聚合缓冲区，"
                    f"跳过 {len(aggregated_sessions)} 个会话的独立推送 "
                    f"(缓冲会话: {', '.join(sorted(aggregated_sessions))})",
                    event_stream="weather_alarm",
                )
            elif not non_aggregated_sessions:
                # 没有会话进入聚合缓冲、也没有非聚合会话需要推送：
                # 说明全部会话在 should_aggregate 阶段就被放行（非 WeatherEvent 不会走到这）
                plugin_logger.debug(
                    f"[灾害预警] 气象预警 {envelope.id} 无会话进入聚合缓冲区，"
                    f"目标会话 {len(target_sessions)} 个",
                    event_stream="weather_alarm",
                )
        else:
            # 非气象预警事件，走原有推送路径
            await self._push_event_normal(event, envelope)
            pushed_this_event = True

        # 第三阶段：记录统计结果。
        # 统计记录与实际是否推送成功解耦，这样后续仍可分析规则过滤命中率、会话覆盖情况，以及"收到事件但未推送"的业务原因。
        # 气象聚合分支若全部会话进入缓冲区，本事件未产生任何常规推送：
        # 显式传入空列表，避免把上一次推送的会话残留记为本事件的推送目标。
        stat_sessions = self.service.message_manager.last_success_sessions
        if not pushed_this_event:
            stat_sessions = []
        await self.service.statistics_manager.record_push(
            event,
            pushed_sessions=stat_sessions,
        )

        # 第四阶段：向管理端广播轻量摘要。
        # 这里只发送展示所需的最小字段，避免把完整事件对象直接传给管理端，
        # 从而降低实时面板负载，并减少内部模型字段外露带来的耦合风险。
        # 摘要需携带 description / magnitude 与事件自身发生时间，
        # 使前端跑马灯首次推送即可展示完整内容，无需依赖后续统计投影回填占位。
        if self.service.web_admin_server:
            try:
                occurred_at = _resolve_event_occurred_time(event)
                event_summary = {
                    "id": envelope.id,  # 事件唯一标识
                    "type": envelope.event_type,  # 灾害事件类型 (如 earthquake, tsunami)
                    "source": envelope.source_id,  # 数据来源
                    # 时间用事件自身发生/发布时间（与数据库口径一致），
                    # 而不是广播到达时间，保证跑马灯时效过滤与排序正确。
                    "time": (
                        occurred_at.isoformat()
                        if occurred_at is not None
                        else envelope.received_at.isoformat()
                    ),
                    # 描述文本与震级随摘要一并下发，前端直接消费，不再出现"无详细描述"占位。
                    "description": _build_event_summary_description(event),
                    "magnitude": _resolve_event_magnitude(event),
                }
                # 台风摘要补充 typhoon_id 个体标识（前端跑马灯按此去重，
                # 每个活跃台风只保留最新一报；id 可能为其他事件指纹，必须显式下发）。
                if isinstance(envelope.event, TyphoonEvent):
                    event_summary["typhoon_id"] = str(
                        getattr(envelope.event, "typhoon_id", "") or ""
                    ).strip()
                # 气象预警补充后端统一解析的 Emoji（与推送展示口径一致），前端跑马灯直接消费。
                if isinstance(envelope.event, WeatherEvent):
                    event_metadata = (
                        envelope.event.metadata
                        if isinstance(envelope.event.metadata, dict)
                        else {}
                    )
                    event_summary["weather_emoji"] = resolve_weather_emoji(
                        envelope.event.title,
                        envelope.event.headline,
                        event_metadata.get("weather_code"),
                        event_metadata.get("weather_type"),
                        event_metadata.get("type"),
                    )
                    # 颜色 emoji（与推送展示器口径一致）：标题/副标题含颜色词时给出 🔴🟠🟡🔵
                    event_summary["weather_color_emoji"] = resolve_weather_color_emoji(
                        event_metadata.get("level"),
                        event_metadata.get("alert_level"),
                        envelope.event.title,
                        envelope.event.headline,
                    )
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
        # 未推送不一定代表异常，常见原因包括规则过滤未命中、会话未订阅，或事件被静默策略抑制。
        # 未推送明细由 _log_filter_summary 的 INFO 汇总（会话筛选结果）承担，此处不再单独兜底。
        await self.service.message_manager.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=self.service.session_config_manager.get_effective_config,
        )

    async def _flush_weather_buffer(
        self,
        session: str,
        entries: list,
        config: dict,
        *,
        mode: str = "forward",
    ) -> tuple[int, list, list]:
        """聚合缓冲区推送回调。

        与 WeatherAggregationService.set_flush_callback 约定一致：
        恒返回 (sent_count, deferred_entries, failed_entries) 三元组：
        - sent_count：实际成功发送（所在节点发送成功）的条目数
          （规则链复核/消息构建可能过滤部分条目，统计口径以实际发送为准）；
        - deferred_entries：fill_nodes 开启时因节点未满本轮未发送、需放回
          缓冲区等待下次推送窗口凑满的条目（不会丢弃）；
        - failed_entries：发送失败节点内的条目，需由聚合服务按失败重试路径
          回收（累计重试计数，达到上限后丢弃），避免静默丢失。
        全部节点发送失败时抛出 RuntimeError，由聚合服务捕获后放回缓冲区重试。

        为每条气象预警构建含图标的完整消息链后发送。
        每条预警在构建消息前先通过规则链复核，未通过的不发送。

        设计约定（对齐"节点内条数尽量塞满、一轮内紧凑发送"）：
        - 先对全部条目完成规则链复核与消息构建（并发）；
        - 通过复核的条目再按 max_batch_size 切分为合并转发节点，
          保证"≤上限时恰 1 个节点、超过上限时前 N-1 个节点塞满上限"；
        - 各节点在短时间内连续发送，避免被串行构建/渲染拉散到数分钟。

        mode="forward" 时打包为合并转发消息；
        mode="single" 时逐条发送（降级路径）。
        """
        if not entries:
            return 0, [], []

        message_manager = self.service.message_manager
        session_config_getter = self.service.session_config_manager.get_effective_config
        runtime_config = session_config_getter(session)
        # 统一会话日志字符串（私聊/群聊 ID (备注名)），与推送执行链保持一致。
        session_log = message_manager._get_session_log_str(session)

        # 聚合配置：单批节点上限（默认 20，对齐 schema 默认值）
        agg_config = (runtime_config.get("push_frequency_control", {}) or {}).get(
            "weather_aggregation", {}
        )
        if not isinstance(agg_config, dict):
            agg_config = {}
        max_batch = int(agg_config.get("max_batch_size", 20) or 20)
        if max_batch < 1:
            max_batch = 20

        # 并发执行规则链复核与消息构建，避免大量条目串行 await 拉散发送节奏。
        # 返回值区分三类结果：
        # - (entry, message)：构建成功，进入发送节点；
        # - ("failed", entry)：消息构建异常，需走失败重试路径（累计重试计数），
        #   避免瞬时构建错误把预警静默丢弃；
        # - None：规则链复核未通过（正常过滤路径），不发送也不重试。
        async def _review_and_build(
            entry: WeatherBufferEntry,
        ):
            if not isinstance(entry, WeatherBufferEntry):
                return None
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
                        f"[灾害预警] 聚合推送事件 {entry.event.id} 在 {session_log} "
                        f"规则链复核未通过: {decision.reason}"
                        + (f"（{decision.detail}）" if decision.detail else ""),
                        event_stream="weather_alarm",
                    )
                    return None

                # 复用消息构建服务构建含图标的完整消息
                message = (
                    await message_manager.message_build_service.build_message_async(
                        entry.event,
                        runtime_config=runtime_config,
                    )
                )
                return entry, message
            except Exception as e:
                logger.error(
                    f"[灾害预警] 聚合推送构建消息失败: {e}, 事件: {entry.event.id}"
                )
                # 构建异常与规则链过滤不同：这是可重试的瞬时故障，
                # 返回失败标记让调用方把它并入 failed_entries 走重试路径。
                return "failed", entry

        results = await asyncio.gather(*[_review_and_build(entry) for entry in entries])
        built_messages: list[tuple[WeatherBufferEntry, MessageChain]] = []
        # 构建失败条目：并入 failed_entries 走失败重试路径（累计重试计数，
        # 达到上限后丢弃），避免瞬时构建错误把预警静默丢弃。
        build_failed_entries: list[WeatherBufferEntry] = []
        for result in results:
            if result is None:
                continue
            if isinstance(result, tuple) and len(result) == 2 and result[0] == "failed":
                build_failed_entries.append(result[1])
                continue
            built_messages.append(result)

        if not built_messages:
            # 全部条目被规则链复核过滤或构建失败，实际未发送任何预警：
            # 规则链过滤属正常业务路径，不重试；构建失败条目仍需走重试路径，
            # 避免瞬时构建错误把预警静默丢弃。
            return 0, [], build_failed_entries

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

            # 按 max_batch_size 切分节点，前面的节点尽量塞满上限，
            # 避免"一个多一个少"（如 4+4+12+1+10）。
            # 每个节点独立构建一个合并转发消息链（含头部节点）。
            total = len(built_messages)

            # 填满节点开关 fill_nodes（默认开启）：
            # 开启后仅发送能装满 max_batch 的完整节点；切分后剩余条数无法
            # 装满一个节点时，该部分本轮不发送，放回缓冲区等待下次推送窗口
            # 凑满后再发（不会丢弃）。关闭后按原有逻辑发送全部条目（最后一
            # 个节点可不装满）。停机 flush_all 强制发送全部，避免积压预警
            # 在停机时因节点未满而滞留。
            # 低流量会话下 total < max_batch 时本应无完整节点可发；但若延期
            # 条目反复滞留聚合服务会以 force_send_all 标记本轮强制发送，此时 fill_nodes 判定失效。
            fill_nodes = bool(agg_config.get("fill_nodes", True))
            is_flush_all = bool(
                self._weather_aggregation is not None
                and self._weather_aggregation.is_flushing_all()
            )
            force_send_all = bool(
                self._weather_aggregation is not None
                and self._weather_aggregation.should_force_send_all(session)
            )
            if fill_nodes and not is_flush_all and not force_send_all:
                full_batch_count = total // max_batch
            else:
                full_batch_count = (total + max_batch - 1) // max_batch
            node_count = full_batch_count
            sent_nodes = 0
            failed_nodes = 0
            # 发送失败节点内的条目：在 except 块内立即收集当前 batch，
            # 避免用"首个失败节点下标"推断失败范围而误收后续成功节点的条目。
            failed_entries: list[WeatherBufferEntry] = []
            # 实际成功发出（所在节点发送成功）的条目数：部分节点发送失败时，
            # 失败节点内的条目不能计入"最后转发"统计，避免高报成功数量。
            sent_entry_count = 0

            for batch_idx in range(node_count):
                batch = built_messages[
                    batch_idx * max_batch : (batch_idx + 1) * max_batch
                ]
                nodes = Comp.Nodes([])

                # 添加头部节点
                header = f"📋 气象预警聚合推送（共 {len(batch)} 条）"
                nodes.nodes.append(
                    Comp.Node(uin=bot_id, name=bot_name, content=[Comp.Plain(header)])
                )

                for entry, message in batch:
                    # 将每条消息链的组件作为节点内容
                    node_content = list(getattr(message, "chain", []))
                    if node_content:
                        nodes.nodes.append(
                            Comp.Node(uin=bot_id, name=bot_name, content=node_content)
                        )

                if len(nodes.nodes) <= 1:
                    continue

                chain = MessageChain([nodes])
                try:
                    await message_manager.session_sender.send(session, chain)
                    sent_nodes += 1
                    # 该节点发送成功：节点内实际加入内容的条目即为成功发出
                    sent_entry_count += len(batch)
                except Exception as e:
                    # 单个节点发送失败不应中断整轮推送（如插件停止/网络瞬时异常时
                    # 框架可能拒绝发送，但这不代表平台不支持合并转发）。
                    # 记录错误后继续发送剩余节点，避免触发无意义的降级限流。
                    failed_nodes += 1
                    # 立即收集本失败节点内的条目，走失败重试路径（累计重试计数）
                    failed_entries.extend(entry for entry, _ in batch)
                    logger.error(
                        f"[灾害预警] 气象预警合并转发节点 {batch_idx + 1}/{node_count} "
                        f"发送失败 ({session_log}): {e}"
                    )

            # 构建失败条目统一并入 failed_entries：构建失败发生在发送前，
            # 不归属于任何节点，因此在节点循环结束后合并，走失败重试路径
            # （累计重试计数，达到上限后丢弃），避免瞬时构建错误把预警静默丢弃。
            failed_entries.extend(build_failed_entries)

            if sent_nodes == 0 and failed_nodes > 0:
                # 全部节点都发送失败：向上抛出，让调用方感知到平台当前不可用，
                # 避免"假装成功"导致用户完全收不到任何预警。
                raise RuntimeError(
                    f"气象预警合并转发全部发送失败 ({session_log}): "
                    f"{failed_nodes}/{node_count} 个节点失败"
                )

            # 节点未满回退：完整节点之后剩余的条目
            # 本轮未发送，放回缓冲区等待下次推送窗口凑满后再发。
            deferred_entries = [
                entry for entry, _ in built_messages[full_batch_count * max_batch :]
            ]

            # 推送结果日志：按"是否有节点成功发出"区分两种主句，
            if sent_nodes > 0:
                summary = (
                    f"[灾害预警] 气象预警聚合推送完成：共发送 {sent_entry_count} 条预警"
                    f"（{sent_nodes} 个节点）到 {session_log}"
                )
                if deferred_entries:
                    summary += (
                        f"，有 {len(deferred_entries)} 条未满，已放回缓冲区等待凑满"
                    )
                if failed_nodes:
                    summary += f"，另有 {failed_nodes} 个节点发送失败"
            else:
                summary = f"[灾害预警] 气象预警聚合推送：本轮无预警发送到 {session_log}"
                if deferred_entries:
                    summary += (
                        f"，预警数 {len(deferred_entries)} 条未满足单节点最低要求 "
                        f"{max_batch} 条、已放回缓冲区等待凑满"
                    )
                if failed_nodes:
                    summary += f"，{failed_nodes} 个节点发送失败"
            plugin_logger.info(
                summary,
                event_stream="weather_alarm",
                is_silent_window=True,
            )
            # 返回实际成功发送（所在节点发送成功）的条目数、需放回缓冲区的
            # 节点未满条目、以及发送失败节点内的条目（走失败重试路径），
            # 供聚合服务统计真实转发量，避免高报成功数量与静默丢失。
            return sent_entry_count, deferred_entries, failed_entries
        else:
            # 逐条发送（降级路径）
            for entry, message in built_messages:
                try:
                    await message_manager.session_sender.send(session, message)
                except Exception as e:
                    logger.error(
                        f"[灾害预警] 聚合推送逐条发送失败: {e}, 事件: {entry.event.id}"
                    )
                    raise
            # 返回实际成功发送的条目数（供聚合服务统计真实转发量）；
            # 构建失败条目同样并入失败重试路径，避免瞬时构建错误把预警静默丢弃。
            return len(built_messages), [], build_failed_entries
