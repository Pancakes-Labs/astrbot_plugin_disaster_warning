"""
会话消息发送适配器。
封装 AstrBot 上下文发送能力，便于后续扩展重试、限速与回执统计。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import time


class SessionSendFailedError(RuntimeError):
    """会话消息发送失败。"""


class WeatherRateLimitedError(RuntimeError):
    """天气发送额度暂时耗尽；调用方应缓冲事件而非阻塞发送链。"""

    def __init__(self, retry_after: float):
        self.retry_after = max(0.01, retry_after)
        super().__init__("气象推送限频，等待下一个可用额度")


class SessionSender:
    """会话发送器。"""

    def __init__(self, context, *, clock=time.monotonic):
        # 发送器只包装 AstrBot 上下文，不额外持有复杂发送状态。
        self.context = context
        self._clock = clock
        self._weather_sent = defaultdict(deque)
        self._weather_locks = defaultdict(asyncio.Lock)

    async def send(self, session: str, message, *, weather_rate_limit=None) -> None:
        """发送消息到指定会话。

        AstrBot 的 context.send_message 会在找不到目标平台实例时返回 False，
        但不会抛出异常。这里将该返回值显式转换为发送失败异常，避免上层把
        “框架未实际投递”的场景误统计为成功。
        """
        config = weather_rate_limit or {}
        if config.get("rate_limit_enabled", False):
            # 锁仅覆盖一次实际发送，不等待额度。不同会话和其他灾种互不阻塞。
            async with self._weather_locks[session]:
                now = self._clock()
                window = max(1.0, float(config.get("rate_limit_window_seconds", 900)))
                maximum = max(1, int(config.get("rate_limit_max_messages", 3)))
                history = self._weather_sent[session]
                while history and history[0] <= now - window:
                    history.popleft()
                if len(history) >= maximum:
                    raise WeatherRateLimitedError(history[0] + window - now)
                await self._send(session, message)
                history.append(self._clock())
            return
        await self._send(session, message)

    async def _send(self, session: str, message) -> None:
        sent = await self.context.send_message(session, message)
        if sent is False:
            raise SessionSendFailedError(
                f"AstrBot 未找到可处理会话 {session} 的平台实例，消息未发送"
            )
