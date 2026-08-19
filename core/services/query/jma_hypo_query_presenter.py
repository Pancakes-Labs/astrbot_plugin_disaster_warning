"""
JMA 震央分布文本展示。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .jma_hypo_query_parser import format_date_range_text
from .jma_hypo_query_service import LARGE_EVENT_MIN_MAG, MAG_BINS


def _fmt_mag(value: float | None) -> str:
    if value is None:
        return "--"
    return f"M{float(value):.1f}"


def _fmt_dep(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{float(value):.0f} km"


def _fmt_event_time(event: dict[str, Any]) -> str:
    occurred_at = event.get("occurred_at")
    if isinstance(occurred_at, datetime):
        return occurred_at.strftime("%m-%d %H:%M")
    date_str = str(event.get("date_str") or "").strip()
    if not date_str:
        return "--"
    # 2026/07/20.00:10 / 2026/07/18.07:52:19.25 -> 07-20 00:10
    text = date_str.replace("/", "-")
    if "." in text:
        day, hm = text.split(".", 1)
        hm = hm.split(".")[0]
        if hm.count(":") >= 2:
            hm = ":".join(hm.split(":")[:2])
        parts = day.split("-")
        if len(parts) == 3:
            return f"{parts[1]}-{parts[2]} {hm}"
    return text


def _iter_display_mag_bins(
    max_mag: float | None,
) -> list[tuple[float, float | None, str]]:
    """按实际最大震级截断分档，最高到 M8.0。"""
    if max_mag is None:
        return list(MAG_BINS[:1])
    cutoff = float(max_mag)
    selected: list[tuple[float, float | None, str]] = []
    for low, high, label in MAG_BINS:
        selected.append((low, high, label))
        if high is None:
            break
        if cutoff < high:
            break
    return selected


def build_jma_hypo_list_text(result: dict[str, Any]) -> str:
    """构建列表命令纯文本。"""
    if not result.get("success"):
        lines = [f"❌ {result.get('error') or '查询失败'}"]
        usage = result.get("usage") or []
        if usage:
            lines.append("用法：")
            lines.extend(f"• {item}" for item in usage)
        return "\n".join(lines)

    start = result.get("start_date")
    end = result.get("end_date")
    if hasattr(start, "isoformat") and hasattr(end, "isoformat"):
        range_text = format_date_range_text(start, end)
    else:
        range_text = str(result.get("date_range_text") or "")

    stats = result.get("stats") or {}
    total = int(stats.get("total") or 0)
    requested_days = int(result.get("requested_days") or 0)
    covered_days = int(result.get("covered_days") or 0)

    lines: list[str] = [
        "[JMA/日本气象厅 震央分布]",
        f"时间范围：{range_text}（{requested_days}天）",
        f"总震央数：{total}",
        f"覆盖天数：{covered_days}",
        f"最大震级：{_fmt_mag(stats.get('max_mag'))}",
        f"最深地震：{_fmt_dep(stats.get('max_dep'))}",
    ]

    mag_bins = stats.get("mag_bins") or {}
    lines.append("震级分布：")
    for _, _, label in _iter_display_mag_bins(stats.get("max_mag")):
        lines.append(f"{label}  {int(mag_bins.get(label) or 0)}次")

    large_events = list(stats.get("large_events") or [])
    lines.append("")
    lines.append(f"较大地震（M≥{LARGE_EVENT_MIN_MAG:.1f}）：")
    if not large_events:
        lines.append("（无）")
    else:
        for idx, ev in enumerate(large_events, start=1):
            # 字段之间双空格；深度不再额外标注“深度”
            lines.append(
                f"{idx}.  {_fmt_event_time(ev)}  {_fmt_mag(ev.get('mag'))}  "
                f"{ev.get('place_cn') or '未知地点'}  {_fmt_dep(ev.get('dep'))}"
            )

    place_counts = list(stats.get("place_counts") or [])
    lines.append("")
    lines.append("JMA震央分布统计结果：")
    lines.append(range_text)
    if not place_counts:
        lines.append("（该时段无地震记录）")
    else:
        for item in place_counts:
            place = str(item.get("place") or "未知地点")
            count = int(item.get("count") or 0)
            lines.append(f"{place}    {count}次")
    return "\n".join(lines)


def build_jma_hypo_plot_caption(result: dict[str, Any]) -> str:
    """绘图命令附带的简短说明文本。"""
    if not result.get("success"):
        lines = [f"❌ {result.get('error') or '查询失败'}"]
        usage = result.get("usage") or []
        if usage:
            lines.append("用法：")
            lines.extend(f"• {item}" for item in usage)
        return "\n".join(lines)

    start = result.get("start_date")
    end = result.get("end_date")
    if hasattr(start, "isoformat") and hasattr(end, "isoformat"):
        range_text = format_date_range_text(start, end)
    else:
        range_text = str(result.get("date_range_text") or "")
    stats = result.get("stats") or {}
    mode = str(result.get("mode") or "经度纬度")
    lines = [
        "[JMA/日本气象厅 震央分布绘图]",
        f"投影类型：{mode}",
        f"时间范围：{range_text}（{int(result.get('requested_days') or 0)}天）",
        f"总震央数：{int(stats.get('total') or 0)}",
        f"覆盖天数：{int(result.get('covered_days') or 0)}",
        f"最大震级：{_fmt_mag(stats.get('max_mag'))}",
        f"最深地震：{_fmt_dep(stats.get('max_dep'))}",
    ]
    return "\n".join(lines)


__all__ = [
    "build_jma_hypo_list_text",
    "build_jma_hypo_plot_caption",
]
