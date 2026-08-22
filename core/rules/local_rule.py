"""
本地监控规则。
负责调用本地烈度估算组件，按用户所在地的预估影响决定是否放行地震事件。
"""

from __future__ import annotations

from ..domain.event_models import EarthquakeEvent
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class LocalIntensityRule(BaseRule):
    """本地烈度规则。"""

    rule_name = "local_rule"

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """按本地烈度估算结果判断是否需要保留事件。"""
        domain_event = context.domain_event
        # 仅针对地震事件运行本地烈度与距离计算，其他事件放行
        if not isinstance(domain_event, EarthquakeEvent):
            return RuleDecision.accept(reason="非地震事件，跳过本地监控规则")

        # 本地监控器可能在部分会话中未启用，此时直接放行，不额外拦截。
        local_monitor = context.policy_state.get("local_monitor")
        if local_monitor is None:
            return RuleDecision.accept(reason="未配置本地监控")

        # 触发本地预估烈度、震源距离与是否允许推送的评测计算
        result = local_monitor.evaluate(domain_event)
        if result is None:
            return RuleDecision.accept(reason="本地监控未启用")

        # 把估算结果写入附加上下文，供后续展示或日志链路复用。
        context.extras["local_estimation"] = dict(result)

        # 模拟演练模式下即使本地未达到震感烈度要求，也依然放行，仅在日志中标注
        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            return RuleDecision.accept(
                reason="模拟模式跳过本地严格拦截",
                detail=(
                    f"本地预估{result.get('threshold_unit', '烈度')} "
                    f"{result.get('intensity', 0):.1f}，"
                    f"距离 {result.get('distance', 0):.1f} km"
                ),
                context=dict(result),
            )

        # 若本地烈度计算得出不合乎阈值条件，执行拦截拒绝
        if not result.get("is_allowed", True):
            return RuleDecision.reject(
                reason="本地烈度规则过滤",
                detail=(
                    f"本地预估{result.get('threshold_unit', '烈度')} "
                    f"{result.get('intensity', 0):.1f} 未达到阈值，"
                    f"距离 {result.get('distance', 0):.1f} km"
                ),
                context=dict(result),
            )

        # 校验通过
        return RuleDecision.accept(
            reason="本地烈度规则通过",
            detail=(
                f"本地预估{result.get('threshold_unit', '烈度')} "
                f"{result.get('intensity', 0):.1f}，"
                f"距离 {result.get('distance', 0):.1f} km"
            ),
            context=dict(result),
        )
