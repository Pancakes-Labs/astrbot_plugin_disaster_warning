"""
事件时间规则。
负责过滤明显过旧的事件，避免历史补发或异常回放消息进入正常推送链路。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..services.identity.event_identity import resolve_event_time_aware
from ..storage.source_compat import is_earthquake_supplement_product
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class EventTimeRule(BaseRule):
    """过滤明显过旧事件。"""

    rule_name = "time_rule"

    # 常规事件的最大允许时效（小时）：60 分钟
    DEFAULT_MAX_AGE_HOURS = 1.0
    # 补充产品（烈度速报 / CMT 等）产出较慢，可能滞后 1～2 小时才发布，单独放宽到 3 小时
    SUPPLEMENT_MAX_AGE_HOURS = 3.0

    def __init__(self, max_age_hours: float = DEFAULT_MAX_AGE_HOURS):
        # 默认限制历史事件时间距今不得超过 60 分钟；
        # 烈度速报 / CMT 等补充产品单独放宽至 3 小时。
        self.max_age_hours = max_age_hours

    @staticmethod
    def _is_supplement_product(context: RuleContext) -> bool:
        """判断当前事件是否为产出较慢的地震补充产品（烈度速报 / CMT）。"""
        try:
            envelope = context.envelope
        except TypeError:
            return False
        source_id = str(context.source_id or "").strip()
        # 从领域事件 metadata 与信封 metadata 双渠道提取 info_type
        info_type = ""
        domain_meta = getattr(envelope.event, "metadata", None)
        if isinstance(domain_meta, dict):
            info_type = str(domain_meta.get("info_type") or "").strip()
        if not info_type:
            envelope_meta = getattr(envelope, "metadata", None)
            if isinstance(envelope_meta, dict):
                info_type = str(envelope_meta.get("info_type") or "").strip()
        return is_earthquake_supplement_product(source_id, info_type=info_type)

    def _resolve_max_age_hours(self, context: RuleContext) -> float:
        """按事件类型解析允许的最大时效（小时）。"""
        # 补充产品产出较慢，保留 3 小时窗口；其余事件一律收紧为 60 分钟。
        if self._is_supplement_product(context):
            return self.SUPPLEMENT_MAX_AGE_HOURS
        return self.max_age_hours

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """检查事件时间是否超过允许的最老时效。"""
        # 从 EventEnvelope 包裹中提取包含时区信息的 datetime 对象
        event_time_aware = resolve_event_time_aware(context.event)
        if event_time_aware is None:
            return RuleDecision.accept(reason="事件时间缺失，跳过时间规则")

        current_time_utc = datetime.now(timezone.utc)
        # 统一换算为小时差值，便于直接与规则配置的时效阈值比较
        time_diff = (current_time_utc - event_time_aware).total_seconds() / 3600

        # 根据事件类型解析适用的时效阈值（补充产品放宽至 3 小时，其余 60 分钟）
        max_age_hours = self._resolve_max_age_hours(context)

        # 超时拦截，丢弃过于陈旧的历史事件，防止刷屏
        if time_diff > max_age_hours:
            return RuleDecision.reject(
                reason="事件时间过早",
                detail=f"事件时间过早（{time_diff:.1f} 小时前，最大允许 {max_age_hours:.0f} 小时）",
                context={"age_hours": time_diff, "max_age_hours": max_age_hours},
            )
        return RuleDecision.accept(reason="事件时间有效")
