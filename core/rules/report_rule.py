"""
报次规则。
负责按不同来源的报次策略控制推送频率，并处理首报、最终报等特殊放行条件。
"""

from __future__ import annotations

from ..domain.event_models import EarthquakeEvent
from ..services.identity.event_identity import resolve_report_num
from ..sources.source_catalog import get_source_entry
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class ReportRule(BaseRule):
    """原生报次规则。"""

    rule_name = "report_rule"

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """根据来源报次策略决定当前事件是否应被推送。"""
        domain_event = context.domain_event
        # 报次限频主要应用在地震预警推送中
        if not isinstance(domain_event, EarthquakeEvent):
            return RuleDecision.accept(reason="非地震事件，跳过报数规则")

        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            return RuleDecision.accept(reason="模拟模式跳过报数控制")

        # 不同来源的报次推进逻辑不同，因此先从目录中读取对应的报次策略。
        source_entry = get_source_entry(context.source_id)
        report_policy = (
            (source_entry.report_policy if source_entry is not None else "none")
            .strip()
            .lower()
        )
        if report_policy == "none":
            return RuleDecision.accept(reason="当前数据源无报数控制")

        push_config = context.runtime_config.get("push_frequency_control", {})
        # 获取当前是第几报
        report_num = resolve_report_num(context.event) or 1
        event_id = context.envelope.id or context.source_id or "unknown_event"

        # 存放本次调用的限频拦截决策修改状态
        state_store = context.extras.setdefault("report_rule_state", {})

        # 根据报数频控策略决定每隔多少报推送一次
        push_every_n = int(push_config.get("cea_cwa_report_n", 1) or 1)
        supports_final = True

        # 策略 1：日本气象厅，日本预警更新迅速且测定频繁，适合多间隔推送
        if report_policy == "jma":
            push_every_n = int(push_config.get("jma_report_n", 3) or 3)
        # 策略 2：Global Quake，更新频繁，默认每 5 报推一次；
        # 新版 GQ 协议已提供 ARCHIVED 归档动作，应作为最终报参与最终报总是推送
        elif report_policy == "global_quake":
            push_every_n = int(push_config.get("gq_report_n", 5) or 5)
            supports_final = True
        # 策略 3：中国与台湾预警，当前策略仍不把 is_final 用于报数放行
        elif report_policy == "cea_cwa":
            supports_final = False

        final_report_always_push = bool(
            push_config.get("final_report_always_push", True)
        )
        ignore_non_final_reports = bool(
            push_config.get("ignore_non_final_reports", False)
        )

        identity = getattr(context.envelope, "identity", None)
        metadata = (
            context.envelope.metadata
            if isinstance(context.envelope.metadata, dict)
            else {}
        )
        is_final = (
            (
                bool(getattr(identity, "is_final", False))
                or bool(metadata.get("is_final", False))
            )
            if supports_final
            else False
        )

        # 规则 1：若是最终报且配置开启了最终报必推送，直接通过
        if is_final and final_report_always_push:
            if context.commit_state:
                state_store[event_id] = report_num
            return RuleDecision.accept(
                reason="最终报直接放行",
                context={"event_id": event_id, "report_num": report_num},
            )

        # 规则 2：首报（第 1 报）承载最先预警信息，强制直接放行
        if report_num == 1:
            if context.commit_state:
                state_store[event_id] = report_num
            return RuleDecision.accept(
                reason="首报直接放行",
                context={"event_id": event_id, "report_num": report_num},
            )

        # 规则 3：如果配置项要求忽略非最终报，那么在这类非首报且非最终报的情况下一律过滤掉
        if ignore_non_final_reports and supports_final and not is_final:
            return RuleDecision.reject(
                reason="忽略非最终报",
                detail=f"事件 {event_id} 第 {report_num} 报未达到最终报条件",
                context={"event_id": event_id, "report_num": report_num},
            )

        # 兜底防御非法配置项，规避除零或恒不命中的情况
        if push_every_n <= 0:
            push_every_n = 1

        # 规则 4：整除模计算，例如每 3 报推送一次，则在第 3, 6, 9 报时放行通过
        if report_num % push_every_n == 0:
            if context.commit_state:
                state_store[event_id] = report_num
            return RuleDecision.accept(
                reason="报数规则通过",
                detail=f"事件 {event_id} 第 {report_num} 报命中间隔值 {push_every_n}",
                context={
                    "event_id": event_id,
                    "report_num": report_num,
                    "push_every_n": push_every_n,
                },
            )

        # 拦截过滤
        return RuleDecision.reject(
            reason="报数规则过滤",
            detail=f"事件 {event_id} 第 {report_num} 报未命中间隔值 {push_every_n}",
            context={
                "event_id": event_id,
                "report_num": report_num,
                "push_every_n": push_every_n,
            },
        )
