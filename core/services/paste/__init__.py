"""
Paste 上传子系统导出。
统一导出 paste 客户端、上传异常与模块级单例入口。
"""

from .paste_client import (
    PASTE_ENDPOINT,
    PasteClient,
    PasteUploadError,
    close_paste_client,
    format_expires_at,
    get_paste_client,
)

__all__ = [
    "PASTE_ENDPOINT",
    "PasteClient",
    "PasteUploadError",
    "close_paste_client",
    "format_expires_at",
    "get_paste_client",
]
