"""载荷特征守卫。

跨路由与解析器共享的误识别防护逻辑，避免 FSSN / USGS 等报文
被宽松签名误判为 ShakeAlert。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# FSSN 常见特征字段（不含 url；url 更偏向 USGS）
FSSN_MARKER_FIELDS: tuple[str, ...] = (
    "infoTypeName",
    "createTime",
    "placeName_zh",
)

# ShakeAlert 路由排除字段：FSSN 特征 + USGS 详情页
SHAKEALERT_EXCLUSION_FIELDS: tuple[str, ...] = (
    "url",
    "infoTypeName",
    "createTime",
    "placeName_zh",
)


def is_fssn_event_id(event_id: object) -> bool:
    """事件 ID 是否呈现 FSSN 前缀。"""
    return str(event_id or "").strip().upper().startswith("FSSN")


def has_fssn_marker_fields(
    payload: dict[str, Any],
    *,
    get_value: Callable[[str], Any] | None = None,
) -> bool:
    """载荷是否含 FSSN 特征字段。

    Args:
        payload: 解包后的业务载荷。
        get_value: 可选取值函数（解析器侧可传入兼容大小写的 _get_field）。
            未提供时按「键是否存在」判定（路由侧）。
    """
    if not isinstance(payload, dict):
        return False
    if get_value is not None:
        return any(get_value(field) is not None for field in FSSN_MARKER_FIELDS)
    return any(field in payload for field in FSSN_MARKER_FIELDS)


def looks_like_fssn_payload(
    payload: dict[str, Any],
    *,
    get_value: Callable[[str], Any] | None = None,
    event_id: object | None = None,
) -> bool:
    """综合 ID 前缀与特征字段，判断是否像 FSSN 报文。"""
    if not isinstance(payload, dict):
        return False
    resolved_id = event_id
    if resolved_id is None:
        if get_value is not None:
            resolved_id = get_value("id")
        else:
            resolved_id = payload.get("id")
    if is_fssn_event_id(resolved_id):
        return True
    return has_fssn_marker_fields(payload, get_value=get_value)


def is_shakealert_compatible_payload(payload: dict[str, Any]) -> bool:
    """ShakeAlert 路由谓词：排除 FSSN / USGS 特征后视为兼容。"""
    if not isinstance(payload, dict):
        return False
    if is_fssn_event_id(payload.get("id")):
        return False
    if any(key in payload for key in SHAKEALERT_EXCLUSION_FIELDS):
        return False
    return True


__all__ = [
    "FSSN_MARKER_FIELDS",
    "SHAKEALERT_EXCLUSION_FIELDS",
    "has_fssn_marker_fields",
    "is_fssn_event_id",
    "is_shakealert_compatible_payload",
    "looks_like_fssn_payload",
]
