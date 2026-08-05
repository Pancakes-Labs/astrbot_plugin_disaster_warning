"""
EQSC API 令牌生命周期管理器。

负责管理 EQSC API 的令牌体系：
用户在 EQuake 设置界面获取的 RefreshToken → AccessToken（有效期约1小时）

AccessToken 缓存在内存中，过期前自动续期；
RefreshToken 由用户在配置中直接提供，无需通过登录密钥创建。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from astrbot.api import logger


class EqscTokenManager:
    """EQSC API 令牌管理器。"""

    def __init__(self, config: dict[str, Any]):
        """初始化令牌管理器。

        Args:
            config: EQSC 配置字典，包含 base_url、refresh_token 等字段。
        """
        self._base_url = str(config.get("base_url", "")).strip().rstrip("/")
        self._refresh_token = str(config.get("refresh_token", "")).strip()
        self._access_token: str = ""
        self._access_token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None
        # 提前量：在令牌过期前提前刷新，避免边界竞态
        self._access_advance_seconds = 60  # 1分钟提前刷新

    @property
    def is_configured(self) -> bool:
        """检查是否已配置必要的认证信息。"""
        return bool(self._base_url and self._refresh_token)

    @property
    def has_valid_access_token(self) -> bool:
        """同步检查当前内存中的 AccessToken 是否仍在有效期内。

        不触发网络刷新，仅用于状态面板/活跃连接统计等只读场景。
        只要未过期即视为有效（不使用提前刷新窗口）。
        """
        return bool(self._access_token and time.time() < self._access_token_expires_at)

    @property
    def access_token_expires_at(self) -> float:
        """当前缓存 AccessToken 的过期时间戳（epoch 秒）；无缓存时为 0。"""
        return float(self._access_token_expires_at or 0.0)

    @property
    def access_advance_seconds(self) -> float:
        """令牌过期前的提前刷新窗口（秒），供保活等外部逻辑只读复用。"""
        return float(self._access_advance_seconds or 0)

    def seconds_until_expiry(self) -> float:
        """距离 AccessToken 真正过期的剩余秒数；无缓存或已过期时返回 0。"""
        if not self._access_token:
            return 0.0
        remaining = float(self._access_token_expires_at) - time.time()
        return remaining if remaining > 0.0 else 0.0

    def _cached_token_if_usable(
        self, *, current_time: float | None = None, require_advance_margin: bool = True
    ) -> str | None:
        """在不触发网络的前提下返回可用缓存 AccessToken。

        Args:
            current_time: 可选时间戳，便于复用同一时刻判断。
            require_advance_margin: True 时要求距过期仍大于提前刷新窗口；
                False 时只要未真正过期即可返回（用于刷新失败回退）。
        """
        if not self._access_token:
            return None
        now = time.time() if current_time is None else current_time
        deadline = self._access_token_expires_at
        if require_advance_margin:
            deadline -= self._access_advance_seconds
        if now < deadline:
            return self._access_token
        return None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """确保 aiohttp 会话可用。"""
        if self._session is None or self._session.closed:
            # 分阶段超时：连接慢时尽快失败，避免把整个插件启动拖死。
            timeout = aiohttp.ClientTimeout(
                total=20,
                connect=8,
                sock_connect=8,
                sock_read=12,
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """关闭会话。"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_access_token(
        self,
        *,
        force_refresh: bool = False,
        stale_token: str | None = None,
    ) -> str | None:
        """获取有效的 AccessToken。

        优先复用内存缓存中的 AccessToken；仅在以下情况才会网络创建：
        1. 缓存不存在；
        2. 缓存即将过期（进入提前刷新窗口）；
        3. 调用方显式 force_refresh（如业务接口 401/403 后重试）。

        Args:
            force_refresh: 为 True 时忽略内存缓存，强制重新创建 AccessToken。
                用于业务接口返回 401/403 后的单次重试。
            stale_token: 触发强制刷新的旧 AccessToken。并发 401 时若其它协程
                已换发新令牌（与 stale_token 不同），直接复用，避免重复创建。
        """
        if not self.is_configured:
            logger.debug("[灾害预警] EQSC 令牌管理器未配置，跳过获取 AccessToken")
            return None

        stale = str(stale_token or "").strip() or None

        # 快路径：有效期内直接复用，避免无意义抢锁与重复创建。
        # force_refresh 且带 stale_token 时，若缓存已是“非 stale”的新令牌，也可快路径返回。
        if not force_refresh:
            cached = self._cached_token_if_usable(require_advance_margin=True)
            if cached:
                logger.debug(
                    f"[灾害预警] EQSC 复用缓存 AccessToken {self._mask_token(cached)}"
                )
                return cached
        elif stale:
            cached = self._cached_token_if_usable(require_advance_margin=False)
            if cached and cached != stale:
                logger.debug(
                    "[灾害预警] EQSC 强制刷新前发现已有更新令牌，直接复用 "
                    f"{self._mask_token(cached)}"
                )
                return cached

        async with self._lock:
            current_time = time.time()
            # 双检：并发等待锁期间可能已有其它协程完成刷新
            if not force_refresh:
                cached = self._cached_token_if_usable(
                    current_time=current_time, require_advance_margin=True
                )
                if cached:
                    logger.debug(
                        "[灾害预警] EQSC 复用缓存 AccessToken "
                        f"{self._mask_token(cached)}"
                    )
                    return cached
            else:
                # 401 并发刷新：若锁内已有可用令牌且不是传入的 stale 令牌，说明别人刚换发成功，复用新令牌。
                # 如果传入的 stale 令牌仍等于当前缓存，或者无 stale 令牌，则执行强制刷新。
                cached = self._cached_token_if_usable(
                    current_time=current_time, require_advance_margin=False
                )
                if cached and stale is not None and cached != stale:
                    logger.debug(
                        "[灾害预警] EQSC 并发强制刷新命中已更新令牌，复用 "
                        f"{self._mask_token(cached)}"
                    )
                    return cached

            # 记录刷新前仍可用的旧 token，网络刷新失败时可安全回退
            previous_token = self._access_token
            previous_expires_at = self._access_token_expires_at

            if force_refresh:
                # 仅在确认当前缓存仍是 stale（或为空）时清空，避免误伤刚换发的新令牌
                if not self._access_token or (
                    stale is not None and self._access_token == stale
                ):
                    self.invalidate()

            # AccessToken 过期/临近过期/被强制刷新：用 RefreshToken 创建
            if await self._create_access_token():
                return self._access_token

            # 提前刷新失败时，必须读取最新当前时间判定旧 token 是否在网络请求期间已真正过期
            now = time.time()
            if previous_token and now < previous_expires_at:
                self._access_token = previous_token
                self._access_token_expires_at = previous_expires_at
                logger.warning(
                    "[灾害预警] EQSC AccessToken 刷新失败，回退复用尚未过期的缓存令牌 "
                    f"{self._mask_token(previous_token)}"
                )
                return previous_token

            # 具体失败原因已在 _create_access_token 中输出（超时/HTTP/格式等）
            logger.warning(
                "[灾害预警] EQSC 令牌获取失败。"
                "若日志显示超时/网络异常，请优先检查到 equake.top 的网络连通性；"
                "若为 HTTP 401/403 或错误码，再检查 refresh_token 是否有效"
            )
            return None

    async def _create_access_token(self) -> bool:
        """使用 RefreshToken 创建 AccessToken。"""
        if not self._refresh_token:
            logger.warning("[灾害预警] EQSC refresh_token 为空，无法创建 AccessToken")
            return False
        if not self._base_url:
            logger.warning("[灾害预警] EQSC base_url 为空，无法创建 AccessToken")
            return False

        url = f"{self._base_url}/createAccessToken"
        try:
            session = await self._ensure_session()
            headers = {"Authorization": f"Bearer {self._refresh_token}"}

            async with session.get(url, headers=headers) as response:
                text = (await response.text()).strip()
                if response.status != 200:
                    # 401/403 更可能是 token 无效；5xx/超时类更可能是服务端或网络
                    hint = ""
                    if response.status in (401, 403):
                        hint = "，refresh_token 可能无效或已过期"
                    elif response.status >= 500:
                        hint = "，EQSC 服务端异常"
                    logger.warning(
                        f"[灾害预警] EQSC 创建 AccessToken 失败: HTTP {response.status}"
                        f"（{url}）{hint}" + (f"，响应: {text[:120]}" if text else "")
                    )
                    return False

            # 响应格式: "AccessToken,有效时间(秒)"
            # 只按“最后一个逗号”切分，避免 token 本体意外含逗号时解析错位。
            if "," not in text:
                if text.lstrip("-").isdigit():
                    logger.warning(
                        f"[灾害预警] EQSC 服务返回错误码 {text}（{url}），"
                        "请检查 refresh_token 是否有效或 EQSC 服务状态"
                    )
                else:
                    logger.warning(
                        f"[灾害预警] EQSC AccessToken 响应格式异常（{url}）: "
                        f"{text[:200] if text else '<empty>'}"
                    )
                return False

            token_part, expires_part = text.rsplit(",", 1)
            access_token = token_part.strip().strip('"').strip("'")
            expires_raw = expires_part.strip().strip('"').strip("'")

            try:
                expires_seconds = int(expires_raw)
            except ValueError:
                logger.warning(
                    f"[灾害预警] EQSC AccessToken 有效期解析失败（{url}）: "
                    f"token={self._mask_token(access_token)}, expires={expires_raw!r}"
                )
                return False

            # 防御：错误码或空 token 不应被当作成功
            if not access_token or access_token.lstrip("-").isdigit():
                logger.warning(
                    f"[灾害预警] EQSC AccessToken 内容无效（{url}）: "
                    f"{self._mask_token(access_token)}"
                )
                return False
            if expires_seconds <= 0:
                logger.warning(
                    f"[灾害预警] EQSC AccessToken 有效期异常（{url}）: {expires_seconds}"
                )
                return False

            # 文档约定：RefreshToken 以 ARh. 开头，AccessToken 以 ATn. 开头
            # 若用户误把 RefreshToken 当 AccessToken，或接口返回了错误类型，尽早拦截。
            if access_token.startswith("ARh."):
                logger.warning(
                    f"[灾害预警] EQSC 返回了疑似 RefreshToken（ARh.*），"
                    f"而不是 AccessToken（ATn.*）。masked={self._mask_token(access_token)}"
                )
                return False
            if not access_token.startswith("ATn."):
                logger.warning(
                    f"[灾害预警] EQSC AccessToken 前缀异常（期望 ATn.）: "
                    f"{self._mask_token(access_token)}；仍尝试使用，但后续业务接口可能 401"
                )

            self._access_token = access_token
            self._access_token_expires_at = time.time() + expires_seconds
            logger.info(
                f"[灾害预警] EQSC AccessToken 创建成功，"
                f"masked={self._mask_token(access_token)}，有效期 {expires_seconds} 秒"
            )
            return True
        except asyncio.TimeoutError:
            logger.error(
                f"[灾害预警] EQSC 创建 AccessToken 超时（{url}）。"
                "这通常不是 token 配置本身错误，而是网络不通、DNS 失败或 EQSC 服务无响应。"
            )
            return False
        except aiohttp.ClientError as e:
            logger.error(
                f"[灾害预警] EQSC 创建 AccessToken 网络异常（{url}）: "
                f"{type(e).__name__}: {e or repr(e)}"
            )
            return False
        except Exception as e:
            # 某些超时/取消异常 str(e) 为空，必须打印类型和 repr
            logger.error(
                f"[灾害预警] EQSC 创建 AccessToken 异常（{url}）: "
                f"{type(e).__name__}: {e or repr(e)}"
            )
            return False

    def invalidate(self) -> None:
        """使当前令牌失效，强制下次重新获取。"""
        self._access_token = ""
        self._access_token_expires_at = 0.0

    @staticmethod
    def _mask_token(token: str) -> str:
        """脱敏展示 token，便于日志排障且不泄露完整密钥。"""
        value = (token or "").strip()
        if not value:
            return "<empty>"
        if len(value) <= 10:
            return value[:2] + "***"
        return f"{value[:6]}...{value[-4:]}(len={len(value)})"


__all__ = ["EqscTokenManager"]
