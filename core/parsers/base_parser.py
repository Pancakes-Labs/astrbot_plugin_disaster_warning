"""
统一解析器抽象定义。
负责提供原始消息解码、心跳识别、告警去重与时间解析等通用能力，
避免各具体解析器重复实现相同基础逻辑。
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from typing import Any

from ...utils.plugin_logger import plugin_logger
from ...utils.time_converter import TimeConverter
from ..sources.source_catalog import get_source_entry


class BaseParser:
    """统一解析器基类。"""

    def __init__(self, source_id: str, message_logger=None):
        """初始化解析器共享状态与运行时缓存。"""
        self.source_id = source_id
        # 获取当前数据源的静态名录定义
        self.source_entry = get_source_entry(source_id)
        self.source_config = self.source_entry
        self.message_logger = message_logger

        # 存放上一次接收到心跳或空载荷数据的时间戳，用于节流检测，避免过多 debug 日志输出
        self._last_heartbeat_check: dict[str, float] = {}

        # 定义心跳包判定规则
        self._heartbeat_patterns = {
            "empty_coordinates": {"latitude": 0, "longitude": 0},
            "empty_fields": ["", None, {}],
        }

        # 警告日志去重缓存：{cache_key: (时间戳, 警告消息内容)}
        self._warning_cache: dict[str, tuple[float, str]] = {}
        self._warning_cache_timeout = 3600  # 去重去噪缓存生存期（秒）

    def parse_message(self, message: str | bytes) -> Any | None:
        """解析原始消息。"""
        try:
            # 统一先做解码，再交给领域事件构建入口，便于子类按需覆写其中某一步
            payload = self.decode_message(message)
            return self.build_event(payload)
        except json.JSONDecodeError as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} JSON解析失败: {exc}")
            return None
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 消息处理失败: {exc}")
            plugin_logger.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            return None

    def decode_message(self, message: str | bytes) -> Any:
        """解码原始消息。"""
        # 如果是 bytes 类型的二进制数据则直接返回，供 protobuf 等特异解析器处理
        if isinstance(message, bytes):
            return message
        # 默认使用 JSON 解码
        return json.loads(message)

    def parse_payload(self, payload: Any):
        """将原始载荷解析为领域事件。"""
        if isinstance(payload, bytes):
            return self._parse_binary_data(payload)
        if not isinstance(payload, dict):
            return None
        return self._parse_data(payload)

    def build_event(self, payload: Any):
        """统一事件构建入口。"""
        return self.parse_payload(payload)

    def _parse_binary_data(self, payload: bytes) -> Any | None:
        """解析二进制载荷，基类默认不支持。"""
        return None

    def _extract_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """提取实际业务数据，兼容多种外层包装格式。"""
        # 兼容首字母大写的 "Data" 键
        if "Data" in data:
            plugin_logger.debug(f"[灾害预警] {self.source_id} 使用 Data 字段获取数据")
            return data["Data"] or {}
        # 兼容小写的 "data" 键
        if "data" in data:
            plugin_logger.debug(f"[灾害预警] {self.source_id} 使用 data 字段获取数据")
            return data["data"] or {}
        # 无包装时，直接视整个载荷为数据体
        plugin_logger.debug(f"[灾害预警] {self.source_id} 使用整个消息作为数据")
        return data

    def _is_heartbeat_message(self, msg_data: dict[str, Any]) -> bool:
        """检测是否为心跳包或空载荷。"""
        current_time = time.time()
        cache_key = f"{self.source_id}_last_check"

        # 对心跳检测本身进行 30 秒节流去噪，避免过多空心跳包引起频繁计算
        if cache_key in self._last_heartbeat_check:
            if current_time - self._last_heartbeat_check[cache_key] < 30:
                return False

        self._last_heartbeat_check[cache_key] = current_time

        # 规则 1：检查坐标值是否为 (0,0) 的空心跳包
        if "latitude" in msg_data and "longitude" in msg_data:
            lat = msg_data.get("latitude")
            lon = msg_data.get("longitude")
            if lat == 0 and lon == 0:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 检测到空坐标心跳包，静默过滤"
                )
                return True

        # 规则 2：根据各数据源特定的必填字段，检查是否大面积为空
        critical_fields = {
            "usgs_fanstudio": ["id", "magnitude", "placeName"],
            "sa_fanstudio": ["id", "magnitude", "placeName"],
            "fssn_cmt_fanstudio": ["id", "eventId", "shockTime"],
            "china_tsunami_fanstudio": ["warningInfo", "code", "timeInfo"],
            "china_weather_fanstudio": ["title", "description"],
        }

        if self.source_id in critical_fields:
            required_fields = critical_fields[self.source_id]
            missing_count = 0

            for field in required_fields:
                field_value = msg_data.get(field)
                if field_value in self._heartbeat_patterns["empty_fields"]:
                    missing_count += 1

            # 若有一半以上的核心必填字段为空，视为无实际有效业务内容的心跳空包
            if missing_count >= len(required_fields) / 2:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 检测到空数据心跳包，静默过滤"
                )
                return True

        return False

    def _should_log_warning(self, warning_type: str, message: str) -> bool:
        """判断是否应该记录警告，避免重复刷同类日志。"""
        current_time = time.time()
        cache_key = f"{self.source_id}_{warning_type}"

        # 检查缓存是否在 1 小时内已经输出过完全相同的警告，是则节流不输出
        if cache_key in self._warning_cache:
            last_time, last_message = self._warning_cache[cache_key]
            if (
                current_time - last_time < self._warning_cache_timeout
                and last_message == message
            ):
                return False

        self._warning_cache[cache_key] = (current_time, message)
        return True

    def _parse_data(self, data: dict[str, Any]) -> Any | None:
        """解析业务数据，具体由子类实现。"""
        raise NotImplementedError

    def _parse_datetime(self, time_str: str) -> datetime | None:
        """解析时间字符串。"""
        # 调用时间转换工具进行多格式兼容解析
        dt = TimeConverter.parse_datetime(time_str)
        if dt is None and time_str:
            plugin_logger.warning(
                f"[灾害预警] 时间解析失败: '{time_str}'", is_event_linked=True
            )
        return dt
