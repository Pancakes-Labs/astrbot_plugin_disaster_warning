"""
Jian Project WebSocket 连接策略与鉴权服务。
负责管理 Jian Project (api.sismotide.top) 的登录密钥 (lk_...) 兑换长期 Token (rt_...)，
以及长期 Token 持久化与换取短期 Access Token (at_...) 的全生命周期管理。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.star import StarTools

# 连接组名称常量
JIAN_PROJECT_PRIMARY_CONNECTION = "jian_project_all"

# 鉴权接口地址
JIAN_PROJECT_REFRESH_URL = "https://auth.sismotide.top/api/refresh"
JIAN_PROJECT_ACCESS_URL = "https://auth.sismotide.top/api/access"

# 默认基础 WebSocket 地址
JIAN_PROJECT_DEFAULT_WS_URL = "wss://api.sismotide.top/all"

# 鉴权持久化文件名
JIAN_PROJECT_TOKEN_FILENAME = "jian_project_token.json"

# 官方数字错误码对应解释
AUTH_ERROR_MESSAGES: dict[int, str] = {
    4001: "未携带访问令牌 (missing_api_key)",
    4002: "令牌前缀或形态错误 (invalid_token_format)",
    4003: "访问令牌无效或已吊销 (invalid_api_key)",
    4004: "访问令牌已过期 (expired_access_token)",
    4005: "该长期 Token 并发连接已满（默认上限 3 条） (conn_limit)",
    4006: "账号被封禁，无法发钥、换票或握手 (account_banned)",
    4101: "登录密钥无效或已使用，请前往 https://auth.sismotide.top/ 重新申请 (invalid_login_key)",
    4102: "登录密钥超过 5 分钟已过期，请重新申请 (expired_login_key)",
    4103: "已有未过期长期 Token 且未勾选覆盖，请在网页勾选「覆盖先前 Token」后再申请 (token_exists)",
    4201: "长期 Token 无效，请重新申请登录密钥换票 (invalid_refresh_token)",
    4202: "长期 Token 已过期（约 180 天），请重新申请登录密钥 (expired_refresh_token)",
    4301: "发信冷却中，请稍后再试 (cooldown)",
    4302: "仅支持 QQ 邮箱域名 (qq_mail_only)",
    4303: "未勾选个人信息处理说明 (consent_required)",
    4399: "服务暂时不可用，请稍后重试 (server_error)",
}


def get_jian_project_storage_path() -> Path:
    """获取 Jian Project 凭证持久化文件路径。"""
    try:
        data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        if data_dir:
            p = Path(data_dir)
            p.mkdir(parents=True, exist_ok=True)
            return p / JIAN_PROJECT_TOKEN_FILENAME
    except Exception:
        pass
    fallback = Path("data")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / JIAN_PROJECT_TOKEN_FILENAME


class JianProjectAuthService:
    """Jian Project 访问令牌鉴权与凭证持久化服务。

    处理流程：
    1. 登录密钥 (lk_...) -> 调 /api/refresh 换取长期 Token (rt_...) 并持久化落盘；
    2. 长期 Token (rt_...) -> 调 /api/access 换取短期访问令牌 (at_...)；
    3. 短期令牌在握手时携带（?key=at_... 与 X-API-Key: at_...），并在到期前 5 分钟自动换新。
    """

    def __init__(self, storage_path: Path | None = None):
        self._storage_path = storage_path or get_jian_project_storage_path()
        self._refresh_token: str | None = None
        self._rt_expires_at: float = 0.0
        self._max_connections: int = 3
        self._last_used_login_key: str | None = None

        # 内存短期 Access Token 缓存
        self._access_token: str | None = None
        self._at_expires_at: float = 0.0

        self._lock = asyncio.Lock()
        self._is_loaded = False

    def _ensure_loaded(self) -> None:
        """从磁盘加载持久化的长期 Token。"""
        if self._is_loaded:
            return
        self._is_loaded = True
        if not self._storage_path.is_file():
            return
        try:
            content = self._storage_path.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                rt = str(data.get("refresh_token") or "").strip()
                if rt:
                    self._refresh_token = rt
                    self._rt_expires_at = float(data.get("expires_at") or 0.0)
                    self._max_connections = int(data.get("max_connections") or 3)
                    self._last_used_login_key = (
                        str(data.get("last_used_login_key") or "").strip() or None
                    )
                    logger.debug(
                        f"[灾害预警] 已从磁盘加载持久化的 Jian Project 长期 Token（前缀: {rt[:6]}...）"
                    )
        except Exception as e:
            logger.warning(f"[灾害预警] 加载 Jian Project 凭证持久化文件失败: {e}")

    def _save_to_disk(self) -> None:
        """将当前长期 Token 持久化落盘。"""
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "refresh_token": self._refresh_token or "",
                "expires_at": self._rt_expires_at,
                "max_connections": self._max_connections,
                "last_used_login_key": self._last_used_login_key or "",
                "updated_at": time.time(),
            }
            tmp_path = self._storage_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp_path.replace(self._storage_path)
            logger.debug("[灾害预警] Jian Project 长期 Token 持久化保存成功")
        except Exception as e:
            logger.error(f"[灾害预警] 持久化保存 Jian Project Token 失败: {e}")

    def has_valid_token(self) -> bool:
        """检查本地是否已存在尚未过期的长期 Token。"""
        self._ensure_loaded()
        if not self._refresh_token:
            return False
        if self._rt_expires_at > 0 and time.time() >= self._rt_expires_at:
            return False
        return True

    def get_persisted_refresh_token(self) -> str | None:
        """获取当前持久化的有效长期 Token。"""
        self._ensure_loaded()
        if self.has_valid_token():
            return self._refresh_token
        return None

    def clear_stored_token(self) -> None:
        """清除持久化的长期 Token（如遇到 4201/4202 过期或失效时）。"""
        self._refresh_token = None
        self._rt_expires_at = 0.0
        self._access_token = None
        self._at_expires_at = 0.0
        self._save_to_disk()

    async def exchange_login_key(
        self,
        login_key: str,
        session: aiohttp.ClientSession | None = None,
    ) -> str:
        """使用一次性登录密钥 (lk_...) 换取长期 Token (rt_...) 并持久化。"""
        clean_key = str(login_key or "").strip()
        if not clean_key:
            raise ValueError("登录密钥为空，无法换取长期 Token")

        headers = {
            "Authorization": f"Bearer {clean_key}",
            "User-Agent": "AstrBot-Disaster-Warning/1.0",
        }

        should_close = False
        client_session = session
        if client_session is None or client_session.closed:
            client_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
            should_close = True

        try:
            logger.info(
                "[灾害预警] 正在使用登录密钥向 Jian Project 换取长期 Token (rt_...)..."
            )
            async with client_session.post(
                JIAN_PROJECT_REFRESH_URL, headers=headers
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json()
                except Exception:
                    raw_text = await resp.text()
                    raise RuntimeError(
                        f"Jian Project 换票接口响应非 JSON (HTTP {status}): {raw_text[:200]}"
                    )

                if status == 200 and data.get("ok"):
                    token = str(data.get("token") or "").strip()
                    if not token:
                        raise RuntimeError("Jian Project 换票响应未包含长期 token 字段")
                    exp_min = float(data.get("expires_after_min") or 259200)
                    max_conn = int(data.get("max_connections") or 3)

                    self._refresh_token = token
                    self._rt_expires_at = time.time() + (exp_min * 60)
                    self._max_connections = max_conn
                    self._last_used_login_key = clean_key
                    self._access_token = None
                    self._at_expires_at = 0.0
                    self._save_to_disk()

                    days = int(exp_min / 1440)
                    logger.info(
                        f"[灾害预警] 🎉 Jian Project 长期 Token 换取成功！"
                        f"有效期约 {days} 天（至 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._rt_expires_at))}），"
                        f"已持久化保存至本地磁盘。"
                    )
                    return token

                # 处理错误
                code = data.get("code")
                msg = data.get("message") or data.get("error") or f"HTTP {status}"
                friendly_msg = (
                    AUTH_ERROR_MESSAGES.get(code, msg) if isinstance(code, int) else msg
                )

                # 特殊场景：4103 token_exists（已有未过期长期 Token 且未勾选覆盖）
                if code == 4103 and self.has_valid_token():
                    logger.warning(
                        f"[灾害预警] Jian Project 提示已有未过期长期 Token ({friendly_msg})，"
                        f"将继续沿用本地已保存的长期 Token。"
                    )
                    return self._refresh_token  # type: ignore[return-value]

                error_detail = (
                    f"Jian Project 换取长期 Token 失败 [{code}]: {friendly_msg}"
                )
                logger.error(f"[灾害预警] {error_detail}")
                raise RuntimeError(error_detail)
        finally:
            if should_close and client_session and not client_session.closed:
                await client_session.close()

    async def exchange_refresh_token(
        self,
        refresh_token: str,
        session: aiohttp.ClientSession | None = None,
    ) -> str:
        """使用长期 Token (rt_...) 换取短期访问令牌 (at_...)。"""
        clean_rt = str(refresh_token or "").strip()
        if not clean_rt:
            raise ValueError("长期 Token 为空，无法换取 Access Token")

        headers = {
            "Authorization": f"Bearer {clean_rt}",
            "User-Agent": "AstrBot-Disaster-Warning/1.0",
        }

        should_close = False
        client_session = session
        if client_session is None or client_session.closed:
            client_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
            should_close = True

        try:
            logger.debug(
                "[灾害预警] 正在使用长期 Token 换取 Jian Project 短期访问令牌 (at_...)..."
            )
            async with client_session.post(
                JIAN_PROJECT_ACCESS_URL, headers=headers
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json()
                except Exception:
                    raw_text = await resp.text()
                    raise RuntimeError(
                        f"Jian Project 换票接口响应非 JSON (HTTP {status}): {raw_text[:200]}"
                    )

                if status == 200 and data.get("ok"):
                    token = str(data.get("token") or "").strip()
                    if not token:
                        raise RuntimeError(
                            "Jian Project 换票响应未包含 access token 字段"
                        )
                    exp_sec = float(data.get("expires_after_sec") or 3600)
                    self._access_token = token
                    self._at_expires_at = time.time() + exp_sec
                    self._max_connections = int(
                        data.get("max_connections") or self._max_connections
                    )
                    logger.debug(
                        f"[灾害预警] Jian Project 短期访问令牌换取成功（有效期 {int(exp_sec)} 秒）"
                    )
                    return token

                code = data.get("code")
                msg = data.get("message") or data.get("error") or f"HTTP {status}"
                friendly_msg = (
                    AUTH_ERROR_MESSAGES.get(code, msg) if isinstance(code, int) else msg
                )

                # 若长期 Token 已失效或过期，清理本地持久化并警告
                if code in (4201, 4202):
                    logger.error(
                        f"[灾害预警] Jian Project 长期 Token 已失效 ({friendly_msg})，正在清理本地过期凭证。"
                    )
                    self.clear_stored_token()

                error_detail = f"Jian Project 换取访问令牌失败 [{code}]: {friendly_msg}"
                logger.error(f"[灾害预警] {error_detail}")
                raise RuntimeError(error_detail)
        finally:
            if should_close and client_session and not client_session.closed:
                await client_session.close()

    async def get_access_token(
        self,
        configured_credential: str | None = None,
        session: aiohttp.ClientSession | None = None,
        force_refresh: bool = False,
    ) -> str:
        """获取用于 WebSocket 握手的短期访问令牌 (at_...)。

        参数:
            configured_credential: 用户配置中传入的凭证字符串（可以是 lk_ 开头的登录密钥，也可以是 rt_ 开头的长期 Token，留空则沿用持久化 Token）。
            session: 可选的 aiohttp 会话。
            force_refresh: 是否强制换取新的访问令牌（用于重连或过期处理）。
        """
        async with self._lock:
            self._ensure_loaded()
            raw_cred = str(configured_credential or "").strip()

            # 1. 用户填入了 lk_ 开头的登录密钥
            if raw_cred.startswith("lk_"):
                # 如果是新的登录密钥，或者是第一次兑换，立即触发兑换
                if raw_cred != self._last_used_login_key or not self.has_valid_token():
                    await self.exchange_login_key(raw_cred, session=session)
                    force_refresh = True
            # 2. 用户直接填入了 rt_ 开头的长期 Token
            elif raw_cred.startswith("rt_"):
                if raw_cred != self._refresh_token:
                    self._refresh_token = raw_cred
                    self._rt_expires_at = time.time() + (180 * 86400)
                    self._access_token = None
                    self._at_expires_at = 0.0
                    self._save_to_disk()
                    force_refresh = True

            # 3. 检查是否有可用的长期 Token
            if not self._refresh_token:
                raise RuntimeError(
                    "未配置 Jian Project 登录密钥 (lk_...) 或长期 Token (rt_...)，且本地无已保存的有效凭证。"
                    "请前往 https://auth.sismotide.top/ 填写 QQ 邮箱申请登录密钥后填入配置中。"
                )

            # 4. 检查内存中短期 Access Token 是否依然有效（提前 5 分钟刷新）
            if (
                not force_refresh
                and self._access_token
                and (time.time() + 300 < self._at_expires_at)
            ):
                return self._access_token

            # 5. 使用长期 Token 换取新的短期访问令牌
            return await self.exchange_refresh_token(
                self._refresh_token, session=session
            )

    def invalidate_token(self) -> None:
        """使当前短期访问令牌失效（握手被拒绝时调用）。"""
        self._access_token = None
        self._at_expires_at = 0.0


# 全局单例鉴权服务
jian_project_auth_service = JianProjectAuthService()


def is_jian_project_connection(name: str) -> bool:
    """判断连接名是否属于 Jian Project 聚合连接。"""
    normalized = str(name or "").strip()
    return (
        normalized == JIAN_PROJECT_PRIMARY_CONNECTION
        or normalized == "jian_project"
        or normalized.startswith("jian_project_")
    )


def attach_jian_project_auth_from_plan(
    connection_info: dict[str, Any],
    conn_config: dict[str, Any],
) -> None:
    """把连接计划中的 Jian Project 鉴权凭证写入 connection_info 上下文。"""
    credential = str(conn_config.get("credential") or "").strip()
    if credential:
        connection_info["credential"] = credential
    base_url = str(conn_config.get("url") or JIAN_PROJECT_DEFAULT_WS_URL).strip()
    connection_info["base_url"] = base_url


__all__ = [
    "JIAN_PROJECT_PRIMARY_CONNECTION",
    "JIAN_PROJECT_REFRESH_URL",
    "JIAN_PROJECT_ACCESS_URL",
    "JIAN_PROJECT_DEFAULT_WS_URL",
    "JIAN_PROJECT_TOKEN_FILENAME",
    "AUTH_ERROR_MESSAGES",
    "get_jian_project_storage_path",
    "JianProjectAuthService",
    "jian_project_auth_service",
    "is_jian_project_connection",
    "attach_jian_project_auth_from_plan",
]
