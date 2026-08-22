"""
服务子系统导出。
统一收口配置、身份、查询、地理、展示、模拟与遥测等核心服务能力。
"""

from .config.config_service import ConfigAccessor
from .config.config_validation_service import ConfigValidator
from .config.connection_plan_builder import ConnectionPlanBuilder
from .display import (
    build_admin_statistics_projection,
    build_display_context,
    build_earthquake_summary_view,
    build_earthquake_views_from_stats,
    build_event_summary_view,
    build_event_summary_views,
    build_recent_earthquake_views,
)
from .geo.intensity_service import IntensityService
from .geo.region_service import RegionService, region_service
from .geo.weather_region_resolver import WeatherRegionResolver
from .identity.event_classifier import (
    MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD,
    MAJOR_WEATHER_LEVEL_KEYWORD,
    MAJOR_WEATHER_TEXT_PHRASES,
    is_major_event,
    is_major_record,
    is_major_weather_level,
    is_major_weather_text,
)
from .identity.event_deduplication_service import EventDeduplicationService
from .identity.event_identity import (
    EventIdentityService,
    ensure_aware_datetime,
    ensure_utc_datetime,
    infer_source_timezone,
    resolve_event_publish_time_utc,
    resolve_event_time_aware,
    resolve_event_time_utc,
    resolve_event_unique_key,
    resolve_report_num,
    resolve_source_id,
)
from .query.earthquake_list_service import EarthquakeListService
from .query.eew_query_state_service import EEWQueryStateService
from .query.source_runtime_query_service import SourceRuntimeQueryService
from .telemetry.telemetry_service import TelemetryManager

__all__ = [
    "ConfigAccessor",
    "ConfigValidator",
    "ConnectionPlanBuilder",
    "EventIdentityService",
    "EventDeduplicationService",
    "EEWQueryStateService",
    "EarthquakeListService",
    "IntensityService",
    "RegionService",
    "WeatherRegionResolver",
    "SourceRuntimeQueryService",
    "TelemetryManager",
    "build_display_context",
    "build_event_summary_view",
    "build_event_summary_views",
    "build_earthquake_summary_view",
    "build_recent_earthquake_views",
    "build_earthquake_views_from_stats",
    "build_admin_statistics_projection",
    "region_service",
    "resolve_source_id",
    "resolve_report_num",
    "infer_source_timezone",
    "ensure_aware_datetime",
    "ensure_utc_datetime",
    "resolve_event_time_aware",
    "resolve_event_time_utc",
    "resolve_event_publish_time_utc",
    "resolve_event_unique_key",
    "MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD",
    "MAJOR_WEATHER_LEVEL_KEYWORD",
    "MAJOR_WEATHER_TEXT_PHRASES",
    "is_major_event",
    "is_major_record",
    "is_major_weather_level",
    "is_major_weather_text",
]
