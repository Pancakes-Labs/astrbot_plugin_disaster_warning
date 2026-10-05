"""
地震强度规则。
负责根据不同数据源的强度判定模式，选择合适的震级、烈度或震度过滤策略。
"""

from __future__ import annotations

from typing import Any

from ...utils.plugin_logger import plugin_logger
from ..domain.event_models import EarthquakeEvent
from ..services.snet.snet_filter_constants import (
    count_triggered_stations,
    normalize_combine_mode,
    normalize_min_shindo,
    normalize_min_triggered_stations,
    normalize_station_min_shindo,
)
from ..sources.source_catalog import get_source_entry
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision

# 强度模式别名归一化：把目录中出现的同义写法收敛到规则实际消费的规范值。
# 未归一化的未知取值会落到 evaluate 末尾的兜底分支被直接放行，
# 导致 min_magnitude / min_scale / min_intensity 等过滤配置对该数据源失效
# （Pancakes 子源曾误用 shindo / mmi，M1.5 小震因此绕过全部过滤直接推送）。
_INTENSITY_MODE_ALIASES: dict[str, str] = {
    "shindo": "scale",  # 日本震度制式，与 scale 同义
    "seismic_scale": "scale",
    "mmi": "magnitude",  # USGS MMI 无烈度阈值可用，按震级过滤
    "mag": "magnitude",
}

# 规则链实际消费的规范强度模式；"none" / "" 表示该灾种无强度过滤语义。
_KNOWN_INTENSITY_MODES: frozenset[str] = frozenset(
    {"intensity", "scale", "snet_shindo", "magnitude", "none", ""}
)


class EarthquakeThresholdRule(BaseRule):
    """按数据源强度模式选择过滤策略。"""

    rule_name = "intensity_rule"

    # 已告警过的 (source_id, intensity_mode) 组合，避免同源未知模式逐条刷屏。
    _warned_unknown_modes: set[tuple[str, str]] = set()

    @staticmethod
    def _combine_checks(
        combine_mode: str, checks: list[tuple[str, bool, str]]
    ) -> RuleDecision | None:
        """基于 combine_mode 组合多个条件判断结果。

        返回 None 表示通过；返回 RuleDecision 表示拒绝。
        """
        if not checks:
            return None
        mode = normalize_combine_mode(combine_mode)
        if mode == "any":
            if any(passed for _, passed, _ in checks):
                return None
            detail = "；".join(desc for _, _, desc in checks)
            return RuleDecision.reject(
                reason="强度过滤器不满足(any)",
                detail=f"满足任一条件组合失败：{detail}",
            )

        failed = [desc for _, passed, desc in checks if not passed]
        if failed:
            return RuleDecision.reject(
                reason="强度过滤器不满足(all)",
                detail=f"满足全部条件失败：{'；'.join(failed)}",
            )
        return None

    @staticmethod
    def _build_threshold_check(
        *,
        key: str,
        label: str,
        current: Any,
        threshold: Any,
        extra_pass: bool = True,
        missing_text: str = "无",
    ) -> tuple[str, bool, str]:
        """构建单条阈值比较检查项。"""
        try:
            current_val = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_val = None
        try:
            threshold_val = float(threshold)
        except (TypeError, ValueError):
            threshold_val = None

        passed = (
            extra_pass
            and current_val is not None
            and threshold_val is not None
            and current_val >= threshold_val
        )
        current_text = missing_text if current_val is None else current_val
        threshold_text = missing_text if threshold_val is None else threshold_val
        return (key, passed, f"{label} {current_text} ≥ {threshold_text}")

    def _evaluate_dual_threshold_filter(
        self,
        *,
        runtime_filter: dict[str, Any],
        primary: tuple[str, str, Any, Any, bool],
        secondary: tuple[str, str, Any, Any, bool],
        accept_reason: str,
        skip_primary: bool = False,
        skip_secondary: bool = False,
    ) -> RuleDecision:
        """评估双阈值过滤器（震级 + 烈度/震度）。

        skip_primary / skip_secondary 用于跳过对应阈值检查项。
        被跳过的检查项不会参与 any/all 组合判定，常用于 PLUM/假定震源等
        震级为占位值（M1.0）的场景，此时震级阈值无意义，应仅按烈度/震度过滤
        避免 all 模式误杀或 any 模式产生误导性失败描述。
        """
        if not runtime_filter.get("enabled", True):
            return RuleDecision.accept(reason=accept_reason)

        combine_mode = normalize_combine_mode(runtime_filter.get("combine_mode"))
        checks: list[tuple[str, bool, str]] = []
        if not skip_primary:
            checks.append(
                self._build_threshold_check(
                    key=primary[0],
                    label=primary[1],
                    current=primary[2],
                    threshold=primary[3],
                    extra_pass=primary[4],
                )
            )
        if not skip_secondary:
            checks.append(
                self._build_threshold_check(
                    key=secondary[0],
                    label=secondary[1],
                    current=secondary[2],
                    threshold=secondary[3],
                    extra_pass=secondary[4],
                )
            )
        # 所有检查项均被跳过时直接放行
        if not checks:
            return RuleDecision.accept(reason=accept_reason)
        decision = self._combine_checks(combine_mode, checks)
        if decision is not None:
            return decision
        return RuleDecision.accept(reason=accept_reason)

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """根据事件来源和强度模式执行地震过滤。"""
        domain_event = context.domain_event
        # 仅针对地震事件做强度限制过滤，非地震事件默认放行
        if not isinstance(domain_event, EarthquakeEvent):
            return RuleDecision.accept(reason="非地震事件，跳过强度规则")

        earthquake = domain_event
        source_id = context.source_id
        policy_state = context.policy_state
        source_entry = get_source_entry(source_id)
        # 获取该数据源的强度判定模式
        intensity_mode = (
            (source_entry.intensity_mode if source_entry is not None else "")
            .strip()
            .lower()
        )
        # 归一化同义写法，避免未知取值落到兜底分支被静默放行。
        intensity_mode = _INTENSITY_MODE_ALIASES.get(intensity_mode, intensity_mode)

        # 单元测试或模拟发震场景，支持强制绕过常规数值过滤
        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            return RuleDecision.accept(reason="模拟模式跳过强度过滤")

        # 模式 1：Global Quake
        if source_id == "global_quake":
            runtime_filter = policy_state.get("global_quake_filter") or {}
            return self._evaluate_dual_threshold_filter(
                runtime_filter=runtime_filter
                if isinstance(runtime_filter, dict)
                else {},
                primary=(
                    "magnitude",
                    "震级",
                    earthquake.magnitude,
                    runtime_filter.get("min_magnitude", 4.5)
                    if isinstance(runtime_filter, dict)
                    else 4.5,
                    True,
                ),
                secondary=(
                    "intensity",
                    "最大烈度",
                    earthquake.intensity,
                    runtime_filter.get("min_intensity", 5.0)
                    if isinstance(runtime_filter, dict)
                    else 5.0,
                    True,
                ),
                accept_reason="Global Quake规则通过",
            )

        # 模式 2：烈度过滤器（通常为中国地震预警）
        if intensity_mode == "intensity":
            runtime_filter = policy_state.get("intensity_filter") or {}
            return self._evaluate_dual_threshold_filter(
                runtime_filter=runtime_filter
                if isinstance(runtime_filter, dict)
                else {},
                primary=(
                    "magnitude",
                    "震级",
                    earthquake.magnitude,
                    runtime_filter.get("min_magnitude", 2.0)
                    if isinstance(runtime_filter, dict)
                    else 2.0,
                    True,
                ),
                secondary=(
                    "intensity",
                    "最大烈度",
                    earthquake.intensity,
                    runtime_filter.get("min_intensity", 4.0)
                    if isinstance(runtime_filter, dict)
                    else 4.0,
                    True,
                ),
                accept_reason="烈度规则通过",
            )

        # 模式 3：震度过滤器（日本、台湾）
        if intensity_mode == "scale":
            runtime_filter = policy_state.get("scale_filter") or {}
            # 缺失震级或占位震级时跳过震级阈值检查，仅按震度过滤
            # 统一跳过震级检查，避免 all 模式误杀此类消息，
            # 或 any 模式产生「震级 无 ≥ 2.0」误导性失败描述。
            eq_metadata = getattr(earthquake, "metadata", {}) or {}
            if not isinstance(eq_metadata, dict):
                eq_metadata = {}
            magnitude = earthquake.magnitude
            skip_magnitude = magnitude is None or bool(
                eq_metadata.get("is_assumption", False)
                or eq_metadata.get("magnitude_is_placeholder", False)
            )
            return self._evaluate_dual_threshold_filter(
                runtime_filter=runtime_filter
                if isinstance(runtime_filter, dict)
                else {},
                primary=(
                    "magnitude",
                    "震级",
                    earthquake.magnitude,
                    runtime_filter.get("min_magnitude", 2.0)
                    if isinstance(runtime_filter, dict)
                    else 2.0,
                    earthquake.magnitude != -1.0,
                ),
                secondary=(
                    "scale",
                    "最大震度",
                    earthquake.scale,
                    runtime_filter.get("min_scale", 1.0)
                    if isinstance(runtime_filter, dict)
                    else 1.0,
                    True,
                ),
                accept_reason=(
                    "震度规则通过（震级缺失或为占位震级，已跳过震级过滤）"
                    if skip_magnitude
                    else "震度规则通过"
                ),
                skip_primary=skip_magnitude,
            )

        # 模式 4：S-Net 海底震度监测（包含最大震度过滤与触发测站数过滤）
        if source_id == "snet_msil" or intensity_mode == "snet_shindo":
            runtime_filter = policy_state.get("snet_filter") or {}
            if not isinstance(runtime_filter, dict):
                runtime_filter = {}
            if runtime_filter.get("enabled", True):
                combine_mode = normalize_combine_mode(
                    runtime_filter.get("combine_mode")
                )

                # 条件一：最大震度过滤
                min_shindo = normalize_min_shindo(runtime_filter.get("min_shindo"))

                metadata = getattr(earthquake, "metadata", {}) or {}
                if not isinstance(metadata, dict):
                    metadata = {}

                max_shindo = earthquake.scale
                if max_shindo is None:
                    max_shindo = metadata.get("max_shindo")
                try:
                    max_shindo_val = (
                        float(max_shindo) if max_shindo is not None else None
                    )
                except (TypeError, ValueError):
                    max_shindo_val = None

                shindo_passed = (
                    max_shindo_val is not None and max_shindo_val >= min_shindo
                )

                # 条件二：触发测站数量过滤
                # 必须按“当前会话”的 station_min_shindo 重新统计，
                # 不能直接复用解析阶段写入的全局 triggered_station_count。
                min_triggered_stations = normalize_min_triggered_stations(
                    runtime_filter.get("min_triggered_stations")
                )
                station_min_shindo = normalize_station_min_shindo(
                    runtime_filter.get("station_min_shindo")
                )
                triggered_station_count = count_triggered_stations(
                    metadata.get("stations"), station_min_shindo
                )

                station_limit_enabled = min_triggered_stations > 0
                station_passed = (
                    triggered_station_count >= min_triggered_stations
                    if station_limit_enabled
                    else True
                )

                # S-Net 语义：
                # 1) 未配置测站数（=0）：仅按最大震度过滤
                # 2) 配置了测站数：测站数始终是硬门槛（避免 any 模式下被最大震度单独放行）
                #    - any：测站数达标即可（支持震度偏低但站数多）
                #    - all：测站数与最大震度都要达标
                if station_limit_enabled:
                    if not station_passed:
                        return RuleDecision.reject(
                            reason="S-Net测站数过滤器",
                            detail=(
                                f"测站震度≥{station_min_shindo} 的触发测站数 "
                                f"{triggered_station_count} < {min_triggered_stations}"
                            ),
                        )
                    if combine_mode == "all" and not shindo_passed:
                        return RuleDecision.reject(
                            reason="S-Net震度过滤器",
                            detail=(
                                f"最大震度 {max_shindo_val if max_shindo_val is not None else '无'} "
                                f"< {min_shindo}（all 模式需同时满足测站数）"
                            ),
                        )
                    return RuleDecision.accept(reason="S-Net规则通过")

                if not shindo_passed:
                    return RuleDecision.reject(
                        reason="S-Net震度过滤器",
                        detail=(
                            f"最大震度 {max_shindo_val if max_shindo_val is not None else '无'} "
                            f"< {min_shindo}"
                        ),
                    )
            return RuleDecision.accept(reason="S-Net规则通过")

        # 模式 5：仅依赖震级阈值的来源统一走这一分支（USGS / ShakeAlert 等）。
        if (
            source_id in {"usgs_fanstudio", "sa_fanstudio"}
            or intensity_mode == "magnitude"
        ):
            runtime_filter = policy_state.get("magnitude_filter") or {}
            if runtime_filter.get("enabled", True):
                min_magnitude = runtime_filter.get("min_magnitude", 4.5)
                # 启用过滤器时，未知震级视为不通过（与双阈值过滤器对缺失值的处理一致）。
                if earthquake.magnitude is None:
                    return RuleDecision.reject(
                        reason="震级过滤器",
                        detail="缺少震级信息",
                    )
                if earthquake.magnitude < min_magnitude:
                    return RuleDecision.reject(
                        reason="震级过滤器",
                        detail=f"震级 {earthquake.magnitude} < {min_magnitude}",
                    )
            return RuleDecision.accept(reason="震级规则通过")

        # 其他未配置强度模式的数据源默认直接通过。
        # 但若目录配置了既非空、又不在规范集合内的未知模式，说明该源可能误配，这里做一次性告警。
        if intensity_mode not in _KNOWN_INTENSITY_MODES:
            self._warn_unknown_intensity_mode(source_id, intensity_mode)
        return RuleDecision.accept(reason="无需强度过滤")

    @classmethod
    def _warn_unknown_intensity_mode(cls, source_id: str, intensity_mode: str) -> None:
        """对未知强度模式做去重告警，避免同源逐条刷屏。"""
        key = (source_id, intensity_mode)
        if key in cls._warned_unknown_modes:
            return
        cls._warned_unknown_modes.add(key)
        plugin_logger.warning(
            f"[灾害预警] 数据源 {source_id} 配置了未识别的强度模式 "
            f"'{intensity_mode}'，强度过滤将被跳过；请检查数据源目录配置"
        )
