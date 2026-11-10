"""
统一数据源路由别名映射。
负责维护来源名称、消息类型与统一数据源标识之间的路由辅助逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .payload_guards import (
    is_shakealert_compatible_payload,
    looks_like_fssn_cmt_payload,
)
from .source_catalog import (
    SOURCE_CATALOG,
    get_source_ids_by_family,
    get_source_ids_by_provider_message_type,
    get_source_ids_by_provider_source_name,
)
from .source_entry import ProviderFamily, SourceEntry


@dataclass(frozen=True, slots=True)
class RoutedMessage:
    """统一的路由结果。

    用于把原始提供方消息转换为统一数据源标识和对应载荷的组合对象。
    """

    source_name: str  # 原始提供方定义的来源名称或通道名
    source_id: str  # 匹配成功后的统一数据源标识
    payload: dict[str, Any]  # 解包后的实际业务数据载荷


def _unwrap_nested_payload(data: dict[str, Any], max_depth: int = 3) -> dict[str, Any]:
    """提取 Jian Project 兼容消息中的核心载荷。

    会递归下钻常见的 `Data` 或 `data` 包裹层，便于后续统一匹配负载特征。
    """
    msg_data: Any = data
    depth = 0
    # 循环提取嵌套的 data 或 Data 字典，限制最大深度防死循环
    while (
        isinstance(msg_data, dict)
        and ("Data" in msg_data or "data" in msg_data)
        and depth < max_depth
    ):
        msg_data = msg_data.get("Data") or msg_data.get("data")
        depth += 1
    if isinstance(msg_data, dict):
        return msg_data
    return data if isinstance(data, dict) else {}


def _payload_has_signature(payload: dict[str, Any], signature: tuple[str, ...]) -> bool:
    """判断载荷是否同时具备一组关键签名字段。"""
    return all(key in payload for key in signature)


def _payload_matches_predicate(payload: dict[str, Any], predicate: str) -> bool:
    """依据高级自定义规则对特定数据源进行精细匹配。"""
    if predicate == "weather_alert":
        # 气象预警必须具有标题类字段并且有 type 标识
        return ("title" in payload or "headline" in payload) and "type" in payload
    if predicate == "cenc_report":
        # 中国地震台网正式测定和自动测定消息都会在类型名中带出固定字样
        info_type_name = str(payload.get("infoTypeName", "") or "")
        return "[正式测定]" in info_type_name or "[自动测定]" in info_type_name
    if predicate == "cenc_intensity_report":
        # 烈度速报：必须有 uniEventId，且具备速报正文/台站/等震线之一；
        # 若误带正式/自动测定类型名，直接排除，避免与 /cenc 测定混淆。
        uni_event_id = str(payload.get("uniEventId", "") or "").strip()
        if not uni_event_id:
            return False
        info_type_name = str(payload.get("infoTypeName", "") or "")
        if "[正式测定]" in info_type_name or "[自动测定]" in info_type_name:
            return False
        has_report_body = any(
            key in payload
            for key in (
                "intensity_info_text",
                "instrument_intensity_json",
                "contour_geojson",
                "nameByInfo",
            )
        )
        return has_report_body
    if predicate == "usgs_report":
        # USGS 报文通常会附带官方详情地址，可作为辅助识别条件
        return "usgs.gov" in str(payload.get("url", "") or "")
    if predicate == "fssn_cmt":
        # FSSN CMT：检测到符合 FSSN CMT 特征的载荷
        return looks_like_fssn_cmt_payload(payload)
    if predicate == "shakealert_eew":
        # ShakeAlert 与 FSSN 字段高度重合；共享守卫排除 FSSN/USGS/CMT 特征。
        return is_shakealert_compatible_payload(payload)
    if predicate == "typhoon_active":
        # 台风数据必须包含移动方向、风速和气压字段，且数据为数组格式
        # Jian Project 台风推送的 Data 字段是数组，unwrap 后可能仍为数组
        if isinstance(payload, list):
            return len(payload) > 0 and isinstance(payload[0], dict)
        if isinstance(payload, dict):
            # 兼容单对象格式
            return "moveDirection" in payload and "windSpeed" in payload
    return False


def _matches_payload_rule(payload: dict[str, Any], entry: SourceEntry) -> bool:
    """判断消息载荷是否满足某个注册项的匹配规则。"""
    if not isinstance(payload, dict):
        return False

    # 先检查必要签名字段，只要有一组命中即可进入下一步
    if entry.payload_signatures:
        signature_ok = any(
            _payload_has_signature(payload, signature)
            for signature in entry.payload_signatures
        )
        if not signature_ok:
            return False

    # 若命中了排除字段组合，则说明这条消息更可能属于其他来源，直接剔除
    if entry.payload_exclusions:
        for excluded_keys in entry.payload_exclusions:
            if all(key in payload for key in excluded_keys):
                return False

    # 当仅靠字段签名仍不足以区分来源时，再用补充谓词做细判
    if entry.payload_predicates:
        return any(
            _payload_matches_predicate(payload, predicate)
            for predicate in entry.payload_predicates
        )

    return bool(entry.payload_signatures)


def get_provider_source_map(provider_family: ProviderFamily) -> dict[str, str]:
    """按提供方家族导出名称到数据源标识的映射。"""
    result: dict[str, str] = {}
    # Jian Project / PancakesAPI 主要按来源名映射，Wolfx 主要按消息类型映射
    for source_id in get_source_ids_by_family(provider_family):
        entry = SOURCE_CATALOG[source_id]
        if provider_family in (
            ProviderFamily.GLOBAL_QUAKE,
            ProviderFamily.JIAN_PROJECT,
        ):
            for source_name in entry.provider_source_names:
                result.setdefault(source_name, source_id)
        elif provider_family == ProviderFamily.WOLFX:
            for message_type in entry.provider_message_types:
                result.setdefault(message_type, source_id)
    return result




def get_wolfx_source_id(message_type: str) -> str | None:
    """根据 Wolfx 消息类型解析统一数据源标识。"""
    source_ids = get_source_ids_by_provider_message_type((message_type or "").strip())
    if not source_ids:
        return None
    return source_ids[0]


def get_pancakes_source_id(source_name: str | None) -> str | None:
    """根据 PancakesAPI RealtimeEvent.source 解析统一数据源标识。

    /ws/all 聚合推送会保留原始 source（gq / nmefc / nmefc-wave / nmefc-surge / cma）。
    当前已接入 Global Quake（gq）与中国气象局气象预警（cma）；
    其余 source 返回 None，便于后续继续挂接。
    source 缺失时按历史兼容回落到 global_quake。
    """
    name = str(source_name or "").strip().lower()
    if not name:
        return "global_quake"

    # 优先走目录声明的 provider_source_names（大小写不敏感）
    for source_id in get_source_ids_by_family(ProviderFamily.GLOBAL_QUAKE):
        entry = SOURCE_CATALOG[source_id]
        aliases = {
            str(item or "").strip().lower()
            for item in (entry.provider_source_names or ())
        }
        aliases.update(
            str(item or "").strip().lower() for item in (entry.provider_aliases or ())
        )
        aliases.add(str(entry.source_id or "").strip().lower())
        if name in aliases:
            return source_id

    # 兼容直接按 source_id 命中
    if name in SOURCE_CATALOG and SOURCE_CATALOG[name].provider_family == (
        ProviderFamily.GLOBAL_QUAKE
    ):
        return name
    return None


# 向后兼容旧函数名
get_openquake_source_id = get_pancakes_source_id







# 预构建常用注册表，便于上层快速按来源名或消息类型查找统一数据源标识
WOLFX_SOURCE_REGISTRY = get_provider_source_map(ProviderFamily.WOLFX)
# Pancakes 聚合路由走 get_pancakes_source_id()，不预构建未使用的 registry


def get_jian_project_source_id(source_name: str | None) -> str | None:
    """根据 Jian Project 消息的 type 解析统一数据源标识。"""
    name = str(source_name or "").strip().lower()
    if not name:
        return None
    for source_id in get_source_ids_by_family(ProviderFamily.JIAN_PROJECT):
        entry = SOURCE_CATALOG[source_id]
        aliases = {
            str(item or "").strip().lower()
            for item in (entry.provider_source_names or ())
        }
        aliases.update(
            str(item or "").strip().lower() for item in (entry.provider_aliases or ())
        )
        aliases.add(str(entry.source_id or "").strip().lower())
        if name in aliases:
            return source_id
    return None


def route_jian_project_message(data: dict[str, Any]) -> list[RoutedMessage]:
    """统一解析 Jian Project 消息并返回路由结果列表。

    按 SOURCE_CATALOG 中 Jian Project 家族的 type 别名路由。
    """
    if not isinstance(data, dict):
        return []

    msg_type = str(data.get("type") or "").strip().lower()
    if not msg_type or msg_type in ("heartbeat", "all", "pong"):
        return []

    source_id = get_jian_project_source_id(msg_type)
    if not source_id:
        return []

    # 提取内部 Data 负载；若不为 dict 则直接透传 data
    raw_payload = data.get("Data")
    payload = raw_payload if isinstance(raw_payload, dict) else data

    return [
        RoutedMessage(
            source_name=msg_type,
            source_id=source_id,
            payload=payload,
        )
    ]


__all__ = [
    "RoutedMessage",
    "WOLFX_SOURCE_REGISTRY",
                "get_jian_project_source_id",
    "get_openquake_source_id",
    "get_pancakes_source_id",
    "get_provider_source_map",
    "get_wolfx_source_id",
        "route_jian_project_message",
]
