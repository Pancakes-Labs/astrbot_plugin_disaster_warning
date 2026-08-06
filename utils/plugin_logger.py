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
        self._silence_checker = None

    def set_config(self, config: dict[str, Any]) -> None:
        """注入最新的插件配置。"""
        self._config = config

    def set_silence_checker(self, checker) -> None:
        """注入启动静默判定回调（通常绑定主服务的 is_silencing）。

        静默期事件流日志抑制依赖该回调动态判断当前是否仍在静默期：
        只有“配置开启 且 当前确实处于静默期”才会丢弃日志，
        静默结束后立即恢复事件流日志打印。
        """
        self._silence_checker = checker

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

    def _is_currently_silencing(self) -> bool:
        """当前是否处于启动静默期（委托主服务 is_silencing 回调）。"""
        checker = self._silence_checker
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _should_suppress_for_silence(self) -> bool:
        """静默启动期间是否丢弃事件流日志。

        两个条件必须同时满足才丢弃：
        1. 配置 debug_config.silent_startup_mute_event_logs 开启（默认 true）；
        2. 当前确实处于启动静默期（动态查询 is_silencing）。

        静默结束后第 2 条自动变为 False，事件流日志立即恢复打印，
        不会出现“静默结束后日志永远消失”的问题。
        """
        if not self._config or not hasattr(self._config, "get"):
            return False
        debug_config = self._config.get("debug_config", {})
        if not hasattr(debug_config, "get"):
            debug_config = {}
        if not bool(debug_config.get("silent_startup_mute_event_logs", True)):
            return False
        return self._is_currently_silencing()

    def _should_suppress_or_downgrade(
        self,
        is_event_linked: bool,
        event_stream: str | None = None,
        is_silent_window: bool | None = None,
    ) -> tuple[bool, str]:
        """
        判断是否需要对当前日志进行处理。
        返回 (是否拦截/降级, 具体行为: "debug" | "mute" | "none")
        """
        if not self._config:
            return False, "none"

        # 静默期抑制：仅对显式标记 is_silent_window=True 的事件流日志生效，
        # 开启配置 且 当前确实处于静默期时整体丢弃（mute），不降级为 DEBUG。
        # 静默结束后 _should_suppress_for_silence() 返回 False，日志立即恢复。
        if is_silent_window is True and self._should_suppress_for_silence():
            return True, "mute"

        if not is_event_linked:
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
        is_silent_window: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """记录 INFO 级别日志。

        Args:
            is_event_linked: 标记为事件流相关日志，受日志降级控制。
            event_stream: 事件流类型标签（如 "weather_alarm"、"global_quake"），
                用于细粒度日志级别覆盖。仅在 is_event_linked=True 时生效。
            is_silent_window: 标记当前处于启动静默期。配合配置项
                silent_startup_mute_event_logs 使用：开启后静默期事件流日志整体丢弃。
        """
        should_process, action = self._should_suppress_or_downgrade(
            is_event_linked, event_stream, is_silent_window
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
        is_silent_window: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """记录 WARNING 级别日志。"""
        should_process, action = self._should_suppress_or_downgrade(
            is_event_linked, event_stream, is_silent_window
        )
        if should_process:
            if action == "debug":
                logger.debug(msg, *args, **kwargs)
            # action == "mute" 则直接屏蔽
        else:
            logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """记录 ERROR 级别日志。错误日志由于其关键性，不受简洁模式限制。"""
        logger.error(msg, *args, **kwargs)

    def debug(
        self,
        msg: str,
        *args: Any,
        is_event_linked: bool = False,
        event_stream: str | None = None,
        is_silent_window: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """记录 DEBUG 级别日志。

        支持事件流覆盖参数（is_event_linked / event_stream / is_silent_window）：
        与 info/warning 共用 _should_suppress_or_downgrade 判定，使事件流日志
        即使在 DEBUG 级别下也能被"事件流屏蔽"（完全丢弃）控制。
        默认情况（is_event_linked=False）行为与原先一致：直接打印 debug。
        """
        should_process, action = self._should_suppress_or_downgrade(
            is_event_linked, event_stream, is_silent_window
        )
        if should_process:
            # action == "mute" 时整体丢弃（插件自处理屏蔽）；
            # action == "debug" 时保持 debug 级别输出。
            if action != "mute":
                logger.debug(msg, *args, **kwargs)
        else:
            logger.debug(msg, *args, **kwargs)


# 全局单例对象
plugin_logger = PluginLogger()
