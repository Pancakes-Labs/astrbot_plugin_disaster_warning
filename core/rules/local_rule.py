"""
本地监控规则。负责调用本地烈度估算组件，
按用户所在地的预估影响决定是否放行地震事件。支持按消息类别独立过滤。
"""

from __future__ import annotations

from ..domain.event_models import EarthquakeEvent
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision

# 地震预警（EEW）事件类型
_EVENT_TYPE_EEW = "earthquake_warning"
# 地震测定/情报事件类型
_EVENT_TYPE_INFO = "earthquake"


class LocalIntensityRule(BaseRule):
    """本地烈度规则。"""

    rule_name = "local_rule"

    @staticmethod
    def _resolve_kind(context: RuleContext) -> str | None:
        """按事件类型解析本地过滤适用的消息类别。"""
        event_type = str(context.event_type or "").strip()
        if event_type == _EVENT_TYPE_EEW:
            return "eew"
        if event_type == _EVENT_TYPE_INFO:
            return "info"
        return None

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

        # 按事件类型解析消息类别（eew / info / None）
        kind = self._resolve_kind(context)

        # 触发本地预估烈度、震源距离与是否允许推送的评测计算
        result = local_monitor.evaluate(domain_event, kind=kind)
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
            kind_label = (
                "预警" if kind == "eew" else "情报" if kind == "info" else "地震"
            )
            return RuleDecision.reject(
                reason="本地烈度规则过滤",
                detail=(
                    f"本地预估{result.get('threshold_unit', '烈度')} "
                    f"{result.get('intensity', 0):.1f} 未达到阈值，"
                    f"距离 {result.get('distance', 0):.1f} km（{kind_label}无感过滤）"
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
