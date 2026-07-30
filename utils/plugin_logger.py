from typing import Any

from astrbot.api import logger


class PluginLogger:
    """插件日志代理，用于控制事件流相关日志的分级、降级和屏蔽。

    支持两级日志控制：
    1. 总开关：log_mode（全量/简洁）+ log_downgrade_behavior（降级为DEBUG/完全屏蔽）
    2. 细粒度覆盖：event_stream_log_level，按事件流类型独立控制日志级别，
       优先级高于总开关。可用于将高频事件流（如气象预警、Global Quake）
       的日志降级为 DEBUG 或屏蔽，而不影响其他事件流。

    细粒度配置结构（debug_config.event_stream_log_level）：
    - all: 全局覆盖开关，设为 DEBUG/屏蔽时覆盖所有事件流独立设置
    - weather_alarm: 气象预警事件流
    - global_quake: Global Quake 事件流
    - earthquake: 地震事件流
    - tsunami: 海啸事件流
    - typhoon: 台风事件流

    优先级：all > 具体事件流 > log_mode 总开关
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] | None = None

    def set_config(self, config: dict[str, Any]) -> None:
        """注入最新的插件配置。"""
        self._config = config

    def _resolve_stream_action(self, event_stream: str | None) -> str | None:
        """解析事件流日志级别覆盖，返回 "debug" / "mute" / None。

        独立于 log_mode 总开关：即使总开关为"全量"，
        事件流级别覆盖仍可生效。
        """
        if not event_stream or not self._config:
            return None
        if not hasattr(self._config, "get"):
            return None

        debug_config = self._config.get("debug_config", {})
        if not hasattr(debug_config, "get"):
            return None

        stream_config = debug_config.get("event_stream_log_level", {})
        if not hasattr(stream_config, "get") or not isinstance(stream_config, dict):
            return None

        # "all" 全局覆盖优先级最高
        all_level = str(stream_config.get("all") or "").strip()
        if all_level == "DEBUG":
            return "debug"
        if all_level == "屏蔽":
            return "mute"

        # 其次按事件流类型匹配
        stream_level = str(stream_config.get(event_stream) or "").strip()
        if stream_level == "DEBUG":
            return "debug"
        if stream_level == "屏蔽":
            return "mute"

        return None

    def _should_suppress_or_downgrade(
        self, is_event_linked: bool, event_stream: str | None = None
    ) -> tuple[bool, str]:
        """
        判断是否需要对当前日志进行处理。
        返回 (是否拦截/降级, 具体行为: "debug" | "mute" | "none")
        """
        if not is_event_linked or not self._config:
            return False, "none"

        # 优先检查事件流级别覆盖（独立于 log_mode 总开关）
        stream_action = self._resolve_stream_action(event_stream)
        if stream_action is not None:
            return True, stream_action

        # 回退到 log_mode 总开关 + 降级行为
        if not hasattr(self._config, "get"):
            return False, "none"

        debug_config = self._config.get("debug_config", {})
        if not hasattr(debug_config, "get"):
            debug_config = {}

        log_mode = debug_config.get("log_mode", self._config.get("log_mode", "全量"))
        if log_mode != "简洁":
            return False, "none"

        # 简洁模式下，获取降级行为
        behavior = debug_config.get(
            "log_downgrade_behavior",
            self._config.get("log_downgrade_behavior", "降级为DEBUG"),
        )
        if behavior == "完全屏蔽":
            return True, "mute"
        return True, "debug"

    def info(
        self,
        msg: str,
        *args: Any,
        is_event_linked: bool = False,
        event_stream: str | None = None,
        **kwargs: Any,
    ) -> None:
        """记录 INFO 级别日志。

        Args:
            is_event_linked: 标记为事件流相关日志，受日志降级控制。
            event_stream: 事件流类型标签（如 "weather_alarm"、"global_quake"），
                用于细粒度日志级别覆盖。仅在 is_event_linked=True 时生效。
        """
        should_process, action = self._should_suppress_or_downgrade(
            is_event_linked, event_stream
        )
        if should_process:
            if action == "debug":
                logger.debug(msg, *args, **kwargs)
            # action == "mute" 则直接屏蔽，什么都不做
        else:
            logger.info(msg, *args, **kwargs)

    def warning(
        self,
        msg: str,
        *args: Any,
        is_event_linked: bool = False,
        event_stream: str | None = None,
        **kwargs: Any,
    ) -> None:
        """记录 WARNING 级别日志。"""
        should_process, action = self._should_suppress_or_downgrade(
            is_event_linked, event_stream
        )
        if should_process:
            if action == "debug":
                logger.debug(msg, *args, **kwargs)
        else:
            logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """记录 ERROR 级别日志。错误日志由于其关键性，不受简洁模式限制。"""
        logger.error(msg, *args, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """记录 DEBUG 级别日志。"""
        logger.debug(msg, *args, **kwargs)


# 全局单例对象
plugin_logger = PluginLogger()
