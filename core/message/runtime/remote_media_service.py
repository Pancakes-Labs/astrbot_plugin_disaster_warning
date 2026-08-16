"""
远程媒体会话与 MIME 支撑服务。
"""

from __future__ import annotations

import mimetypes
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientSession

from astrbot.api import logger


class MessageRemoteMediaService:
    """远程媒体会话与 MIME 支撑服务。"""

    def __init__(self, manager):
        # 该服务专门负责远程媒体抓取会话的复用与内容类型辅助判断。
        self.manager = manager

    async def get_session(self, timeout_seconds: int | None = None) -> ClientSession:
        """获取可复用的远程媒体抓取 Session。"""
        session_timeout_seconds = (
            timeout_seconds
            if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0
            else self.manager._remote_media_session_timeout_seconds
        )

        if (
            self.manager._remote_media_session
            and not self.manager._remote_media_session.closed
        ):
            current_timeout = getattr(
                self.manager._remote_media_session.timeout,
                "total",
                session_timeout_seconds,
            )
            # 仅当超时配置一致时复用旧 session；否则重建，避免旧超时参数继续泄漏到新请求。
            if current_timeout == session_timeout_seconds:
                return self.manager._remote_media_session

            try:
                await self.manager._remote_media_session.close()
            except Exception as e:
                logger.debug(f"[灾害预警] 关闭旧远程媒体 Session 失败: {e}")
            finally:
                self.manager._remote_media_session = None

        timeout = aiohttp.ClientTimeout(total=session_timeout_seconds)
        # 统一伪装常见浏览器请求头，提升部分远程图片服务的兼容性。
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }
        self.manager._remote_media_session = aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
        )
        return self.manager._remote_media_session

    async def close_session(self) -> None:
        """关闭远程媒体抓取 Session。"""
        if (
            self.manager._remote_media_session
            and not self.manager._remote_media_session.closed
        ):
            await self.manager._remote_media_session.close()
        self.manager._remote_media_session = None

    @staticmethod
    def is_image_content_type(content_type: str | None) -> bool:
        """判断响应 Content-Type 是否为图片。"""
        if not isinstance(content_type, str):
            return False
        mime = content_type.split(";", 1)[0].strip().lower()
        return mime.startswith("image/")

    @staticmethod
    def looks_like_image_bytes(data: bytes | bytearray | memoryview | None) -> bool:
        """根据文件头判断二进制内容是否像真实图片。

        部分上游图标接口会在资源缺失时返回 Content-Type=image/png 的 HTML 错误页，
        仅校验 MIME 会误判成功，导致后续 QQ 富媒体传输失败。
        """
        if not data:
            return False

        sample = bytes(data[:64])
        if not sample:
            return False

        # 常见图片魔数
        if sample.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if sample.startswith(b"\xff\xd8\xff"):
            return True
        if sample.startswith((b"GIF87a", b"GIF89a")):
            return True
        if sample.startswith(b"BM"):
            return True
        if len(sample) >= 12 and sample.startswith(b"RIFF") and sample[8:12] == b"WEBP":
            return True

        # SVG：仅接受明确 SVG 内容，避免把 SOAP/RSS/Atom 等 XML 错误页当成图片。
        stripped = sample.lstrip().lower()
        if stripped.startswith(b"<svg"):
            return True
        if stripped.startswith(b"<?xml") and b"svg" in sample.lower():
            return True

        # 明确拒绝 HTML/文本伪图
        if stripped.startswith(
            (b"<!doctype", b"<html", b"<head", b"<body", b"{", b"[")
        ):
            return False

        return False

    @staticmethod
    def guess_image_content_type(url: str) -> str | None:
        """根据 URL 后缀猜测图片 MIME。"""
        guessed_type, _ = mimetypes.guess_type(url)
        if isinstance(guessed_type, str) and guessed_type.startswith("image/"):
            return guessed_type
        return None

    @staticmethod
    def guess_referer(url: str) -> str | None:
        """按目标 URL 推导防盗链 Referer。

        部分图片服务（如台湾中央气象署 scweb.cwa.gov.tw）对无 Referer 或
        非浏览器 Referer 的请求会返回 403，这里按域名推导同源站点根路径
        作为 Referer，模拟浏览器行为以提升抓取成功率；非 http(s) 返回 None。
        """
        try:
            parsed = urlparse(str(url or "").strip())
            host = (parsed.hostname or "").lower()
        except Exception:
            return None
        if parsed.scheme not in ("http", "https") or not host:
            return None
        # 已知存在防盗链检查的域名：显式使用站点根路径（与浏览器行为一致）。
        if host.endswith("cwa.gov.tw"):
            return "https://scweb.cwa.gov.tw/"
        # 通用兜底：其余站点使用同源根路径，通常无害且贴近浏览器请求。
        return f"{parsed.scheme}://{host}/"
