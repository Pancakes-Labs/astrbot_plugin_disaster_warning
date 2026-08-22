"""
插件辅助工具子包。
包含 IP 物理定位（GeoIP）、地图瓦片 URL 映射、时区转换格式化、emoji过滤、
统一等级视觉指示器、版本识别探测以及烈度制式转换等基础通用库。
"""

from .converters import ScaleConverter
from .emoji_filter import (
    EMOJI_FILTER_MODE_DEFAULT,
    EMOJI_FILTER_MODE_MINIMAL,
    EMOJI_FILTER_MODE_OFF,
    filter_push_text_emoji,
    is_known_emoji_filter_mode,
    normalize_emoji_filter_mode,
)
from .geolocation import close_geoip_session, fetch_location_from_ip, get_geoip_session
from .map_tile_sources import get_tile_url, get_tile_url_js, normalize_map_source
from .severity_emoji import (
    SEVERITY_INDICATOR_EMOJIS,
    aqi_level_emoji,
    cn_tsunami_level_emoji,
    intensity_level_emoji,
    rank_level_emoji,
    typhoon_level_emoji,
)
from .time_converter import TimeConverter
from .version import get_astrbot_version, get_astrbot_version_info, get_plugin_version

__all__ = [
    "ScaleConverter",
    "EMOJI_FILTER_MODE_DEFAULT",
    "EMOJI_FILTER_MODE_MINIMAL",
    "EMOJI_FILTER_MODE_OFF",
    "filter_push_text_emoji",
    "is_known_emoji_filter_mode",
    "normalize_emoji_filter_mode",
    "close_geoip_session",
    "fetch_location_from_ip",
    "get_geoip_session",
    "get_tile_url",
    "get_tile_url_js",
    "normalize_map_source",
    "SEVERITY_INDICATOR_EMOJIS",
    "aqi_level_emoji",
    "cn_tsunami_level_emoji",
    "intensity_level_emoji",
    "rank_level_emoji",
    "typhoon_level_emoji",
    "TimeConverter",
    "get_astrbot_version",
    "get_astrbot_version_info",
    "get_plugin_version",
]
