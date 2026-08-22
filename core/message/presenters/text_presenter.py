"""
文本展示器分发器。

该模块负责根据来源描述或灾种类型，
把展示上下文分派到具体的文本展示器实现。
"""

from __future__ import annotations

from typing import Any

from ...domain.event_context import DisplayContext
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
from .tsunami_presenter import JmaTsunamiPresenter, TsunamiAlertPresenter
from .typhoon_presenter import TyphoonPresenter
from .weather_presenter import WeatherAlertPresenter

# 文本展示键到具体展示器的映射表。
_TEXT_PRESENTER_REGISTRY = {
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

# 当来源未声明专用文本展示器时，按灾种选择默认展示器。
DEFAULT_TEXT_PRESENTERS_BY_EVENT_TYPE = {
    "earthquake": CwaReportPresenter,
    "tsunami": TsunamiAlertPresenter,
    "weather": WeatherAlertPresenter,
    "typhoon": TyphoonPresenter,
}


def get_text_presenter_keys() -> set[str]:
    """返回已注册的 text presenter key 集合。"""
    return set(_TEXT_PRESENTER_REGISTRY.keys())


def resolve_text_presenter(presenter_key: str):
    """按 text_presenter_key 解析文本展示器。"""
    normalized_key = (presenter_key or "").strip()
    if not normalized_key:
        return None
    return _TEXT_PRESENTER_REGISTRY.get(normalized_key)


class TextPresenter(BasePresenter):
    """文本展示器。"""

    presenter_name = "text_presenter"

    @classmethod
    def _resolve_presenter(cls, display_context: DisplayContext):
        """为当前展示上下文解析最合适的文本展示器。"""
        source_descriptor = display_context.source_descriptor
        presenter_key = ""
        event_type = display_context.event_type

        if source_descriptor is not None:
            presenter_key = source_descriptor.text_presenter_key.strip()

        presenter = resolve_text_presenter(presenter_key)
        if presenter is not None:
            return presenter

        return DEFAULT_TEXT_PRESENTERS_BY_EVENT_TYPE.get(
            event_type, WeatherAlertPresenter
        )

    @classmethod
    def present(
        cls, display_context: DisplayContext, options: dict[str, Any] | None = None
    ) -> str:
        presenter = cls._resolve_presenter(display_context)
        # 调用时传入的选项优先级高于上下文自带选项。
        merged_options = dict(display_context.options or {})
        if options:
            merged_options.update(options)
        return presenter.present(display_context, merged_options)
