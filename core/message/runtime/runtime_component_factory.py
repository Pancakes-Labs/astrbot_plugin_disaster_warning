"""
消息运行时组件工厂。
负责根据全局配置或会话级运行时配置构建过滤器、报数控制器等推送策略依赖，
减少 MessagePushManager 中的配置解释与对象装配职责。
"""

from __future__ import annotations

from typing import Any

from ...services.snet.snet_filter_constants import (
    normalize_combine_mode,
    normalize_min_shindo,
    normalize_min_triggered_stations,
    normalize_station_min_shindo,
)
from .local_monitor import LocalMonitor


class MessageRuntimeComponentFactory:
    """消息运行时组件工厂。"""

    def __init__(self):
        # 当前工厂无须持久状态，保留构造函数是为了统一实例化入口。
        pass

    @staticmethod
    def _build_keyword_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建关键词过滤配置。"""
        keyword_filter_config = earthquake_filters.get("keyword_filter", {})
        return {
            "enabled": keyword_filter_config.get("enabled", False),
            "blacklist": list(keyword_filter_config.get("blacklist", [])),
            "whitelist": list(keyword_filter_config.get("whitelist", [])),
        }

    @staticmethod
    def _build_intensity_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建烈度过滤配置。"""
        intensity_filter_config = earthquake_filters.get("intensity_filter", {})
        return {
            "enabled": intensity_filter_config.get("enabled", True),
            "combine_mode": normalize_combine_mode(
                intensity_filter_config.get("combine_mode")
            ),
            "min_magnitude": intensity_filter_config.get("min_magnitude", 2.0),
            "min_intensity": intensity_filter_config.get("min_intensity", 4.0),
        }

    @staticmethod
    def _build_scale_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建震度等级过滤配置。"""
        scale_filter_config = earthquake_filters.get("scale_filter", {})
        return {
            "enabled": scale_filter_config.get("enabled", True),
            "combine_mode": normalize_combine_mode(
                scale_filter_config.get("combine_mode")
            ),
            "min_magnitude": scale_filter_config.get("min_magnitude", 2.0),
            "min_scale": scale_filter_config.get("min_scale", 1.0),
        }

    @staticmethod
    def _build_magnitude_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建仅震级阈值过滤配置。"""
        magnitude_only_filter_config = earthquake_filters.get(
            "magnitude_only_filter", {}
        )
        return {
            "enabled": magnitude_only_filter_config.get("enabled", True),
            "min_magnitude": magnitude_only_filter_config.get("min_magnitude", 4.5),
        }

    @staticmethod
    def _build_global_quake_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建 Global Quake 专用过滤配置。"""
        global_quake_filter_config = earthquake_filters.get("global_quake_filter", {})
        return {
            "enabled": global_quake_filter_config.get("enabled", True),
            "combine_mode": normalize_combine_mode(
                global_quake_filter_config.get("combine_mode")
            ),
            "min_magnitude": global_quake_filter_config.get("min_magnitude", 4.5),
            "min_intensity": global_quake_filter_config.get("min_intensity", 5.0),
        }

    @staticmethod
    def _build_snet_filter_config(
        earthquake_filters: dict[str, Any],
    ) -> dict[str, Any]:
        """构建 S-Net 海底震度过滤配置。"""
        snet_filter_config = earthquake_filters.get("snet_filter", {})
        if not isinstance(snet_filter_config, dict):
            snet_filter_config = {}
        # 兼容旧字段 min_magnitude -> min_shindo
        min_shindo = snet_filter_config.get("min_shindo")
        if min_shindo is None and "min_magnitude" in snet_filter_config:
            min_shindo = snet_filter_config.get("min_magnitude")

        return {
            "enabled": snet_filter_config.get("enabled", True),
            "combine_mode": normalize_combine_mode(
                snet_filter_config.get("combine_mode")
            ),
            "min_shindo": normalize_min_shindo(min_shindo),
            "station_min_shindo": normalize_station_min_shindo(
                snet_filter_config.get("station_min_shindo")
            ),
            "min_triggered_stations": normalize_min_triggered_stations(
                snet_filter_config.get("min_triggered_stations")
            ),
        }

    @staticmethod
    def _build_local_monitor(runtime_config: dict[str, Any]) -> LocalMonitor:
        """构建本地监控组件。"""
        return LocalMonitor(runtime_config.get("local_monitoring", {}))

    @staticmethod
    def _build_weather_filter_config(
        runtime_config: dict[str, Any],
        *,
        emit_enable_log: bool,
    ) -> dict[str, Any]:
        top_level_weather_filter = runtime_config.get("weather_filter", {})
        weather_config = runtime_config.get("weather_config", {})
        nested_weather_filter = (
            weather_config.get("weather_filter", {})
            if isinstance(weather_config, dict)
            else {}
        )

        weather_filter_config: dict[str, Any] = {}
        if isinstance(top_level_weather_filter, dict):
            weather_filter_config.update(top_level_weather_filter)
        if isinstance(nested_weather_filter, dict):
            weather_filter_config.update(nested_weather_filter)

        legacy_provinces = weather_filter_config.get("provinces")
        if not isinstance(legacy_provinces, list):
            legacy_provinces = []
        else:
            legacy_provinces = [
                str(item).strip() for item in legacy_provinces if str(item).strip()
            ]
        weather_filter_config["provinces"] = legacy_provinces

        keywords = weather_filter_config.get("keywords")
        if not isinstance(keywords, list):
            keywords = []
        else:
            keywords = [str(item).strip() for item in keywords if str(item).strip()]
        weather_filter_config["keywords"] = keywords

        weather_filter_config["emit_enable_log"] = emit_enable_log
        return weather_filter_config

    @staticmethod
    def _build_typhoon_filter_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
        """构建台风过滤配置，供 TyphoonRule 读取。"""
        top_level_typhoon_filter = runtime_config.get("typhoon_filter", {})
        typhoon_config = runtime_config.get("typhoon_config", {})
        nested_typhoon_filter = (
            typhoon_config.get("typhoon_filter", {})
            if isinstance(typhoon_config, dict)
            else {}
        )

        typhoon_filter_config: dict[str, Any] = {}
        if isinstance(top_level_typhoon_filter, dict):
            typhoon_filter_config.update(top_level_typhoon_filter)
        if isinstance(nested_typhoon_filter, dict):
            typhoon_filter_config.update(nested_typhoon_filter)

        # 规范化名称名单
        for key in ("name_whitelist", "name_blacklist"):
            raw_list = typhoon_filter_config.get(key)
            if not isinstance(raw_list, list):
                typhoon_filter_config[key] = []
            else:
                typhoon_filter_config[key] = [
                    str(item).strip() for item in raw_list if str(item).strip()
                ]

        # 规范化嵌套过滤器
        distance_filter = typhoon_filter_config.get("distance_filter")
        if not isinstance(distance_filter, dict):
            typhoon_filter_config["distance_filter"] = {}
        approach_filter = typhoon_filter_config.get("approach_filter")
        if not isinstance(approach_filter, dict):
            typhoon_filter_config["approach_filter"] = {}

        # 组合方式默认与地震类过滤器保持一致：OR
        combine_mode = str(typhoon_filter_config.get("combine_mode") or "any").strip()
        if combine_mode not in {"all", "any"}:
            combine_mode = "any"
        typhoon_filter_config["combine_mode"] = combine_mode
        return typhoon_filter_config

    @staticmethod
    def _build_tsunami_config(runtime_config: dict[str, Any]) -> dict[str, Any]:
        """构建海啸过滤配置，供 TsunamiRule 读取。"""
        tsunami_config = runtime_config.get("tsunami_config", {})
        if not isinstance(tsunami_config, dict):
            tsunami_config = {}

        result: dict[str, Any] = {}
        for key, default_min in (
            ("china_filter", "信息"),
            ("japan_filter", "若干海面变动"),
        ):
            block = tsunami_config.get(key)
            if not isinstance(block, dict):
                block = {}
            result[key] = {
                "enabled": bool(block.get("enabled", False)),
                "min_level": str(block.get("min_level") or default_min).strip()
                or default_min,
            }
        return result

    @staticmethod
    def build_shared_components(
        runtime_config: dict[str, Any],
        *,
        emit_weather_enable_log: bool = False,
    ) -> dict[str, Any]:
        """构建与会话无关的共享过滤组件，供初始化与运行时复用。"""
        earthquake_filters = runtime_config.get("earthquake_filters", {})
        return {
            "keyword_filter": MessageRuntimeComponentFactory._build_keyword_filter_config(
                earthquake_filters
            ),
            "intensity_filter": MessageRuntimeComponentFactory._build_intensity_filter_config(
                earthquake_filters
            ),
            "scale_filter": MessageRuntimeComponentFactory._build_scale_filter_config(
                earthquake_filters
            ),
            "magnitude_filter": MessageRuntimeComponentFactory._build_magnitude_filter_config(
                earthquake_filters
            ),
            "global_quake_filter": MessageRuntimeComponentFactory._build_global_quake_filter_config(
                earthquake_filters
            ),
            "snet_filter": MessageRuntimeComponentFactory._build_snet_filter_config(
                earthquake_filters
            ),
            "local_monitor": MessageRuntimeComponentFactory._build_local_monitor(
                runtime_config
            ),
            "weather_filter": MessageRuntimeComponentFactory._build_weather_filter_config(
                runtime_config,
                emit_enable_log=emit_weather_enable_log,
            ),
            "typhoon_filter": MessageRuntimeComponentFactory._build_typhoon_filter_config(
                runtime_config
            ),
            "tsunami_config": MessageRuntimeComponentFactory._build_tsunami_config(
                runtime_config
            ),
        }

    def build(
        self,
        runtime_config: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """基于运行时配置构建过滤组件（支持会话级配置）。"""
        del session_id
        return self.build_shared_components(runtime_config)
