"""
统一数据源路由别名映射。
负责维护来源名称、消息类型与统一数据源标识之间的路由辅助逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .payload_guards import is_shakealert_compatible_payload
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
    """提取 FAN Studio 兼容消息中的核心载荷。

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
    if predicate == "shakealert_eew":
        # ShakeAlert 与 FSSN 字段高度重合；共享守卫排除 FSSN/USGS 特征。
        return is_shakealert_compatible_payload(payload)
    if predicate == "typhoon_active":
        # 台风数据必须包含移动方向、风速和气压字段，且数据为数组格式
        # FAN Studio 台风推送的 Data 字段是数组，unwrap 后可能仍为数组
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
    # FAN Studio 主要按来源名映射，Wolfx 主要按消息类型映射
    for source_id in get_source_ids_by_family(provider_family):
        entry = SOURCE_CATALOG[source_id]
        if provider_family == ProviderFamily.FAN_STUDIO:
            for source_name in entry.provider_source_names:
                result.setdefault(source_name, source_id)
        elif provider_family == ProviderFamily.WOLFX:
            for message_type in entry.provider_message_types:
                result.setdefault(message_type, source_id)
    return result


def get_fan_studio_source_id(source_name: str) -> str | None:
    """根据 FAN Studio 来源名称解析统一数据源标识。"""
    source_ids = get_source_ids_by_provider_source_name((source_name or "").strip())
    if not source_ids:
        return None
    return source_ids[0]


def get_wolfx_source_id(message_type: str) -> str | None:
    """根据 Wolfx 消息类型解析统一数据源标识。"""
    source_ids = get_source_ids_by_provider_message_type((message_type or "").strip())
    if not source_ids:
        return None
    return source_ids[0]


def detect_fan_studio_source_entry(data: dict[str, Any]) -> SourceEntry | None:
    """根据消息载荷特征识别 FAN Studio 注册项。"""
    if not isinstance(data, dict):
        return None

    payload = _unwrap_nested_payload(data)
    # 仅挑出具备来源名声明的 FAN Studio 注册项，并按优先级排序后逐个匹配
    fan_entries = [
        entry
        for source_id in get_source_ids_by_family(ProviderFamily.FAN_STUDIO)
        if (entry := SOURCE_CATALOG[source_id]).provider_source_names
    ]
    # 按优先级从高到低排序，高优先级优先检测，防止通用宽松规则覆盖了精确规则
    fan_entries.sort(key=lambda entry: (entry.priority, entry.source_id), reverse=True)

    for entry in fan_entries:
        if _matches_payload_rule(payload, entry):
            return entry
    return None


def detect_fan_studio_source_id(data: dict[str, Any]) -> str | None:
    """从 FAN Studio 兼容消息直接识别统一数据源标识。"""
    entry = detect_fan_studio_source_entry(data)
    if entry is None:
        return None
    return entry.source_id


def route_fan_studio_message(data: dict[str, Any]) -> list[RoutedMessage]:
    """统一解析 FAN Studio 消息并返回路由结果列表。

    兼容全量初始化消息、增量更新消息以及缺少显式来源名的特征识别场景。
    """
    if not isinstance(data, dict):
        return []

    routed_messages: list[RoutedMessage] = []
    msg_type = str(data.get("type") or "").strip()

    # initial_all 表示一条消息里携带多个来源的初始化快照，需要逐项拆包
    if msg_type == "initial_all":
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            source_id = get_fan_studio_source_id(key)
            if source_id:
                routed_messages.append(
                    RoutedMessage(source_name=key, source_id=source_id, payload=value)
                )
        return routed_messages

    if msg_type == "update":
        # update 消息通常携带显式 source。
        # 1) 已注册显式来源：直接路由，避免被宽松载荷签名覆盖
        # 2) 未注册显式来源（如 fssn）：直接丢弃，禁止特征猜测误路由
        # 3) 无显式来源：才按载荷特征做兼容识别
        source_name = str(data.get("source") or "").strip()
        if source_name:
            source_id = get_fan_studio_source_id(source_name)
            if source_id:
                return [
                    RoutedMessage(
                        source_name=source_name, source_id=source_id, payload=data
                    )
                ]
            # 显式来源存在但未适配：不猜测，避免 FSSN 等被误识别为 SA
            return []

        detected_entry = detect_fan_studio_source_entry(data)
        if detected_entry is not None:
            routed_source_name = (
                detected_entry.provider_source_names[0]
                if detected_entry.provider_source_names
                else detected_entry.source_id
            )
            return [
                RoutedMessage(
                    source_name=routed_source_name,
                    source_id=detected_entry.source_id,
                    payload=data,
                )
            ]

    explicit_source = str(data.get("source") or "").strip()
    # 若显式来源存在却没匹配成功，则不再做特征猜测，避免误路由
    if explicit_source:
        return []

    detected_entry = detect_fan_studio_source_entry(data)
    # 最后一层兜底：对缺少来源字段的兼容消息尝试按载荷特征反推来源
    if detected_entry is None:
        return []

    routed_source_name = (
        detected_entry.provider_source_names[0]
        if detected_entry.provider_source_names
        else detected_entry.source_id
    )
    return [
        RoutedMessage(
            source_name=routed_source_name,
            source_id=detected_entry.source_id,
            payload=data,
        )
    ]


# 预构建两类常用注册表，便于上层快速按来源名或消息类型查找统一数据源标识
FAN_STUDIO_SOURCE_REGISTRY = get_provider_source_map(ProviderFamily.FAN_STUDIO)
WOLFX_SOURCE_REGISTRY = get_provider_source_map(ProviderFamily.WOLFX)


__all__ = [
    "RoutedMessage",
    "FAN_STUDIO_SOURCE_REGISTRY",
    "WOLFX_SOURCE_REGISTRY",
    "detect_fan_studio_source_entry",
    "detect_fan_studio_source_id",
    "get_fan_studio_source_id",
    "get_provider_source_map",
    "get_wolfx_source_id",
    "route_fan_studio_message",
]
