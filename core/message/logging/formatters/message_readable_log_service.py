"""
消息可读日志构建服务。
负责把原始日志条目格式化为可读文本，并提供二进制摘要回退展示。
"""

from __future__ import annotations

import hashlib
import json
import re
import string
from datetime import datetime
from typing import Any

from astrbot.api import logger


class MessageReadableLogService:
    """消息可读日志构建服务。"""

    def __init__(self, logger_instance):
        # 通过主记录器复用底层字典格式化与二进制解析能力。
        self.logger = logger_instance

    def format_readable_log(self, log_entry: dict[str, Any]) -> str:
        """格式化可读性强的日志内容。"""
        try:
            # 统一先把日志时间转换到本地显示时区，方便人工排查时直接阅读。
            dt = datetime.fromisoformat(log_entry["timestamp"])
            if dt.tzinfo is not None:
                dt = dt.astimezone()

            timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
            source = log_entry["source"]
            message_type = log_entry["message_type"]

            log_content = f"\n{'=' * 35}\n"
            log_content += f"🕐 日志写入时间: {timestamp}\n"
            log_content += f"📡 来源: {source}\n"
            log_content += f"📋 类型: {message_type}\n"

            connection_info = log_entry.get("connection_info", {})
            if connection_info:
                log_content += self._format_connection_info(connection_info)

            payload_data = log_entry.get("payload_data")
            log_content += "\n📊 原始数据:\n"
            log_content += self._format_payload_data(
                payload_data,
                source=source,
                message_type=message_type,
                connection_info=connection_info,
            )
            log_content += (
                f"\n🔧 插件版本: {log_entry.get('plugin_version', 'unknown')}\n"
            )
            log_content += f"{'=' * 35}\n"
            return log_content
        except Exception as e:
            logger.warning(f"[灾害预警] 日志格式化失败，使用回退格式: {e}")
            return json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n\n"

    def format_binary_data(
        self,
        data: bytes | bytearray | memoryview,
        indent: int = 0,
    ) -> str:
        """格式化二进制数据摘要，提供可读性信息而不写入完整原始内容。"""
        result = ""
        indent_str = "  " * indent
        binary_data = bytes(data)

        result += f"{indent_str}📋 数据类型: binary\n"
        result += f"{indent_str}📋 字节长度: {len(binary_data)}\n"

        md5_digest = hashlib.md5(binary_data).hexdigest()
        sha256_digest = hashlib.sha256(binary_data).hexdigest()
        result += f"{indent_str}📋 MD5: {md5_digest}\n"
        result += f"{indent_str}📋 SHA256: {sha256_digest}\n"

        preview_len = 32
        hex_preview = binary_data[:preview_len].hex(" ")
        result += f"{indent_str}📋 十六进制预览(前{min(len(binary_data), preview_len)}字节): {hex_preview}\n"

        ascii_preview_len = 64
        preview_chunk = binary_data[:ascii_preview_len]
        printable_chars = set(string.printable) - {"\x0b", "\x0c"}
        ascii_preview = "".join(
            chr(b) if chr(b) in printable_chars and b >= 32 else "."
            for b in preview_chunk
        )
        result += f"{indent_str}📋 ASCII预览(前{min(len(binary_data), ascii_preview_len)}字节): {ascii_preview}\n"
        return result

    def _format_connection_info(self, connection_info: dict[str, Any]) -> str:
        """格式化连接信息展示文本。"""
        result = "🔗 连接: "
        parts: list[str] = []
        if "url" in connection_info:
            parts.append(f"URL: {connection_info['url']}")
        elif "server" in connection_info and "port" in connection_info:
            parts.append(
                f"服务器: {connection_info['server']}:{connection_info['port']}"
            )
        if connection_info.get("status_code") is not None:
            parts.append(f"状态码: {connection_info['status_code']}")
        if connection_info.get("connection_type"):
            parts.append(f"类型: {connection_info['connection_type']}")
        if connection_info.get("provider"):
            parts.append(f"服务商: {connection_info['provider']}")
        if parts:
            result += " | ".join(parts)
        return result + "\n"

    def _format_payload_data(
        self,
        payload_data: Any,
        *,
        source: str,
        message_type: str,
        connection_info: dict[str, Any],
    ) -> str:
        """根据数据类型格式化原始载荷。"""
        # 这里统一处理字符串、字典和二进制三大类输入，向上层屏蔽具体差异。
        if isinstance(payload_data, str):
            try:
                parsed_data = json.loads(payload_data)
                return self.logger._format_json_data(parsed_data, indent=2)
            except json.JSONDecodeError:
                binary_match = re.match(
                    r"^<binary:(\d+)\s+bytes>$", payload_data.strip()
                )
                if binary_match:
                    return (
                        "  📋 二进制消息摘要:\n"
                        f"    📋 字节长度: {binary_match.group(1)} (历史占位符，原始二进制不可用)\n"
                    )
                return f"  {payload_data}\n"

        if isinstance(payload_data, dict):
            return self.logger._format_json_data(payload_data, indent=2)

        if isinstance(payload_data, (bytes, bytearray, memoryview)):
            parsed_binary = self.logger._try_parse_binary_message(
                payload_data,
                source=source,
                message_type=message_type,
                connection_info=connection_info,
            )
            if isinstance(parsed_binary, dict):
                return self.logger._format_json_data(parsed_binary, indent=2)
            return self.format_binary_data(payload_data, indent=2)

        return f"  {str(payload_data)}\n"
