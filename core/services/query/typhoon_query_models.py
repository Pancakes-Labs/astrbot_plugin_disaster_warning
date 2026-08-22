"""台风查询稳定结果模型。

查询层优先产出结构化字段，减少调用方直接依赖松散 dict 键名。
当前以 TypedDict 描述公开契约；运行时仍返回普通 dict 以兼容现有 API。
"""

from __future__ import annotations

from typing import Any, TypedDict


class TyphoonTrackSummary(TypedDict, total=False):
    """轨迹摘要结构。"""

    history_count: int
    future_count: int
    history_lines: list[str]
    future_lines: list[str]


class TyphoonQueryItem(TypedDict, total=False):
    """统一查询结果项。

    字段同时服务命令侧与 Web 管理端：
    - identity: typhoon_id / eqsc_id / display_name
    - metrics: typhoon_type / wind_speed / pressure / wind_circle
    - presentation: source_label / level_key / track_summary / summary_text
    """

    typhoon_id: str
    eqsc_id: str
    name: str
    name_en: str
    display_name: str
    typhoon_type: str
    is_active: bool
    latitude: float | None
    longitude: float | None
    pressure: float | int | None
    wind_speed: float | None
    power: int | None
    move_direction: str
    move_speed: float | None
    radius7: int | None
    radius10: int | None
    wind_circle: dict[str, Any]
    updated_at: str
    updated_at_text: str
    info_type: str
    data_source: str
    source_label: str
    weather_detail: str
    history_track: list[dict[str, Any]]
    future_track: list[dict[str, Any]]
    track_summary: TyphoonTrackSummary
    level_key: str
    summary_text: str


class TyphoonQueryResult(TypedDict, total=False):
    """统一查询返回包。"""

    success: bool
    query_mode: str
    source: str
    detail: str
    data: TyphoonQueryItem
    items: list[TyphoonQueryItem]
    total: int
    error: str
    usage: list[str]
    filters: dict[str, Any]
    fallback_from: str
    eqsc_error: str


__all__ = [
    "TyphoonQueryItem",
    "TyphoonQueryResult",
    "TyphoonTrackSummary",
]
