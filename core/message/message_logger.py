"""
原始消息记录器。

该模块负责把接入层收到的原始消息整理为便于排障与回溯的日志记录，
同时结合过滤、去重、摘要统计与文件轮转能力，
避免原始数据日志无限膨胀或充斥无意义噪声。
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

from ...utils.version import get_plugin_version
from ..services.config.config_service import ConfigAccessor
from .logging.filters.event_hash_builder import EventHashBuilder
from .logging.filters.message_log_dedup_service import MessageLogDedupService
from .logging.filters.raw_message_filter import RawMessageFilter
from .logging.formatters.earthquake_list_summary_service import (
    EarthquakeListSummaryService,
)
from .logging.formatters.log_summary_service import LogSummaryService
from .logging.formatters.message_json_formatter_service import (
    MessageJsonFormatterService,
)
from .logging.formatters.message_readable_log_service import MessageReadableLogService
from .logging.parsers.global_quake_protobuf_parser import GlobalQuakeProtobufParser
from .logging.stores.log_file_store import LogFileStore
from .logging.stores.log_stats_repository import LogStatsRepository
from .logging.stores.raw_message_logging_service import RawMessageLoggingService
from .logging.support.message_log_helper_service import MessageLogHelperService
from .logging.support.p2p_area_mapping_loader import P2PAreaMappingLoader


class MessageLogger:
    """原始消息格式记录器。"""

    # 原始消息日志文件名（硬编码，不再暴露为用户配置项）。
    RAW_MESSAGE_LOG_FILE_NAME = "raw_messages.log"

    def __init__(self, config: dict[str, Any], plugin_name: str):
        # 消息记录器负责装配日志子系统，并持有共享配置与运行时状态。
        self.config = config
        self.plugin_name = plugin_name
        self.config_accessor = ConfigAccessor(config)

        self.p2p_area_mapping = (
            self._load_p2p_area_mapping()
        )  # 载入 P2P 气象预警区域代码对应表
        debug_config = self.config_accessor.debug_config()

        # 是否启用、轮转大小与保留份数都由调试配置控制。
        self.enabled = debug_config.get("enable_raw_message_logging", False)
        self.log_file_name = self.RAW_MESSAGE_LOG_FILE_NAME
        self.max_size_mb = debug_config.get(
            "log_max_size_mb", 50
        )  # 单个日志文件最大 MB 数
        self.max_files = debug_config.get("log_max_files", 5)  # 最大保留备份日志文件数

        # 过滤项用于裁剪高频噪声消息，降低日志体积并提升可读性。
        self.filter_heartbeat = debug_config.get("filter_heartbeat_messages", True)
        self.filter_types = debug_config.get(
            "filtered_message_types", ["heartbeat", "ping", "pong"]
        )
        self.filter_p2p_areas = debug_config.get("filter_p2p_areas_messages", True)
        self.filter_duplicate_events = debug_config.get("filter_duplicate_events", True)
        self.filter_connection_status = debug_config.get(
            "filter_connection_status", True
        )
        self.wolfx_list_log_max_items = debug_config.get("wolfx_list_log_max_items", 5)
        # 静默启动由主服务 StartupSilenceCoordinator 统一判定；
        # 实际日志静默走 silence_checker 回调。
        self.startup_silence_duration = 0
        self._silence_checker = None

        self.start_time = datetime.now(timezone.utc)
        # 这些内存缓存分别服务于事件级去重与最近日志内容比对。
        self.recent_event_hashes: dict[str, float] = {}
        self.recent_raw_logs: list[str] = []
        self.max_cache_size = 1000
        self.max_raw_log_cache = 30

        # 过滤统计用于命令查询和调试观察，不影响主业务功能。
        self.filter_stats = {
            "heartbeat_filtered": 0,
            "p2p_areas_filtered": 0,
            "duplicate_events_filtered": 0,
            "connection_status_filtered": 0,
            "total_filtered": 0,
        }

        self.data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.log_file_path = self.data_dir / self.log_file_name
        self.stats_file = self.data_dir / "logger_stats.json"
        # 日志主文件与统计文件统一放在插件数据目录下，便于命令与管理端集中管理。
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._log_file_store = LogFileStore(
            self.log_file_path,
            max_size_mb=self.max_size_mb,
            max_files=self.max_files,
        )
        self._log_summary_service = LogSummaryService()
        self._log_stats_repository = LogStatsRepository(self.stats_file)
        self._load_stats()  # 恢复持久化的日志过滤统计数据

        self.plugin_version = get_plugin_version()
        self._log_helper_service = MessageLogHelperService()
        # 以下子服务共同构成“解析 -> 过滤 -> 格式化 -> 去重 -> 落盘 -> 摘要查询”的日志处理链。
        self._event_hash_builder = EventHashBuilder(
            self._log_helper_service.extract_payload
        )
        self._json_formatter_service = MessageJsonFormatterService(self)
        self._readable_log_service = MessageReadableLogService(self)
        self._log_dedup_service = MessageLogDedupService(self)
        self._protobuf_parser = GlobalQuakeProtobufParser(
            self._log_helper_service.format_binary_timestamp
        )
        self._raw_message_filter = RawMessageFilter(
            enabled=self.enabled,
            filter_heartbeat=self.filter_heartbeat,
            filter_types=self.filter_types,
            filter_p2p_areas=self.filter_p2p_areas,
            filter_duplicate_events=self.filter_duplicate_events,
            filter_connection_status=self.filter_connection_status,
            filter_stats=self.filter_stats,
            is_p2p_areas_message=self._log_helper_service.is_p2p_areas_message,
            is_duplicate_event=self._is_duplicate_event,
            generate_event_hash=self._generate_event_hash,
            is_connection_status_message=self._log_helper_service.is_connection_status_message,
            try_parse_binary_message=self._try_parse_binary_message,
        )
        self._raw_message_logging_service = RawMessageLoggingService(self)
        self._earthquake_list_summary_service = EarthquakeListSummaryService(self)

        logger.debug("[灾害预警] 消息记录器初始化完成")

    def set_silence_checker(self, checker) -> None:
        """注入启动静默判定回调（通常绑定主服务 is_silencing）。"""
        self._silence_checker = checker

    def is_in_startup_silence(self) -> bool:
        """是否处于启动静默期（委托主服务协调器）。"""
        checker = self._silence_checker
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False

    def _should_filter_message(self, payload_data: Any, source_id: str = "") -> str:
        """判断是否应该过滤该消息，返回过滤原因，空字符串表示不过滤。"""
        # 具体过滤规则统一委托给独立过滤器，记录器本身只负责总协调。
        return self._raw_message_filter.should_filter_message(payload_data, source_id)

    def _is_duplicate_event(self, data: dict[str, Any], source_id: str) -> bool:
        """判断是否为重复事件（利用历史哈希滑动窗口）"""
        try:
            event_hash = self._generate_event_hash(data, source_id)
            if event_hash in self.recent_event_hashes:
                return True

            self.recent_event_hashes[event_hash] = datetime.now().timestamp()

            # 超出滑动窗口最大限制时淘汰最老条目
            if len(self.recent_event_hashes) > self.max_cache_size:
                oldest = next(iter(self.recent_event_hashes))
                self.recent_event_hashes.pop(oldest)

            return False

        except Exception as e:
            logger.debug(f"[灾害预警] 去重检查异常: {e}")
            return False

    def _generate_event_hash(self, data: dict[str, Any], source_id: str) -> str:
        """生成用于去重的事件哈希。"""
        return self._event_hash_builder.generate_event_hash(data, source_id)

    def _try_parse_binary_message(
        self,
        data: bytes | bytearray | memoryview,
        source: str,
        message_type: str,
        connection_info: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """尝试解析二进制消息（目前支持 GlobalQuake protobuf）"""
        binary_data = bytes(data)
        conn_type = (connection_info or {}).get("connection_type", "")
        if message_type != "websocket_message" and conn_type != "websocket":
            return None
        if "global_quake" not in source.lower() and "openquake" not in source.lower():
            return None

        try:
            return self._protobuf_parser.parse(binary_data)
        except Exception as e:
            logger.debug(f"[灾害预警] 二进制消息解析失败，回退为摘要模式: {e}")
            return None

    def _format_json_data(self, data: dict[str, Any], indent: int = 0) -> str:
        """递归格式化字典数据，提升日志可读性。"""
        return self._json_formatter_service.format_json_data(data, indent=indent)

    def _load_p2p_area_mapping(self) -> dict[int, str]:
        """加载 P2P 区域代码映射。"""
        csv_path = Path(__file__).parent.parent.parent / "resources/epsp-area.csv"
        return P2PAreaMappingLoader.load(csv_path)

    def _extract_content_without_timestamp(self, log_content: str) -> str:
        """提取日志内容中排除时间戳的部分，用于重复检测"""
        return self._log_dedup_service.extract_content_without_timestamp(log_content)

    def _is_exact_duplicate_in_log(self, new_log_content: str) -> bool:
        """检查最近的日志中是否存在完全重复的内容（基于内存缓存）"""
        return self._log_dedup_service.is_exact_duplicate_in_log(new_log_content)

    def log_raw_message(
        self,
        source: str,
        message_type: str,
        payload_data: Any,
        connection_info: dict | None = None,
    ):
        """记录原始消息。"""
        # 这是记录器对外最核心的统一入口，
        # 无论消息来自 WebSocket、HTTP 还是列表摘要，最终都会汇入这里进入日志处理链。
        self._raw_message_logging_service.log_raw_message(
            source,
            message_type,
            payload_data,
            connection_info,
        )

    def _write_log_to_file_sync(self, content: str):
        """同步写入日志文件（在线程池中运行）"""
        success = self._log_file_store.write(content)
        if not success:
            self.enabled = False  # 写入失败则关闭落盘开关

    def log_websocket_message(
        self, connection_name: str, message: Any, url: str | None = None
    ):
        """记录WebSocket消息"""
        self.log_raw_message(
            source=f"websocket_{connection_name}",
            message_type="websocket_message",
            payload_data=message,
            connection_info={"url": url, "connection_type": "websocket"}
            if url
            else {"connection_type": "websocket"},
        )

    def log_http_response(
        self, url: str, response_data: Any, status_code: int | None = None
    ):
        """记录HTTP响应"""
        self.log_raw_message(
            source="http_response",
            message_type="http_response",
            payload_data=response_data,
            connection_info={
                "url": url,
                "status_code": status_code,
                "connection_type": "http",
            },
        )

    def log_earthquake_list_summary(
        self,
        source: str,
        earthquake_list: dict[str, Any],
        url: str | None = None,
        max_items: int | None = None,
    ):
        """记录地震列表数据摘要。"""
        self._earthquake_list_summary_service.log_summary(
            source=source,
            earthquake_list=earthquake_list,
            url=url,
            max_items=max_items,
        )

    def get_log_summary(self) -> dict[str, Any]:
        """获取日志统计信息（支持新可读性格式）"""
        # 统一从摘要服务构建结果，保证命令输出与 Web 管理端查看到的是同一统计口径。
        return self._log_summary_service.build_summary(
            enabled=self.enabled,
            log_file_path=self.log_file_path,
            max_files=self.max_files,
            max_size_mb=self.max_size_mb,
            filter_stats=self.filter_stats,
        )

    def clear_logs(self):
        """清除所有日志文件与去重缓存"""
        try:
            if self.log_file_path.exists():
                self.log_file_path.unlink()

            for i in range(1, self.max_files + 1):
                old_file = self.log_file_path.with_suffix(f".log.{i}")
                if old_file.exists():
                    old_file.unlink()

            self.recent_event_hashes.clear()

            for key in self.filter_stats:
                self.filter_stats[key] = 0

            self.save_stats()
            logger.info("[灾害预警] 所有日志文件已清除，去重缓存已清空")

        except Exception as e:
            logger.error(f"[灾害预警] 清除日志失败: {e}")

    def save_stats(self):
        """保存统计数据到文件"""
        self._log_stats_repository.save(self.filter_stats)

    def _load_stats(self):
        """加载统计数据"""
        data = self._log_stats_repository.load()
        if isinstance(data, dict):
            self.filter_stats = data.get("filter_stats", self.filter_stats)

    def _save_stats_if_needed(self):
        """按需保存统计（减少IO频率，例如每10次过滤保存一次）"""
        if self.filter_stats["total_filtered"] % 10 == 0:
            self.save_stats()


def get_message_logger(config: dict[str, Any], plugin_name: str) -> MessageLogger:
    """获取消息记录器实例"""
    return MessageLogger(config, plugin_name)
