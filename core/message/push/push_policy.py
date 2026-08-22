"""
消息推送过滤策略。
从 MessagePushManager 中拆出的纯判定逻辑，负责根据规则状态判断事件是否应推送。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_models import EventEnvelope
from ...rules import RuleContext, RuleDecision, build_default_rule_chain


def evaluate_push_decision_with_components(
    event: EventEnvelope,
    *,
    runtime_config: dict[str, Any],
    policy_state: dict[str, Any],
    session_id: str | None = None,
    emit_filter_log: bool = True,
    commit_state: bool = True,
    logger_instance=None,
) -> RuleDecision:
    """基于规则状态评估事件推送决策。"""

    # 规则链每次按统一入口构建，保证不同调用点使用同一套判定顺序。
    rule_chain = build_default_rule_chain()
    rule_context = RuleContext(
        event=event,
        runtime_config=runtime_config,
        policy_state=policy_state,
        session_id=session_id,
        commit_state=commit_state,
        logger_instance=logger_instance,
    )
    decision = rule_chain.evaluate(rule_context)

    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        event.metadata = metadata

    local_estimation = rule_context.extras.get("local_estimation")
    if isinstance(local_estimation, dict) and local_estimation:
        # 规则链可能在判定过程中顺带产出本地预估信息，
        # 这里把它回写到事件元数据，供后续展示器直接复用。
        metadata["local_estimation"] = dict(local_estimation)

    typhoon_local_estimation = rule_context.extras.get("typhoon_local_estimation")
    if isinstance(typhoon_local_estimation, dict) and typhoon_local_estimation:
        # 台风距离/预报逼近估算同样回写元数据，供文本展示与调试复用。
        metadata["typhoon_local_estimation"] = dict(typhoon_local_estimation)

    if not decision.accepted and emit_filter_log and logger_instance is not None:
        detail_suffix = f"，{decision.detail}" if decision.detail else ""
        logger_instance.info(
            f"[灾害预警] 事件被规则链过滤：{decision.reason}{detail_suffix}"
        )

    return decision
