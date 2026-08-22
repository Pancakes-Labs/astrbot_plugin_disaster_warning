"""
规则链执行器。
负责按既定顺序执行多个规则，并在出现首个拒绝结果时立即短路返回。
"""

from __future__ import annotations

from .base_rule import RuleContext
from .intensity_rule import EarthquakeThresholdRule
from .keyword_rule import KeywordRule
from .local_rule import LocalIntensityRule
from .report_rule import ReportRule
from .rule_result import RuleDecision
from .source_rule import SourceEnabledRule
from .time_rule import EventTimeRule
from .tsunami_rule import TsunamiRule
from .typhoon_rule import TyphoonRule
from .weather_rule import WeatherRule


class RuleChain:
    """统一串联多个规则。

    规则链本身不关心具体业务，只负责维持执行顺序与短路语义。
    """

    def __init__(self, rules=None):
        # 接收并按传入顺序实例化过滤规则链表
        self.rules = list(rules or [])

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """依次执行规则链中的每个规则。"""
        for rule in self.rules:
            # 依次执行各过滤子项评估
            decision = rule.evaluate(context)
            # 任一规则判定不放行时，执行短路保护，停止后续链式判断并立即向上层返回拒信结果
            if not decision.accepted:
                return decision
        # 全部通过，生成放行决策
        return RuleDecision.accept(reason="规则链通过")


def build_default_rule_chain() -> RuleChain:
    """构建默认推送规则链。

    默认顺序从基础有效性校验开始，再逐步进入来源、气象、台风、海啸、关键词、
    震动强度、报次与本地烈度判断。
    """
    # 顺次装配核心过滤逻辑规则实例
    return RuleChain(
        [
            EventTimeRule(),
            SourceEnabledRule(),
            WeatherRule(),
            TyphoonRule(),
            TsunamiRule(),
            KeywordRule(),
            EarthquakeThresholdRule(),
            ReportRule(),
            LocalIntensityRule(),
        ]
    )
