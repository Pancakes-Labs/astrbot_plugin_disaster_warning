"""
事件时间规则。
负责过滤明显过旧的事件，避免历史补发或异常回放消息进入正常推送链路。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..services.identity.event_identity import resolve_event_time_aware
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class EventTimeRule(BaseRule):
    """过滤明显过旧事件。"""

    rule_name = "time_rule"

    def __init__(self, max_age_hours: float = 3.0):
        # 默认限制历史事件时间距今不得超过 3 小时
        # （CMT 等补充产品可能在发震后 1～2 小时才产出，1 小时阈值会误拦）
        self.max_age_hours = max_age_hours

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """检查事件时间是否超过允许的最老时效。"""
        # 从 EventEnvelope 包裹中提取包含时区信息的 datetime 对象
        event_time_aware = resolve_event_time_aware(context.event)
        if event_time_aware is None:
            return RuleDecision.accept(reason="事件时间缺失，跳过时间规则")

        current_time_utc = datetime.now(timezone.utc)
        # 统一换算为小时差值，便于直接与规则配置的时效阈值比较
        time_diff = (current_time_utc - event_time_aware).total_seconds() / 3600

        # 超时拦截，丢弃过于陈旧的历史事件，防止刷屏
        if time_diff > self.max_age_hours:
            return RuleDecision.reject(
                reason="事件时间过早",
                detail=f"事件时间过早（{time_diff:.1f}小时前）",
                context={"age_hours": time_diff},
            )
        return RuleDecision.accept(reason="事件时间有效")
