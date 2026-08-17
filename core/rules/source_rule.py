"""
数据源开关规则。
负责根据会话运行时配置，判断当前事件所属数据源及其分组是否启用。
"""

from __future__ import annotations

from typing import Any

from ..sources.source_catalog import get_source_entry
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class SourceEnabledRule(BaseRule):
    """运行时数据源开关规则。"""

    rule_name = "source_rule"

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

        group_cfg = data_sources_cfg.get(source_entry.config_group, {})
        if not isinstance(group_cfg, dict):
            group_cfg = {}

        # 分组总开关：缺省 False
        if not bool(group_cfg.get("enabled", False)):
            return False, f"已禁用数据源分组 {source_entry.config_group}"

        # 组内子源开关：缺省 False。
        # 单源组（S-Net）的 config_key 可能与分组开关同为 "enabled"；
        # OpenQuakeAPI / Fan / Wolfx / P2P / EQSC 等则检查独立子键。
        if not bool(group_cfg.get(source_entry.config_key, False)):
            return False, f"已禁用数据源 {source_id}"

        return True, ""

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """检查当前事件对应的数据源是否允许推送到该会话。

        推送判定 = 全局总闸 AND 会话生效配置：
        1. 全局 data_sources 必须开启（采集/轮询总闸；全局关则任何会话都不推）
        2. 会话生效配置（全局默认 + 会话 override）也必须开启

        因此：
        - 全局开 + 会话未覆写 → 继承全局，可推送
        - 全局开 + 会话显式 false → 不推送
        - 全局关 + 会话显式 true → 仍不推送（会话不能突破全局总闸）

        采集/轮询只看全局；本规则只决定“该会话是否推送”。
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

        # 1) 全局总闸：policy_state.global_data_sources 由推送侧注入
        policy_state = (
            context.policy_state if isinstance(context.policy_state, dict) else {}
        )
        global_data_sources = policy_state.get("global_data_sources")
        if global_data_sources is not None:
            global_enabled, global_detail = self._is_enabled_in_data_sources(
                source_id,
                global_data_sources,
                source_entry=source_entry,
            )
            if not global_enabled:
                return RuleDecision.reject(
                    reason="会话数据源开关关闭",
                    detail=f"全局配置{global_detail}",
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
