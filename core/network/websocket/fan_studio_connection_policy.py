"""
FAN Studio 连接配额与优先级策略。

上游按 IP 限制并发 WebSocket（通常最多 5 条）。
插件侧策略：
1. 启动时先建 fan_studio_all（/all），主通道在线后再建次要独立通道
2. 运行中优先保活 /all；主通道遇配额/策略拒绝时，才释放次要通道让路
3. 次要通道在主通道离线或命中配额时拉长退避，避免与 /all 抢连接
4. 建连成功后发送应用层鉴权包：{"type":"auth","appId":"...","key":"sk-..."}
"""

from __future__ import annotations

import json
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
FAN_IP_CONNECTION_LIMIT = 5

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
        True 表示已成功发送；False 表示缺少凭证或发送失败。
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
    try:
        await websocket.send_str(json.dumps(payload, ensure_ascii=False))
        logger.debug(
            f"[灾害预警] 已向 FAN Studio 连接 {connection_name} 发送鉴权包 "
            f"(appId 为 {app_id[:8]}…)"
        )
        return True
    except Exception as exc:
        logger.error(
            f"[灾害预警] FAN Studio 连接 {connection_name} 鉴权包发送失败: {exc}"
        )
        return False
