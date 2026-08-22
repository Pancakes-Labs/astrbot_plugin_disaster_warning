"""
海啸推送规则。
负责按中国/日本独立阈值过滤海啸事件；解除通告默认放行。
"""

from __future__ import annotations

from typing import Any

from ..domain.event_models import TsunamiEvent
from ..domain.tsunami.tsunami_levels import (
    cn_tsunami_level_weight,
    jp_tsunami_level_weight,
    normalize_cn_tsunami_level,
    normalize_jp_tsunami_level,
    resolve_tsunami_region,
)
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class TsunamiRule(BaseRule):
    """海啸过滤器规则。"""

    rule_name = "tsunami_rule"

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """按海啸过滤配置评估是否放行。"""
        domain_event = context.domain_event
        if not isinstance(domain_event, TsunamiEvent):
            return RuleDecision.accept(reason="非海啸事件，跳过海啸规则")

        # 模拟演练模式：不拦截。
        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            return RuleDecision.accept(reason="模拟模式跳过海啸过滤")

        tsunami_config = self._resolve_tsunami_config(context)
        metadata = (
            context.envelope.metadata
            if isinstance(context.envelope.metadata, dict)
            else {}
        )
        event_metadata = (
            domain_event.metadata if isinstance(domain_event.metadata, dict) else {}
        )
        merged_meta = {**event_metadata, **metadata}

        region = resolve_tsunami_region(context.source_id, merged_meta)
        raw_level = (
            domain_event.level
            or merged_meta.get("level")
            or merged_meta.get("max_grade")
            or ""
        )
        cancelled = bool(
            merged_meta.get("cancelled")
            or str(raw_level).strip() in {"解除", "取消"}
            or "解除" in str(domain_event.title or "")
        )

        decision_context: dict[str, Any] = {
            "region": region,
            "source_id": context.source_id,
            "raw_level": raw_level,
            "cancelled": cancelled,
        }

        if region == "china":
            return self._evaluate_china(
                tsunami_config=tsunami_config,
                raw_level=raw_level,
                cancelled=cancelled,
                decision_context=decision_context,
            )
        if region == "japan":
            return self._evaluate_japan(
                tsunami_config=tsunami_config,
                raw_level=raw_level,
                cancelled=cancelled,
                decision_context=decision_context,
            )

        # 无法识别区域时不误杀
        return RuleDecision.accept(
            reason="海啸区域未知，跳过阈值过滤",
            context=decision_context,
        )

    @staticmethod
    def _resolve_tsunami_config(context: RuleContext) -> dict[str, Any]:
        """从 policy_state / runtime_config 解析海啸配置。"""
        policy_cfg = context.policy_state.get("tsunami_config")
        if isinstance(policy_cfg, dict) and policy_cfg:
            return policy_cfg

        runtime_cfg = context.runtime_config.get("tsunami_config")
        if isinstance(runtime_cfg, dict):
            return runtime_cfg
        return {}

    @staticmethod
    def _filter_block(config: dict[str, Any], key: str) -> dict[str, Any]:
        block = config.get(key)
        return block if isinstance(block, dict) else {}

    def _evaluate_china(
        self,
        *,
        tsunami_config: dict[str, Any],
        raw_level: Any,
        cancelled: bool,
        decision_context: dict[str, Any],
    ) -> RuleDecision:
        china_filter = self._filter_block(tsunami_config, "china_filter")
        if not china_filter.get("enabled", False):
            return RuleDecision.accept(
                reason="中国海啸过滤未启用",
                context=decision_context,
            )

        level = normalize_cn_tsunami_level(raw_level)
        decision_context["normalized_level"] = level

        # 解除通告始终放行，避免漏掉取消信息
        if cancelled or level == "解除":
            return RuleDecision.accept(
                reason="中国海啸解除通告放行",
                context=decision_context,
            )

        min_level = str(china_filter.get("min_level") or "信息").strip() or "信息"
        current_weight = cn_tsunami_level_weight(level)
        required_weight = cn_tsunami_level_weight(min_level)
        decision_context["min_level"] = min_level
        decision_context["current_weight"] = current_weight
        decision_context["required_weight"] = required_weight

        # 未知等级不误杀
        if (
            required_weight > 0
            and current_weight > 0
            and current_weight < required_weight
        ):
            return RuleDecision.reject(
                reason="中国海啸级别过滤",
                detail=f"当前级别 {level or '未知'} 低于最低要求 {min_level}",
                context=decision_context,
            )
        return RuleDecision.accept(
            reason="中国海啸规则通过",
            detail=f"级别 {level or '未知'} ≥ {min_level}",
            context=decision_context,
        )

    def _evaluate_japan(
        self,
        *,
        tsunami_config: dict[str, Any],
        raw_level: Any,
        cancelled: bool,
        decision_context: dict[str, Any],
    ) -> RuleDecision:
        japan_filter = self._filter_block(tsunami_config, "japan_filter")
        if not japan_filter.get("enabled", False):
            return RuleDecision.accept(
                reason="日本海啸过滤未启用",
                context=decision_context,
            )

        level = normalize_jp_tsunami_level(raw_level, cancelled=cancelled)
        decision_context["normalized_level"] = level

        if cancelled or level == "解除":
            return RuleDecision.accept(
                reason="日本海啸解除通告放行",
                context=decision_context,
            )

        min_level = (
            str(japan_filter.get("min_level") or "若干海面变动").strip()
            or "若干海面变动"
        )
        current_weight = jp_tsunami_level_weight(level, cancelled=False)
        # min_level 配置为中文描述，normalize 后与内部枚举统一比较
        required_weight = jp_tsunami_level_weight(min_level, cancelled=False)
        decision_context["min_level"] = min_level
        decision_context["current_weight"] = current_weight
        decision_context["required_weight"] = required_weight

        if (
            required_weight > 0
            and current_weight > 0
            and current_weight < required_weight
        ):
            return RuleDecision.reject(
                reason="日本海啸级别过滤",
                detail=f"当前级别 {level or '未知'} 低于最低要求 {min_level}",
                context=decision_context,
            )
        return RuleDecision.accept(
            reason="日本海啸规则通过",
            detail=f"级别 {level or '未知'} ≥ {min_level}",
            context=decision_context,
        )
