"""
Jian Project WebSocket 连接策略与鉴权服务。

负责：
1. 登录密钥 (lk_...) 兑换长期 Token (rt_...) 并持久化；
2. 长期 Token 换取短期 Access Token (at_...)；
3. 主/备服务器偏好与 URL 顺序解析；
4. 按目标 API 主机选择对应鉴权换票地址。

主服务器：api.sismotide.top / auth.sismotide.top
备服务器：api.jianproject.cloud / auth.jianproject.cloud
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import logger
from astrbot.api.star import StarTools

# 连接组名称常量
JIAN_PROJECT_PRIMARY_CONNECTION = "jian_project_all"

# 主/备 API 主机
JIAN_PROJECT_PRIMARY_WS_HOST = "api.sismotide.top"
JIAN_PROJECT_BACKUP_WS_HOST = "api.jianproject.cloud"
JIAN_PROJECT_PRIMARY_AUTH_BASE = "https://auth.sismotide.top"
JIAN_PROJECT_BACKUP_AUTH_BASE = "https://auth.jianproject.cloud"

# 登录密钥换长期 Token：固定走主鉴权站（发钥页同源）
JIAN_PROJECT_REFRESH_URL = f"{JIAN_PROJECT_PRIMARY_AUTH_BASE}/api/refresh"
# 默认访问令牌换票（主服务器）；备服务器见 resolve_jian_access_url
JIAN_PROJECT_ACCESS_URL = f"{JIAN_PROJECT_PRIMARY_AUTH_BASE}/api/access"

# 默认 WebSocket 地址
JIAN_PROJECT_DEFAULT_WS_URL = f"wss://{JIAN_PROJECT_PRIMARY_WS_HOST}/all"
JIAN_PROJECT_BACKUP_WS_URL = f"wss://{JIAN_PROJECT_BACKUP_WS_HOST}/all"

# 服务器类型标签
SERVER_TYPE_PRIMARY = "primary"
SERVER_TYPE_BACKUP = "backup"

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

        # 内存短期 Access Token 缓存（按鉴权主机区分）
        self._access_token: str | None = None
        self._at_expires_at: float = 0.0
        self._access_token_auth_base: str | None = None

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
        self._access_token_auth_base = None
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
                    self._access_token_auth_base = None
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
        *,
        access_url: str | None = None,
    ) -> str:
        """使用长期 Token (rt_...) 换取短期访问令牌 (at_...)。

        access_url 须与当前连接的 API 主机一致（主/备鉴权站不同）。
        """
        clean_rt = str(refresh_token or "").strip()
        if not clean_rt:
            raise ValueError("长期 Token 为空，无法换取 Access Token")

        target_access_url = str(access_url or JIAN_PROJECT_ACCESS_URL).strip()
        auth_base = target_access_url.rsplit("/api/access", 1)[0] or JIAN_PROJECT_PRIMARY_AUTH_BASE

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
                f" auth={auth_base}"
            )
            async with client_session.post(
                target_access_url, headers=headers
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
                    self._access_token_auth_base = auth_base
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
        ws_uri: str | None = None,
    ) -> str:
        """获取用于 WebSocket 握手的短期访问令牌 (at_...)。

        参数:
            configured_credential: 用户配置中传入的凭证字符串（可以是 lk_ 开头的登录密钥，也可以是 rt_ 开头的长期 Token，留空则沿用持久化 Token）。
            session: 可选的 aiohttp 会话。
            force_refresh: 是否强制换取新的访问令牌（用于重连或过期处理）。
            ws_uri: 目标 WebSocket 地址；用于选择主/备鉴权换票主机。
        """
        async with self._lock:
            self._ensure_loaded()
            raw_cred = str(configured_credential or "").strip()
            access_url = resolve_jian_access_url(ws_uri)
            auth_base = access_url.rsplit("/api/access", 1)[0]

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
            # 且必须与当前目标主机对应的鉴权站一致。
            if (
                not force_refresh
                and self._access_token
                and self._access_token_auth_base == auth_base
                and (time.time() + 300 < self._at_expires_at)
            ):
                return self._access_token

            # 5. 使用长期 Token 换取新的短期访问令牌
            return await self.exchange_refresh_token(
                self._refresh_token,
                session=session,
                access_url=access_url,
            )

    def invalidate_token(self) -> None:
        """使当前短期访问令牌失效（握手被拒绝时调用）。"""
        self._access_token = None
        self._at_expires_at = 0.0
        self._access_token_auth_base = None


# 全局单例鉴权服务
jian_project_auth_service = JianProjectAuthService()


class ServerPreference(Enum):
    """Jian Project 服务器偏好枚举。"""

    PRIMARY_FIRST = "主服务器优先"
    BACKUP_FIRST = "备用服务器优先"
    AUTO = "自动"

    @classmethod
    def from_config(cls, value: str) -> ServerPreference:
        """从配置值解析偏好枚举，无效值回退到主服务器优先。"""
        if not value:
            return cls.PRIMARY_FIRST
        text = str(value).strip()
        for member in cls:
            if member.value == text:
                return member
        return cls.PRIMARY_FIRST

    @classmethod
    def normalize(cls, value: str) -> str:
        """规范化配置值。"""
        return cls.from_config(value).value

    @classmethod
    def parse_strict(cls, value: str | None) -> ServerPreference | None:
        """严格解析偏好枚举；无效值返回 None。"""
        if not value:
            return None
        text = str(value).strip()
        for member in cls:
            if member.value == text:
                return member
        return None


def resolve_jian_access_url(ws_uri: str | None) -> str:
    """按目标 WebSocket 主机选择访问令牌换票地址。"""
    host = ""
    raw = str(ws_uri or "").strip()
    if raw:
        try:
            host = str(urlsplit(raw).hostname or "").strip().lower()
        except Exception:
            host = ""
    if host == JIAN_PROJECT_BACKUP_WS_HOST or host.endswith(".jianproject.cloud"):
        return f"{JIAN_PROJECT_BACKUP_AUTH_BASE}/api/access"
    return JIAN_PROJECT_ACCESS_URL


def resolve_server_urls(
    connection_url: str,
    backup_url: str,
    preference: str | ServerPreference,
) -> tuple[str, str]:
    """按偏好解析主备 URL 顺序，返回 (first_try_url, second_try_url)。"""
    pref = (
        preference
        if isinstance(preference, ServerPreference)
        else ServerPreference.from_config(preference)
    )
    primary = str(connection_url or "").strip()
    backup = str(backup_url or "").strip()
    if not primary:
        return (backup, backup)
    if not backup:
        return (primary, primary)
    if pref == ServerPreference.BACKUP_FIRST:
        return (backup, primary)
    return (primary, backup)


def resolve_active_server_label(
    connection_info: dict[str, Any] | None,
) -> str:
    """从连接信息解析当前活跃的服务器标签。"""
    if not isinstance(connection_info, dict):
        return "未知"
    server_type = str(connection_info.get("active_server") or "").strip()
    if server_type == SERVER_TYPE_PRIMARY:
        return "主服务器"
    if server_type == SERVER_TYPE_BACKUP:
        return "备用服务器"
    return "未知"


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


def is_tls_blocked_signal(text: str | Exception | None) -> bool:
    """识别 TLS/连接被中间设备 RST 阻断的信号，便于尽快切到另一台服务器。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return False
    keywords = (
        "10054",
        "10022",
        "connectionreseterror",
        "远程主机强迫关闭了一个现有的连接",
        "指定的网络名不再可用",
        "connection reset by peer",
        "tls handshake failed",
        "ssl: default",
        "ssl:default",
        "ssl handshake",
        "certificate verify failed",
        "certificate_verify_failed",
        "ssl证书",
        "ssl 证书",
    )
    return any(token in raw for token in keywords)


def is_fan_secondary_connection(name: str) -> bool:
    """历史兼容：FAN 次要通道已移除，恒为 False。"""
    del name
    return False


def is_primary_fan_connected(manager: Any) -> bool:
    """历史兼容名：判断 Jian Project /all 主连接是否在线。"""
    connections = getattr(manager, "connections", {}) or {}
    websocket = connections.get(JIAN_PROJECT_PRIMARY_CONNECTION)
    if websocket is None:
        return False
    try:
        return not bool(getattr(websocket, "closed", True))
    except Exception:
        return False


def resolve_secondary_reconnect_interval(
    *,
    default_interval: int,
    quota_hit: bool,
    primary_online: bool,
) -> int:
    """历史兼容：无次要通道配额策略时直接返回默认间隔。"""
    del quota_hit, primary_online
    return max(1, int(default_interval or 10))


__all__ = [
    "JIAN_PROJECT_PRIMARY_CONNECTION",
    "JIAN_PROJECT_PRIMARY_WS_HOST",
    "JIAN_PROJECT_BACKUP_WS_HOST",
    "JIAN_PROJECT_PRIMARY_AUTH_BASE",
    "JIAN_PROJECT_BACKUP_AUTH_BASE",
    "JIAN_PROJECT_REFRESH_URL",
    "JIAN_PROJECT_ACCESS_URL",
    "JIAN_PROJECT_DEFAULT_WS_URL",
    "JIAN_PROJECT_BACKUP_WS_URL",
    "JIAN_PROJECT_TOKEN_FILENAME",
    "SERVER_TYPE_PRIMARY",
    "SERVER_TYPE_BACKUP",
    "AUTH_ERROR_MESSAGES",
    "ServerPreference",
    "get_jian_project_storage_path",
    "JianProjectAuthService",
    "jian_project_auth_service",
    "resolve_jian_access_url",
    "resolve_server_urls",
    "resolve_active_server_label",
    "is_jian_project_connection",
    "attach_jian_project_auth_from_plan",
    "is_tls_blocked_signal",
    "is_fan_secondary_connection",
    "is_primary_fan_connected",
    "resolve_secondary_reconnect_interval",
]
