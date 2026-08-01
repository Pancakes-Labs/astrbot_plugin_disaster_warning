"""
气象预警聚合推送服务。

在时间窗口内积攒气象预警事件，到期后合并推送：
- 支持合并转发的平台（如 QQ / aiocqhttp）打包为合并转发消息（Comp.Nodes）；
  平台是否支持合并转发由框架层处理，插件统一尝试合并转发，
  失败时自动降级为逐条文本发送。
- 不支持合并转发的平台启用限流：在限流时间窗口内最多推送指定数量的消息，
  优先推送高级别（红色 > 橙色 > 黄色 > 蓝色 > 白色）预警。
  限流仅在合并转发失败降级为逐条发送时才生效，合并转发成功时不限流。

设计要点：
1. 按会话维度独立缓冲，避免跨群混淆；
2. 缓冲期间对同一预警 ID 去重，只保留最新；
3. 触发推送条件：时间窗口到期 / 缓冲条数达上限 / 收到红色预警立即推送；
   红色预警立即推送时仍需通过规则链复核，不绕过已有过滤配置；
   若红色预警触发推送时缓冲区仅有这一条事件，则直接走常规推送流，
   避免为单条事件构建合并转发节点的额外开销；
4. 合并转发失败时降级为逐条文本发送，此时才启用限流；
5. 限流平台按颜色级别排序，超出配额的低级别预警被丢弃并记录 debug 日志。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import EventEnvelope, WeatherEvent

# 气象预警颜色级别 → 数值（用于排序，数值越大优先级越高）
_COLOR_LEVEL_MAP: dict[str, int] = {
    "白色": 0,
    "蓝色": 1,
    "黄色": 2,
    "橙色": 3,
    "红色": 4,
}


@dataclass
class WeatherBufferEntry:
    """缓冲区中的单条气象预警条目。"""

    event: EventEnvelope
    color_level: int = 0
    color_name: str = "白色"
    title: str = ""
    received_at: float = field(default_factory=time.time)


class WeatherAggregationService:
    """气象预警聚合推送服务。

    由 EventPipeline 在气象预警事件推送前调用 should_aggregate() 判断是否需要缓冲。
    若返回 True，事件进入缓冲区，由定时推送或红色预警触发推送。
    若返回 False，调用方应继续走原有独立推送路径。
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config
        # 按会话维度的缓冲区：session -> {event_id: WeatherBufferEntry}
        self._buffers: dict[str, dict[str, WeatherBufferEntry]] = {}
        # 按会话维度的定时推送器
        self._flush_timers: dict[str, asyncio.TimerHandle] = {}
        # 按会话维度的限流计数器：session -> [(timestamp, ...)]
        self._rate_limit_counters: dict[str, list[float]] = {}
        # 推送回调，由 EventPipeline 注入
        self._flush_callback = None

    def set_flush_callback(self, callback) -> None:
        """注入推送回调，签名: async def callback(session, entries, config, *, mode) -> bool。"""
        self._flush_callback = callback

    def update_config(self, config: dict[str, Any]) -> None:
        """更新配置（支持运行时热更新）。"""
        self._config = config

    def _get_aggregation_config(
        self, runtime_config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """获取聚合推送配置。

        优先从会话级配置读取，回退到全局配置。
        """
        config = runtime_config if isinstance(runtime_config, dict) else self._config
        push_freq = config.get("push_frequency_control", {})
        if not isinstance(push_freq, dict):
            return {}
        agg = push_freq.get("weather_aggregation", {})
        return agg if isinstance(agg, dict) else {}

    def is_enabled(self, runtime_config: dict[str, Any] | None = None) -> bool:
        """聚合功能是否启用（支持会话级配置）。"""
        return bool(self._get_aggregation_config(runtime_config).get("enabled", True))

    def _resolve_color_level(self, event: EventEnvelope) -> tuple[int, str]:
        """从事件元数据中解析颜色级别。"""
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        domain_event = event.event
        title_text = ""
        if isinstance(domain_event, WeatherEvent):
            title_text = getattr(domain_event, "title", "") or getattr(
                domain_event, "headline", ""
            )
        if not title_text:
            title_text = metadata.get("title", "") or metadata.get("headline", "")

        # 按由高到低优先级识别
        for color in ["红色", "橙色", "黄色", "蓝色", "白色"]:
            if color in title_text:
                return _COLOR_LEVEL_MAP.get(color, 0), color

        # 回退：从 severity_color / level 字段推断
        severity = str(
            metadata.get("severity_color") or metadata.get("level") or ""
        ).strip()
        for color in ["红色", "橙色", "黄色", "蓝色", "白色"]:
            if color in severity:
                return _COLOR_LEVEL_MAP.get(color, 0), color

        return 0, "白色"

    def _is_red_level(self, entry: WeatherBufferEntry) -> bool:
        """判断是否为红色级别预警。"""
        return entry.color_level >= _COLOR_LEVEL_MAP["红色"]

    def _is_event_fresh(self, event: EventEnvelope, max_age_hours: float = 3.0) -> bool:
        """判断气象预警事件是否仍在时效内。

        上游（如 OQ 全量轮询）会推送仍在生效的历史预警，
        这些旧事件若进入聚合缓冲，会混入本轮节点、拉低时效性，
        也会在 flush 复核时被 EventTimeRule 剔除，导致节点条数参差。
        超过时效的事件直接交回常规推送路径，由规则链统一兜底拦截。
        """
        try:
            from ...services.identity.event_identity import resolve_event_time_aware

            event_time = resolve_event_time_aware(event)
            if event_time is None:
                # 无时间信息时放行，交给后续规则链兜底
                return True
            time_diff = (datetime.now(timezone.utc) - event_time).total_seconds() / 3600
            return time_diff <= max_age_hours
        except Exception:
            return True

    def should_aggregate(
        self,
        event: EventEnvelope,
        session: str,
        runtime_config: dict[str, Any] | None = None,
    ) -> bool:
        """判断事件是否应进入聚合缓冲区。

        返回 True 表示事件已被缓冲（或已触发立即推送），调用方应跳过本次独立推送。
        返回 False 表示不聚合，调用方应继续走原有推送路径。

        Args:
            runtime_config: 会话级生效配置。若提供则从中读取聚合配置，
                支持会话级差异配置（部分会话可关闭聚合）。
        """
        if not self.is_enabled(runtime_config):
            return False

        domain_event = event.event
        if not isinstance(domain_event, WeatherEvent):
            return False

        agg_config = self._get_aggregation_config(runtime_config)
        time_window = float(agg_config.get("time_window_seconds", 900))
        # 默认值与 _conf_schema.json / 配置校验器保持一致
        flush_on_red = bool(agg_config.get("flush_on_red", False))

        # 初始化会话缓冲区
        if session not in self._buffers:
            self._buffers[session] = {}

        buffer = self._buffers[session]

        # 构建缓冲条目
        color_level, color_name = self._resolve_color_level(event)
        title = ""
        if isinstance(domain_event, WeatherEvent):
            title = getattr(domain_event, "title", "") or getattr(
                domain_event, "headline", ""
            )

        entry = WeatherBufferEntry(
            event=event,
            color_level=color_level,
            color_name=color_name,
            title=title,
        )

        # 按 event_id 去重：同一预警在窗口内只保留最新
        event_id = str(event.id or "")
        if not event_id:
            # 无 ID 的事件直接放行，不缓冲
            return False

        # 时效检查：OQ 全量轮询可能会推送仍在生效的旧预警，
        # 直接进入缓冲会把"历史补发"混入本轮聚合节点。
        # 超过时效的旧预警不缓冲，交回常规推送路径（由 EventTimeRule 拦截）。
        if not self._is_event_fresh(event):
            plugin_logger.debug(
                f"[灾害预警] 气象预警 {event_id} 超过时效，跳过聚合缓冲 ({session})",
                event_stream="weather_alarm",
            )
            return False

        is_red = flush_on_red and self._is_red_level(entry)

        # 红色预警且缓冲区为空时，直接走常规推送流，不进入缓冲区
        # 避免为单条事件构建合并转发节点的额外开销
        if is_red and not buffer:
            plugin_logger.debug(
                f"[灾害预警] 红色预警 {event_id} 缓冲区为空，直接走常规推送流 ({session})",
                event_stream="weather_alarm",
            )
            return False

        # 将事件加入缓冲区
        buffer[event_id] = entry

        plugin_logger.debug(
            f"[灾害预警] 气象预警 {event_id} ({color_name}) 进入聚合缓冲区 "
            f"({session}), 当前缓冲 {len(buffer)} 条",
            event_stream="weather_alarm",
        )

        # 红色预警且缓冲区有其他事件时，立即触发推送
        if is_red:
            plugin_logger.info(
                f"[灾害预警] 收到红色级别气象预警，立即触发聚合推送 "
                f"({session}), 缓冲 {len(buffer)} 条",
                is_event_linked=True,
                event_stream="weather_alarm",
            )
            asyncio.create_task(self._flush_session(session))
            return True

        # 设置定时推送（仅在尚未设置定时器时创建，不重置已有定时器）
        # 避免高频事件持续到达导致定时器被不断重置、窗口永远不到期
        # max_batch_size 仅控制推送时每批合并转发节点的最大数量，不作为提前触发条件
        self._schedule_flush(session, time_window)
        return True

    def _schedule_flush(self, session: str, delay: float) -> None:
        """设置会话的定时推送。

        仅在尚未设置定时器时创建，不重置已有定时器。
        这样从第一条事件进入缓冲区开始计时，窗口到期后必定触发推送，
        避免高频事件持续到达导致定时器被不断重置、窗口永远不到期。
        """
        # 已有定时器则不重置
        if session in self._flush_timers:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        timer = loop.call_later(
            delay,
            lambda: asyncio.create_task(self._flush_session(session)),
        )
        self._flush_timers[session] = timer

    async def _flush_session(self, session: str) -> None:
        """执行指定会话的缓冲区推送。"""
        # 取消定时器
        timer = self._flush_timers.pop(session, None)
        if timer is not None:
            timer.cancel()

        buffer = self._buffers.pop(session, None)
        if not buffer:
            return

        entries = list(buffer.values())
        if not entries:
            return

        agg_config = self._get_aggregation_config()

        # 统一走合并转发，平台是否支持由框架层处理
        await self._flush_via_forward(session, entries, agg_config)

    async def _flush_via_forward(
        self,
        session: str,
        entries: list[WeatherBufferEntry],
        agg_config: dict[str, Any],
    ) -> None:
        """通过合并转发方式推送。

        先尝试合并转发，成功则不限流。
        合并转发失败时记录错误、保留合并转发语义，不自动降级限流——
        一次发送失败不代表平台不支持合并转发（如插件停止/网络瞬时异常），
        盲目降级会错误启用限流配置，把大量预警直接丢弃。
        """
        # 按颜色级别降序排序，高级别优先展示
        entries.sort(key=lambda e: e.color_level, reverse=True)

        plugin_logger.info(
            f"[灾害预警] 气象预警聚合推送: {len(entries)} 条 ({session})",
            is_event_linked=True,
            event_stream="weather_alarm",
        )

        if self._flush_callback is None:
            plugin_logger.warning(
                "[灾害预警] 聚合推送回调未注入，跳过推送",
                is_event_linked=True,
                event_stream="weather_alarm",
            )
            return

        # 整批交给回调：回调内部先完成规则链复核与消息构建，
        # 再按 max_batch_size 切分合并转发节点，保证节点内条数尽量塞满上限，
        # 避免"先切批后复核"导致节点条数参差（如 4+4+12+1+10）。
        try:
            # 先尝试合并转发，成功则不限流
            await self._flush_callback(session, entries, self._config, mode="forward")
        except Exception as e:
            # 合并转发失败：仅当平台确实不支持合并转发时，才允许降级为逐条发送。
            # 注意：不能仅凭一次发送异常就判定"平台不支持"——插件停止/网络瞬时
            # 异常也可能导致发送失败，此时平台实际支持合并转发。
            # 盲目降级会错误启用限流（rate_limit_max_messages 默认 3），
            # 在支持合并转发的平台上把大量预警直接丢弃。
            # 因此这里统一记录错误、保留合并转发语义，不自动降级限流；
            # 降级路径仅由上层明确平台能力后以 mode="single" 调用。
            plugin_logger.error(
                f"[灾害预警] 气象预警合并转发推送失败 ({session})，"
                f"本轮 {len(entries)} 条预警未能通过合并转发发出: {e}",
                is_event_linked=True,
                event_stream="weather_alarm",
            )

    async def flush_all(self) -> None:
        """强制推送所有会话的缓冲区（用于插件关闭/重载）。"""
        sessions = list(self._buffers.keys())
        for session in sessions:
            await self._flush_session(session)

    def get_buffer_stats(self) -> dict[str, int]:
        """获取各会话缓冲区状态（用于状态展示）。"""
        return {
            session: len(buffer) for session, buffer in self._buffers.items() if buffer
        }
