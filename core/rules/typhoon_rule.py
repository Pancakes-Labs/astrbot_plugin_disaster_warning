"""
台风推送规则。
负责按强度等级、中心气压、风速/风力、名称名单、本地距离与预报路径逼近筛选台风事件。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ..domain.event_models import TyphoonEvent
from ..domain.typhoon import level_weight, to_float
from ..services.geo.intensity_service import IntensityCalculator
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class TyphoonRule(BaseRule):
    """台风过滤器规则。"""

    rule_name = "typhoon_rule"

    # 停编通知的最大允许时效（小时）。
    # 停编事件的时间字段（updated_at）缺失或距今超过该阈值时拒绝放行，
    # 作为前置 time_rule 的兜底（time_rule 目前对台风事件不设时效限制），
    # 避免早已停编的历史台风在开关开启时被无条件放行刷屏。
    DEACTIVATE_NOTIFY_MAX_AGE_HOURS = 6.0

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """按台风过滤配置评估是否放行。"""
        domain_event = context.domain_event
        if not isinstance(domain_event, TyphoonEvent):
            return RuleDecision.accept(reason="非台风事件，跳过台风规则")

        typhoon_filter = self._resolve_typhoon_filter(context)
        if not typhoon_filter.get("enabled", False):
            # 即使过滤关闭，仍尽量补充本地距离信息供展示复用。
            estimation = self._build_location_estimation(
                domain_event,
                typhoon_filter,
                context.runtime_config,
            )
            if estimation:
                context.extras["typhoon_local_estimation"] = estimation
            return RuleDecision.accept(reason="台风规则未启用")

        # 模拟演练模式：只写估算信息，不拦截。
        if context.runtime_config.get("__simulation_bypass_regular_filters", False):
            estimation = self._build_location_estimation(
                domain_event,
                typhoon_filter,
                context.runtime_config,
            )
            if estimation:
                context.extras["typhoon_local_estimation"] = estimation
            return RuleDecision.accept(reason="模拟模式跳过台风过滤")

        decision_context: dict[str, Any] = {
            "typhoon_id": domain_event.typhoon_id,
            "typhoon_type": domain_event.typhoon_type,
            "pressure": domain_event.pressure,
            "wind_speed": domain_event.wind_speed,
            "power": domain_event.power,
            "is_active": domain_event.is_active,
        }

        # 1) 活跃状态
        if not bool(domain_event.is_active):
            # 停编通知：typhoon_filter.typhoon_deactivate_notify 开启时，
            # 不受名称/强度/距离等过滤约束（停编即视为值得通知），
            # 但仍受时效兜底约束：以最新观测时间（updated_at）为准，
            # 距今超过 DEACTIVATE_NOTIFY_MAX_AGE_HOURS 或时间缺失时拒绝放行，
            # 避免早已停编的历史台风在开关开启时被无条件放行刷屏
            # （time_rule 目前对台风事件不设时效限制，需在此处兜底）。
            deactivate_notify = bool(
                typhoon_filter.get("typhoon_deactivate_notify", True)
            )
            if deactivate_notify:
                # 时效兜底：停编事件必须发生在 6 小时之内。
                updated_at = self._coerce_datetime(domain_event.updated_at)
                if updated_at is None:
                    return RuleDecision.reject(
                        reason="台风停编通知时效过滤",
                        detail="该台风缺少观测时间，无法确认在 6 小时内停编",
                        context=decision_context,
                    )
                age_hours = (
                    datetime.now(timezone.utc) - updated_at
                ).total_seconds() / 3600
                if age_hours < 0:
                    # 未来时间戳（观测时间晚于当前时间）：不属于 6 小时之内，
                    # 拒绝放行并附负数老化小时数便于排查时钟偏差。
                    return RuleDecision.reject(
                        reason="台风停编通知时效过滤",
                        detail="该台风最后一次观测时间晚于当前时间，无法确认在 6 小时内停编",
                        context={
                            **decision_context,
                            "deactivate_age_hours": round(age_hours, 2),
                        },
                    )
                if age_hours > self.DEACTIVATE_NOTIFY_MAX_AGE_HOURS:
                    return RuleDecision.reject(
                        reason="台风停编通知时效过滤",
                        detail=(
                            f"该台风最后一次观测于 {age_hours:.1f} 小时前，"
                            f"超过停编通知最大时效 "
                            f"{self.DEACTIVATE_NOTIFY_MAX_AGE_HOURS:.0f} 小时"
                        ),
                        context={
                            **decision_context,
                            "deactivate_age_hours": round(age_hours, 2),
                            "deactivate_max_age_hours": (
                                self.DEACTIVATE_NOTIFY_MAX_AGE_HOURS
                            ),
                        },
                    )
                # 仍尽量补充本地距离信息供展示复用。
                estimation = self._build_location_estimation(
                    domain_event,
                    typhoon_filter,
                    context.runtime_config,
                )
                if estimation:
                    context.extras["typhoon_local_estimation"] = estimation
                return RuleDecision.accept(
                    reason="台风停编通知",
                    detail=f"该台风已停止编报（{age_hours:.1f} 小时内停编）",
                    context={
                        **decision_context,
                        "deactivate_age_hours": round(age_hours, 2),
                    },
                )
            if typhoon_filter.get("only_active", True):
                return RuleDecision.reject(
                    reason="台风活跃状态过滤",
                    detail="该台风已停止编报，且仅推送活跃台风",
                    context=decision_context,
                )

        # 2) 名称黑白名单
        name_decision = self._evaluate_name_lists(domain_event, typhoon_filter)
        if name_decision is not None:
            name_decision.context.update(decision_context)
            return name_decision

        # 3) 基础强度条件
        basic_decision = self._evaluate_basic_thresholds(domain_event, typhoon_filter)
        if basic_decision is not None:
            basic_decision.context.update(decision_context)
            return basic_decision

        # 4) 距离 + 预报逼近
        location_result = self._evaluate_location_filters(
            domain_event,
            typhoon_filter,
            context.runtime_config,
        )
        estimation = location_result.get("estimation") or {}
        if estimation:
            context.extras["typhoon_local_estimation"] = estimation
            decision_context["local_estimation"] = dict(estimation)

        if not location_result.get("accepted", True):
            return RuleDecision.reject(
                reason=str(location_result.get("reason") or "台风距离过滤"),
                detail=str(location_result.get("detail") or ""),
                context=decision_context,
            )

        detail_parts = [
            part
            for part in (
                location_result.get("detail"),
                basic_decision_detail(domain_event, typhoon_filter),
            )
            if part
        ]
        return RuleDecision.accept(
            reason="台风规则通过",
            detail="；".join(detail_parts),
            context=decision_context,
        )

    @staticmethod
    def _resolve_typhoon_filter(context: RuleContext) -> dict[str, Any]:
        """从 policy_state / runtime_config 解析台风过滤配置。"""
        policy_filter = context.policy_state.get("typhoon_filter")
        if isinstance(policy_filter, dict) and policy_filter:
            return policy_filter

        typhoon_config = context.runtime_config.get("typhoon_config")
        if isinstance(typhoon_config, dict):
            nested = typhoon_config.get("typhoon_filter")
            if isinstance(nested, dict):
                return nested
        return {}

    @classmethod
    def _normalize_name_tokens(cls, event: TyphoonEvent) -> list[str]:
        """生成用于名单匹配的名称/编号 token。"""
        tokens = [
            str(event.name or "").strip(),
            str(event.name_en or "").strip(),
            str(event.typhoon_id or "").strip(),
        ]
        typhoon_id = str(event.typhoon_id or "").strip()
        if len(typhoon_id) >= 4:
            tokens.append(typhoon_id[-4:])
        return [token for token in tokens if token]

    def _evaluate_name_lists(
        self,
        event: TyphoonEvent,
        typhoon_filter: dict[str, Any],
    ) -> RuleDecision | None:
        """评估名称黑白名单；返回 None 表示继续后续判断。"""
        whitelist = [
            str(item).strip()
            for item in (typhoon_filter.get("name_whitelist") or [])
            if str(item).strip()
        ]
        blacklist = [
            str(item).strip()
            for item in (typhoon_filter.get("name_blacklist") or [])
            if str(item).strip()
        ]
        tokens = self._normalize_name_tokens(event)
        haystack = " ".join(tokens).lower()

        if blacklist:
            hits = [item for item in blacklist if item.lower() in haystack]
            if hits:
                return RuleDecision.reject(
                    reason="台风名称黑名单过滤",
                    detail=f"命中黑名单：{', '.join(hits)}",
                    context={"blacklist_hits": hits, "tokens": tokens},
                )

        if whitelist:
            hits = [item for item in whitelist if item.lower() in haystack]
            if not hits:
                return RuleDecision.reject(
                    reason="台风名称白名单过滤",
                    detail="名称/编号未命中白名单",
                    context={"whitelist": whitelist, "tokens": tokens},
                )
        return None

    def _evaluate_basic_thresholds(
        self,
        event: TyphoonEvent,
        typhoon_filter: dict[str, Any],
    ) -> RuleDecision | None:
        """评估强度/气压/风速/风力。返回 None 表示通过。"""
        combine_mode = str(typhoon_filter.get("combine_mode") or "any").strip().lower()
        if combine_mode not in {"all", "any"}:
            combine_mode = "any"

        checks: list[tuple[str, bool, str]] = []

        min_level = str(typhoon_filter.get("min_level") or "").strip()
        if min_level:
            current_weight = level_weight(event.typhoon_type)
            required_weight = level_weight(min_level)
            # 未知等级：不因缺失字段直接误杀，视为该条件跳过。
            if required_weight > 0 and current_weight > 0:
                passed = current_weight >= required_weight
                checks.append(
                    (
                        "level",
                        passed,
                        f"等级 {event.typhoon_type or '未知'} "
                        f"{'≥' if passed else '<'} {min_level}",
                    )
                )

        max_pressure = to_float(typhoon_filter.get("max_pressure"))
        if max_pressure is not None and max_pressure > 0:
            pressure = to_float(event.pressure)
            if pressure is not None and pressure > 0:
                passed = pressure <= max_pressure
                checks.append(
                    (
                        "pressure",
                        passed,
                        f"气压 {pressure:.0f} hPa "
                        f"{'≤' if passed else '>'} {max_pressure:.0f} hPa",
                    )
                )

        min_wind_speed = to_float(typhoon_filter.get("min_wind_speed"))
        if min_wind_speed is not None and min_wind_speed > 0:
            wind_speed = to_float(event.wind_speed)
            if wind_speed is not None and wind_speed > 0:
                passed = wind_speed >= min_wind_speed
                checks.append(
                    (
                        "wind_speed",
                        passed,
                        f"风速 {wind_speed:.1f} m/s "
                        f"{'≥' if passed else '<'} {min_wind_speed:.1f} m/s",
                    )
                )

        min_power = to_float(typhoon_filter.get("min_power"))
        if min_power is not None and min_power > 0:
            power = to_float(event.power)
            if power is not None and power > 0:
                passed = power >= min_power
                checks.append(
                    (
                        "power",
                        passed,
                        f"风力 {power:.0f} 级 "
                        f"{'≥' if passed else '<'} {min_power:.0f} 级",
                    )
                )

        if not checks:
            return None

        if combine_mode == "any":
            if any(passed for _, passed, _ in checks):
                return None
            detail = "；".join(detail for _, _, detail in checks)
            return RuleDecision.reject(
                reason="台风基础条件过滤",
                detail=f"any 模式下均未满足：{detail}",
                context={"combine_mode": combine_mode, "checks": checks},
            )

        failed = [detail for _, passed, detail in checks if not passed]
        if failed:
            return RuleDecision.reject(
                reason="台风基础条件过滤",
                detail="；".join(failed),
                context={"combine_mode": combine_mode, "checks": checks},
            )
        return None

    def _resolve_reference_point(
        self,
        typhoon_filter: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """解析用于距离/逼近计算的本地参考点。

        语义（改进后）：
        - 优先采用 distance_filter 显式配置的坐标；未显式配置时
          仅当 local_monitoring.enabled 为真才复用其坐标。
        - 会话关闭本地监控（enabled=false）或未配置任何可用坐标时，
          返回 None → 上层按“未配置可用本地坐标”跳过距离/逼近过滤，避免误杀。
        - 返回值携带 reference_source 字段，供日志/展示透出参考点来源，
          便于排查“为什么按某坐标算距离”。
        """
        distance_filter = typhoon_filter.get("distance_filter")
        if not isinstance(distance_filter, dict):
            distance_filter = {}

        approach_filter = typhoon_filter.get("approach_filter")
        if not isinstance(approach_filter, dict):
            approach_filter = {}

        # 只要距离或逼近任一启用，就尝试解析坐标；过滤关闭时也会用于展示估算。
        use_local = bool(distance_filter.get("use_local_monitoring", True))
        local_monitoring = runtime_config.get("local_monitoring")
        if not isinstance(local_monitoring, dict):
            local_monitoring = {}

        latitude = None
        longitude = None
        place_name = ""
        source = ""

        # 优先级 1：distance_filter 显式坐标（无论是否复用本地监控均优先）。
        distance_lat = to_float(distance_filter.get("latitude"))
        distance_lon = to_float(distance_filter.get("longitude"))

        # 只配置了其中一个坐标视为不完整配置：记警告日志并忽略。
        if (distance_lat is None) ^ (distance_lon is None):
            logger.warning(
                "[灾害预警] 距离过滤坐标配置不完整："
                f"纬度为{distance_filter.get('latitude')!r}, "
                f"经度为{distance_filter.get('longitude')!r}。"
                "必须同时设置经纬度才能生效，本次将忽略该配置。"
            )

        if distance_lat is not None and distance_lon is not None:
            latitude = distance_lat
            longitude = distance_lon
            source = "distance_filter"
            override_place = str(distance_filter.get("place_name") or "").strip()
            if override_place:
                place_name = override_place

        # 优先级 2：仅当本地监控显式启用时，才复用其坐标。
        # 修复：原先只判断 local_monitoring 是否为 dict，忽略了 enabled=false，
        # 导致“已关闭本地预估”的会话仍被全局默认坐标套住。
        if latitude is None or longitude is None:
            local_enabled = bool(local_monitoring.get("enabled", False))
            if use_local and local_enabled:
                local_lat = to_float(local_monitoring.get("latitude"))
                local_lon = to_float(local_monitoring.get("longitude"))
                if local_lat is not None and local_lon is not None:
                    latitude = local_lat
                    longitude = local_lon
                    source = "local_monitoring"
                    local_place = str(local_monitoring.get("place_name") or "").strip()
                    if not place_name and local_place:
                        place_name = local_place

        if not place_name:
            place_name = "本地"

        if latitude is None or longitude is None:
            return None
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            return None

        return {
            "latitude": latitude,
            "longitude": longitude,
            "place_name": place_name,
            "use_local_monitoring": use_local,
            "reference_source": source,
        }

    def _effective_wind_radius_km(self, event: TyphoonEvent) -> float | None:
        """估算用于“是否在风圈内”的有效半径（km）。"""
        candidates: list[float] = []

        for value in (event.radius7, event.radius10):
            number = to_float(value)
            if number is not None and number > 0:
                candidates.append(number)

        wind_circle = event.wind_circle if isinstance(event.wind_circle, dict) else {}
        for circle_key in ("30KTS", "50KTS", "64KTS"):
            circle = wind_circle.get(circle_key)
            if not isinstance(circle, dict):
                continue
            for quadrant in ("NE", "SE", "SW", "NW"):
                number = to_float(circle.get(quadrant))
                if number is not None and number > 0:
                    candidates.append(number)

        if not candidates:
            return None
        return max(candidates)

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        """尽力解析时间字段。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            # 统一为 aware UTC，便于与预报点比较。
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        text = str(value).strip()
        if not text or text.upper() in {"NULL", "NONE", "无数据", "-"}:
            return None

        normalized = text.replace("Z", "+0000")
        for fmt in (
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ):
            try:
                parsed = datetime.strptime(normalized, fmt)
                if parsed.tzinfo is None:
                    # EQSC 轨迹时间按北京时间理解，再转 UTC。
                    parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
        return None

    def _evaluate_approach(
        self,
        event: TyphoonEvent,
        approach_filter: dict[str, Any],
        reference: dict[str, Any],
    ) -> dict[str, Any]:
        """评估 future_track 是否在时间窗内逼近本地。"""
        result: dict[str, Any] = {
            "evaluated": False,
            "hit": False,
            "min_distance_km": None,
            "nearest_point_time": None,
            "nearest_point_coords": None,
            "horizon_hours": None,
            "threshold_km": None,
            "point_count": 0,
        }

        if not approach_filter.get("enabled", False):
            return result

        future_track = (
            event.future_track if isinstance(event.future_track, list) else []
        )
        if not future_track:
            return result

        horizon_hours = to_float(approach_filter.get("horizon_hours"))
        if horizon_hours is None or horizon_hours <= 0:
            horizon_hours = 48.0
        threshold_km = to_float(approach_filter.get("max_approach_distance_km"))
        if threshold_km is None or threshold_km <= 0:
            threshold_km = 500.0

        result["horizon_hours"] = horizon_hours
        result["threshold_km"] = threshold_km

        base_time = self._coerce_datetime(event.updated_at) or datetime.now(
            timezone.utc
        )
        horizon_end = base_time + timedelta(hours=float(horizon_hours))

        min_distance: float | None = None
        nearest_time: str | None = None
        nearest_coords: tuple[float, float] | None = None
        evaluated_points = 0

        for node in future_track:
            if not isinstance(node, dict):
                continue
            lat = to_float(node.get("latitude"))
            lon = to_float(node.get("longitude"))
            if lat is None or lon is None:
                continue

            node_time = self._coerce_datetime(node.get("time"))
            # 无时间戳时仍纳入评估，避免因字段缺失完全失效。
            if node_time is not None and node_time > horizon_end:
                continue

            distance = IntensityCalculator.calculate_distance(
                float(lat),
                float(lon),
                float(reference["latitude"]),
                float(reference["longitude"]),
            )
            evaluated_points += 1
            if min_distance is None or distance < min_distance:
                min_distance = distance
                nearest_time = str(node.get("time") or "") or (
                    node_time.isoformat() if node_time else None
                )
                nearest_coords = (float(lat), float(lon))

        if evaluated_points <= 0 or min_distance is None:
            return result

        result["evaluated"] = True
        result["point_count"] = evaluated_points
        result["min_distance_km"] = round(float(min_distance), 1)
        result["nearest_point_time"] = nearest_time
        result["nearest_point_coords"] = nearest_coords
        result["hit"] = float(min_distance) <= float(threshold_km)
        return result

    def _build_location_estimation(
        self,
        event: TyphoonEvent,
        typhoon_filter: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """构建可供展示复用的本地距离/逼近估算。"""
        reference = self._resolve_reference_point(typhoon_filter, runtime_config)
        if reference is None:
            return None

        estimation: dict[str, Any] = {
            "place_name": reference["place_name"],
            "local_latitude": reference["latitude"],
            "local_longitude": reference["longitude"],
        }

        center_lat = to_float(event.latitude)
        center_lon = to_float(event.longitude)
        if center_lat is not None and center_lon is not None:
            distance = IntensityCalculator.calculate_distance(
                float(center_lat),
                float(center_lon),
                float(reference["latitude"]),
                float(reference["longitude"]),
            )
            estimation["distance_km"] = round(float(distance), 1)
            wind_radius = self._effective_wind_radius_km(event)
            if wind_radius is not None:
                estimation["wind_radius_km"] = round(float(wind_radius), 1)
                estimation["within_wind_circle"] = float(distance) <= float(wind_radius)
            else:
                estimation["within_wind_circle"] = False

        approach_filter = typhoon_filter.get("approach_filter")
        if not isinstance(approach_filter, dict):
            approach_filter = {}
        approach = self._evaluate_approach(event, approach_filter, reference)
        if approach.get("evaluated"):
            estimation["approach_evaluated"] = True
            estimation["approach_hit"] = bool(approach.get("hit"))
            estimation["approach_min_distance_km"] = approach.get("min_distance_km")
            estimation["approach_nearest_point_time"] = approach.get(
                "nearest_point_time"
            )
            estimation["approach_horizon_hours"] = approach.get("horizon_hours")
            estimation["approach_threshold_km"] = approach.get("threshold_km")
        else:
            estimation["approach_evaluated"] = False
            estimation["approach_hit"] = False

        return estimation

    def _evaluate_location_filters(
        self,
        event: TyphoonEvent,
        typhoon_filter: dict[str, Any],
        runtime_config: dict[str, Any],
    ) -> dict[str, Any]:
        """评估距离与预报逼近。

        语义：
        - 两者都未启用 / 无法评估：不拦截
        - 距离失败但逼近命中：放行（提前关注）
        - 距离或逼近有有效失败且无命中：拦截
        """
        distance_filter = typhoon_filter.get("distance_filter")
        if not isinstance(distance_filter, dict):
            distance_filter = {}
        approach_filter = typhoon_filter.get("approach_filter")
        if not isinstance(approach_filter, dict):
            approach_filter = {}

        estimation = self._build_location_estimation(
            event,
            typhoon_filter,
            runtime_config,
        )

        distance_enabled = bool(distance_filter.get("enabled", False))
        approach_enabled = bool(approach_filter.get("enabled", False))
        if not distance_enabled and not approach_enabled:
            return {
                "accepted": True,
                "reason": "",
                "detail": "",
                "estimation": estimation,
            }

        reference = self._resolve_reference_point(typhoon_filter, runtime_config)
        if reference is None:
            # 没有可用坐标时不因位置条件误杀。
            # 透出 local_monitoring 开关与 use_local_monitoring，便于排查
            # “为什么没算距离/逼近”（例如本地监控已关闭）。
            local_monitoring = runtime_config.get("local_monitoring")
            if not isinstance(local_monitoring, dict):
                local_monitoring = {}
            skip_detail = (
                "未配置可用本地坐标，跳过距离/逼近过滤"
                f"（local_monitoring.enabled={bool(local_monitoring.get('enabled', False))}，"
                f"use_local_monitoring={distance_filter.get('use_local_monitoring', True)}）"
            )
            return {
                "accepted": True,
                "reason": "",
                "detail": skip_detail,
                "estimation": estimation,
            }

        distance_hit = False
        distance_evaluated = False
        distance_detail = ""

        if distance_enabled:
            center_lat = to_float(event.latitude)
            center_lon = to_float(event.longitude)
            if center_lat is not None and center_lon is not None:
                distance_evaluated = True
                distance_km = IntensityCalculator.calculate_distance(
                    float(center_lat),
                    float(center_lon),
                    float(reference["latitude"]),
                    float(reference["longitude"]),
                )
                max_distance = to_float(distance_filter.get("max_distance_km"))
                if max_distance is None or max_distance <= 0:
                    max_distance = 1200.0

                within_circle_enabled = bool(
                    distance_filter.get("within_wind_circle", True)
                )
                wind_radius = self._effective_wind_radius_km(event)
                in_circle = bool(
                    within_circle_enabled
                    and wind_radius is not None
                    and distance_km <= float(wind_radius)
                )
                center_ok = distance_km <= float(max_distance)
                distance_hit = center_ok or in_circle

                place = reference["place_name"]
                if in_circle and not center_ok:
                    distance_detail = (
                        f"中心距{place} {distance_km:.1f} km 超限，"
                        f"但位于风圈内（有效半径 {float(wind_radius):.0f} km）"
                    )
                elif center_ok:
                    distance_detail = (
                        f"中心距{place} {distance_km:.1f} km ≤ {max_distance:.0f} km"
                    )
                else:
                    distance_detail = (
                        f"中心距{place} {distance_km:.1f} km > {max_distance:.0f} km"
                    )

        approach = self._evaluate_approach(event, approach_filter, reference)
        approach_hit = bool(approach.get("hit"))
        approach_evaluated = bool(approach.get("evaluated"))

        # 距离命中或逼近命中任一即可放行。
        if distance_hit or approach_hit:
            details: list[str] = []
            if distance_hit and distance_detail:
                details.append(distance_detail)
            if approach_hit:
                place = reference["place_name"]
                details.append(
                    f"预报路径{approach.get('horizon_hours')}h内最近距{place} "
                    f"{approach.get('min_distance_km')} km ≤ "
                    f"{approach.get('threshold_km')} km"
                )
            return {
                "accepted": True,
                "reason": "",
                "detail": "；".join(details),
                "estimation": estimation,
            }

        # 有可评估的位置条件且全部未命中时拒绝。
        if distance_evaluated or approach_evaluated:
            details = []
            if distance_evaluated and distance_detail:
                details.append(distance_detail)
            if approach_evaluated:
                place = reference["place_name"]
                details.append(
                    f"预报路径{approach.get('horizon_hours')}h内最近距{place} "
                    f"{approach.get('min_distance_km')} km > "
                    f"{approach.get('threshold_km')} km"
                )
            elif approach_enabled and not approach_evaluated:
                details.append("无有效预报路径，无法判定逼近")
            return {
                "accepted": False,
                "reason": "台风距离/逼近过滤",
                "detail": "；".join(details) or "未满足距离或预报逼近条件",
                "estimation": estimation,
            }

        # 启用了位置过滤但两边都无法评估（例如缺台风坐标且无路径）时放行。
        return {
            "accepted": True,
            "reason": "",
            "detail": "距离/逼近条件缺少有效数据，已跳过",
            "estimation": estimation,
        }


def basic_decision_detail(
    event: TyphoonEvent,
    typhoon_filter: dict[str, Any],
) -> str:
    """生成基础条件摘要，便于 accept 日志阅读。"""
    parts: list[str] = []
    if event.typhoon_type:
        parts.append(f"等级 {event.typhoon_type}")
    if event.pressure is not None:
        parts.append(f"气压 {event.pressure} hPa")
    min_level = str(typhoon_filter.get("min_level") or "").strip()
    if min_level:
        parts.append(f"阈值≥{min_level}")
    return "，".join(parts)


__all__ = ["TyphoonRule"]
