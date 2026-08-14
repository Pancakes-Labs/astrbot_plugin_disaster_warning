"""
原始消息记录编排服务。
负责原始日志记录的主流程编排：静默期、摘要分流、过滤、格式化、去重与异步写入。
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger


class RawMessageLoggingService:
    """原始消息记录编排服务。"""

    EQLIST_TYPES = {
        "jma_eqlist",
        "cenc_eqlist",
        "wolfx_jma_eqlist",
        "wolfx_cenc_eqlist",
    }

    def __init__(self, logger_instance):
        # 通过主记录器实例复用过滤、格式化、摘要和写文件等能力。
        self.logger = logger_instance

    def log_raw_message(
        self,
        source: str,
        message_type: str,
        payload_data: Any,
        connection_info: dict | None = None,
    ) -> None:
        """记录原始消息。"""
        # 启动静默期内跳过原始日志，可避免启动阶段缓存恢复/建连噪声淹没真正业务消息。
        if self._in_startup_silence():
            return

        if not self.logger.enabled:
            return

        try:
            parsed_data = self._try_parse_structured_payload(payload_data)
            if self._dispatch_earthquake_list_summary_if_needed(
                source,
                parsed_data,
                connection_info,
            ):
                return

            filter_reason = self.logger._should_filter_message(payload_data, source)
            if filter_reason:
                self._handle_filtered_message(source, message_type, filter_reason)
                return

            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "message_type": message_type,
                "payload_data": payload_data,
                "connection_info": connection_info or {},
                "plugin_version": self.logger.plugin_version,
            }

            log_content = self._format_log_entry(log_entry)
            if self.logger._is_exact_duplicate_in_log(log_content):
                logger.debug(
                    f"[灾害预警] 跳过写入内容完全重复的日志 - 来源: {source}, 类型: {message_type}"
                )
                return

            self._write_log_content(log_content)
        except Exception as e:
            logger.error(f"[灾害预警] 记录原始消息失败: {e}")
            logger.error(
                f"[灾害预警] 失败的消息 - 来源: {source}, 类型: {message_type}"
            )
            logger.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")

    def _in_startup_silence(self) -> bool:
        """判断是否仍处于启动静默期。"""
        checker = getattr(self.logger, "is_in_startup_silence", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        # 兼容旧字段：仅当仍配置了正数秒数时按墙钟判断
        duration = float(getattr(self.logger, "startup_silence_duration", 0) or 0)
        if duration <= 0:
            return False
        start = getattr(self.logger, "start_time", None)
        if start is None:
            return False
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        return elapsed < duration

    def _try_parse_structured_payload(self, payload_data: Any) -> dict[str, Any] | None:
        """尽量把原始载荷解析为结构化字典。"""
        if isinstance(payload_data, dict):
            return payload_data

        if isinstance(payload_data, str) and len(payload_data) > 10:
            if '"type"' in payload_data[:200] or "'type'" in payload_data[:200]:
                try:
                    parsed = json.loads(payload_data)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    return None
        return None

    def _dispatch_earthquake_list_summary_if_needed(
        self,
        source: str,
        parsed_data: dict[str, Any] | None,
        connection_info: dict | None,
    ) -> bool:
        # 地震列表消息会先摘要化再记录，避免整份列表重复写入导致日志体积膨胀。
        if not isinstance(parsed_data, dict):
            return False

        msg_type = parsed_data.get("type", "")
        if msg_type not in self.EQLIST_TYPES:
            return False

        self.logger.log_earthquake_list_summary(
            source=source,
            earthquake_list=parsed_data,
            url=connection_info.get("url") if connection_info else None,
        )
        return True

    def _handle_filtered_message(
        self,
        source: str,
        message_type: str,
        filter_reason: str,
    ) -> None:
        """处理被过滤消息的统计与日志输出。"""
        # 心跳/类型/P2P/重复事件等高频过滤逐条打日志会刷屏，统计由 filter_stats 汇总承担；
        # 仅对低频过滤原因留一条 debug 便于排障。
        is_high_frequency = any(
            keyword in filter_reason
            for keyword in ["消息类型过滤", "P2P节点状态", "心跳", "重复事件"]
        )
        if not is_high_frequency:
            logger.debug(
                f"[灾害预警] 过滤日志消息 - 来源: {source}, 类型: {message_type}, 原因: {filter_reason}"
            )

        self.logger.filter_stats["total_filtered"] += 1
        self.logger._save_stats_if_needed()

    def _format_log_entry(self, log_entry: dict[str, Any]) -> str:
        try:
            return self.logger._readable_log_service.format_readable_log(log_entry)
        except Exception as format_error:
            logger.warning(f"[灾害预警] 可读格式失败，回退到JSON格式: {format_error}")
            return json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n\n"

    def _write_log_content(self, log_content: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.logger._write_log_to_file_sync, log_content)
        except RuntimeError:
            self.logger._write_log_to_file_sync(log_content)
