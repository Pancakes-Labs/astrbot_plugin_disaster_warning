"""
数据源开关规则。
负责根据会话运行时配置，判断当前事件所属数据源及其分组是否启用。
"""

from __future__ import annotations

from typing import Any

from ..sources.source_catalog import get_legacy_group_names, get_source_entry
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class SourceEnabledRule(BaseRule):
    """运行时数据源开关规则。"""

    rule_name = "source_rule"

    @staticmethod
    def _resolve_group_config(
        data_sources_cfg: dict[str, Any], config_group: str
    ) -> dict[str, Any]:
        """合并规范组名与历史别名组名的配置。"""
        merged: dict[str, Any] = {}
        for legacy in get_legacy_group_names(config_group):
            legacy_cfg = data_sources_cfg.get(legacy)
            if isinstance(legacy_cfg, dict):
                merged.update(legacy_cfg)
        canonical_cfg = data_sources_cfg.get(config_group)
        if isinstance(canonical_cfg, dict):
            merged.update(canonical_cfg)
        return merged

    @staticmethod
    def _is_enabled_in_data_sources(
        source_id: str,
        data_sources_cfg: Any,
        *,
        source_entry,
    ) -> tuple[bool, str]:
        """在给定 data_sources 配置中判断是否启用（opt-in）。

        Returns:
            (enabled, reject_detail)
        """
        if not isinstance(data_sources_cfg, dict):
            return False, "数据源配置无效"

        group_cfg = SourceEnabledRule._resolve_group_config(
            data_sources_cfg, source_entry.config_group
        )

        # 分组总开关：缺省 False
        if not bool(group_cfg.get("enabled", False)):
            return False, f"已禁用数据源分组 {source_entry.config_group}"

        # 组内子源开关：缺省 False。
        # 单源组（S-Net）的 config_key 可能与分组开关同为 "enabled"；
        # PancakesAPI / Fan / Wolfx / P2P / EQSC 等则检查独立子键。
        if not bool(group_cfg.get(source_entry.config_key, False)):
            return False, f"已禁用数据源 {source_id}"

        return True, ""

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """检查当前事件对应的数据源是否允许推送到该会话。

        推送判定 = 全局「组级闸刀」AND 会话生效配置：
        1. 全局仅校验分组开关（批量闸刀；关组则任何会话都不推）
        2. 会话生效配置（全局默认 + 会话 override）校验具体子源开关

        设计取舍（组级 = 批量闸刀，子源级 = 会话默认值）：
        - 组级开关是「一次性停用整组」的粗粒度开关，用于批量关停。
        - 子源级开关是「会话默认值」：全局值决定未覆写会话的默认行为，
          会话可覆写，因此「全局关 + 会话显式 true」应放行该会话。

        因此：
        - 组关 → 不推送
        - 组开 + 全局子源开 + 会话未覆写 → 继承全局，可推送
        - 组开 + 全局子源开 + 会话显式 false → 不推送
        - 组开 + 全局子源关 + 会话显式 true → 推送（会话覆写子源默认值）
        - 组开 + 全局子源关 + 会话未覆写 → 不推送（继承全局 false）

        采集/轮询只看组级总闸；本规则只决定“该会话是否推送”。
        """
        # 单元测试模拟发震，直接通过，绕开全局数据源开关限制
        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            return RuleDecision.accept(reason="模拟模式跳过数据源开关过滤")

        source_id = context.source_id
        source_entry = get_source_entry(source_id)

        # 未注册数据源：不推送，与运行时查询服务一致
        if source_entry is None:
            return RuleDecision.reject(
                reason="会话数据源开关关闭",
                detail=f"未注册数据源{source_id or '（未知数据源）'}，拒绝推送",
                context={"source_id": source_id},
            )

        session_label = context.session_id or "global"

        # 1) 全局总闸：仅校验组级开关，不校验子源级开关。
        policy_state = (
            context.policy_state if isinstance(context.policy_state, dict) else {}
        )
        global_data_sources = policy_state.get("global_data_sources")
        if isinstance(global_data_sources, dict):
            global_group_cfg = self._resolve_group_config(
                global_data_sources, source_entry.config_group
            )
            if not bool(global_group_cfg.get("enabled", False)):
                return RuleDecision.reject(
                    reason="会话数据源开关关闭",
                    detail=f"全局配置已禁用数据源分组 {source_entry.config_group}",
                    context={"source_id": source_id},
                )

        # 2) 会话生效配置（已含全局默认 + 会话 override）
        data_sources_cfg = context.runtime_config.get("data_sources", {})
        session_enabled, session_detail = self._is_enabled_in_data_sources(
            source_id,
            data_sources_cfg,
            source_entry=source_entry,
        )
        if not session_enabled:
            return RuleDecision.reject(
                reason="会话数据源开关关闭",
                detail=f"会话 {session_label} {session_detail}",
                context={"source_id": source_id},
            )

        return RuleDecision.accept(reason="数据源已启用")
