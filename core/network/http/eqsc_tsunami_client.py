"""
EQSC 海啸 HTTP 客户端。

负责拉取 JMA 海啸最新快照。
公共鉴权 / 会话 / 日志能力由 EqscHttpClient 提供。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from .eqsc_http_client import EqscHttpClient
from .eqsc_token_manager import EqscTokenManager


class EqscTsunamiClient(EqscHttpClient):
    """EQSC JMA 海啸数据 HTTP 客户端。"""

    # 海啸快照内存缓存有效期（秒），用于短时并发复用；轮询本身会绕过缓存强制刷新。
    TSUNAMI_CACHE_TTL = 60

    def __init__(
        self,
        token_manager: EqscTokenManager,
        config: dict[str, Any],
        message_logger: Any | None = None,
        *,
        owns_token_manager: bool = False,
    ):
        """初始化海啸客户端。

        Args:
            token_manager: EQSC 令牌管理器（可与台风通道共享）。
            config: EQSC 配置字典。
            message_logger: 可选原始消息记录器。
            owns_token_manager: 为 True 时 close() 会一并关闭 token_manager。
                默认 False，便于与台风客户端共享同一 token_manager。
        """
        super().__init__(
            token_manager,
            config,
            message_logger=message_logger,
            owns_token_manager=owns_token_manager,
            # 海啸上游更新不频繁；硬编码 60 秒缓存，减少重复请求。
            # 传入 None 完全禁用配置读取，避免任何残留键覆盖默认值。
            default_cache_ttl=self.TSUNAMI_CACHE_TTL,
            cache_ttl_config_key=None,
        )
        # 最新快照缓存：(data, expires_at)
        self._latest_cache: tuple[dict[str, Any], float] | None = None

    def clear_cache(self) -> None:
        """清除快照缓存。"""
        self._latest_cache = None

    async def fetch_latest_tsunami(
        self,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any] | None:
        """获取 EQSC 最新 JMA 海啸情报快照。

        Returns:
            海啸数据字典；失败返回 None。
        """
        if (
            use_cache
            and self._latest_cache
            and self._is_cache_valid(self._latest_cache[1])
        ):
            logger.debug("[灾害预警] EQSC 海啸快照命中缓存")
            return self._latest_cache[0]

        if not self._base_url:
            logger.warning("[灾害预警] EQSC base_url 为空，无法拉取海啸情报")
            return None

        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return None

        try:
            url = f"{self._base_url}/jma_tsunami.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                log_label="EQSC 查询 JMA 海啸情报",
            )
            if status != 200 or not isinstance(data, dict):
                return None

            # 空对象或缺少关键字段时视为无效快照
            if not data:
                logger.debug("[灾害预警] EQSC 海啸快照为空")
                return None

            self._latest_cache = (data, time.time() + self._cache_ttl)
            logger.debug("[灾害预警] EQSC 海啸快照查询成功并已缓存")
            return data
        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询海啸情报异常: {type(e).__name__}: {e or repr(e)}"
            )
            return None


__all__ = ["EqscTsunamiClient"]
