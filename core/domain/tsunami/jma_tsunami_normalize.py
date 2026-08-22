"""
日本气象厅海啸情报字段归一化。

统一处理 P2P / EQSC 在等级、区域、布尔值与内容指纹上的差异，
避免解析器、去重器与展示器各自维护一套规则。
"""

from __future__ import annotations

import json
from typing import Any

# 等级从低到高；用于取“全域最高警报级别”。
GRADE_ORDER: list[str] = [
    "None",
    "Unknown",
    "Minor",
    "Watch",
    "Warning",
    "MajorWarning",
]

# 展示标题映射（日文官方用语）。
GRADE_TITLE_MAP: dict[str, str] = {
    "MajorWarning": "大津波警報",
    "Warning": "津波警報",
    "Watch": "津波注意報",
    "Minor": "若干の海面変動",
    "Unknown": "津波予報",
    "None": "津波予報",
    "解除": "津波予報（解除）",
}


def coerce_bool(value: Any, default: bool = False) -> bool:
    """宽松解析布尔值，兼容 EQSC 字符串 true/false。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", "null", "none"}:
        return False
    return default


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return ""
    return text


def _normalize_grade(raw_grade: Any, *, cancelled: bool = False) -> str:
    if cancelled:
        return "解除"
    grade = _clean_text(raw_grade) or "Unknown"
    # 兼容偶发大小写差异
    lowered = grade.lower()
    mapping = {
        "majorwarning": "MajorWarning",
        "warning": "Warning",
        "watch": "Watch",
        "minor": "Minor",
        "none": "None",
        "unknown": "Unknown",
        "解除": "解除",
    }
    return mapping.get(lowered, grade)


def normalize_jma_tsunami_areas(
    areas: Any,
    *,
    cancelled: bool = False,
) -> list[dict[str, Any]]:
    """把 P2P / EQSC 区域列表归一为展示与去重共用结构。"""
    if not isinstance(areas, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in areas:
        if not isinstance(item, dict):
            continue
        name = _clean_text(
            item.get("name") or item.get("forecastArea") or item.get("forecastPoint")
        )
        if not name:
            continue

        first_height = item.get("firstHeight")
        if not isinstance(first_height, dict):
            first_height = {}
        max_height = item.get("maxHeight")
        if not isinstance(max_height, dict):
            max_height = {}

        condition = _clean_text(
            first_height.get("condition")
            or item.get("condition")
            or item.get("estimatedArrivalTime")
        )
        arrival_time = _clean_text(
            first_height.get("arrivalTime")
            or item.get("estimatedArrivalTime")
            or item.get("arrivalTime")
        )
        max_desc = _clean_text(
            max_height.get("description")
            or item.get("maxWaveHeight")
            or item.get("max_height")
        )
        max_value_raw = max_height.get("value")
        if max_value_raw is None:
            max_value_raw = item.get("maxHeightValue")
        max_value_text = _clean_text(max_value_raw)
        max_value: float | None = None
        if max_value_text:
            try:
                max_value = float(max_value_text)
            except ValueError:
                max_value = None

        grade = _normalize_grade(
            item.get("grade") or item.get("warningLevel"), cancelled=cancelled
        )
        immediate = coerce_bool(item.get("immediate"), default=False)

        # 展示侧统一消费这些键；保留原始块便于排障。
        normalized.append(
            {
                "name": name,
                "grade": grade,
                "immediate": immediate,
                "condition": condition,
                "estimatedArrivalTime": arrival_time,
                "maxWaveHeight": max_desc or max_value_text,
                "maxHeightValue": max_value,
                "maxHeightDescription": max_desc,
                "firstHeight": dict(first_height) if first_height else {},
                "maxHeight": dict(max_height) if max_height else {},
            }
        )
    return normalized


def resolve_jma_tsunami_max_grade(
    areas: list[dict[str, Any]] | None,
    *,
    cancelled: bool = False,
) -> str:
    """从归一化区域列表中取最高警报等级。"""
    if cancelled:
        return "解除"
    max_grade = "Unknown"
    max_idx = 0
    for area in areas or []:
        grade = _normalize_grade(area.get("grade"), cancelled=False)
        if grade not in GRADE_ORDER:
            continue
        idx = GRADE_ORDER.index(grade)
        if idx > max_idx:
            max_idx = idx
            max_grade = grade
    return max_grade


def resolve_jma_tsunami_title(
    max_grade: str,
    *,
    cancelled: bool = False,
) -> str:
    """根据最高等级生成标题。"""
    if cancelled or max_grade == "解除":
        return GRADE_TITLE_MAP["解除"]
    return GRADE_TITLE_MAP.get(max_grade, "津波予報")


def build_jma_tsunami_content_fingerprint(
    *,
    event_id: str = "",
    cancelled: bool = False,
    max_grade: str = "",
    areas: list[dict[str, Any]] | None = None,
    is_training: bool = False,
) -> str:
    """构建同源内容指纹（事件身份 + 等级 + 区域核心字段）。

    用于同源去重：同一数据源内，内容未变化时过滤重复推送。
    P2P 与 EQSC 各自独立推送，不再做跨源去重。

    event_id 必须参与指纹：解除报 areas 通常为空，若不含事件身份，
    不同事件的解除会撞成同一指纹，导致后续解除通知被误杀。
    """
    area_rows: list[dict[str, Any]] = []
    for area in areas or []:
        if not isinstance(area, dict):
            continue
        area_rows.append(
            {
                "name": _clean_text(area.get("name")),
                "grade": _normalize_grade(area.get("grade"), cancelled=cancelled),
                "immediate": bool(area.get("immediate")),
                "condition": _clean_text(area.get("condition")),
                "arrival": _clean_text(area.get("estimatedArrivalTime")),
                "height": _clean_text(
                    area.get("maxWaveHeight") or area.get("maxHeightDescription")
                ),
            }
        )
    area_rows.sort(
        key=lambda row: (row["name"], row["grade"], row["condition"], row["height"])
    )
    payload = {
        "event_id": _clean_text(event_id),
        "cancelled": bool(cancelled),
        "max_grade": _normalize_grade(max_grade, cancelled=cancelled),
        "is_training": bool(is_training),
        "areas": area_rows,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


__all__ = [
    "GRADE_ORDER",
    "GRADE_TITLE_MAP",
    "build_jma_tsunami_content_fingerprint",
    "coerce_bool",
    "normalize_jma_tsunami_areas",
    "resolve_jma_tsunami_max_grade",
    "resolve_jma_tsunami_title",
]
