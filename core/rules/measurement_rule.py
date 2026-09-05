"""
测定类型规则。负责根据会话运行时配置，
对区分"自动测定/正式测定"的地震情报源按测定类型执行接收过滤。
"""

from __future__ import annotations

from ..domain.event_models import EarthquakeEvent, EventEnvelope
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class MeasurementTypeRule(BaseRule):
    """测定类型过滤规则。"""

    rule_name = "measurement_type_rule"

    # 会区分自动测定/正式测定并携带测定类型标记的数据源
    _MEASUREMENT_SOURCE_IDS: frozenset[str] = frozenset(
        {
            # 中国地震台网测定：infoTypeName 含 [正式测定] / [自动测定]
            "cenc_fanstudio",
            "cenc_wolfx",
            # 美国地质调查局测定：info_type 为 reviewed / automatic
            "usgs_fanstudio",
        }
    )

    @staticmethod
    def _extract_measurement_type(event: EventEnvelope) -> str:
        """提取事件的测定类型，统一归一为 automatic / reviewed / unknown。

        兼容解析器在 domain_event.metadata 与 envelope.metadata 双渠道写入的
        info_type / infoTypeName / issue_type 字段。
        """
        domain_event = event.event
        domain_meta = (
            getattr(domain_event, "metadata", None)
            if isinstance(domain_event, EarthquakeEvent)
            else None
        )
        if isinstance(domain_meta, dict):
            domain_meta = dict(domain_meta)
        else:
            domain_meta = {}

        envelope_meta = event.metadata if isinstance(event.metadata, dict) else {}

        raw_value = ""
        for candidate in (
            domain_meta.get("info_type"),
            domain_meta.get("infoTypeName"),
            envelope_meta.get("info_type"),
            envelope_meta.get("infoTypeName"),
            envelope_meta.get("issue_type"),
        ):
            text = str(candidate or "").strip()
            if text:
                raw_value = text
                break

        if not raw_value:
            return "unknown"

        text_lower = raw_value.lower()
        if "正式测定" in raw_value or text_lower == "reviewed":
            return "reviewed"
        if "自动测定" in raw_value or text_lower == "automatic":
            return "automatic"
        return "unknown"

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """按测定类型过滤地震情报消息。"""
        source_id = context.source_id
        # 仅对会区分测定类型的数据源执行过滤
        if source_id not in self._MEASUREMENT_SOURCE_IDS:
            return RuleDecision.accept(reason="数据源不区分测定类型，跳过测定类型规则")

        filter_cfg = context.policy_state.get("measurement_type_filter") or {}
        # 过滤器未启用时直接放行，保持与旧行为一致
        if not filter_cfg.get("enabled", False):
            return RuleDecision.accept(reason="测定类型过滤器未启用")

        receive_automatic = bool(filter_cfg.get("receive_automatic", True))
        receive_reviewed = bool(filter_cfg.get("receive_reviewed", True))

        measurement_type = self._extract_measurement_type(context.envelope)

        # 类型无法识别时：两个开关都关闭则拒绝（避免漏推），否则放行
        if measurement_type == "unknown":
            if receive_automatic or receive_reviewed:
                return RuleDecision.accept(reason="无法识别测定类型，按放行处理")
            return RuleDecision.reject(
                reason="测定类型过滤",
                detail="无法识别消息的测定类型，且自动/正式测定均未开启接收",
                context={"source_id": source_id, "measurement_type": measurement_type},
            )

        if measurement_type == "automatic" and not receive_automatic:
            return RuleDecision.reject(
                reason="测定类型过滤",
                detail="已关闭接收自动测定消息",
                context={"source_id": source_id, "measurement_type": measurement_type},
            )
        if measurement_type == "reviewed" and not receive_reviewed:
            return RuleDecision.reject(
                reason="测定类型过滤",
                detail="已关闭接收正式测定消息",
                context={"source_id": source_id, "measurement_type": measurement_type},
            )

        return RuleDecision.accept(reason="测定类型过滤通过")
