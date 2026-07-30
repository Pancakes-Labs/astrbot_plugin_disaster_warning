"""
EQSC HTTP 客户端公共基类。

统一承接：
- AccessToken 鉴权请求与 401/403 刷新重试
- aiohttp 会话生命周期
- 原始消息日志落盘
- 简单 TTL 缓存判定

台风 / 海啸等业务客户端只保留各自接口与缓存键策略。
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from astrbot.api import logger

from .eqsc_token_manager import EqscTokenManager


class EqscHttpClient:
    """EQSC HTTP 客户端基类。"""

    def __init__(
        self,
        token_manager: EqscTokenManager,
        config: dict[str, Any],
        message_logger: Any | None = None,
        *,
        owns_token_manager: bool = True,
        default_cache_ttl: int = 300,
        cache_ttl_config_key: str = "cache_ttl",
    ):
        """初始化公共 HTTP 能力。

        Args:
            token_manager: EQSC 令牌管理器。
            config: EQSC 配置字典。
            message_logger: 可选原始消息记录器。
            owns_token_manager: close() 时是否关闭 token_manager。
                共享同一 token_manager 的附属客户端应设为 False。
            default_cache_ttl: 缓存 TTL 默认值（秒）。
            cache_ttl_config_key: 从 config 读取 TTL 的键名。
        """
        self._token_manager = token_manager
        self._base_url = str(config.get("base_url", "") or "").strip().rstrip("/")
        self._message_logger = message_logger
        self._owns_token_manager = bool(owns_token_manager)
        try:
            cache_ttl = int(
                config.get(cache_ttl_config_key, default_cache_ttl) or default_cache_ttl
            )
        except (TypeError, ValueError):
            cache_ttl = default_cache_ttl
        self._cache_ttl = max(1, cache_ttl)
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        """EQSC API 基础地址。"""
        return self._base_url

    @property
    def cache_ttl(self) -> int:
        """当前缓存 TTL（秒）。"""
        return self._cache_ttl

    @property
    def token_manager(self) -> EqscTokenManager:
        """关联的令牌管理器。"""
        return self._token_manager

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用。

        超时参数说明：
        - connect/sock_connect=15：国内到 equake.top 的 TCP+TLS 握手在网络抖动时
          容易超过 8 秒，放宽到 15 秒减少 ConnectionTimeoutError。
        - sock_read=30：EQSC 列表接口（如 typhoonNMC.json 含 20 个台风完整轨迹）
          响应体较大，上游聚合耗时 + 大包传输容易超 15 秒，放宽到 30 秒。
        - total=45：connect(15) + sock_read(30) 的上限，留足完整请求生命周期。
        """
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=45,
                connect=15,
                sock_connect=15,
                sock_read=30,
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """关闭 HTTP 会话；按所有权决定是否关闭 token_manager。"""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._owns_token_manager:
            await self._token_manager.close()

    @staticmethod
    def _is_cache_valid(expires_at: float) -> bool:
        """检查缓存是否仍然有效。"""
        return time.time() < expires_at

    @staticmethod
    def _mask_token(token: str | None) -> str:
        """脱敏 token，便于 401 排障。"""
        value = str(token or "").strip()
        if not value:
            return "<empty>"
        if len(value) <= 10:
            return value[:2] + "***"
        return f"{value[:6]}...{value[-4:]}(len={len(value)})"

    def _build_request_url(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """拼接带查询参数的完整请求 URL，供原始日志展示。"""
        if not params:
            return url
        query = urlencode(
            {str(key): str(value) for key, value in params.items() if value is not None}
        )
        if not query:
            return url
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{query}"

    @staticmethod
    def _sanitize_log_payload(response_data: Any, *, max_chars: int = 8000) -> Any:
        """截断过大响应，降低原始日志意外膨胀/敏感字段风险。"""
        if response_data is None:
            return None
        if isinstance(response_data, str):
            text = response_data
            if len(text) <= max_chars:
                return text
            return f"{text[:max_chars]}...[truncated len={len(text)}]"
        try:
            encoded = json.dumps(response_data, ensure_ascii=False)
        except Exception:
            text = str(response_data)
            if len(text) <= max_chars:
                return text
            return f"{text[:max_chars]}...[truncated]"
        if len(encoded) <= max_chars:
            return response_data
        return {
            "_truncated": True,
            "_original_chars": len(encoded),
            "preview": encoded[:max_chars],
        }

    def _log_http_response(
        self,
        *,
        url: str,
        params: dict[str, Any] | None,
        status_code: int | None,
        response_data: Any,
    ) -> None:
        """将 EQSC HTTP 响应写入原始消息日志（若记录器已启用）。"""
        if not self._message_logger:
            return
        try:
            log_url = self._build_request_url(url, params)
            safe_payload = self._sanitize_log_payload(response_data)
            connection_info = {
                "url": log_url,
                "status_code": status_code,
                "connection_type": "http",
                "provider": "eqsc",
            }
            if hasattr(self._message_logger, "log_raw_message"):
                self._message_logger.log_raw_message(
                    source="http_eqsc",
                    message_type="http_response",
                    payload_data=safe_payload,
                    connection_info=connection_info,
                )
            else:
                self._message_logger.log_http_response(
                    url=log_url,
                    response_data=safe_payload,
                    status_code=status_code,
                )
        except Exception as e:
            logger.debug(f"[灾害预警] EQSC 原始响应日志写入失败: {e}")

    async def _request_json(
        self,
        *,
        url: str,
        access_token: str,
        params: dict[str, Any] | None = None,
        log_label: str,
        allow_retry_on_auth_error: bool = True,
    ) -> tuple[int, Any, str]:
        """发送 EQSC GET 请求，返回 (status, json_or_none, raw_text)。

        遇到 401/403 时会强制刷新 AccessToken 并重试一次。
        成功响应会同步写入原始消息日志。
        """
        session = await self._ensure_session()
        current_token = access_token
        last_status = 0
        last_text = ""

        for attempt in range(2):
            headers = {"Authorization": f"Bearer {current_token}"}
            async with session.get(url, headers=headers, params=params) as response:
                last_status = response.status
                last_text = (await response.text()).strip()
                if response.status == 200:
                    try:
                        parsed = json.loads(last_text) if last_text else {}
                        self._log_http_response(
                            url=url,
                            params=params,
                            status_code=response.status,
                            response_data=parsed,
                        )
                        return response.status, parsed, last_text
                    except Exception:
                        logger.warning(
                            f"[灾害预警] {log_label} 响应 JSON 解析失败: {last_text[:200]}"
                        )
                        self._log_http_response(
                            url=url,
                            params=params,
                            status_code=response.status,
                            response_data=last_text[:500] if last_text else "",
                        )
                        return response.status, None, last_text

                if response.status in (401, 403):
                    logger.warning(
                        f"[灾害预警] {log_label} 鉴权失败: HTTP {response.status}；"
                        f"token={self._mask_token(current_token)}"
                        + (f"；响应: {last_text[:160]}" if last_text else "")
                    )
                    if allow_retry_on_auth_error and attempt == 0:
                        # 不在锁外主动失效：交给 token_manager 在锁内按 stale_token
                        # 去重，避免台风/海啸并发 401 时重复 createAccessToken。
                        refreshed = await self._token_manager.get_access_token(
                            force_refresh=True,
                            stale_token=current_token,
                        )
                        if refreshed and refreshed != current_token:
                            logger.info(
                                f"[灾害预警] {log_label} 已刷新 AccessToken，准备重试一次"
                            )
                            current_token = refreshed
                            continue
                    return response.status, None, last_text

                logger.warning(
                    f"[灾害预警] {log_label} 失败: HTTP {response.status}"
                    + (f"；响应: {last_text[:160]}" if last_text else "")
                )
                return response.status, None, last_text

        return last_status, None, last_text

    async def _resolve_access_token(
        self, access_token: str | None = None
    ) -> str | None:
        """解析可用 AccessToken：优先使用调用方传入值。"""
        if access_token:
            return access_token
        return await self._token_manager.get_access_token()


__all__ = ["EqscHttpClient"]
