"""
统计规则服务。
负责重大事件判定、地震/气象详细统计与时间序列分桶更新，
减少 StatisticsManager 中残留的领域规则实现。
"""

from __future__ import annotations

from datetime import datetime

from astrbot.api import logger

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...domain.typhoon import format_display_name
from ...message.presenters.weather_constants import (
    COLOR_LEVEL_EMOJI,
    SORTED_WEATHER_TYPES,
)
from ...services.identity.event_classifier import is_major_event
from ..source_compat import is_earthquake_supplement_product
from .typhoon_stats_accumulator import record_typhoon_observation


class StatsRuleService:
    """统计规则服务。"""

    def __init__(self, manager):
        self.manager = manager

    def is_major_event(self, event: EventEnvelope) -> bool:
        """判断是否为重大事件。"""
        # 统计侧复用身份分类服务的重大事件规则，确保运行时、入库与统计口径一致。
        return is_major_event(event)

    def record_earthquake_stats(self, event: EventEnvelope) -> None:
        """记录地震详细统计。"""
        # 地震统计既包含震级分布，也负责维护“最大地震”和国内区域统计等派生指标。
        envelope = event
        data = envelope.event
        if not isinstance(data, EarthquakeEvent):
            return

        event_metadata = getattr(data, "metadata", None)
        if not isinstance(event_metadata, dict):
            event_metadata = {}
        envelope_metadata = (
            envelope.metadata if isinstance(envelope.metadata, dict) else {}
        )
        info_type = str(
            getattr(data, "info_type", "")
            or event_metadata.get("info_type")
            or envelope_metadata.get("info_type")
            or ""
        )
        # 补充产品（烈度速报 / CMT 等）不参与震级分布 / 最大震级 / 地区统计。
        if is_earthquake_supplement_product(
            envelope.source_id or "", info_type=info_type
        ):
            return

        mag = data.magnitude
        if mag is not None:
            if mag < 3.0:
                key = "< M3.0"
            elif 3.0 <= mag < 4.0:
                key = "M3.0 - M3.9"
            elif 4.0 <= mag < 5.0:
                key = "M4.0 - M4.9"
            elif 5.0 <= mag < 6.0:
                key = "M5.0 - M5.9"
            elif 6.0 <= mag < 7.0:
                key = "M6.0 - M6.9"
            elif 7.0 <= mag < 8.0:
                key = "M7.0 - M7.9"
            else:
                key = ">= M8.0"
            self.manager.stats["earthquake_stats"]["by_magnitude"][key] += 1

            is_reliable = False
            is_cenc_official = False
            if info_type:
                # 只有较可靠的正式报、审定报或完整参数报，才参与最大地震等派生统计。
                info_lower = info_type.lower()
                if "正式" in info_type:
                    is_reliable = True
                    is_cenc_official = True
                elif "reviewed" in info_lower:
                    is_reliable = True
                elif info_type in [
                    "Destination",
                    "ScaleAndDestination",
                    "DetailScale",
                ]:
                    is_reliable = True
                elif "震源" in info_type or "各地" in info_type:
                    is_reliable = True
                elif envelope.source_id == "fssn_cmt_fanstudio" and info_type == "CMT":
                    # CMT 虽是补充产品，但在 record_earthquake_stats 外层已被 is_earthquake_supplement_product 过滤掉。
                    # 这里保持逻辑一致即可。
                    is_reliable = False

            if is_reliable:
                # 最大地震摘要只接受可信事件，避免临时报文把峰值统计刷乱。
                current_max = self.manager.stats["earthquake_stats"].get(
                    "max_magnitude"
                )
                source_id = envelope.source_id or ""
                event_time = self.manager.normalize_utc_datetime(
                    getattr(data, "occurred_at", None),
                    source_id=source_id,
                )
                event_id = str(envelope.identity.event_id or envelope.id or "").strip()
                if current_max is None or mag > current_max.get("value", 0):
                    self.manager.stats["earthquake_stats"]["max_magnitude"] = {
                        "value": mag,
                        "event_id": event_id,
                        "place_name": data.place_name,
                        "time": event_time.isoformat(),
                        "source": source_id,
                    }
                elif mag == current_max.get("value", 0):
                    current_time_str = current_max.get("time")
                    if current_time_str:
                        try:
                            current_time = datetime.fromisoformat(current_time_str)
                            if event_time > current_time:
                                self.manager.stats["earthquake_stats"][
                                    "max_magnitude"
                                ] = {
                                    "value": mag,
                                    "event_id": event_id,
                                    "place_name": data.place_name,
                                    "time": event_time.isoformat(),
                                    "source": source_id,
                                }
                        except Exception:
                            pass

            if is_cenc_official:
                self.record_cenc_official_region_stats(event)

    def is_cenc_official_earthquake(self, event: EventEnvelope) -> bool:
        """判断事件是否属于 CENC 正式测定地区统计口径。"""
        data = event.event
        if not isinstance(data, EarthquakeEvent):
            return False
        source_id = event.source_id or ""
        if source_id not in {"cenc_fanstudio", "cenc_wolfx"}:
            return False
        event_metadata = getattr(data, "metadata", None)
        if not isinstance(event_metadata, dict):
            event_metadata = {}
        info_type = str(
            getattr(data, "info_type", "") or event_metadata.get("info_type") or ""
        )
        return "正式" in info_type

    def record_cenc_official_region_stats(self, event: EventEnvelope) -> bool:
        """按独立去重键记录 CENC 正式测定国内地区统计。"""
        if not self.is_cenc_official_earthquake(event):
            return False
        data = event.event
        if not isinstance(data, EarthquakeEvent):
            return False
        event_key = str(event.identity.event_id or event.id or "").strip()
        if not event_key:
            event_key = self.manager.get_unique_event_id(event)
        region_key = f"cenc_official_region:{event_key}"
        if region_key in self.manager._recorded_cenc_official_region_ids:
            return False

        region = self.manager.event_support_service.extract_region(
            data.place_name,
            strict=True,
        )
        if not region:
            return False

        self.manager._recorded_cenc_official_region_ids.add(region_key)
        self.manager.stats["earthquake_stats"]["by_region"][region] += 1
        return True

    async def record_weather_stats(self, data) -> bool | dict[str, str]:
        """记录气象预警详细统计。

        成功返回 True；失败返回 context 字典（无可用上下文时返回 None），
        context 携带提取地名、标题、头条等上下文，供 log_weather_stats_skip 输出可排障日志。
        调用方通过返回值是否非 True 判断失败，不再依赖布尔值身份比较。
        """
        # 气象统计依赖地区解析成功，否则只保留总量，不把不可靠地区写入分布统计。
        title_text = getattr(data, "title", "") or getattr(data, "headline", "") or ""
        headline_text = getattr(data, "headline", "") or ""

        direct_region = self.manager._weather_region_resolver.extract_province(
            title_text
        )
        if direct_region:
            region = direct_region
        else:
            region = await self.manager._weather_region_resolver.extract_province_with_fallback(
                title_text, headline_text
            )
            if not region:
                # 提取到的地名（可能为空）：供日志区分
                # “headline 中根本提不出地名”与“地名存在但外部查询失败”两种场景。
                place_name = (
                    self.manager._weather_region_resolver._extract_place_from_headline(
                        headline_text
                    )
                )
                # 返回 context 字典（而非 (False, context) 元组）：
                # 调用方通过“返回值非 True”判断失败，避免对 True/False 做身份比较。
                return {
                    "place_name": place_name or "",
                    "title_text": title_text,
                    "headline_text": headline_text,
                }

        level = "未知"
        # 颜色级别通过标题关键词匹配，统一映射成带符号的展示文本。
        for color, emoji in COLOR_LEVEL_EMOJI.items():
            if color in title_text:
                level = f"{emoji}{color}"
                break
        self.manager.stats["weather_stats"]["by_level"][level] += 1

        w_type = "其他"
        # 类型按预设顺序匹配，优先命中更具体、排序更靠前的灾种名称。
        # 仅从代表标题的 headline（即 headline_text）提取预警类型，若不存在才回退到 title_text，排除 description 的干扰
        search_text = headline_text if headline_text else title_text
        for name in SORTED_WEATHER_TYPES:
            if name in search_text:
                w_type = name
                break
        self.manager.stats["weather_stats"]["by_type"][w_type] += 1
        self.manager.stats["weather_stats"]["by_region"][region] += 1
        return True

    def record_time_series(self, event: EventEnvelope) -> None:
        """记录时间序列统计。"""
        envelope = event
        domain_event = envelope.event
        source_id = envelope.source_id or ""

        event_time = None
        if isinstance(domain_event, EarthquakeEvent):
            event_time = domain_event.occurred_at
        elif isinstance(domain_event, TsunamiEvent):
            event_time = domain_event.issued_at
        elif isinstance(domain_event, WeatherEvent):
            event_time = domain_event.effective_at
        elif isinstance(domain_event, TyphoonEvent):
            event_time = domain_event.updated_at

        # 各类事件时间字段名称不同，这里统一归一为 UTC 时间后再写入时间序列桶。
        event_time = self.manager.normalize_utc_datetime(
            event_time, source_id=source_id
        )
        hour_key = event_time.strftime("%Y-%m-%d %H:00")
        self.manager.stats["hourly_counts"][hour_key] += 1

        day_key = event_time.strftime("%Y-%m-%d")
        self.manager.stats["daily_counts"][day_key] += 1

    def record_typhoon_stats(self, event: EventEnvelope) -> None:
        """记录一次实时台风观测，聚合公式由共享累加器统一维护。"""
        data = event.event
        if not isinstance(data, TyphoonEvent):
            return
        display_name = format_display_name(
            str(data.name or "").strip(),
            str(data.name_en or "").strip(),
            str(data.typhoon_id or "").strip(),
            fallback="",
        )
        record_typhoon_observation(
            self.manager.stats["typhoon_stats"],
            display_name=display_name,
            level=str(data.typhoon_type or "未知").strip(),
            wind_speed=data.wind_speed,
            pressure=data.pressure,
        )

    def log_weather_stats_skip(
        self,
        *,
        event_id: str = "",
        source_id: str = "",
        place_name: str = "",
        title_text: str = "",
        headline_text: str = "",
    ) -> None:
        """记录气象统计被跳过的日志，附带地区解析失败上下文以便排障。"""
        detail_parts = [
            f"事件编号为 {event_id or '未知'}",
            f"来源：{source_id or '未知来源'}",
        ]
        if place_name:
            detail_parts.append(f"提取地名为 {place_name}")
        else:
            detail_parts.append("未提取出可查询地名")
        if title_text:
            detail_parts.append(f"标题为{title_text[:60]}")
        if headline_text:
            detail_parts.append(f"副标题为 {headline_text[:80]}")
        logger.warning(
            "[灾害预警] 气象预警地区信息无效或缺失，已跳过该次气象详细统计"
            f"（{'; '.join(detail_parts)}）"
        )
