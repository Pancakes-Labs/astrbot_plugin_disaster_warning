"""
Web 管理端事件路由。
负责注册历史事件分页、数据源筛选与重大事件查询接口，减少 WebAdminServer 的路由定义体积。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from .....utils.time_converter import TimeConverter
from ....domain.typhoon import resolve_data_mode
from ....message.presenters.weather_alarm_code_map import (
    build_weather_icon_url,
    resolve_weather_icon_code,
)
from ....storage.source_compat import format_event_source_name
from ..payloads.api_response import ApiResponse

# 简单的基于内存的轻量级缓存，用来缓存数据源选项，降低分页查询和 sources 查询时的开销。
# 缓存有效期设为 10 秒，数据源不频繁变化但加载很频繁。
_SOURCES_CACHE_LIMIT = 10.0
_sources_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _get_cached_source_options(db, event_type: str | None) -> list[dict[str, str]]:
    """获取缓存的数据源选项，若失效则拉取最新并更新缓存。"""
    now = time.time()
    cache_key = event_type or ""
    if cache_key in _sources_cache:
        t, data = _sources_cache[cache_key]
        if now - t < _SOURCES_CACHE_LIMIT:
            return data
    return None


def _set_cached_source_options(event_type: str | None, data: list[dict[str, str]]):
    """设置数据源选项的缓存。"""
    cache_key = event_type or ""
    _sources_cache[cache_key] = (time.time(), data)


# 为了配合写入操作清除缓存，我们也提供失效函数
def invalidate_sources_cache():
    """手动失效数据源的缓存。"""
    _sources_cache.clear()


def _enrich_event_list(events: list[dict]) -> None:
    """事件列表后处理：为气象事件补充图标地址，为台风事件注入
    source_label / data_mode / _snapshot_level 展示友好字段。

    该函数原地修改 events 中的字典，供分页列表与重大事件列表共用，
    避免两处路由各自维护一套等价的后处理逻辑。
    """
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        if event_type == "weather_alarm":
            weather_type_code = str(event.get("weather_type_code") or "").strip()
            # 本地优先：p 编码经统一映射转为 11B 码
            # 内部优先返回 /weatheralarm_logo/ 静态 URL，本地文件缺失时依次回退
            # 本地通用颜色图标（fallback_{color}.png）与 Fan Studio 官方接口，
            # 避免远程接口返回伪图片导致前端无法回退。
            if weather_type_code:
                title_text = str(event.get("description") or "").strip()
                headline_text = str(event.get("subtitle") or "").strip()
                icon_code = resolve_weather_icon_code(
                    weather_type_code,
                    title=title_text,
                    headline=headline_text,
                )
                event["icon_url"] = build_weather_icon_url(
                    icon_code or weather_type_code
                )
            else:
                event["icon_url"] = None
        elif event_type == "typhoon":
            info_type = str(event.get("info_type") or "").strip()
            event["data_mode"] = resolve_data_mode(info_type, default="fan")
            event["source_label"] = format_event_source_name(
                event.get("source_id") or event.get("source") or "typhoon_fanstudio",
                event_type="typhoon",
                info_type=info_type,
            )
            # 注入当前观测等级（_snapshot_level）：
            # 数据库主表 level 存峰值，event_updates 的 level 存每次观测快照。
            # 台风的 _attach_history 保留了全部 updates（含最新一条），reversed 后
            # history[0] 即为最新观测点，其 level 就是当前观测等级。
            # 提取后从 history 中移除最新一条，避免前端 latestEvent 与 history 重复。
            history = event.get("history") or []
            if isinstance(history, list) and history:
                latest_snapshot = history[0]
                if isinstance(latest_snapshot, dict):
                    event["_snapshot_level"] = str(
                        latest_snapshot.get("level") or ""
                    ).strip()
                    # 当前报次的风速、气压、坐标也来自快照，不能继续使用主表峰值字段。
                    event["_snapshot_wind_speed"] = latest_snapshot.get("wind_speed")
                    event["_snapshot_pressure"] = latest_snapshot.get("pressure")
                    event["_snapshot_latitude"] = latest_snapshot.get("latitude")
                    event["_snapshot_longitude"] = latest_snapshot.get("longitude")
                    # 移除最新一条，避免与 latestEvent（主表行）重复展示
                    event["history"] = history[1:]
            if not event.get("_snapshot_level"):
                # 无 history 或 history 缺失 level 时回退到主表 level
                event["_snapshot_level"] = str(event.get("level") or "").strip()


def register_events_routes(app, *, disaster_service):
    """注册事件相关路由。"""

    @app.get("/api/events")
    async def get_events_paginated(
        page: int = 1,
        limit: int = 50,
        type: str = "",
        source: str = "",
        min_magnitude: float | None = None,
        magnitude_order: str = "",
        keyword: str = "",
        level_filter: str = "",
        min_wind_speed: float | None = None,
        time_from: str = "",
        time_to: str = "",
        min_depth: float | None = None,
        max_depth: float | None = None,
        min_intensity: float | None = None,
        intensity_system: str = "",
        max_pressure: float | None = None,
        active_only: bool = False,
    ):
        """分页获取历史事件记录。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success(
                    {
                        "events": [],
                        "total": 0,
                        "page": page,
                        "limit": limit,
                        "total_pages": 0,
                        "sources": [],
                        "max_limit": 200,
                    }
                )

            db = disaster_service.statistics_manager.db
            event_type = type if type else None
            # 数据源筛选支持逗号分隔，便于前端一次性组合多个来源条件。
            source_filters = [s.strip() for s in source.split(",") if s.strip()]
            max_limit = 200
            # 在接口层统一收敛分页参数，避免极端查询直接压垮数据库。
            limit = min(max(1, limit), max_limit)
            page = max(1, page)

            normalized_magnitude_order = magnitude_order.lower().strip()
            if normalized_magnitude_order not in {"", "asc", "desc"}:
                normalized_magnitude_order = ""

            normalized_keyword = keyword.strip()
            normalized_level_filter = level_filter.strip()
            normalized_time_from = str(time_from or "").strip()
            normalized_time_to = str(time_to or "").strip()
            normalized_intensity_system = str(intensity_system or "").strip().lower()
            if normalized_intensity_system not in {"", "cn", "jma"}:
                normalized_intensity_system = ""
            # 烈度/震度阈值必须绑定体系，避免 CN 与 JMA 混比。
            if min_intensity is not None and normalized_intensity_system not in {
                "cn",
                "jma",
            }:
                return ApiResponse.error(
                    "使用 min_intensity 时必须提供 intensity_system（cn 或 jma）",
                    status_code=400,
                )

            # 利用 asyncio.gather 并发查询总数与分页数据，最大化 SQLite I/O 效率
            total, events = await asyncio.gather(
                db.get_events_count(
                    event_type,
                    source_filters,
                    min_magnitude=min_magnitude,
                    keyword=normalized_keyword or None,
                    level_filter=normalized_level_filter or None,
                    min_wind_speed=min_wind_speed,
                    time_from=normalized_time_from or None,
                    time_to=normalized_time_to or None,
                    min_depth=min_depth,
                    max_depth=max_depth,
                    min_intensity=min_intensity,
                    intensity_system=normalized_intensity_system or None,
                    max_pressure=max_pressure,
                    active_only=bool(active_only),
                ),
                db.get_events_paginated(
                    page,
                    limit,
                    event_type,
                    source_filters,
                    min_magnitude=min_magnitude,
                    magnitude_order=normalized_magnitude_order or None,
                    keyword=normalized_keyword or None,
                    level_filter=normalized_level_filter or None,
                    min_wind_speed=min_wind_speed,
                    time_from=normalized_time_from or None,
                    time_to=normalized_time_to or None,
                    min_depth=min_depth,
                    max_depth=max_depth,
                    min_intensity=min_intensity,
                    intensity_system=normalized_intensity_system or None,
                    max_pressure=max_pressure,
                    active_only=bool(active_only),
                ),
            )

            # 事件列表后处理：统一调用公共函数补充图标、来源标签与台风当前观测等级
            _enrich_event_list(events)
            total_pages = (total + limit - 1) // limit if total > 0 else 0

            # 优先从缓存获取数据源列表
            source_options = _get_cached_source_options(db, event_type)
            if source_options is None:
                source_options = await db.get_event_source_options(event_type)
                _set_cached_source_options(event_type, source_options)

            available_sources = [
                item.get("source_label", "")
                for item in source_options
                if item.get("source_label")
            ]

            return ApiResponse.success(
                {
                    "events": events,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "sources": available_sources,
                    "source_options": source_options,
                    "max_limit": max_limit,
                }
            )
        except Exception as e:
            logger.error(f"[灾害预警] 分页获取事件失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/events/sources")
    async def get_event_sources(type: str = ""):
        """获取可筛选的数据源列表。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success({"sources": []})

            db = disaster_service.statistics_manager.db
            event_type = type if type else None

            # 优先从缓存获取数据源列表
            source_options = _get_cached_source_options(db, event_type)
            if source_options is None:
                source_options = await db.get_event_source_options(event_type)
                _set_cached_source_options(event_type, source_options)

            sources = [
                item.get("source_label", "")
                for item in source_options
                if item.get("source_label")
            ]
            return ApiResponse.success(
                {"sources": sources, "source_options": source_options}
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取数据源列表失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/events/major")
    async def get_major_events(limit: int = 50):
        """获取重大事件列表。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success({"events": []})

            stats_manager = disaster_service.statistics_manager
            db = stats_manager.db
            # 统一钳制查询上限，避免 limit<=0 绕过 500 条保护导致全表扫描。
            if limit <= 0:
                safe_limit = 50
            else:
                safe_limit = min(max(1, int(limit)), 500)

            events = await db.get_major_events(safe_limit)

            # S-Net 峰值重大条目（震度 >= 5弱）由峰值服务单独注入，不进通用 events 表。
            peak_service = getattr(stats_manager, "snet_peak_service", None)
            if peak_service is not None:
                try:
                    snet_events = await peak_service.list_major_peak_events(
                        limit=safe_limit
                    )
                    if snet_events:
                        events.extend(snet_events)

                        def _major_event_sort_key(item: dict[str, Any]):
                            parsed = TimeConverter.parse_datetime(item.get("time"))
                            if parsed is None:
                                parsed = TimeConverter.parse_datetime(
                                    item.get("updated_at")
                                )
                            ts = parsed.timestamp() if parsed is not None else 0.0
                            raw_id = item.get("id") or 0
                            try:
                                id_part = int(raw_id)
                            except (TypeError, ValueError):
                                id_part = 0
                            return (ts, id_part)

                        events.sort(key=_major_event_sort_key, reverse=True)
                        if limit > 0:
                            events = events[:safe_limit]
                except Exception as snet_exc:
                    logger.debug(f"[灾害预警] 注入 S-Net 重大峰值失败: {snet_exc}")

            # 重大事件列表同样需要台风后处理（图标、来源标签、当前观测等级）
            _enrich_event_list(events)
            return ApiResponse.success({"events": events})
        except Exception as e:
            logger.error(f"[灾害预警] 获取重大事件失败: {e}")
            return ApiResponse.error(str(e), status_code=500)
