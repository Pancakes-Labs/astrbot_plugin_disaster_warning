"""台风查询命令文本展示。

只负责把统一查询结果格式化为命令侧可读文本，
依赖领域展示工具，不反向依赖 TyphoonPresenter。
"""

from __future__ import annotations

import math
from typing import Any

from ...domain.typhoon.typhoon_display_format import (
    format_coordinates,
    format_move_direction,
    format_typhoon_short_id,
    format_wind_circle,
    format_wind_speed,
    get_typhoon_level_emoji,
    is_valid_radius_value,
)
from ..geo.region_service import region_service
from .typhoon_query_parser import DETAIL_CURRENT, DETAIL_FULL

# 统一短编号格式化复用公共展示工具，避免展示逻辑分叉。
_format_typhoon_short_id = format_typhoon_short_id


def _format_reference_place(latitude: Any, longitude: Any) -> str:
    """根据经纬度解析 F-E 区域近似地名（如“菲律宾海附近”）。

    拒绝非有限坐标（inf/NaN），避免传给 region_service 时触发
    int(lat + 90) 的 OverflowError。
    """
    try:
        lat_num = float(latitude)
        lon_num = float(longitude)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(lat_num) or not math.isfinite(lon_num):
        return ""
    return region_service.get_fe_name(lat_num, lon_num) or ""


def build_summary_text(item: dict[str, Any], *, detail: str) -> str:
    """生成单条台风摘要文本，供命令侧与详情卡片复用。"""
    lines: list[str] = []
    display_name = item.get("display_name") or "未知台风"

    # 状态后缀
    status_suffix = ""
    if item.get("is_active") is False:
        status_suffix = "（已停止编报）"
    elif item.get("is_active") is True:
        status_suffix = "（活跃编报中）"

    lines.append(f"{display_name}{status_suffix}")
    lines.append("")

    short_id = _format_typhoon_short_id(
        item.get("eqsc_id"),
        item.get("typhoon_id"),
        item.get("real_event_id"),
        item.get("unique_id"),
    )
    if short_id:
        lines.append(f"📌编号：{short_id}")

    typhoon_type = item.get("typhoon_type") or ""
    level_emoji = get_typhoon_level_emoji(typhoon_type)
    if typhoon_type:
        lines.append(f"⚠️等级：{typhoon_type}{level_emoji}")

    coords = format_coordinates(item.get("latitude"), item.get("longitude"))
    if coords:
        lines.append(f"🌍中心位置：({coords})")
        reference = _format_reference_place(item.get("latitude"), item.get("longitude"))
        if reference:
            lines.append(f"📍参考位置：{reference}")

    wind_speed = item.get("wind_speed")
    power = item.get("power")
    wind_text = format_wind_speed(
        float(wind_speed) if wind_speed is not None else None,
        int(power) if power is not None else None,
    )
    if wind_text:
        lines.append(f"💨最大风速：{wind_text}")

    if item.get("pressure") is not None:
        lines.append(f"🎈中心气压：{item.get('pressure')} hPa")

    move_parts: list[str] = []
    if item.get("move_direction"):
        move_parts.append(format_move_direction(str(item.get("move_direction"))))
    if item.get("move_speed") is not None:
        move_parts.append(f"({item.get('move_speed')} KM/H)")
    if move_parts:
        lines.append(f"🧭移动方向：{' '.join(move_parts)}")

    circle_lines = format_wind_circle(item.get("wind_circle") or {})
    if circle_lines:
        lines.append("")
        lines.append(f"🌪️风圈半径：{circle_lines[0]}")
        lines.extend(circle_lines[1:])
    else:
        radius_lines: list[str] = []
        if is_valid_radius_value(item.get("radius7")):
            radius_lines.append(f"7 级：{item.get('radius7')} (KM)")
        if is_valid_radius_value(item.get("radius10")):
            radius_lines.append(f"10级：{item.get('radius10')} (KM)")
        if radius_lines:
            lines.append("")
            lines.append("🌪️风圈半径：")
            lines.extend(radius_lines)

    if item.get("updated_at_text"):
        lines.append("")
        lines.append(f"🕒更新时间：{item.get('updated_at_text')}")

    if detail == DETAIL_FULL:
        track = item.get("track_summary") or {}
        history_lines = track.get("history_lines") or []
        future_lines = track.get("future_lines") or []
        if history_lines:
            lines.append("")
            history_total = int(track.get("history_count") or len(history_lines))
            lines.append(f"📜历史路径（共 {history_total} 点）：")
            for line in history_lines:
                lines.append(f"  • {line}")
        if future_lines:
            lines.append("")
            future_total = int(track.get("future_count") or len(future_lines))
            lines.append(f"🔮预报路径（共 {future_total} 点）：")
            for line in future_lines:
                lines.append(f"  • {line}")
        if not history_lines and not future_lines:
            lines.append("")
            lines.append("📜完整路径：当前数据源未提供轨迹节点")

    return "\n".join(lines)


def attach_summary_text(item: dict[str, Any], *, detail: str) -> dict[str, Any]:
    """就地补充 summary_text 后返回同一对象。"""
    item["summary_text"] = build_summary_text(item, detail=detail)
    return item


def build_typhoon_query_text(result: dict[str, Any]) -> str:
    """把查询结果格式化为命令侧可读文本。

    - 单条（id）：直接输出摘要正文。
    - 多条（list/search）：
      - current：紧凑列表
      - full：每条输出完整摘要，条目之间用分隔线区分
    """
    if not result.get("success"):
        error = str(result.get("error") or "查询失败")
        lines = [f"❌ {error}"]
        filters = result.get("filters")
        if isinstance(filters, dict):
            parts = []
            if filters.get("keyword"):
                parts.append(f"关键词={filters.get('keyword')}")
            if filters.get("active_only"):
                parts.append("仅活跃=是")
            if filters.get("count"):
                parts.append(f"数量={filters.get('count')}")
            if parts:
                lines.append(f"检索条件：{'，'.join(parts)}")
        usage = result.get("usage") or []
        if usage:
            lines.append("用法：")
            lines.extend(f"• {line}" for line in usage)
        return "\n".join(lines)

    # 单条详情不再附加“来源/模式”标题行，直接输出摘要正文。
    if result.get("query_mode") == "id":
        data = result.get("data") or {}
        return str(data.get("summary_text") or "暂无详情")

    source = result.get("source") or "unknown"
    detail = result.get("detail") or DETAIL_CURRENT
    source_label = {
        "eqsc": "EQSC",
        "local": "本地数据库",
    }.get(str(source), str(source))
    fallback_hint = ""
    if result.get("fallback_from") == "eqsc":
        fallback_hint = "，EQSC不可用已回退"

    items = result.get("items") or []
    total = result.get("total", len(items))
    lines = [
        f"🌀 台风信息列表（共 {total} 条，来源：{source_label}{fallback_hint}，"
        f"模式：{'完整路径' if detail == DETAIL_FULL else '当前信息'}）",
        "",
    ]

    if detail == DETAIL_FULL:
        for index, item in enumerate(items, start=1):
            lines.append(f"──── 第 {index}/{total} 条 ────")
            summary = str(item.get("summary_text") or "").strip()
            if summary:
                lines.append(summary)
            else:
                lines.append(item.get("display_name") or "未知台风")
            if index != len(items):
                lines.append("")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        lines.append(f"[{index}] {item.get('display_name') or '未知台风'}")
        short_id = (
            _format_typhoon_short_id(
                item.get("eqsc_id"),
                item.get("typhoon_id"),
                item.get("real_event_id"),
                item.get("unique_id"),
            )
            or "未知"
        )
        lines.append(f"编号：{short_id}")
        level = item.get("typhoon_type") or "未知等级"
        level_emoji = get_typhoon_level_emoji(level)
        lines.append(f"等级：{level}{level_emoji}")
        if item.get("is_active") is False:
            lines.append("状态：已停止编报")
        elif item.get("is_active") is True:
            lines.append("状态：活跃")
        wind = item.get("wind_speed")
        if wind is not None:
            lines.append(f"最大风速：{wind} m/s")
        pressure = item.get("pressure")
        if pressure is not None:
            lines.append(f"中心气压：{pressure} hPa")
        if item.get("updated_at_text"):
            lines.append(f"更新时间：{item.get('updated_at_text')}")
        if index != len(items):
            lines.append("")
    return "\n".join(lines)


__all__ = [
    "attach_summary_text",
    "build_summary_text",
    "build_typhoon_query_text",
]
