"""
JMA 震央分布查询服务。

职责：
- 编排日期参数与 JMA hypo 拉取
- 聚合统计（震级分档、地名频次、较大地震）
- 输出统一结果字典，供文本 presenter / 绘图 renderer 复用
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from astrbot.api import logger

from ...network.http.jma_hypo_client import JmaHypoClient
from .jma_hypo_place_map import translate_jma_hypo_place
from .jma_hypo_query_parser import (
    SOFT_RANGE_HINT_DAYS,
    format_date_range_text,
    parse_jma_hypo_list_args,
    parse_jma_hypo_plot_args,
)

# 震级分档（1.0 一档，左闭右开；最后一档 M≥8.0）
# 展示时按实际最大震级提前截断，避免空高档刷屏。
MAG_BINS: list[tuple[float, float | None, str]] = [
    (float("-inf"), 1.0, "M<1.0"),
    (1.0, 2.0, "M1.0~M2.0"),
    (2.0, 3.0, "M2.0~M3.0"),
    (3.0, 4.0, "M3.0~M4.0"),
    (4.0, 5.0, "M4.0~M5.0"),
    (5.0, 6.0, "M5.0~M6.0"),
    (6.0, 7.0, "M6.0~M7.0"),
    (7.0, 8.0, "M7.0~M8.0"),
    (8.0, None, "M≥8.0"),
]

# 摘要中展示的较大地震阈值
LARGE_EVENT_MIN_MAG = 4.0
LARGE_EVENT_LIMIT = 12


def _mag_bin_label(mag: float) -> str:
    for low, high, label in MAG_BINS:
        if high is None:
            if mag >= low:
                return label
        elif low <= mag < high:
            return label
    return MAG_BINS[0][2]


def _enrich_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for ev in events:
        item = dict(ev)
        place_en = str(item.get("place") or "").strip()
        item["place_en"] = place_en
        item["place_cn"] = translate_jma_hypo_place(place_en)
        enriched.append(item)
    return enriched


def _build_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    if total <= 0:
        return {
            "total": 0,
            "min_mag": None,
            "max_mag": None,
            "avg_dep": None,
            "max_dep": None,
            "mag_bins": {label: 0 for _, _, label in MAG_BINS},
            "place_counts": [],
            "large_events": [],
        }

    mags = [float(e.get("mag") or 0.0) for e in events]
    # 仅收集有效数值深度：缺失/空深度不参与统计，避免被当作 0 km 浅源
    valid_deps: list[float] = []
    for e in events:
        raw_dep = e.get("dep")
        if raw_dep is None or str(raw_dep).strip() == "":
            continue
        try:
            valid_deps.append(float(raw_dep))
        except (TypeError, ValueError):
            continue
    mag_bins = {label: 0 for _, _, label in MAG_BINS}
    for mag in mags:
        mag_bins[_mag_bin_label(mag)] += 1

    place_counter: Counter[str] = Counter()
    for e in events:
        place_counter[str(e.get("place_cn") or "未知地点")] += 1
    place_counts = [
        {"place": place, "count": count} for place, count in place_counter.most_common()
    ]

    large_events = [
        e for e in events if float(e.get("mag") or 0.0) >= LARGE_EVENT_MIN_MAG
    ]
    large_events.sort(
        key=lambda x: (
            float(x.get("mag") or 0.0),
            str(x.get("date_str") or ""),
        ),
        reverse=True,
    )
    large_events = large_events[:LARGE_EVENT_LIMIT]

    return {
        "total": total,
        "min_mag": min(mags),
        "max_mag": max(mags),
        "avg_dep": (sum(valid_deps) / len(valid_deps)) if valid_deps else None,
        "max_dep": max(valid_deps) if valid_deps else None,
        "mag_bins": mag_bins,
        "place_counts": place_counts,
        "large_events": large_events,
    }


async def query_jma_hypo_data(
    *,
    dates: list[date],
    start_date: date,
    end_date: date,
    mode: str | None = None,
    soft_hint: bool = False,
    client: JmaHypoClient | None = None,
) -> dict[str, Any]:
    """拉取并聚合 JMA 震央数据。"""
    hypo_client = client or JmaHypoClient()
    try:
        raw = await hypo_client.fetch_range(dates)
    except Exception as exc:
        logger.error(f"[灾害预警] JMA 震央查询失败: {exc}")
        return {
            "success": False,
            "error": f"拉取 JMA 震央数据失败: {exc}",
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
        }

    events = _enrich_events(list(raw.get("events") or []))
    stats = _build_stats(events)
    requested_days = int(raw.get("requested_days") or len(dates))
    covered_days = int(raw.get("covered_days") or 0)
    missing_days = list(raw.get("missing_days") or [])
    zero_event_days = list(raw.get("zero_event_days") or [])

    # 不写死历史窗口起始日；仅在确实存在缺失日时给出中性提示
    if missing_days:
        data_note = (
            "JMA bosai/hypo 按日提供；部分日期暂无公开数据或拉取失败"
            f"（缺失 {len(missing_days)} 天）。"
        )
    else:
        data_note = "JMA bosai/hypo 按日提供。"

    return {
        "success": True,
        "start_date": start_date,
        "end_date": end_date,
        "date_range_text": format_date_range_text(start_date, end_date),
        "dates": dates,
        "mode": mode,
        "events": events,
        "day_counts": dict(raw.get("day_counts") or {}),
        "missing_days": missing_days,
        "zero_event_days": zero_event_days,
        "requested_days": requested_days,
        "covered_days": covered_days,
        "stats": stats,
        "soft_hint": bool(soft_hint) or requested_days > SOFT_RANGE_HINT_DAYS,
        "data_note": data_note,
    }


async def query_jma_hypo_list(
    arg1: str | None = None,
    arg2: str | None = None,
    *,
    client: JmaHypoClient | None = None,
) -> dict[str, Any]:
    """列表命令入口。"""
    parsed = parse_jma_hypo_list_args(arg1, arg2)
    if not parsed.get("success"):
        return {
            "success": False,
            "error": parsed.get("error") or "参数解析失败",
            "usage": parsed.get("usage") or [],
        }
    return await query_jma_hypo_data(
        dates=list(parsed["dates"]),
        start_date=parsed["start_date"],
        end_date=parsed["end_date"],
        mode=None,
        soft_hint=bool(parsed.get("soft_hint")),
        client=client,
    )


async def query_jma_hypo_plot(
    arg1: str | None = None,
    arg2: str | None = None,
    arg3: str | None = None,
    *,
    client: JmaHypoClient | None = None,
) -> dict[str, Any]:
    """绘图命令入口。"""
    parsed = parse_jma_hypo_plot_args(arg1, arg2, arg3)
    if not parsed.get("success"):
        return {
            "success": False,
            "error": parsed.get("error") or "参数解析失败",
            "usage": parsed.get("usage") or [],
        }
    return await query_jma_hypo_data(
        dates=list(parsed["dates"]),
        start_date=parsed["start_date"],
        end_date=parsed["end_date"],
        mode=str(parsed.get("mode") or ""),
        soft_hint=bool(parsed.get("soft_hint")),
        client=client,
    )


__all__ = [
    "LARGE_EVENT_LIMIT",
    "LARGE_EVENT_MIN_MAG",
    "MAG_BINS",
    "query_jma_hypo_data",
    "query_jma_hypo_list",
    "query_jma_hypo_plot",
]
