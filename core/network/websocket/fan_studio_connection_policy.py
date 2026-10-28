"""
FAN Studio 连接配额与优先级策略。

上游按 IP 限制并发 WebSocket（通常最多 5 条）。
插件侧策略：
1. 启动时先建 fan_studio_all（/all），主通道在线后再建次要独立通道
2. 运行中优先保活 /all；主通道遇配额/策略拒绝时，才释放次要通道让路
3. 次要通道在主通道离线或命中配额时拉长退避，避免与 /all 抢连接
4. 建连成功后发送应用层鉴权包：{"type":"auth","appId":"...","key":"sk-..."}

新增服务器偏好功能：
- 支持配置"主服务器优先"、"备用服务器优先"、"自动"三种策略
- 连接时按偏好决定主备 URL 顺序
- 追踪当前活跃的服务器类型
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from astrbot.api import logger

# 主通道：聚合 /all
FAN_PRIMARY_CONNECTION = "fan_studio_all"

# 已知次要独立通道（不在 /all 内）
FAN_SECONDARY_CONNECTIONS: frozenset[str] = frozenset(
    {
        "fan_studio_cenc_ir",
    }
)

# 上游常见 IP 并发上限（文档/经验值）；用于日志与策略说明，不作为硬编码断连阈值。
FAN_IP_CONNECTION_LIMIT = 3

# 服务器类型标签
SERVER_TYPE_PRIMARY = "primary"  # 主服务器
SERVER_TYPE_BACKUP = "backup"  # 备用服务器


class ServerPreference(Enum):
    """FAN Studio 服务器偏好枚举。"""

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
        """规范化配置值，确保配置持久化时使用标准值。"""
        return cls.from_config(value).value

    @classmethod
    def parse_strict(cls, value: str | None) -> ServerPreference | None:
        """严格解析偏好枚举；无效值返回 None（不静默回退）。

        供运行期命令（如 /服务器切换）调用，在切换前拒绝无效参数，
        避免无效值被静默当作"主服务器优先"而误切换。
        """
        if not value:
            return None
        text = str(value).strip()
        for member in cls:
            if member.value == text:
                return member
        return None


def resolve_server_urls(
    connection_url: str,
    backup_url: str,
    preference: str | ServerPreference,
) -> tuple[str, str]:
    """按偏好解析主备 URL 顺序。

    返回 (first_try_url, second_try_url)：
    - 主服务器优先：first=主, second=备
    - 备用服务器优先：first=备, second=主
    - 自动：first=主, second=备（与主服务器优先相同）
    """
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


def resolve_active_server_domain(
    connection_info: dict[str, Any] | None,
) -> str:
    """从连接信息解析当前活跃的服务器域名（用于调试/日志）。

    返回 URI 解析后的 hostname（如 ws.fanstudio.tech），
    不包含协议、路径或查询参数；URI 不可解析时回退原始值。
    """
    if not isinstance(connection_info, dict):
        return ""
    uri = str(connection_info.get("uri") or "").strip()
    if not uri:
        return ""
    from urllib.parse import urlsplit

    try:
        hostname = urlsplit(uri).hostname
        return hostname or uri
    except Exception:
        return uri


# 次要通道命中配额后的短时重连间隔（秒）
SECONDARY_QUOTA_RECONNECT_INTERVAL = 120

# 次要通道在主通道离线时的等待间隔（秒）
SECONDARY_WAIT_PRIMARY_INTERVAL = 30


def is_fan_studio_connection(name: str) -> bool:
    """判断连接名是否属于 FAN Studio 家族。"""
    text = str(name or "").strip().lower()
    if not text:
        return False
    return text.startswith("fan_studio") or "fanstudio" in text


def is_fan_primary_connection(name: str) -> bool:
    """是否为应优先保活的 /all 主连接。"""
    return str(name or "").strip() == FAN_PRIMARY_CONNECTION


def is_fan_secondary_connection(name: str) -> bool:
    """是否为可让路的次要独立连接。"""
    text = str(name or "").strip()
    if not text:
        return False
    if is_fan_primary_connection(text):
        return False
    if text in FAN_SECONDARY_CONNECTIONS:
        return True
    # 兼容未来新增的 fan_studio_* 独立路径
    return is_fan_studio_connection(text) and text != FAN_PRIMARY_CONNECTION


def is_connection_limit_signal(text: str | Exception | None) -> bool:
    """识别连接数上限 / 配额 / 策略拒绝相关信号。"""
    raw = str(text or "").strip().lower()
    if not raw:
        return False

    keywords = (
        "连接数",
        "连接上限",
        "并发连接",
        "too many connection",
        "too many connections",
        "connection limit",
        "max connection",
        "maximum connection",
        "quota",
        "限流",
        "策略违规",
        "policy violation",
        "policy error",
        "1008",
    )
    return any(token in raw for token in keywords)


def is_tls_blocked_signal(text: str | Exception | None) -> bool:
    """识别 TLS/连接被中间设备 RST 阻断的信号。

    典型场景：目标主机 TCP 端口可达（ping / 裸 TCP 探测均通），但一旦发起
    TLS ClientHello（携带 SNI）就被连接路径上的设备（如 GFW/运营商）RST，
    表现为 "ConnectionResetError"（Windows errno=10054）或 aiohttp 包装后的
    "指定的网络名不再可用"（errno=10022/10054）。

    命中此类信号时说明当前目标地址在该网络环境下不可用，继续在同一地址上
    反复短时重试没有意义，应尽快切换到另一台服务器（主/备）。
    """
    raw = str(text or "").strip().lower()
    if not raw:
        return False

    keywords = (
        "10054",  # WSAECONNRESET：远程主机强迫关闭连接（常见于 TLS 被 RST）
        "10022",  # WSAEINVAL：无效参数（aiohttp 包装 SNI 阻断的典型 errno）
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


def list_active_fan_secondary_names(manager: Any) -> list[str]:
    """列出当前仍占用句柄的 FAN 次要连接名。"""
    connections = getattr(manager, "connections", {}) or {}
    names: list[str] = []
    for name, websocket in list(connections.items()):
        if not is_fan_secondary_connection(name):
            continue
        try:
            if websocket is not None and not getattr(websocket, "closed", True):
                names.append(name)
        except Exception:
            # 句柄异常时也视为可清理对象
            names.append(name)
    return sorted(names)


def is_primary_fan_connected(manager: Any) -> bool:
    """主通道 /all 是否当前在线。"""
    connections = getattr(manager, "connections", {}) or {}
    websocket = connections.get(FAN_PRIMARY_CONNECTION)
    if websocket is None:
        return False
    try:
        return not bool(getattr(websocket, "closed", True))
    except Exception:
        return False


async def yield_secondary_for_primary(
    manager: Any,
    *,
    reason: str,
) -> list[str]:
    """为保活主通道，主动释放次要 FAN 连接占用的配额。

    释放后会在 connection_info 上标记 quota_hit，确保主通道恢复后
    能被 _kick_deferred_fan_secondary_reconnects 重新唤醒。

    Returns:
        实际释放的次要连接名列表。
    """
    released: list[str] = []
    connection_info = getattr(manager, "connection_info", None)
    if not isinstance(connection_info, dict):
        connection_info = {}
        try:
            manager.connection_info = connection_info
        except Exception:
            # manager 不可写时仍尝试释放连接，仅无法持久化唤醒标记
            connection_info = {}

    for name in list_active_fan_secondary_names(manager):
        try:
            await manager._release_existing_connection(
                name,
                reason=reason,
                keep_connection_info=True,
            )
            # 取消次要通道上可能正在排队的重连，避免立刻抢回配额
            reconnect_tasks = getattr(manager, "reconnect_tasks", {}) or {}
            task = reconnect_tasks.pop(name, None)
            if task is not None and not task.done():
                task.cancel()

            # 关键唤醒标记：仅释放句柄不会自动重连，必须让 kick 路径可见。
            info = dict(connection_info.get(name) or {})
            info["quota_hit"] = True
            info.pop("quota_deferred", None)
            connection_info[name] = info

            released.append(name)
        except Exception as exc:
            logger.warning(
                f"[灾害预警] 释放次要 FAN 连接失败: {name}，原因: {reason}，错误: {exc}"
            )
            continue
    return released


def resolve_secondary_reconnect_interval(
    *,
    default_interval: int,
    quota_hit: bool,
    primary_online: bool,
) -> int:
    """计算次要通道重连间隔。"""
    base = max(1, int(default_interval or 10))
    if not primary_online:
        # 主通道离线时，次要通道先等主通道，避免抢占
        return max(base, SECONDARY_WAIT_PRIMARY_INTERVAL)
    if quota_hit:
        return max(base, SECONDARY_QUOTA_RECONNECT_INTERVAL)
    return base


def resolve_fan_auth_credentials(
    connection_info: dict[str, Any] | None,
) -> tuple[str, str]:
    """从连接附加信息解析 FAN Studio appId 与 API Key。"""
    info = connection_info if isinstance(connection_info, dict) else {}
    app_id = str(
        info.get("fan_app_id") or info.get("app_id") or info.get("appId") or ""
    ).strip()
    api_key = str(
        info.get("fan_api_key") or info.get("api_key") or info.get("key") or ""
    ).strip()
    return app_id, api_key


def attach_fan_auth_from_plan(
    connection_info: dict[str, Any],
    conn_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """将连接计划中的 FAN 鉴权字段写入 connection_info。

    供 runtime 建连与手动重连共用，避免两处字段拷贝逻辑漂移。
    """
    cfg = conn_config if isinstance(conn_config, dict) else {}
    if cfg.get("fan_app_id"):
        connection_info["fan_app_id"] = cfg["fan_app_id"]
    if cfg.get("fan_api_key"):
        connection_info["fan_api_key"] = cfg["fan_api_key"]
    return connection_info


def build_fan_auth_payload(app_id: str, api_key: str) -> dict[str, str]:
    """构造 FAN Studio WebSocket 鉴权 JSON 对象。"""
    return {
        "type": "auth",
        "appId": str(app_id or "").strip(),
        "key": str(api_key or "").strip(),
    }


async def send_fan_studio_auth(
    websocket: Any,
    *,
    connection_name: str,
    connection_info: dict[str, Any] | None,
) -> bool:
    """在 FAN Studio 连接建立后发送鉴权包。

    Returns:
        True 表示已成功发送；False 表示缺少凭证（配置问题）。

    Raises:
        网络/传输相关异常会原样向上抛出，由 websocket_manager
        走标准重试与退避逻辑，避免被误判为永久配置失败。
    """
    if not is_fan_studio_connection(connection_name):
        return True

    app_id, api_key = resolve_fan_auth_credentials(connection_info)
    if not app_id or not api_key:
        logger.error(
            f"[灾害预警] FAN Studio 连接 {connection_name} 缺少 appId/api_key，"
            "无法发送鉴权包"
        )
        return False

    payload = build_fan_auth_payload(app_id, api_key)
    # 不吞掉 send_str 异常：瞬时网络错误应进入 manager 的重试路径。
    await websocket.send_str(json.dumps(payload, ensure_ascii=False))
    logger.debug(
        f"[灾害预警] 已向 FAN Studio 连接 {connection_name} 发送鉴权包 "
        f"(appId 为 {app_id[:8]}…)"
    )
    return True
