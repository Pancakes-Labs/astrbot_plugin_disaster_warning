"""远端通知客户端。"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode

import aiohttp

from ....utils.version import get_plugin_version


class NotificationRemoteClient:
    """负责构造远端通知接口请求并拉取原始通知数据。"""

    # 插件统一系统推送的接口服务主域名
    NOTIFICATION_BASE_URL = "https://plugincenter.aloys23.link"
    # 应用的身份识别码 GUID
    NOTIFICATION_APP_SLUG = "f059581e-fca6-4fba-a3be-1b16642ca923"

    def __init__(self, plugin_version_getter=None):
        self._plugin_version_getter = plugin_version_getter

    def _get_plugin_version(self) -> str:
        """获取用于通知平台版本过滤的插件版本号。"""
        if self._plugin_version_getter:
            version = self._plugin_version_getter()
        else:
            version = get_plugin_version()
        # 清理版本号的 V 前缀或多余空格
        normalized = str(version or "0.0.0").strip().lstrip("vV")
        return normalized if normalized and normalized != "unknown" else "0.0.0"

    def build_remote_url(self) -> str:
        """构造远端通知更新接口地址。"""
        base_url = self.NOTIFICATION_BASE_URL.strip().rstrip("/")
        app_slug = self.NOTIFICATION_APP_SLUG.strip().strip("/")
        if not base_url or not app_slug:
            return ""
        # 携带版本号参数，实现针对低版本插件的专属通知及停更提醒等高级过滤投递
        query = urlencode({"plugin_version": self._get_plugin_version()})
        return f"{base_url}/api/v1/{app_slug}/notifications/updates?{query}"

    async def fetch(self) -> list[dict[str, Any]]:
        """拉取远端原始通知数组。"""
        url = self.build_remote_url()
        if not url:
            return []

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            ),
        }
        # 单次 HTTP 请求全局超时限制为 10 秒
        timeout = aiohttp.ClientTimeout(total=10)
        # 采用阶梯递增两次重试机制，应对微弱网络扰动
        retry_delays = (0.5, 1.0)
        last_error: Exception | None = None

        for attempt in range(len(retry_delays) + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=timeout, headers=headers
                ) as session:
                    async with session.get(url) as response:
                        if response.status >= 400:
                            raise RuntimeError(
                                f"通知接口请求失败，HTTP {response.status}"
                            )
                        payload = await response.json(content_type=None)
                if not isinstance(payload, list):
                    raise ValueError("通知接口返回体不是数组")
                return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
                last_error = e
                # 重试次数耗尽，直接跳出重试循环抛出异常
                if attempt >= len(retry_delays):
                    break
                await asyncio.sleep(retry_delays[attempt])

        if isinstance(last_error, asyncio.TimeoutError):
            raise RuntimeError("通知接口请求超时") from last_error
        raise RuntimeError(f"通知接口连接失败: {last_error}") from last_error
