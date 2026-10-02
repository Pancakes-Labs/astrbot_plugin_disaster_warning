"""
气象规则。
负责按预警颜色等级与关键词白名单筛选气象事件。
"""

from __future__ import annotations

from ...utils.china_regions import province_short
from ..domain.event_models import WeatherEvent
from ..domain.event_payload import SourcePayload
from .base_rule import BaseRule, RuleContext
from .rule_result import RuleDecision


class WeatherRule(BaseRule):
    """旧气象过滤器的规则化包装。"""

    rule_name = "weather_rule"

    def evaluate(self, context: RuleContext) -> RuleDecision:
        """按颜色等级与地区关键词对白名单气象事件做过滤。"""
        domain_event = context.domain_event
        # 仅针对气象预警事件生效
        if not isinstance(domain_event, WeatherEvent):
            return RuleDecision.accept(reason="非气象事件，跳过气象规则")

        weather_filter = context.policy_state.get("weather_filter") or {}
        if not weather_filter:
            return RuleDecision.accept(reason="未配置气象过滤器")

        # 检查是否启用了气象过滤器开关
        if not weather_filter.get("enabled", False):
            return RuleDecision.accept(reason="气象规则未启用")

        # 标题与说明可能分散在领域对象、元数据和原始载荷中，这里统一兜底提取。
        envelope_payload = context.envelope.payload
        payload = (
            envelope_payload.to_dict()
            if isinstance(envelope_payload, SourcePayload)
            else {}
        )
        metadata = (
            context.envelope.metadata
            if isinstance(context.envelope.metadata, dict)
            else {}
        )
        title_text = (
            getattr(domain_event, "title", "")
            or getattr(domain_event, "headline", "")
            or metadata.get("title", "")
            or metadata.get("headline", "")
            or payload.get("title", "")
            or payload.get("headline", "")
            or ""
        )
        min_color_level = weather_filter.get("min_color_level", "白色")

        # 规范化关键字过滤列表
        keywords = [
            str(keyword).strip()
            for keyword in weather_filter.get("keywords", [])
            if str(keyword).strip()
        ]
        legacy_provinces = [
            str(keyword).strip()
            for keyword in weather_filter.get("provinces", [])
            if str(keyword).strip()
        ]
        # 兼容老版本用户遗留的 provinces 属性作为区域白名单
        if not keywords:
            keywords = legacy_provinces
        elif legacy_provinces:
            keywords = list(dict.fromkeys([*keywords, *legacy_provinces]))

        color_levels = {"白色": 0, "蓝色": 1, "黄色": 2, "橙色": 3, "红色": 4}

        # 颜色按由高到低的优先级识别，命中后即可停止继续扫描。
        detected_color = "白色"
        for color in ["红色", "橙色", "黄色", "蓝色", "白色"]:
            if color in title_text:
                detected_color = color
                break

        # 对比颜色等级阈值进行过滤
        if color_levels.get(detected_color, 0) < color_levels.get(min_color_level, 0):
            return RuleDecision.reject(
                reason="气象颜色级别过滤",
                detail=f"当前颜色 {detected_color} 低于最低要求 {min_color_level}",
                context={
                    "detected_color": detected_color,
                    "min_color_level": min_color_level,
                },
            )

        # 整理气象灾害具体描述文本
        headline_text = (
            getattr(domain_event, "headline", "")
            or metadata.get("headline", "")
            or metadata.get("description", "")
            or payload.get("headline", "")
            or payload.get("description", "")
            or ""
        )

        # 白名单关键词校验：要求标题、灾害描述正文或解析出的地区归属中命中关键词
        if keywords:
            title_hits = [keyword for keyword in keywords if keyword in title_text]
            headline_hits = [
                keyword for keyword in keywords if keyword in headline_text
            ]

            province_val = str(
                metadata.get("province") or payload.get("province") or ""
            ).strip()
            city_val = str(metadata.get("city") or payload.get("city") or "").strip()
            district_val = str(
                metadata.get("district") or payload.get("district") or ""
            ).strip()
            geo_text = f"{province_val} {city_val} {district_val}".strip()

            geo_hits = [
                keyword
                for keyword in keywords
                if (geo_text and keyword in geo_text)
                or (
                    province_val
                    and (
                        keyword == province_val
                        or keyword == province_short(province_val)
                    )
                )
            ]

            if not title_hits and not headline_hits and not geo_hits:
                return RuleDecision.reject(
                    reason="气象关键词白名单过滤",
                    detail="标题、正文与地区归属均未命中关键词白名单",
                    context={
                        "keywords": keywords,
                        "title_hits": title_hits,
                        "headline_hits": headline_hits,
                        "geo_hits": geo_hits,
                    },
                )

        return RuleDecision.accept(reason="气象规则通过")
