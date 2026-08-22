"""
事件分类服务。
负责承接旧工具层中迁移出的业务判定逻辑，用于统一识别重大事件。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...domain.event_payload import SourcePayload

# 重大地震震级触发标准
MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD = 6.0
# 重大灾害预警颜色关键字
MAJOR_WEATHER_LEVEL_KEYWORD = "红"
# 重大灾害预警文本短语匹配列表
MAJOR_WEATHER_TEXT_PHRASES = ("红色预警",)
# 重大台风等级关键词：强台风及以上（强台风、超强台风）
MAJOR_TYPHOON_LEVEL_KEYWORDS = ("强台风", "超强台风")


def is_major_weather_level(*candidates: Any) -> bool:
    """判断结构化气象等级字段是否表示红色预警。"""
    return any(
        MAJOR_WEATHER_LEVEL_KEYWORD in str(candidate or "") for candidate in candidates
    )


def is_major_typhoon_level(*candidates: Any) -> bool:
    """判断台风等级字段是否达到强台风及以上级别。"""
    text = "".join(str(candidate or "") for candidate in candidates)
    return any(keyword in text for keyword in MAJOR_TYPHOON_LEVEL_KEYWORDS)


def is_major_weather_text(*candidates: Any) -> bool:
    """判断标题或描述文本是否明确表示红色预警。"""
    # 只要有任何一条文本匹配到“红色预警”即判定为重大
    return any(
        phrase in text
        for phrase in MAJOR_WEATHER_TEXT_PHRASES
        for text in (str(candidate or "") for candidate in candidates)
    )


def is_major_record(record: dict[str, Any]) -> bool:
    """根据持久化记录判断是否为重大事件。

    该函数主要服务于历史记录筛选与管理端重大事件列表构建。
    """
    record_type = str(record.get("type", "") or "").strip()
    # 持久化记录按事件类型分别采用震级、海啸类型或气象级别做重大性判断。
    if record_type in {"earthquake", "earthquake_warning"}:
        magnitude = record.get("magnitude")
        return (
            magnitude is not None and magnitude >= MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD
        )
    if record_type == "tsunami":
        # 海啸事件因为罕见且危险性高，一律判定为重大事件
        return True
    if record_type == "typhoon":
        # 台风仅强台风及以上级别判定为重大事件。
        # 注意：数据库主表 level 存储的是历史峰值（用于风王榜等统计），
        # 因此重大事件判断必须使用 _snapshot_level（当前观测等级），
        # 避免已减弱的台风（如巴威从超强台风回落到热带风暴）仍被误判为重大事件。
        # 峰值 level 仅在 _snapshot_level 缺失时（如历史重建记录）作为回退。
        current_level = str(record.get("_snapshot_level") or "")
        if current_level:
            return is_major_typhoon_level(current_level)
        level = str(record.get("level") or "")
        return is_major_typhoon_level(level)
    if record_type == "weather_alarm":
        level = str(record.get("level") or "")
        description = str(record.get("description") or "")
        return is_major_weather_level(level) or is_major_weather_text(description)
    return False


def is_major_event(event: EventEnvelope) -> bool:
    """根据运行时事件判断是否为重大事件。

    与持久化记录版本类似，但这里优先利用领域事件对象与运行时载荷信息。
    """
    envelope = event
    domain_event = envelope.event

    # 地震、海啸和气象事件采用不同的重大性判断标准。
    if isinstance(domain_event, EarthquakeEvent):
        # 震级大于等于 6.0 的地震视为重大地震
        return (
            domain_event.magnitude is not None
            and domain_event.magnitude >= MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD
        )
    if isinstance(domain_event, TsunamiEvent):
        # 海啸事件一律视为重大事件
        return True
    if isinstance(domain_event, TyphoonEvent):
        # 台风仅强台风及以上级别判定为重大事件
        return is_major_typhoon_level(domain_event.typhoon_type)
    if isinstance(domain_event, WeatherEvent):
        # 获取源数据载荷字典
        payload = (
            envelope.payload.to_dict()
            if isinstance(envelope.payload, SourcePayload)
            else {}
        )
        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        # 提取警报级别字符串
        level = str(
            getattr(domain_event, "alert_level", "")
            or metadata.get("level", "")
            or payload.get("alert_level")
            or payload.get("level")
        )
        # 提取警报标题
        title = str(
            domain_event.title
            or domain_event.headline
            or metadata.get("title", "")
            or metadata.get("headline", "")
            or payload.get("title", "")
            or payload.get("headline", "")
        )
        return is_major_weather_level(level) or is_major_weather_text(title)
    return False
