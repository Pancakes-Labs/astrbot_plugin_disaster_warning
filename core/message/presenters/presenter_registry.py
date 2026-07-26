"""
展示器注册中心。

该模块负责维护展示类型、文本展示键与具体展示器类之间的映射关系，
并提供统一的消息展示入口。
"""

from __future__ import annotations

from ...services.display import build_display_context
from ...sources.source_catalog import SOURCE_CATALOG
from .base_presenter import BasePresenter
from .earthquake_presenter import (
    CeaEewPresenter,
    CencEarthquakePresenter,
    CencIntensityReportPresenter,
    CwaEewPresenter,
    CwaReportPresenter,
    FssnCmtPresenter,
    GlobalQuakeTextPresenter,
    JmaEarthquakeInfoPresenter,
    JmaEewPresenter,
    ShakeAlertEewPresenter,
    SnetPresenter,
    UsgsEarthquakePresenter,
)
from .text_presenter import TextPresenter, get_text_presenter_keys
from .tsunami_presenter import JmaTsunamiPresenter, TsunamiAlertPresenter
from .typhoon_presenter import TyphoonPresenter
from .weather_presenter import WeatherAlertPresenter

# 按展示类型分发的主注册表，适合从来源目录中的展示类型直接解析展示器。
_PRESENTATION_PRESENTER_REGISTRY: dict[str, type[BasePresenter]] = {
    "earthquake_eew": TextPresenter,
    "earthquake_report": TextPresenter,
    "global_quake": GlobalQuakeTextPresenter,
    "snet": SnetPresenter,
    "tsunami": TextPresenter,
    "weather": WeatherAlertPresenter,
    "typhoon": TyphoonPresenter,
}

# 按文本展示键分发的细粒度注册表，用于来源级别的精确匹配。
_TEXT_KEY_PRESENTER_REGISTRY: dict[str, type[BasePresenter]] = {
    "cea_eew": CeaEewPresenter,
    "cwa_eew": CwaEewPresenter,
    "cwa_report": CwaReportPresenter,
    "jma_eew": JmaEewPresenter,
    "global_quake": GlobalQuakeTextPresenter,
    "cenc_report": CencEarthquakePresenter,
    "cenc_ir_report": CencIntensityReportPresenter,
    "jma_report": JmaEarthquakeInfoPresenter,
    "usgs_report": UsgsEarthquakePresenter,
    "shakealert_eew": ShakeAlertEewPresenter,
    "fssn_cmt": FssnCmtPresenter,
    "snet": SnetPresenter,
    "tsunami_cn": TsunamiAlertPresenter,
    "tsunami_jma": JmaTsunamiPresenter,
    "weather_cn": WeatherAlertPresenter,
    "typhoon": TyphoonPresenter,
}

# 当前两类注册都无法命中时，按灾种选择默认展示器兜底。
_DEFAULT_PRESENTERS_BY_EVENT_TYPE: dict[str, type[BasePresenter]] = {
    "earthquake": TextPresenter,
    "tsunami": TextPresenter,
    "weather": WeatherAlertPresenter,
    "typhoon": TyphoonPresenter,
}


def get_presenter(presentation_type: str):
    """按 presentation_type 获取 presenter 类。"""
    normalized = (presentation_type or "").strip()
    if not normalized:
        return None
    return _PRESENTATION_PRESENTER_REGISTRY.get(normalized)


def get_presenter_by_text_key(text_presenter_key: str):
    """按 text presenter key 获取 presenter 类。"""
    normalized = (text_presenter_key or "").strip()
    if not normalized:
        return None
    return _TEXT_KEY_PRESENTER_REGISTRY.get(normalized)


def get_presentation_type_for_source(source_id: str) -> str:
    """按 source_id 获取 presentation_type。"""
    entry = SOURCE_CATALOG.get((source_id or "").strip())
    if entry is None:
        return ""
    return entry.presentation_type


def present_message(
    event,
    source_id: str,
    *,
    presentation_type: str | None = None,
    options: dict | None = None,
) -> str:
    """构建展示结果。展示入口统一由展示类型或文本展示键分发。"""
    resolved_presentation_type = (
        presentation_type or ""
    ).strip() or get_presentation_type_for_source(source_id)
    context = build_display_context(event, source_id, options)

    presenter_class = None
    source_descriptor = getattr(context, "source_descriptor", None)
    if source_descriptor is not None:
        # 若来源描述中已声明更精确的文本展示键，则优先按该键命中具体展示器。
        presenter_class = get_presenter_by_text_key(
            getattr(source_descriptor, "text_presenter_key", "")
        )

    if presenter_class is None:
        presenter_class = get_presenter(resolved_presentation_type)

    if presenter_class is None:
        # 最后一层兜底按灾种选择默认展示器，避免因为注册缺失导致整个消息构建失败。
        presenter_class = _DEFAULT_PRESENTERS_BY_EVENT_TYPE.get(
            getattr(context, "event_type", ""), TextPresenter
        )

    return presenter_class.present(context, options)


__all__ = [
    "get_presenter",
    "get_presenter_by_text_key",
    "get_presentation_type_for_source",
    "present_message",
    "get_text_presenter_keys",
]
