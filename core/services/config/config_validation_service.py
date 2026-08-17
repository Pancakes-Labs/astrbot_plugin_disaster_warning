"""
配置校验服务主入口。
负责对插件配置进行统一的合法性校验、范围修正和默认值填充。
"""

from typing import Any

from astrbot.api import logger

from ....utils.emoji_filter import (
    EMOJI_FILTER_MODE_DEFAULT,
    is_known_emoji_filter_mode,
    normalize_emoji_filter_mode,
)
from ....utils.map_tile_sources import (
    MAP_SOURCE_NAME_TO_ID,
    MAP_TILE_SOURCES,
    normalize_map_source,
)
from ..snet.snet_filter_constants import (
    DEFAULT_MIN_SHINDO,
    DEFAULT_MIN_TRIGGERED_STATIONS,
    DEFAULT_STATION_MIN_SHINDO,
    MIN_TRIGGERED_STATIONS_MAX,
    MIN_TRIGGERED_STATIONS_MIN,
    SHINDO_MAX,
    SHINDO_MIN,
    normalize_combine_mode,
    normalize_min_shindo,
    normalize_min_triggered_stations,
    normalize_station_min_shindo,
)


class ConfigValidator:
    """
    配置校验器。
    负责对插件配置进行统一的合法性校验、范围修正和默认值填充，
    作为运行时读取配置前的最后一道结构整理入口。
    """

    @staticmethod
    def validate(config: dict[str, Any]) -> dict[str, Any]:
        """
        执行全部配置校验流程。

        参数说明：
        - config：原始配置字典

        返回值：
        - 校验并修正后的配置字典
        """
        # 按配置分组依次处理，确保每一类配置都在进入运行态前完成基础修正。

        # 1. 本地监控配置校验
        if "local_monitoring" in config:
            config["local_monitoring"] = ConfigValidator._validate_local_monitoring(
                config["local_monitoring"]
            )

        # 2. WebSocket 配置校验
        if "websocket_config" in config:
            config["websocket_config"] = ConfigValidator._validate_websocket_config(
                config["websocket_config"]
            )

        # 3. Web 管理端配置校验
        if "web_admin" in config:
            config["web_admin"] = ConfigValidator._validate_web_admin(
                config["web_admin"]
            )

        # 4. 策略配置校验
        if "strategies" in config:
            config["strategies"] = ConfigValidator._validate_strategies(
                config["strategies"]
            )

        # 5. 过滤器配置校验
        if "earthquake_filters" in config:
            config["earthquake_filters"] = ConfigValidator._validate_earthquake_filters(
                config["earthquake_filters"]
            )

        # 6. 气象配置校验
        if "weather_config" in config:
            config["weather_config"] = ConfigValidator._validate_weather_config(
                config["weather_config"]
            )

        # 7 台风配置校验
        if "typhoon_config" in config:
            config["typhoon_config"] = ConfigValidator._validate_typhoon_config(
                config["typhoon_config"]
            )

        # 8 海啸配置校验
        if "tsunami_config" in config:
            config["tsunami_config"] = ConfigValidator._validate_tsunami_config(
                config["tsunami_config"]
            )

        # 9. 调试配置校验
        if "debug_config" in config:
            config["debug_config"] = ConfigValidator._validate_debug_config(
                config["debug_config"]
            )

        # 10. 推送列表校验
        if "target_sessions" in config:
            config["target_sessions"] = ConfigValidator._validate_target_sessions(
                config["target_sessions"], key_name="target_sessions"
            )

        # 11. 离线通知会话列表校验
        if "offline_notification_sessions" in config:
            config["offline_notification_sessions"] = (
                ConfigValidator._validate_target_sessions(
                    config["offline_notification_sessions"],
                    key_name="offline_notification_sessions",
                )
            )

        # 12. 管理员列表校验
        if "admin_users" in config:
            config["admin_users"] = ConfigValidator._validate_admin_users(
                config["admin_users"]
            )

        # 13. 消息格式配置校验
        if "message_format" in config:
            config["message_format"] = ConfigValidator._validate_message_format(
                config["message_format"]
            )

        # 14. 推送频率控制校验
        if "push_frequency_control" in config:
            config["push_frequency_control"] = ConfigValidator._validate_push_frequency(
                config["push_frequency_control"]
            )

        # 15. 时区配置校验
        if "display_timezone" in config:
            config["display_timezone"] = ConfigValidator._validate_timezone(
                config["display_timezone"]
            )

        # 16. 遥测配置校验
        if "telemetry_config" in config:
            config["telemetry_config"] = ConfigValidator._validate_telemetry(
                config["telemetry_config"]
            )

        # 17. 数据源配置结构校验
        if "data_sources" in config:
            config["data_sources"] = ConfigValidator._validate_data_sources(
                config["data_sources"]
            )

        # 18. 通知中心配置校验
        if "notification_settings" in config:
            config["notification_settings"] = (
                ConfigValidator._validate_notification_settings(
                    config["notification_settings"]
                )
            )

        # 19. 顶层开关校验
        if "enabled" in config and not isinstance(config["enabled"], bool):
            config["enabled"] = True
        logger.debug("[灾害预警] 配置校验完成")
        return config

    @staticmethod
    def _ensure_bool(cfg: dict[str, Any], key: str, default: bool = False):
        """确保指定配置项最终为布尔值。"""
        if key in cfg and not isinstance(cfg[key], bool):
            logger.warning(
                f"[灾害预警] 配置警告: '{key}' 类型错误 (应为 bool)，已重置为 {default}。"
            )
            cfg[key] = default

    @staticmethod
    def _validate_local_monitoring(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验本地监控配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 经纬度校验：防止经纬度配置越界导致地图定位或烈度估算崩溃
        lat = cfg.get("latitude")
        if isinstance(lat, (int, float)):
            if lat < -90 or lat > 90:
                logger.warning(
                    f"[灾害预警] 配置警告: 纬度 {lat} 超出范围 (-90~90)，已自动修正。"
                )
                # 限制纬度在 -90.0 至 90.0 度区间内
                cfg["latitude"] = max(-90.0, min(90.0, float(lat)))

        lon = cfg.get("longitude")
        if isinstance(lon, (int, float)):
            if lon < -180 or lon > 180:
                logger.warning(
                    f"[灾害预警] 配置警告: 经度 {lon} 超出范围 (-180~180)，已自动修正。"
                )
                # 限制经度在 -180.0 至 180.0 度区间内
                cfg["longitude"] = max(-180.0, min(180.0, float(lon)))

        # 阈值校验：本地预计烈度报警下限阈值（中国烈度体系）
        threshold = cfg.get("intensity_threshold")
        if isinstance(threshold, (int, float)):
            if threshold < 0 or threshold > 12:
                logger.warning(
                    f"[灾害预警] 配置警告: 烈度阈值 {threshold} 超出范围 (0~12)，已自动修正。"
                )
                # 烈度通常在 0 至 12 级范围内
                cfg["intensity_threshold"] = max(0.0, min(12.0, float(threshold)))

        # 阈值校验：本地预计震度报警下限阈值（日本震度体系，計測震度 0~7）
        shindo_threshold = cfg.get("shindo_threshold")
        if isinstance(shindo_threshold, (int, float)):
            if shindo_threshold < 0 or shindo_threshold > 7:
                logger.warning(
                    f"[灾害预警] 配置警告: 震度阈值 {shindo_threshold} 超出范围 (0~7)，已自动修正。"
                )
                # 計測震度通常在 0 至 7 档范围内（震度 7 为最高档）
                cfg["shindo_threshold"] = max(0.0, min(7.0, float(shindo_threshold)))

        # 地名校验：确保本地监控参考地名为字符串
        if "place_name" in cfg and not isinstance(cfg["place_name"], str):
            cfg["place_name"] = str(cfg["place_name"])

        # 布尔值校验：校验本地预计烈度监控开关及严格模式开关
        ConfigValidator._ensure_bool(cfg, "enabled", False)
        ConfigValidator._ensure_bool(cfg, "strict_mode", False)

        # 强度体系校验：兼容中英文别名，统一规范化为英文内部值
        # 中文别名用于配置页下拉展示（自动判定/中国烈度/日本震度）
        system_map = {
            "auto": "auto",
            "cenc": "cenc",
            "jma": "jma",
            "自动判定": "auto",
            "中国烈度": "cenc",
            "日本震度": "jma",
        }
        raw_system = cfg.get("intensity_system")
        if raw_system is not None:
            normalized = system_map.get(str(raw_system).strip())
            if normalized is None:
                logger.warning(
                    f"[灾害预警] 配置警告: 本地强度体系 {raw_system!r} 非法，已重置为自动判定。"
                )
                cfg["intensity_system"] = "auto"
            else:
                cfg["intensity_system"] = normalized

        return cfg

    @staticmethod
    def _validate_websocket_config(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验 WebSocket 配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 重连间隔
        interval = cfg.get("reconnect_interval")
        if isinstance(interval, (int, float)):
            if interval < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: 重连间隔 {interval} 过小，已修正为 1 秒。"
                )
                cfg["reconnect_interval"] = 1
            elif interval > 60:
                logger.warning(
                    f"[灾害预警] 配置警告: 重连间隔 {interval} 过大，已修正为 60 秒。"
                )
                cfg["reconnect_interval"] = 60

        # 最大重连次数
        max_retries = cfg.get("max_reconnect_retries")
        if isinstance(max_retries, int):
            if max_retries < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: 最大重连次数 {max_retries} 过小，已修正为 1。"
                )
                cfg["max_reconnect_retries"] = 1
            elif max_retries > 10:
                logger.warning(
                    f"[灾害预警] 配置警告: 最大重连次数 {max_retries} 过大，已修正为 10。"
                )
                cfg["max_reconnect_retries"] = 10

        # 超时时间
        timeout = cfg.get("connection_timeout")
        if isinstance(timeout, (int, float)):
            if timeout < 5:
                logger.warning(
                    f"[灾害预警] 配置警告: 连接超时 {timeout} 过小，已修正为 5 秒。"
                )
                cfg["connection_timeout"] = 5
            elif timeout > 120:
                logger.warning(
                    f"[灾害预警] 配置警告: 连接超时 {timeout} 过大，已修正为 120 秒。"
                )
                cfg["connection_timeout"] = 120

        # 心跳间隔
        heartbeat = cfg.get("heartbeat_interval")
        if isinstance(heartbeat, (int, float)):
            if heartbeat < 10:
                logger.warning(
                    f"[灾害预警] 配置警告: 心跳间隔 {heartbeat} 过小，已修正为 10 秒。"
                )
                cfg["heartbeat_interval"] = 10
            elif heartbeat > 600:
                logger.warning(
                    f"[灾害预警] 配置警告: 心跳间隔 {heartbeat} 过大，已修正为 600 秒。"
                )
                cfg["heartbeat_interval"] = 600

        # 兜底重试间隔
        fallback_interval = cfg.get("fallback_retry_interval")
        if isinstance(fallback_interval, int):
            if fallback_interval < 300:
                logger.warning(
                    f"[灾害预警] 配置警告: 兜底重试间隔 {fallback_interval} 过小，已修正为 300 秒。"
                )
                cfg["fallback_retry_interval"] = 300
            elif fallback_interval > 86400:
                logger.warning(
                    f"[灾害预警] 配置警告: 兜底重试间隔 {fallback_interval} 过大，已修正为 86400 秒。"
                )
                cfg["fallback_retry_interval"] = 86400

        # 兜底重试最大次数
        fallback_count = cfg.get("fallback_retry_max_count")
        if isinstance(fallback_count, int):
            if fallback_count < -1:
                logger.warning(
                    f"[灾害预警] 配置警告: 兜底重试最大次数 {fallback_count} 无效，已修正为 -1 (无限)。"
                )
                cfg["fallback_retry_max_count"] = -1
            elif fallback_count > 100:
                logger.warning(
                    f"[灾害预警] 配置警告: 兜底重试最大次数 {fallback_count} 过大，已修正为 100。"
                )
                cfg["fallback_retry_max_count"] = 100

        # 布尔值校验
        ConfigValidator._ensure_bool(cfg, "fallback_retry_enabled", True)

        return cfg

    @staticmethod
    def _validate_web_admin(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验 Web 管理端配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 端口校验
        port = cfg.get("port")
        if isinstance(port, int):
            if port < 1 or port > 65535:
                logger.warning(
                    f"[灾害预警] 配置警告: Web端口 {port} 无效，已重置为默认值 8089。"
                )
                cfg["port"] = 8089
            elif port < 1024:
                logger.warning(
                    f"[灾害预警] 配置提示: Web端口 {port} 为特权端口，请确保有足够权限。"
                )

        # Host 校验
        if "host" in cfg and not isinstance(cfg["host"], str):
            logger.warning(
                "[灾害预警] 配置警告: Web Host 类型错误，已重置为 '0.0.0.0'。"
            )
            cfg["host"] = "0.0.0.0"

        # 密码校验：确保为字符串类型
        if "password" in cfg and not isinstance(cfg["password"], str):
            logger.warning(
                "[灾害预警] 配置警告: Web 管理端密码类型错误，已重置为空字符串。"
            )
            cfg["password"] = ""

        # 布尔值校验
        ConfigValidator._ensure_bool(cfg, "enabled", False)

        return cfg

    @staticmethod
    def _validate_strategies(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验策略配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # CENC 融合策略超时
        cenc_fusion = cfg.get("cenc_fusion", {})
        if isinstance(cenc_fusion, dict):
            timeout = cenc_fusion.get("timeout")
            if isinstance(timeout, (int, float)):
                if timeout < 1:
                    logger.warning(
                        f"[灾害预警] 配置警告: CENC 融合策略超时 {timeout} 过小，已修正为 1 秒。"
                    )
                    cenc_fusion["timeout"] = 1
                elif timeout > 60:
                    logger.warning(
                        f"[灾害预警] 配置警告: CENC 融合策略超时 {timeout} 过大，已修正为 60 秒。"
                    )
                    cenc_fusion["timeout"] = 60

            # 未配置 FAN 鉴权时默认关闭融合；已有显式 true 配置不强制改写。
            if "enabled" not in cenc_fusion:
                cenc_fusion["enabled"] = False
            ConfigValidator._ensure_bool(cenc_fusion, "enabled", False)
            cfg["cenc_fusion"] = cenc_fusion

        # CWA EEW 最大震度融合策略超时
        cwa_eew_fusion = cfg.get("cwa_eew_fusion", {})
        if isinstance(cwa_eew_fusion, dict):
            timeout = cwa_eew_fusion.get("timeout")
            if isinstance(timeout, (int, float)):
                if timeout < 1:
                    logger.warning(
                        f"[灾害预警] 配置警告: CWA EEW 最大震度融合策略超时 {timeout} 过小，已修正为 1 秒。"
                    )
                    cwa_eew_fusion["timeout"] = 1
                elif timeout > 60:
                    logger.warning(
                        f"[灾害预警] 配置警告: CWA EEW 最大震度融合策略超时 {timeout} 过大，已修正为 60 秒。"
                    )
                    cwa_eew_fusion["timeout"] = 60

            if "enabled" not in cwa_eew_fusion:
                cwa_eew_fusion["enabled"] = False
            ConfigValidator._ensure_bool(cwa_eew_fusion, "enabled", False)
            cfg["cwa_eew_fusion"] = cwa_eew_fusion

        return cfg

    @staticmethod
    def _validate_earthquake_filters(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验地震过滤器配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 1. 关键词过滤器
        keyword_filter = cfg.get("keyword_filter", {})
        if isinstance(keyword_filter, dict):
            if not isinstance(keyword_filter.get("blacklist"), list):
                keyword_filter["blacklist"] = []
            if not isinstance(keyword_filter.get("whitelist"), list):
                keyword_filter["whitelist"] = []
            ConfigValidator._ensure_bool(keyword_filter, "enabled", False)
            cfg["keyword_filter"] = keyword_filter

        # 助手方法：校验 combine_mode
        def _validate_combine_mode(filter_dict: dict, name: str) -> None:
            raw_mode = filter_dict.get("combine_mode")
            mode = normalize_combine_mode(raw_mode)
            if raw_mode is not None and str(raw_mode).strip().lower() != mode:
                logger.warning(
                    f"[灾害预警] 配置警告: {name} 组合方式 {raw_mode} 无效，已重置为 {mode}。"
                )
            filter_dict["combine_mode"] = mode

        # 2. 烈度过滤器
        intensity_filter = cfg.get("intensity_filter", {})
        if isinstance(intensity_filter, dict):
            min_mag = intensity_filter.get("min_magnitude")
            if isinstance(min_mag, (int, float)) and (min_mag < 0 or min_mag > 10):
                logger.warning(
                    f"[灾害预警] 配置警告: 烈度过滤器最小震级 {min_mag} 超出常规范围，已修正。"
                )
                intensity_filter["min_magnitude"] = max(0.0, min(10.0, float(min_mag)))

            min_int = intensity_filter.get("min_intensity")
            if isinstance(min_int, (int, float)) and (min_int < 0 or min_int > 12):
                logger.warning(
                    f"[灾害预警] 配置警告: 烈度过滤器最小烈度 {min_int} 超出范围，已修正。"
                )
                intensity_filter["min_intensity"] = max(0.0, min(12.0, float(min_int)))

            _validate_combine_mode(intensity_filter, "烈度过滤器")
            ConfigValidator._ensure_bool(intensity_filter, "enabled", True)
            cfg["intensity_filter"] = intensity_filter

        # 3. 震度过滤器 (Scale Filter)
        scale_filter = cfg.get("scale_filter", {})
        if isinstance(scale_filter, dict):
            min_mag = scale_filter.get("min_magnitude")
            if isinstance(min_mag, (int, float)) and (min_mag < 0 or min_mag > 10):
                logger.warning(
                    f"[灾害预警] 配置警告: 震度过滤器最小震级 {min_mag} 超出常规范围，已修正。"
                )
                scale_filter["min_magnitude"] = max(0.0, min(10.0, float(min_mag)))

            min_scale = scale_filter.get("min_scale")
            if isinstance(min_scale, (int, float)) and (min_scale < 0 or min_scale > 7):
                logger.warning(
                    f"[灾害预警] 配置警告: 震度过滤器最小震度 {min_scale} 超出范围 (0-7)，已修正。"
                )
                scale_filter["min_scale"] = max(0.0, min(7.0, float(min_scale)))

            _validate_combine_mode(scale_filter, "震度过滤器")
            ConfigValidator._ensure_bool(scale_filter, "enabled", True)
            cfg["scale_filter"] = scale_filter

        # 4 S-Net 海底震度过滤器
        snet_filter = cfg.get("snet_filter", {})
        if isinstance(snet_filter, dict):
            # 兼容旧字段 min_magnitude -> min_shindo
            if "min_shindo" not in snet_filter and "min_magnitude" in snet_filter:
                snet_filter["min_shindo"] = snet_filter.get("min_magnitude")

            min_shindo = snet_filter.get("min_shindo")
            if isinstance(min_shindo, (int, float)):
                if min_shindo < SHINDO_MIN or min_shindo > SHINDO_MAX:
                    logger.warning(
                        f"[灾害预警] 配置警告: S-Net 最小震度 {min_shindo} 超出范围 "
                        f"({SHINDO_MIN:g}~{SHINDO_MAX:g})，已修正。"
                    )
                snet_filter["min_shindo"] = normalize_min_shindo(min_shindo)
            elif min_shindo is not None:
                logger.warning(
                    f"[灾害预警] 配置警告: S-Net 最小震度类型错误，已重置为 {DEFAULT_MIN_SHINDO}。"
                )
                snet_filter["min_shindo"] = DEFAULT_MIN_SHINDO

            # 站数测站震度阈值
            st_shindo = snet_filter.get("station_min_shindo")
            if isinstance(st_shindo, (int, float)):
                if st_shindo < SHINDO_MIN or st_shindo > SHINDO_MAX:
                    logger.warning(
                        f"[灾害预警] 配置警告: S-Net 测站最小震度 {st_shindo} 超出范围 "
                        f"({SHINDO_MIN:g}~{SHINDO_MAX:g})，已修正。"
                    )
                snet_filter["station_min_shindo"] = normalize_station_min_shindo(
                    st_shindo
                )
            elif st_shindo is not None:
                logger.warning(
                    f"[灾害预警] 配置警告: S-Net 测站最小震度类型错误，已重置为 {DEFAULT_STATION_MIN_SHINDO}。"
                )
                snet_filter["station_min_shindo"] = DEFAULT_STATION_MIN_SHINDO

            # 最小触发测站数（上限与 schema 统一为 156）
            min_st = snet_filter.get("min_triggered_stations")
            if isinstance(min_st, int):
                if (
                    min_st < MIN_TRIGGERED_STATIONS_MIN
                    or min_st > MIN_TRIGGERED_STATIONS_MAX
                ):
                    logger.warning(
                        f"[灾害预警] 配置警告: S-Net 最小触发测站数 {min_st} 超出范围 "
                        f"({MIN_TRIGGERED_STATIONS_MIN}-{MIN_TRIGGERED_STATIONS_MAX})，已修正。"
                    )
                snet_filter["min_triggered_stations"] = (
                    normalize_min_triggered_stations(min_st)
                )
            elif min_st is not None:
                logger.warning(
                    f"[灾害预警] 配置警告: S-Net 最小触发测站数类型错误，已重置为 {DEFAULT_MIN_TRIGGERED_STATIONS}。"
                )
                snet_filter["min_triggered_stations"] = DEFAULT_MIN_TRIGGERED_STATIONS

            _validate_combine_mode(snet_filter, "S-Net过滤器")
            ConfigValidator._ensure_bool(snet_filter, "enabled", True)
            cfg["snet_filter"] = snet_filter

        # 5. 震级过滤器（配置键 magnitude_only_filter，适用于 USGS / ShakeAlert 等）
        mag_filter = cfg.get("magnitude_only_filter", {})
        if isinstance(mag_filter, dict):
            min_mag = mag_filter.get("min_magnitude")
            if isinstance(min_mag, (int, float)) and (min_mag < 0 or min_mag > 10):
                logger.warning(
                    f"[灾害预警] 配置警告: 震级过滤器最小震级 {min_mag} 超出常规范围，已修正。"
                )
                mag_filter["min_magnitude"] = max(0.0, min(10.0, float(min_mag)))

            ConfigValidator._ensure_bool(mag_filter, "enabled", True)
            cfg["magnitude_only_filter"] = mag_filter

        # 6. Global Quake 过滤器
        gq_filter = cfg.get("global_quake_filter", {})
        if isinstance(gq_filter, dict):
            min_mag = gq_filter.get("min_magnitude")
            if isinstance(min_mag, (int, float)) and (min_mag < 0 or min_mag > 10):
                logger.warning(
                    f"[灾害预警] 配置警告: GQ过滤器最小震级 {min_mag} 超出常规范围，已修正。"
                )
                gq_filter["min_magnitude"] = max(0.0, min(10.0, float(min_mag)))

            min_int = gq_filter.get("min_intensity")
            if isinstance(min_int, (int, float)) and (min_int < 0 or min_int > 12):
                logger.warning(
                    f"[灾害预警] 配置警告: GQ过滤器最小烈度 {min_int} 超出范围，已修正。"
                )
                gq_filter["min_intensity"] = max(0.0, min(12.0, float(min_int)))

            _validate_combine_mode(gq_filter, "Global Quake过滤器")
            ConfigValidator._ensure_bool(gq_filter, "enabled", True)
            cfg["global_quake_filter"] = gq_filter

        return cfg

    @staticmethod
    def _validate_weather_config(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验气象配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 气象过滤器
        weather_filter = cfg.get("weather_filter", {})
        if isinstance(weather_filter, dict):
            # 新字段：keywords
            if "keywords" in weather_filter and not isinstance(
                weather_filter.get("keywords"), list
            ):
                weather_filter["keywords"] = []

            # 兼容旧字段：provinces
            if "provinces" in weather_filter and not isinstance(
                weather_filter.get("provinces"), list
            ):
                weather_filter["provinces"] = []

            min_level = weather_filter.get("min_color_level")
            valid_levels = ["白色", "蓝色", "黄色", "橙色", "红色"]
            if min_level and min_level not in valid_levels:
                # 仅警告，不强制重置
                logger.warning(
                    f"[灾害预警] 配置警告: 气象预警级别 {min_level} 不在标准列表中。"
                )

            ConfigValidator._ensure_bool(weather_filter, "enabled", False)
            cfg["weather_filter"] = weather_filter

        max_len = cfg.get("max_description_length")
        if isinstance(max_len, int) and max_len < 0:
            logger.warning(
                f"[灾害预警] 配置警告: 气象描述长度限制 {max_len} 无效，已修正为 0 (不限制)。"
            )
            cfg["max_description_length"] = 0

        ConfigValidator._ensure_bool(cfg, "enable_weather_icon", True)
        ConfigValidator._ensure_bool(cfg, "record_weather_description", True)

        return cfg

    @staticmethod
    def _clamp_number(
        value: Any,
        *,
        minimum: float,
        maximum: float,
        default: float,
        field_name: str,
    ) -> float:
        """将数值限制在闭区间内，非法类型回退默认值。"""
        if not isinstance(value, (int, float)):
            return float(default)
        number = float(value)
        if number < minimum or number > maximum:
            logger.warning(
                f"[灾害预警] 配置警告: {field_name} {number} 超出范围 "
                f"({minimum}~{maximum})，已自动修正。"
            )
            return max(minimum, min(maximum, number))
        return number

    @staticmethod
    def _validate_tsunami_config(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验海啸配置（中国/日本独立阈值）。"""
        if not isinstance(cfg, dict):
            return {}

        china_filter = cfg.get("china_filter", {})
        if not isinstance(china_filter, dict):
            china_filter = {}
        ConfigValidator._ensure_bool(china_filter, "enabled", False)
        cn_valid = ["信息", "蓝色", "黄色", "橙色", "红色"]
        cn_min = str(china_filter.get("min_level") or "信息").strip() or "信息"
        if cn_min not in cn_valid:
            logger.warning(
                f"[灾害预警] 配置警告: 中国海啸最低级别 {cn_min} 无效，已修正为 信息。"
            )
            cn_min = "信息"
        china_filter["min_level"] = cn_min
        cfg["china_filter"] = china_filter

        japan_filter = cfg.get("japan_filter", {})
        if not isinstance(japan_filter, dict):
            japan_filter = {}
        ConfigValidator._ensure_bool(japan_filter, "enabled", False)
        # 配置侧使用中文描述；兼容历史英文枚举
        jp_valid = ["若干海面变动", "海啸注意报", "海啸警报", "大海啸警报"]
        jp_alias = {
            "minor": "若干海面变动",
            "watch": "海啸注意报",
            "warning": "海啸警报",
            "majorwarning": "大海啸警报",
            "若干の海面変動": "若干海面变动",
            "若干的海面变动": "若干海面变动",
            "若干海面变动": "若干海面变动",
            "津波注意報": "海啸注意报",
            "海啸注意报": "海啸注意报",
            "津波警報": "海啸警报",
            "海啸警报": "海啸警报",
            "大津波警報": "大海啸警报",
            "大海啸警报": "大海啸警报",
        }
        jp_raw = (
            str(japan_filter.get("min_level") or "若干海面变动").strip()
            or "若干海面变动"
        )
        jp_min = jp_alias.get(jp_raw, jp_alias.get(jp_raw.lower(), jp_raw))
        if jp_min not in jp_valid:
            logger.warning(
                f"[灾害预警] 配置警告: 日本海啸最低级别 {jp_raw} 无效，已修正为 若干海面变动。"
            )
            jp_min = "若干海面变动"
        japan_filter["min_level"] = jp_min
        cfg["japan_filter"] = japan_filter
        return cfg

    @staticmethod
    def _validate_typhoon_config(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验台风配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 展示开关与过滤逻辑解耦：关闭后消息不暴露本地距离/逼近文案。
        ConfigValidator._ensure_bool(cfg, "show_local_estimation", False)
        # 台风路径图附件开关：必须为布尔值，避免历史/手工配置写入异常类型。
        ConfigValidator._ensure_bool(cfg, "include_track_map", True)

        typhoon_filter = cfg.get("typhoon_filter", {})
        if not isinstance(typhoon_filter, dict):
            return cfg

        ConfigValidator._ensure_bool(typhoon_filter, "enabled", False)
        ConfigValidator._ensure_bool(typhoon_filter, "only_active", True)
        # 台风停编通知开关：默认开启，停编时即使核心参数未变化也推送一条。
        ConfigValidator._ensure_bool(typhoon_filter, "typhoon_deactivate_notify", True)

        valid_levels = [
            "热带低压",
            "热带风暴",
            "强热带风暴",
            "台风",
            "强台风",
            "超强台风",
        ]
        min_level = typhoon_filter.get("min_level")
        if min_level and min_level not in valid_levels:
            logger.warning(
                f"[灾害预警] 配置警告: 台风强度等级 {min_level} 不在标准列表中，"
                "已重置为 热带风暴。"
            )
            typhoon_filter["min_level"] = "热带风暴"

        combine_mode = str(typhoon_filter.get("combine_mode") or "any").strip().lower()
        if combine_mode not in {"all", "any"}:
            logger.warning(
                f"[灾害预警] 配置警告: 台风 combine_mode {combine_mode} 无效，已重置为 any。"
            )
            combine_mode = "any"
        typhoon_filter["combine_mode"] = combine_mode

        typhoon_filter["max_pressure"] = int(
            ConfigValidator._clamp_number(
                typhoon_filter.get("max_pressure", 0),
                minimum=0,
                maximum=1050,
                default=0,
                field_name="台风中心气压上限",
            )
        )
        typhoon_filter["min_wind_speed"] = ConfigValidator._clamp_number(
            typhoon_filter.get("min_wind_speed", 0),
            minimum=0,
            maximum=100,
            default=0,
            field_name="台风最小风速",
        )
        typhoon_filter["min_power"] = int(
            ConfigValidator._clamp_number(
                typhoon_filter.get("min_power", 0),
                minimum=0,
                maximum=20,
                default=0,
                field_name="台风最小风力等级",
            )
        )

        for list_key in ("name_whitelist", "name_blacklist"):
            raw_list = typhoon_filter.get(list_key)
            if not isinstance(raw_list, list):
                typhoon_filter[list_key] = []
            else:
                typhoon_filter[list_key] = [
                    str(item).strip() for item in raw_list if str(item).strip()
                ]

        distance_filter = typhoon_filter.get("distance_filter", {})
        if not isinstance(distance_filter, dict):
            distance_filter = {}
        ConfigValidator._ensure_bool(distance_filter, "enabled", False)
        ConfigValidator._ensure_bool(distance_filter, "use_local_monitoring", True)
        ConfigValidator._ensure_bool(distance_filter, "within_wind_circle", True)
        distance_filter["max_distance_km"] = ConfigValidator._clamp_number(
            distance_filter.get("max_distance_km", 1200),
            minimum=50,
            maximum=5000,
            default=1200,
            field_name="台风最大中心距离",
        )
        # 距离过滤坐标仅在用户显式配置有效数值时写入；未配置或非法值不注入默认坐标
        # 避免“未配置本地参考点”的会话被默认北京坐标兜底、误触发距离/逼近过滤。
        for coord_key, minimum, maximum, default, field_name in (
            ("latitude", -90.0, 90.0, 39.9042, "台风关注点纬度"),
            ("longitude", -180.0, 180.0, 116.4074, "台风关注点经度"),
        ):
            raw_coord = distance_filter.get(coord_key)
            # bool 是 int 的子类，显式排除，避免 True/False 被当作 1.0/0.0 坐标。
            if raw_coord is None or isinstance(raw_coord, bool):
                distance_filter.pop(coord_key, None)
                continue
            if isinstance(raw_coord, (int, float)):
                number = float(raw_coord)
                if number < minimum or number > maximum:
                    logger.warning(
                        f"[灾害预警] 配置警告: {field_name} {number} 超出范围 "
                        f"({minimum}~{maximum})，已删除该无效坐标。"
                    )
                    distance_filter.pop(coord_key, None)
                else:
                    distance_filter[coord_key] = number
            else:
                logger.warning(
                    f"[灾害预警] 配置警告: {field_name} 值 {raw_coord!r} 不是有效数值，"
                    "已删除该无效坐标。"
                )
                distance_filter.pop(coord_key, None)
        if "place_name" in distance_filter and not isinstance(
            distance_filter.get("place_name"), str
        ):
            distance_filter["place_name"] = str(distance_filter.get("place_name") or "")
        typhoon_filter["distance_filter"] = distance_filter

        approach_filter = typhoon_filter.get("approach_filter", {})
        if not isinstance(approach_filter, dict):
            approach_filter = {}
        ConfigValidator._ensure_bool(approach_filter, "enabled", True)
        approach_filter["horizon_hours"] = int(
            ConfigValidator._clamp_number(
                approach_filter.get("horizon_hours", 48),
                minimum=6,
                maximum=120,
                default=48,
                field_name="台风预报时间窗",
            )
        )
        approach_filter["max_approach_distance_km"] = ConfigValidator._clamp_number(
            approach_filter.get("max_approach_distance_km", 500),
            minimum=50,
            maximum=2000,
            default=500,
            field_name="台风预报最近距离阈值",
        )
        typhoon_filter["approach_filter"] = approach_filter

        cfg["typhoon_filter"] = typhoon_filter
        return cfg

    @staticmethod
    def _validate_debug_config(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验调试配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 日志大小限制
        max_size = cfg.get("log_max_size_mb")
        if isinstance(max_size, (int, float)):
            if max_size < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: 日志最大大小 {max_size} MB 过小，已修正为 1 MB。"
                )
                cfg["log_max_size_mb"] = 1
            elif max_size > 1024:
                logger.warning(
                    f"[灾害预警] 配置警告: 日志最大大小 {max_size} MB 过大，已修正为 1024 MB。"
                )
                cfg["log_max_size_mb"] = 1024

        # 保留文件数量
        max_files = cfg.get("log_max_files")
        if isinstance(max_files, int):
            if max_files < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: 日志保留文件数 {max_files} 过小，已修正为 1。"
                )
                cfg["log_max_files"] = 1
            elif max_files > 64:
                logger.warning(
                    f"[灾害预警] 配置警告: 日志保留文件数 {max_files} 过大，已修正为 64。"
                )
                cfg["log_max_files"] = 64

        # Wolfx 列表日志最大条目
        wolfx_max = cfg.get("wolfx_list_log_max_items")
        if isinstance(wolfx_max, int):
            if wolfx_max < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: Wolfx日志条目数 {wolfx_max} 过小，已修正为 1。"
                )
                cfg["wolfx_list_log_max_items"] = 1
            elif wolfx_max > 50:
                logger.warning(
                    f"[灾害预警] 配置警告: Wolfx日志条目数 {wolfx_max} 过大，已修正为 50。"
                )
                cfg["wolfx_list_log_max_items"] = 50

        # 静默启动开关（bool）；兼容旧 startup_silence_duration 秒数配置
        if "silent_startup" in cfg:
            raw_silent = cfg.get("silent_startup")
            if isinstance(raw_silent, bool):
                cfg["silent_startup"] = raw_silent
            elif isinstance(raw_silent, (int, float)):
                cfg["silent_startup"] = bool(raw_silent)
            elif isinstance(raw_silent, str):
                cfg["silent_startup"] = raw_silent.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "是",
                }
            else:
                cfg["silent_startup"] = True
        elif "startup_silence_duration" in cfg:
            legacy = cfg.get("startup_silence_duration")
            if isinstance(legacy, bool):
                cfg["silent_startup"] = legacy
            elif isinstance(legacy, (int, float)):
                cfg["silent_startup"] = float(legacy) > 0
            else:
                cfg["silent_startup"] = True
            cfg.pop("startup_silence_duration", None)
            logger.info(
                f"[灾害预警] 已将旧配置 startup_silence_duration 迁移为 "
                f"silent_startup={cfg['silent_startup']}"
            )
        else:
            cfg["silent_startup"] = True

        # 已存在 silent_startup 时清理残留旧秒数字段
        if "startup_silence_duration" in cfg:
            cfg.pop("startup_silence_duration", None)

        # 过滤消息类型列表校验
        if "filtered_message_types" in cfg:
            if not isinstance(cfg["filtered_message_types"], list):
                cfg["filtered_message_types"] = ["heartbeat", "ping", "pong"]
            else:
                # 确保元素都是字符串
                cfg["filtered_message_types"] = [
                    str(x) for x in cfg["filtered_message_types"] if x
                ]

        # 日志模式校验
        log_mode = cfg.get("log_mode")
        valid_log_modes = ["全量", "简洁"]
        if log_mode and log_mode not in valid_log_modes:
            logger.warning(
                f"[灾害预警] 配置警告: 日志模式 {log_mode} 不在标准列表中，已重置为 全量。"
            )
            cfg["log_mode"] = "全量"

        # 简洁模式日志处理行为校验
        downgrade_behavior = cfg.get("log_downgrade_behavior")
        valid_behaviors = ["降级为DEBUG", "完全屏蔽"]
        if downgrade_behavior and downgrade_behavior not in valid_behaviors:
            logger.warning(
                f"[灾害预警] 配置警告: 简洁模式日志处理行为 {downgrade_behavior} 不在标准列表中，已重置为 降级为DEBUG。"
            )
            cfg["log_downgrade_behavior"] = "降级为DEBUG"

        # 布尔值校验
        ConfigValidator._ensure_bool(cfg, "enable_raw_message_logging", False)
        ConfigValidator._ensure_bool(cfg, "filter_heartbeat_messages", True)
        ConfigValidator._ensure_bool(cfg, "filter_p2p_areas_messages", True)
        ConfigValidator._ensure_bool(cfg, "filter_duplicate_events", True)
        ConfigValidator._ensure_bool(cfg, "filter_connection_status", True)

        return cfg

    @staticmethod
    def _validate_target_sessions(
        sessions: Any, key_name: str = "target_sessions"
    ) -> list[str]:
        """校验推送会话列表。"""
        if not isinstance(sessions, list):
            logger.warning(
                f"[灾害预警] 配置警告: {key_name} 不是列表，已重置为空列表。"
            )
            return []

        # 过滤非字符串项，清洗空字符串或类型错误的会话标识
        valid_sessions = [s for s in sessions if isinstance(s, str) and s.strip()]
        if len(valid_sessions) != len(sessions):
            logger.warning(
                f"[灾害预警] 配置警告: {key_name} 中包含无效项，已自动过滤。"
            )

        return valid_sessions

    @staticmethod
    def _validate_admin_users(users: Any) -> list[str]:
        """校验管理员列表。"""
        if not isinstance(users, list):
            return []

        # 确保都是字符串或数字，并转为字符串
        valid_users = []
        for u in users:
            if isinstance(u, (str, int)) and str(u).strip():
                valid_users.append(str(u))

        return valid_users

    @staticmethod
    def _validate_message_format(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验消息格式配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 地图缩放级别
        zoom = cfg.get("map_zoom_level")
        if isinstance(zoom, int):
            if zoom < 0:
                logger.warning(
                    f"[灾害预警] 配置警告: 地图缩放级别 {zoom} 过小，已修正为 0。"
                )
                cfg["map_zoom_level"] = 0
            elif zoom > 18:
                logger.warning(
                    f"[灾害预警] 配置警告: 地图缩放级别 {zoom} 过大，已修正为 18。"
                )
                cfg["map_zoom_level"] = 18

        # 浏览器池大小
        pool_size = cfg.get("browser_pool_size")
        if isinstance(pool_size, int):
            if pool_size < 1:
                logger.warning(
                    f"[灾害预警] 配置警告: 浏览器池大小 {pool_size} 过小，已修正为 1。"
                )
                cfg["browser_pool_size"] = 1
            elif pool_size > 10:
                logger.warning("[灾害预警] 配置警告: 浏览器池大小过大，已限制为 10。")
                cfg["browser_pool_size"] = 10

        # 是否忽略 HTTPS 证书错误：TLS 安全控制，必须为布尔值。
        # 若手工配置为字符串 "false"，bool("false") 为 True 会错误启用证书忽略。
        ConfigValidator._ensure_bool(cfg, "browser_ignore_https_errors", False)

        # 地图源校验（通用地图 / 台风路径图共用选项表）
        valid_source_ids = set(MAP_TILE_SOURCES.keys())
        valid_source_names = set(MAP_SOURCE_NAME_TO_ID.keys())

        def _validate_map_source_field(
            field_name: str,
            *,
            default_value: str,
            label: str,
        ) -> None:
            value = cfg.get(field_name)
            if value is None:
                return
            if not isinstance(value, str):
                logger.warning(
                    f"[灾害预警] 配置警告: {label}类型错误 "
                    f"({type(value).__name__})，已重置为 {default_value}。"
                )
                cfg[field_name] = default_value
                return
            text = value.strip()
            if not text:
                cfg[field_name] = default_value
                return
            normalized_source = normalize_map_source(text)
            if (
                text not in valid_source_names
                and normalized_source not in valid_source_ids
            ):
                # 仅警告，不强制重置，以支持未来扩展或自定义源
                logger.warning(
                    f"[灾害预警] 配置警告: {label} {text} 不在标准列表中，请确认是否为自定义源。"
                )
            else:
                cfg[field_name] = text

        _validate_map_source_field(
            "map_source",
            default_value="PetalMap矢量图亮",
            label="地图源",
        )
        _validate_map_source_field(
            "typhoon_map_source",
            default_value="PetalMap矢量图暗",
            label="台风路径图瓦片源",
        )

        # Global Quake 模板校验
        gq_template = cfg.get("global_quake_template")
        valid_templates = ["Aurora", "DarkNight"]
        if gq_template and gq_template not in valid_templates:
            # 仅警告，不强制重置
            logger.warning(
                f"[灾害预警] 配置警告: GQ模板 {gq_template} 不在标准列表中，请确认是否为自定义模板。"
            )

        # 推送文本 Emoji 过滤模式校验（规范化逻辑统一委托给 emoji_filter）
        emoji_filter_mode = cfg.get("emoji_filter_mode")
        if emoji_filter_mode is None:
            cfg["emoji_filter_mode"] = EMOJI_FILTER_MODE_DEFAULT
        elif not isinstance(emoji_filter_mode, str):
            logger.warning(
                f"[灾害预警] 配置警告: emoji 过滤模式类型错误 "
                f"({type(emoji_filter_mode).__name__})，"
                f"已重置为 {EMOJI_FILTER_MODE_DEFAULT}。"
            )
            cfg["emoji_filter_mode"] = EMOJI_FILTER_MODE_DEFAULT
        else:
            if not is_known_emoji_filter_mode(emoji_filter_mode):
                logger.warning(
                    f"[灾害预警] 配置警告: emoji_filter_mode={emoji_filter_mode} 无效，"
                    f"已重置为 {EMOJI_FILTER_MODE_DEFAULT}。"
                )
            cfg["emoji_filter_mode"] = normalize_emoji_filter_mode(emoji_filter_mode)

        # Playwright 模式校验
        pw_mode = cfg.get("playwright_mode")
        valid_modes = ["local", "remote"]
        if pw_mode and pw_mode not in valid_modes:
            logger.warning(
                f"[灾害预警] 配置警告: Playwright 模式 {pw_mode} 无效，已重置为 local。"
            )
            cfg["playwright_mode"] = "local"

        # 远程 Playwright 地址校验
        if cfg.get("playwright_mode") == "remote":
            server_url = cfg.get("playwright_server_url")
            if (
                not server_url
                or not isinstance(server_url, str)
                or not server_url.strip()
            ):
                logger.warning(
                    "[灾害预警] 配置警告: 远程 Playwright 模式已启用但未配置服务器地址，已自动切换回 local 模式。"
                )
                cfg["playwright_mode"] = "local"
            else:
                # 简单检查 URL 格式
                server_url = server_url.strip()
                if not (
                    server_url.startswith("ws://")
                    or server_url.startswith("wss://")
                    or server_url.startswith("http://")
                    or server_url.startswith("https://")
                ):
                    logger.warning(
                        f"[灾害预警] 配置警告: 远程 Playwright 地址 {server_url} 格式可能不正确 (应以 ws://, wss://, http:// 或 https:// 开头)。"
                    )

        # 布尔值校验
        ConfigValidator._ensure_bool(cfg, "include_map", False)
        ConfigValidator._ensure_bool(cfg, "detailed_jma_intensity", False)
        ConfigValidator._ensure_bool(cfg, "jma_region_intensity", True)
        ConfigValidator._ensure_bool(cfg, "use_global_quake_card", False)
        # 影响区县/地域预估开关：若手工配置为字符串 "false"，bool("false") 为 True
        # 会错误启用地域估算，因此必须强制为布尔值。
        ConfigValidator._ensure_bool(cfg, "cn_district_intensity_estimate", False)
        ConfigValidator._ensure_bool(cfg, "jma_shindo_estimate", False)

        return cfg

    @staticmethod
    def _validate_push_frequency(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验推送频率控制。"""
        if not isinstance(cfg, dict):
            return cfg

        # 报数限制校验
        for key, max_val in [
            ("cea_cwa_report_n", 10),
            ("jma_report_n", 20),
            ("gq_report_n", 20),
        ]:
            val = cfg.get(key)
            if isinstance(val, int):
                if val < 1:
                    logger.warning(
                        f"[灾害预警] 配置警告: 推送频率 {key}={val} 过小，已修正为 1。"
                    )
                    cfg[key] = 1
                elif val > max_val:
                    logger.warning(
                        f"[灾害预警] 配置警告: 推送频率 {key}={val} 过大，已修正为 {max_val}。"
                    )
                    cfg[key] = max_val

        # 布尔值校验
        ConfigValidator._ensure_bool(cfg, "final_report_always_push", True)
        ConfigValidator._ensure_bool(cfg, "ignore_non_final_reports", False)

        # 气象预警聚合推送配置校验
        agg = cfg.get("weather_aggregation")
        if isinstance(agg, dict):
            ConfigValidator._ensure_bool(agg, "enabled", True)
            ConfigValidator._ensure_bool(agg, "flush_on_red", True)
            ConfigValidator._ensure_bool(agg, "rate_limit_enabled", True)

            agg["time_window_seconds"] = int(
                ConfigValidator._clamp_number(
                    agg.get("time_window_seconds", 900),
                    minimum=60,
                    maximum=3600,
                    default=900,
                    field_name="气象预警聚合时间窗口",
                )
            )
            agg["max_batch_size"] = int(
                ConfigValidator._clamp_number(
                    agg.get("max_batch_size", 20),
                    minimum=1,
                    maximum=20,
                    default=20,
                    field_name="气象预警聚合单批最大条数",
                )
            )
            # 节点未满时等待凑满再推送：剩余条数无法装满节点时本轮不发送，放回缓冲区等下次窗口
            ConfigValidator._ensure_bool(agg, "fill_nodes", True)

            agg["rate_limit_max_messages"] = int(
                ConfigValidator._clamp_number(
                    agg.get("rate_limit_max_messages", 3),
                    minimum=1,
                    maximum=20,
                    default=3,
                    field_name="气象预警限流最大消息数",
                )
            )
            agg["rate_limit_window_seconds"] = int(
                ConfigValidator._clamp_number(
                    agg.get("rate_limit_window_seconds", 900),
                    minimum=60,
                    maximum=3600,
                    default=900,
                    field_name="气象预警限流时间窗口",
                )
            )
            cfg["weather_aggregation"] = agg

        return cfg

    @staticmethod
    def _validate_timezone(tz: Any) -> str:
        """校验时区配置。"""
        if not isinstance(tz, str) or not tz.strip():
            return "UTC+8"
        return tz

    @staticmethod
    def _validate_telemetry(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验遥测配置。"""
        if not isinstance(cfg, dict):
            return cfg

        # 确保 enabled 是布尔值
        if "enabled" in cfg and not isinstance(cfg["enabled"], bool):
            cfg["enabled"] = True

        return cfg

    @staticmethod
    def _validate_notification_settings(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验通知中心配置。"""
        if not isinstance(cfg, dict):
            return cfg

        ConfigValidator._ensure_bool(cfg, "enabled", True)

        poll_interval = cfg.get("poll_interval_seconds")
        if isinstance(poll_interval, int):
            if poll_interval < 30:
                logger.warning(
                    f"[灾害预警] 配置警告: 通知轮询间隔 {poll_interval} 秒过小，已修正为 30。"
                )
                cfg["poll_interval_seconds"] = 30
        elif poll_interval is not None:
            logger.warning("[灾害预警] 配置警告: 通知轮询间隔类型错误，已重置为 300。")
            cfg["poll_interval_seconds"] = 300

        return cfg

    @staticmethod
    def _validate_data_sources(cfg: dict[str, Any]) -> dict[str, Any]:
        """校验数据源配置结构。"""
        if not isinstance(cfg, dict):
            return cfg

        # OpenQuakeAPI 配置组更名：旧 key "global_quake" → 新 key "openquake_api"。
        # 仅做复制迁移，保留旧 key 避免破坏未知扩展；新 key 优先。
        if "openquake_api" not in cfg and "global_quake" in cfg:
            cfg["openquake_api"] = cfg["global_quake"]

        # 确保主要分类存在且为字典，规避非字典类型在运行时发生键提取错误
        for key in [
            "fan_studio",
            "p2p_earthquake",
            "wolfx",
            "openquake_api",
            "snet",
        ]:
            if key in cfg:
                if not isinstance(cfg[key], dict):
                    logger.warning(
                        f"[灾害预警] 配置警告: 数据源 {key} 格式错误，已重置。"
                    )
                    cfg[key] = {"enabled": True}
                else:
                    # 仅确保 enabled 为 bool，其他字段保持原样以支持扩展（如 API Key 等字符串配置）
                    ConfigValidator._ensure_bool(cfg[key], "enabled", True)

        # OpenQuakeAPI：组总闸 + Global Quake 子源开关 + CMA 气象预警子源开关
        # 旧配置仅有 enabled 时，将子源开关回填为 enabled 的值，避免升级后静默关闭。
        gq_cfg = cfg.get("openquake_api")
        if isinstance(gq_cfg, dict):
            ConfigValidator._ensure_bool(gq_cfg, "enabled", True)
            if "global_quake" not in gq_cfg:
                gq_cfg["global_quake"] = bool(gq_cfg.get("enabled", True))
            ConfigValidator._ensure_bool(gq_cfg, "global_quake", True)
            # CMA 气象预警子源：默认 true（高优先级源）
            if "china_weather_alarm" not in gq_cfg:
                gq_cfg["china_weather_alarm"] = bool(gq_cfg.get("enabled", True))
            ConfigValidator._ensure_bool(gq_cfg, "china_weather_alarm", True)

        # S-Net 轮询间隔校验
        snet_cfg = cfg.get("snet")
        if isinstance(snet_cfg, dict):
            # 测站分布图附件开关：必须为布尔值，避免异常类型被保留并写回。
            ConfigValidator._ensure_bool(snet_cfg, "include_station_map", True)
            poll_interval = snet_cfg.get("poll_interval_seconds")
            if isinstance(poll_interval, int):
                if poll_interval < 30:
                    logger.warning(
                        f"[灾害预警] 配置警告: S-Net 轮询间隔 {poll_interval} 过短，已修正为 30。"
                    )
                    snet_cfg["poll_interval_seconds"] = 30
                elif poll_interval > 600:
                    logger.warning(
                        f"[灾害预警] 配置警告: S-Net 轮询间隔 {poll_interval} 过长，已修正为 600。"
                    )
                    snet_cfg["poll_interval_seconds"] = 600
            elif poll_interval is not None:
                logger.warning(
                    "[灾害预警] 配置警告: S-Net 轮询间隔类型错误，已重置为 60。"
                )
                snet_cfg["poll_interval_seconds"] = 60

        # 校验 FAN Studio 下的扩展开关与鉴权字段
        fan_studio_cfg = cfg.get("fan_studio")
        if isinstance(fan_studio_cfg, dict):
            # FAN 台风触发为遗留路径；缺省关闭。
            if "china_typhoon" not in fan_studio_cfg:
                fan_studio_cfg["china_typhoon"] = False
            ConfigValidator._ensure_bool(fan_studio_cfg, "china_typhoon", False)
            # 旧配置可能没有新键；缺省按 schema 默认开启，避免静默丢弃 /sa
            if "usa_shakealert" not in fan_studio_cfg:
                fan_studio_cfg["usa_shakealert"] = True
            ConfigValidator._ensure_bool(fan_studio_cfg, "usa_shakealert", True)
            if "fssn_cmt" not in fan_studio_cfg:
                fan_studio_cfg["fssn_cmt"] = True
            ConfigValidator._ensure_bool(fan_studio_cfg, "fssn_cmt", True)

            # api_key：WebSocket 建连后发送 {"type":"auth","appId","key"}
            api_key = fan_studio_cfg.get("api_key")
            if api_key is None:
                fan_studio_cfg["api_key"] = ""
            elif not isinstance(api_key, str):
                logger.warning(
                    "[灾害预警] 配置警告: FAN Studio api_key 类型错误，已重置为空。"
                )
                fan_studio_cfg["api_key"] = ""
            else:
                fan_studio_cfg["api_key"] = api_key.strip()

            if (
                fan_studio_cfg.get("enabled")
                and not str(fan_studio_cfg.get("api_key", "")).strip()
            ):
                logger.warning(
                    "[灾害预警] 配置警告: FAN Studio 数据源已启用但未配置 api_key，"
                    "WebSocket 鉴权将无法完成，相关连接会被跳过。"
                )

        # 校验 EQSC 数据源配置（组总闸 + 台风富化 + 海啸轮询子开关）
        eqsc_cfg = cfg.get("eqsc")
        if isinstance(eqsc_cfg, dict):
            ConfigValidator._ensure_bool(eqsc_cfg, "enabled", False)
            # 台风子开关：缺省时跟随通道总闸，便于旧配置平滑启用
            if "typhoon" not in eqsc_cfg:
                eqsc_cfg["typhoon"] = bool(eqsc_cfg.get("enabled", False))
            ConfigValidator._ensure_bool(eqsc_cfg, "typhoon", False)
            # 海啸子开关：缺省时跟随通道总闸，便于旧配置平滑启用
            if "jma_tsunami" not in eqsc_cfg:
                eqsc_cfg["jma_tsunami"] = bool(eqsc_cfg.get("enabled", False))
            ConfigValidator._ensure_bool(eqsc_cfg, "jma_tsunami", False)
            ConfigValidator._ensure_bool(
                eqsc_cfg, "jma_tsunami_include_training", False
            )
            if "china_cenc_intensity_report" not in eqsc_cfg:
                eqsc_cfg["china_cenc_intensity_report"] = bool(
                    eqsc_cfg.get("enabled", False)
                )
            ConfigValidator._ensure_bool(eqsc_cfg, "china_cenc_intensity_report", False)

            # RefreshToken 校验：确保为字符串类型
            refresh_token = eqsc_cfg.get("refresh_token")
            if refresh_token is not None and not isinstance(refresh_token, str):
                logger.warning(
                    "[灾害预警] 配置警告: EQSC refresh_token 类型错误，已重置为空。"
                )
                eqsc_cfg["refresh_token"] = ""

            # 统一轮询间隔校验
            # 缺失（键不存在）与显式 None 都视为未配置，统一回退默认 120，
            # 避免 None 原样保留导致下游轮询调度收到无效间隔。
            poll_interval = eqsc_cfg.get("poll_interval_seconds")
            if poll_interval is None:
                # 未配置时静默填充默认值（正常升级路径）；仅 null 显式写入属异常，保留 warning
                if "poll_interval_seconds" in eqsc_cfg:
                    logger.warning(
                        "[灾害预警] 配置警告: EQSC 轮询间隔为 null，已重置为 120。"
                    )
                eqsc_cfg["poll_interval_seconds"] = 120
            elif isinstance(poll_interval, int) and not isinstance(poll_interval, bool):
                if poll_interval < 30:
                    logger.warning(
                        f"[灾害预警] 配置警告: EQSC 轮询间隔 {poll_interval} 过短，已修正为 30。"
                    )
                    eqsc_cfg["poll_interval_seconds"] = 30
                elif poll_interval > 600:
                    logger.warning(
                        f"[灾害预警] 配置警告: EQSC 轮询间隔 {poll_interval} 过长，已修正为 600。"
                    )
                    eqsc_cfg["poll_interval_seconds"] = 600
            else:
                logger.warning(
                    "[灾害预警] 配置警告: EQSC 轮询间隔类型错误，已重置为 120。"
                )
                eqsc_cfg["poll_interval_seconds"] = 120

            # EQSC 启用但缺少 RefreshToken 时输出警告
            if (
                eqsc_cfg.get("enabled")
                and not str(eqsc_cfg.get("refresh_token", "")).strip()
            ):
                logger.warning(
                    "[灾害预警] 配置警告: EQSC 数据源已启用但未配置 refresh_token，"
                    "鉴权、台风富化与海啸轮询将无法正常工作。"
                )

        return cfg


__all__ = ["ConfigValidator"]
