"""
消息日志内容去重服务。
负责提取稳定内容并在内存缓存中进行完全重复检测，
减少 core/message/message_logger.py中的去重实现细节。
"""

from __future__ import annotations

from astrbot.api import logger


class MessageLogDedupService:
    """消息日志内容去重服务。"""

    def __init__(self, logger_instance):
        # 通过主记录器实例复用最近日志缓存与容量配置。
        self.logger = logger_instance

    def extract_content_without_timestamp(self, log_content: str) -> str:
        """提取日志内容中排除时间戳的部分，用于重复检测。"""
        # 去掉写入时间后再比较，可避免同一条消息仅因记录时刻不同而绕过去重。
        lines = log_content.split("\n")
        content_without_timestamp: list[str] = []

        for line in lines:
            if line.strip().startswith("🕐 日志写入时间:"):
                continue
            content_without_timestamp.append(line)

        return "\n".join(content_without_timestamp)

    def is_exact_duplicate_in_log(self, new_log_content: str) -> bool:
        """检查最近日志中是否存在完全重复的内容（基于内存缓存）。"""
        try:
            new_content_clean = self.extract_content_without_timestamp(new_log_content)
            if new_content_clean in self.logger.recent_raw_logs:
                return True

            self.logger.recent_raw_logs.append(new_content_clean)
            if len(self.logger.recent_raw_logs) > self.logger.max_raw_log_cache:
                self.logger.recent_raw_logs.pop(0)
            return False
        except Exception as e:
            logger.warning(f"[灾害预警] 检查重复内容时出错: {e}")
            return False
