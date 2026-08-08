"""
消息推送流处理器。
负责串联去重、执行、结果后处理与错误遥测，
统一协调推送链路中的轻量流程编排职责。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...services.identity.event_identity import resolve_report_num
from ...services.telemetry.telemetry_utils import track_feature_safely
from ...sources.source_catalog import get_source_ids_by_type
from ...sources.source_entry import SourceType


class PushFlowHandler:
    """推送流处理器。"""

    def __init__(self, manager):
        # 该处理器专注于推送链中的轻量流程编排，不直接承担消息构建细节。
        self.manager = manager  # 主消息管理器 MessagePushManager 实例
        # 去重状态快照/回滚串行化锁：多个事件并发推送时，
        # 防止事件 A 失败回滚覆盖事件 B 刚提交的去重状态。
        self._dedup_lock = asyncio.Lock()

    async def execute_push(
        self,
        event: EventEnvelope,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
        *,
        commit_state: bool = True,
        skip_dedup: bool = False,
        return_details: bool = False,
        aggregated_session_count: int = 0,
    ) -> bool | dict[str, Any]:
        """执行完整推送流程：去重、会话执行、后处理与异常遥测。"""
        plugin_logger.debug(f"[灾害预警] 执行事件推送流程: {event.id}")

        # 去重状态（recent_events 等）由 should_push_event 写入、由本次推送的成败决定是否回滚。
        # 用锁串行化整个快照→推送→回滚流程，防止并发事件互相覆盖去重状态：
        # 事件 A 失败时 restore_state 不能把事件 B 刚提交的状态一并回滚掉。
        async with self._dedup_lock:
            # 去重检查会在 should_push_event 中写入 recent_events 等去重状态；
            # 若后续推送失败，需回滚这些状态，避免同一状态升级重试被误过滤导致通知丢失。
            dedup_snapshot = None
            if not skip_dedup and commit_state:
                try:
                    dedup_snapshot = self.manager.deduplicator.snapshot_state()
                except Exception as snapshot_e:
                    # 快照失败不阻塞推送，仅无法回滚。
                    plugin_logger.warning(
                        f"[灾害预警] 去重状态快照失败，推送失败时将无法回滚: {snapshot_e}"
                    )
                    dedup_snapshot = None

            # 地震重复与烈度/警报合并过滤判断
            if not skip_dedup and not self.manager.deduplicator.should_push_event(
                event
            ):
                plugin_logger.debug(f"[灾害预警] 事件 {event.id} 被去重器过滤")
                return False

            try:
                # 委托消息推送执行服务进行多会话分发与降级尝试
                execution_result = await self.manager.push_execution_service.execute(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                    commit_state=commit_state,
                )
                # 执行完成后处理，例如发送分离地图、输出统计过滤摘要与上报指标
                success = await self.handle_execution_result(
                    event,
                    execution_result,
                    aggregated_session_count=aggregated_session_count,
                )
                # 推送未成功（push_success_count == 0）时回滚去重状态，
                # 允许同一事件稍后重试时再次通过升级放行。
                # 注意：回滚必须先于 return_details 提前返回执行，
                # 否则详情模式下的失败推送会保留已写入的去重状态，阻塞后续重试。
                if not success and dedup_snapshot is not None:
                    try:
                        self.manager.deduplicator.restore_state(dedup_snapshot)
                        plugin_logger.info(
                            f"[灾害预警] 事件 {event.id} 推送未成功，已回滚去重状态以允许重试"
                        )
                    except Exception as rollback_e:
                        plugin_logger.warning(
                            f"[灾害预警] 去重状态回滚失败: {rollback_e}"
                        )
                if return_details:
                    execution_result["success"] = bool(success)
                    return execution_result
                return success
            except Exception as e:
                plugin_logger.error(f"[灾害预警] 推送事件失败: {e}")
                # 异常路径同样回滚去重状态，避免失败的重试被误过滤。
                if dedup_snapshot is not None:
                    try:
                        self.manager.deduplicator.restore_state(dedup_snapshot)
                        plugin_logger.info(
                            f"[灾害预警] 事件 {event.id} 推送异常，已回滚去重状态以允许重试"
                        )
                    except Exception as rollback_e:
                        plugin_logger.warning(
                            f"[灾害预警] 去重状态回滚失败: {rollback_e}"
                        )
                if self.manager._telemetry and self.manager._telemetry.enabled:
                    await self.manager._telemetry.track_error(
                        e,
                        module="core.message_manager._execute_push",
                    )
                return False

    async def handle_execution_result(
        self,
        event: EventEnvelope,
        execution_result: dict[str, Any],
        *,
        aggregated_session_count: int = 0,
    ) -> bool:
        """处理推送执行结果并返回最终是否有成功推送。"""
        push_success_count = int(execution_result.get("push_success_count", 0))
        passed_sessions = list(execution_result.get("passed_sessions", []))
        session_message_format_config = dict(
            execution_result.get("session_message_format_config", {})
        )
        session_display_timezone_map = dict(
            execution_result.get("session_display_timezone_map", {})
        )
        filter_reason_stats = dict(execution_result.get("filter_reason_stats", {}))
        filter_reason_detail_stats = dict(
            execution_result.get("filter_reason_detail_stats", {})
        )
        send_failure_stats = dict(execution_result.get("send_failure_stats", {}))

        # 分离地图属于后处理动作，只有在主消息已完成发送筛选后才有意义。
        await self._dispatch_split_maps(
            event,
            passed_sessions=passed_sessions,
            session_message_format_config=session_message_format_config,
            session_display_timezone_map=session_display_timezone_map,
        )

        # 打印本次推送的中文日志摘要 (如通过几个，拦截几个，什么原因拦截等)
        self._log_filter_summary(
            event,
            push_success_count=push_success_count,
            filter_reason_stats=filter_reason_stats,
            filter_reason_detail_stats=filter_reason_detail_stats,
            send_failure_stats=send_failure_stats,
            aggregated_session_count=aggregated_session_count,
        )

        execution_result["final_failure_reason"] = self._build_failure_summary(
            filter_reason_stats=filter_reason_stats,
            filter_reason_detail_stats=filter_reason_detail_stats,
            send_failure_stats=send_failure_stats,
        )
        self.manager.last_success_sessions = list(passed_sessions)
        self._log_push_completion(
            event,
            push_success_count=push_success_count,
            filter_reason_stats=filter_reason_stats,
            filter_reason_detail_stats=filter_reason_detail_stats,
            send_failure_stats=send_failure_stats,
        )
        # 上报遥测统计
        await self._track_push_result(
            event,
            push_success_count=push_success_count,
            filter_reason_stats=filter_reason_stats,
            filter_reason_detail_stats=filter_reason_detail_stats,
            send_failure_stats=send_failure_stats,
        )
        return push_success_count > 0

    async def _track_push_result(
        self,
        event: EventEnvelope,
        *,
        push_success_count: int,
        filter_reason_stats: dict[str, int],
        filter_reason_detail_stats: dict[str, int],
        send_failure_stats: dict[str, int],
    ) -> None:
        """上报匿名推送结果统计，不包含会话标识或消息正文。"""
        telemetry = getattr(self.manager, "_telemetry", None)
        if not telemetry or not telemetry.enabled:
            return

        send_failure_types = sorted(
            send_failure_stats,
            key=send_failure_stats.get,
            reverse=True,
        )[:8]
        await track_feature_safely(
            telemetry,
            "push_result",
            {
                "source_id": getattr(event, "source_id", "") or "unknown",
                "event_type": getattr(event, "event_type", "") or "unknown",
                "success": push_success_count > 0,
                "success_count": push_success_count,
                "filter_reason_count": sum(filter_reason_stats.values()),
                "filter_detail_reason_count": sum(filter_reason_detail_stats.values()),
                "send_failure_count": sum(send_failure_stats.values()),
                "send_failure_types": send_failure_types,
            },
            log_context="推送结果遥测",
        )

    async def _dispatch_split_maps(
        self,
        event: EventEnvelope,
        *,
        passed_sessions: list[str],
        session_message_format_config: dict[str, dict[str, Any]],
        session_display_timezone_map: dict[str, str] | None = None,
    ) -> None:
        """为需要拆分地图的事件按配置分组异步派发地图推送。

        Args:
            event: 事件信封。
            passed_sessions: 成功推送的会话列表。
            session_message_format_config: 会话 -> message_format 配置映射。
            session_display_timezone_map: 会话 -> 展示时区映射（可能为空），
                用于地图描述时间与各会话文本对齐；缺失时回退到全局时区。
        """
        session_display_timezone_map = session_display_timezone_map or {}
        source_id = (getattr(event, "source_id", "") or "").strip()
        split_map_sources = set(
            get_source_ids_by_type(SourceType.EARTHQUAKE_WARNING)
        ) - {"global_quake"}
        domain_event = event.event
        if source_id not in split_map_sources or not isinstance(
            domain_event, EarthquakeEvent
        ):
            return

        identity = getattr(event, "identity", None)
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        current_report = resolve_report_num(event) or 1
        is_final = bool(
            getattr(identity, "is_final", False) or metadata.get("is_final", False)
        )
        # 分离地图不必每一报都发送，按首报、固定间隔报或最终报触发即可。
        map_push_n = 5
        should_gen_map = (
            current_report == 1 or current_report % map_push_n == 0 or is_final
        )
        if not should_gen_map or not passed_sessions:
            return

        plugin_logger.debug(f"[灾害预警] 触发异步地图渲染 (第 {current_report} 报)")
        # 分组键 = 消息格式配置 + 展示时区：同一会话组内地图样式与描述时间一致。
        grouped_sessions: dict[str, list[str]] = {}
        grouped_config: dict[str, dict[str, Any]] = {}
        grouped_timezone: dict[str, str] = {}
        for session in passed_sessions:
            msg_cfg = session_message_format_config.get(session, {})
            if not msg_cfg.get("include_map", False):
                continue

            session_tz = str(session_display_timezone_map.get(session) or "UTC+8")
            config_key = json.dumps(
                {
                    "cfg": msg_cfg,
                    "timezone": session_tz,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if config_key not in grouped_sessions:
                grouped_sessions[config_key] = []
                grouped_config[config_key] = msg_cfg
                grouped_timezone[config_key] = session_tz
            grouped_sessions[config_key].append(session)

        # 启动异步截图并多路发送的后台协程
        for config_key, sessions in grouped_sessions.items():
            # 按消息格式配置 + 展示时区分组后再异步派发，
            # 避免不同地图样式/不同描述时间被错误混发。
            asyncio.create_task(
                self.manager.message_build_service.push_split_map(
                    event,
                    sessions,
                    grouped_config[config_key],
                    display_timezone=grouped_timezone[config_key],
                )
            )

    @staticmethod
    def _build_failure_summary(
        *,
        filter_reason_stats: dict[str, int],
        filter_reason_detail_stats: dict[str, int],
        send_failure_stats: dict[str, int],
    ) -> str:
        """汇总最终失败原因，供模拟链路回显。"""
        detail_stats = filter_reason_detail_stats or filter_reason_stats
        if detail_stats:
            return "；".join(
                f"{reason}×{count}" for reason, count in sorted(detail_stats.items())
            )
        if send_failure_stats:
            return "；".join(
                f"{reason}×{count}"
                for reason, count in sorted(send_failure_stats.items())
            )
        return "未找到明确失败原因"

    def _log_filter_summary(
        self,
        event: EventEnvelope,
        *,
        push_success_count: int,
        filter_reason_stats: dict[str, int],
        filter_reason_detail_stats: dict[str, int],
        send_failure_stats: dict[str, int],
        aggregated_session_count: int = 0,
    ) -> None:
        """记录会话筛选结果摘要。"""
        # 这里把“规则拦截”和“发送失败”分开汇总，避免排障时混淆问题阶段。
        # aggregated_session_count：本次事件已进入气象聚合缓冲区的会话数
        # （这些会话不会走常规推送，因此不计入“通过/拦截”统计）。
        failure_summary = "，".join(
            f"{reason} {count} 个会话"
            for reason, count in sorted(send_failure_stats.items())
        )
        failure_suffix = f"，另有 {failure_summary} 发送失败" if failure_summary else ""

        # 聚合缓冲会话并入同一句汇总日志，避免额外刷一条日志。
        agg_prefix = (
            f"{aggregated_session_count} 个会话已进入聚合缓冲区，"
            if aggregated_session_count > 0
            else ""
        )

        if filter_reason_stats:
            summary = "，".join(
                f"{reason} {count} 个会话"
                for reason, count in sorted(filter_reason_stats.items())
            )
            if push_success_count > 0:
                plugin_logger.info(
                    f"[灾害预警] 事件 {event.id} 会话筛选结果: {agg_prefix}"
                    f"{push_success_count} 个会话通过，另有 {summary} 被拦截{failure_suffix}",
                    is_event_linked=True,
                    event_stream=self._resolve_event_stream(event),
                )
            else:
                plugin_logger.info(
                    f"[灾害预警] 事件 {event.id} 会话筛选结果: {agg_prefix}拦截情况：{summary}{failure_suffix}",
                    is_event_linked=True,
                    event_stream=self._resolve_event_stream(event),
                )
        elif push_success_count > 0:
            plugin_logger.info(
                f"[灾害预警] 事件 {event.id} 会话筛选结果: {agg_prefix}"
                f"{push_success_count} 个会话通过{failure_suffix}",
                is_event_linked=True,
                event_stream=self._resolve_event_stream(event),
            )
        elif failure_summary:
            plugin_logger.info(
                f"[灾害预警] 事件 {event.id} 会话筛选结果: {agg_prefix}"
                f"已通过发送前筛选，但发送阶段全部失败：{failure_summary}",
                is_event_linked=True,
                event_stream=self._resolve_event_stream(event),
            )
        elif aggregated_session_count > 0:
            # 没有非聚合会话需要推送，且没有通过/拦截/失败记录：
            # 全部目标会话都进入了聚合缓冲，等待后续定时聚合推送。
            plugin_logger.info(
                f"[灾害预警] 事件 {event.id} 会话筛选结果: "
                f"{aggregated_session_count} 个会话已进入聚合缓冲区，"
                f"等待后续定时聚合推送",
                is_event_linked=True,
                event_stream=self._resolve_event_stream(event),
            )

    @staticmethod
    def _resolve_event_stream(event: EventEnvelope) -> str:
        """根据事件类型解析事件流标签，用于细粒度日志级别控制。"""
        event_type = str(getattr(event, "event_type", "") or "").strip()
        if event_type == "weather_alarm":
            return "weather_alarm"
        if event_type == "typhoon":
            return "typhoon"
        if event_type == "tsunami":
            return "tsunami"
        # 地震类事件统一归入 earthquake 流
        source_id = str(getattr(event, "source_id", "") or "").strip()
        if source_id == "global_quake":
            return "global_quake"
        return "earthquake"

    def _log_push_completion(
        self,
        event: EventEnvelope,
        *,
        push_success_count: int,
        filter_reason_stats: dict[str, int],
        filter_reason_detail_stats: dict[str, int],
        send_failure_stats: dict[str, int],
    ) -> None:
        """记录推送完成日志。"""
        # 这一层更偏向最终完成态日志，与前面的筛选摘要日志形成互补。
        detailed_stats = filter_reason_detail_stats or filter_reason_stats
        if detailed_stats and push_success_count > 0:
            summary = "，".join(
                f"{reason}×{count}"
                for reason, count in sorted(filter_reason_stats.items())
            )
            plugin_logger.debug(f"[灾害预警] 事件 {event.id} 部分会话被过滤: {summary}")
            detailed_summary = "，".join(
                f"{reason}×{count}" for reason, count in sorted(detailed_stats.items())
            )
            plugin_logger.debug(
                f"[灾害预警] 事件 {event.id} 过滤明细: {detailed_summary}"
            )
        if send_failure_stats:
            summary = "，".join(
                f"{reason}×{count}"
                for reason, count in sorted(send_failure_stats.items())
            )
            plugin_logger.debug(
                f"[灾害预警] 事件 {event.id} 部分会话发送失败: {summary}"
            )

        if push_success_count > 0:
            plugin_logger.info(
                f"[灾害预警] 事件 {event.id} 推送完成，成功推送到 {push_success_count} 个会话",
                is_event_linked=True,
                event_stream=self._resolve_event_stream(event),
                is_silent_window=True,
            )
        elif send_failure_stats:
            plugin_logger.warning(
                f"[灾害预警] 事件 {event.id} 推送完成，但没有任何会话发送成功",
                is_event_linked=True,
                event_stream=self._resolve_event_stream(event),
            )
