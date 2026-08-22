"""
JMA 震央分布命令参数解析。

支持：
- JMA震央分布 [<开始日期> [<结束日期>]]
- JMA震央分布绘图 [(投影类型)] [<开始日期> [<结束日期>]]
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

# 投影类型
PLOT_LON_LAT = "经度纬度"
PLOT_LON_DEP = "经度深度"
PLOT_LAT_DEP = "纬度深度"
PLOT_LON_TIME = "经度时间"
PLOT_LAT_TIME = "纬度时间"
PLOT_DEP_TIME = "深度时间"

DEFAULT_PLOT_MODE = PLOT_LON_LAT

PLOT_MODE_ALIASES: dict[str, str] = {
    "经度纬度": PLOT_LON_LAT,
    "纬度经度": PLOT_LON_LAT,
    "地图": PLOT_LON_LAT,
    "map": PLOT_LON_LAT,
    "lonlat": PLOT_LON_LAT,
    "latlon": PLOT_LON_LAT,
    "经度深度": PLOT_LON_DEP,
    "londep": PLOT_LON_DEP,
    "lon-depth": PLOT_LON_DEP,
    "纬度深度": PLOT_LAT_DEP,
    "latdep": PLOT_LAT_DEP,
    "lat-depth": PLOT_LAT_DEP,
    "经度时间": PLOT_LON_TIME,
    "lontime": PLOT_LON_TIME,
    "lon-time": PLOT_LON_TIME,
    "纬度时间": PLOT_LAT_TIME,
    "lattime": PLOT_LAT_TIME,
    "lat-time": PLOT_LAT_TIME,
    "深度时间": PLOT_DEP_TIME,
    "deptime": PLOT_DEP_TIME,
    "depth-time": PLOT_DEP_TIME,
    "depthtime": PLOT_DEP_TIME,
}

ALL_PLOT_MODES = (
    PLOT_LON_LAT,
    PLOT_LON_DEP,
    PLOT_LAT_DEP,
    PLOT_LON_TIME,
    PLOT_LAT_TIME,
    PLOT_DEP_TIME,
)

# 软提示阈值：超过后在结果中提示耗时可能较长（不拦截）
SOFT_RANGE_HINT_DAYS = 30
# 硬上限：防止一次请求拖垮进程（约一年）
HARD_MAX_DAYS = 370


def normalize_plot_mode(value: str | None) -> str | None:
    token = str(value or "").strip().lower()
    raw = str(value or "").strip()
    if not token:
        return None
    if raw in PLOT_MODE_ALIASES:
        return PLOT_MODE_ALIASES[raw]
    if token in PLOT_MODE_ALIASES:
        return PLOT_MODE_ALIASES[token]
    return None


def is_plot_mode_token(token: str | None) -> bool:
    return normalize_plot_mode(token) is not None


def parse_date_token(
    token: str | None, *, default_year: int | None = None
) -> date | None:
    """解析单个日期 token。"""
    text = str(token or "").strip()
    if not text:
        return None
    text = text.replace("／", "/").replace("．", ".").replace("。", ".")
    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = re.sub(r"[．.]", "-", text)
    text = re.sub(r"[/年]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")

    year = default_year or date.today().year
    # YYYY-MM-DD / YYYY-M-D
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # YYYYMMDD
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # M-D / MM-DD（默认当年）
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def expand_date_range(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    days = (end - start).days + 1
    if days > HARD_MAX_DAYS:
        return []
    return [start + timedelta(days=i) for i in range(days)]


def parse_jma_hypo_list_args(
    arg1: str | None = None,
    arg2: str | None = None,
) -> dict[str, Any]:
    """解析列表命令参数。"""
    today = date.today()
    tokens = [str(x).strip() for x in (arg1, arg2) if str(x or "").strip()]
    if not tokens:
        return {
            "success": True,
            "start_date": today,
            "end_date": today,
            "dates": [today],
            "soft_hint": False,
        }

    if len(tokens) == 1:
        d = parse_date_token(tokens[0])
        if d is None:
            return {
                "success": False,
                "error": f"无法解析日期：{tokens[0]}",
                "usage": [
                    "JMA震央分布",
                    "JMA震央分布 2025-01-01",
                    "JMA震央分布 2025-01-01 2025-01-31",
                ],
            }
        return {
            "success": True,
            "start_date": d,
            "end_date": d,
            "dates": [d],
            "soft_hint": False,
        }

    start = parse_date_token(tokens[0])
    end = parse_date_token(tokens[1])
    if start is None or end is None:
        return {
            "success": False,
            "error": f"无法解析日期区间：{tokens[0]} {tokens[1]}",
            "usage": [
                "JMA震央分布 2025-01-01 2025-01-31",
            ],
        }
    dates = expand_date_range(start, end)
    if not dates:
        return {
            "success": False,
            "error": f"日期跨度过大（最多 {HARD_MAX_DAYS} 天）",
        }
    if end < start:
        start, end = end, start
    return {
        "success": True,
        "start_date": start,
        "end_date": end,
        "dates": dates,
        "soft_hint": len(dates) > SOFT_RANGE_HINT_DAYS,
    }


def parse_jma_hypo_plot_args(
    arg1: str | None = None,
    arg2: str | None = None,
    arg3: str | None = None,
) -> dict[str, Any]:
    """解析绘图命令参数。"""
    today = date.today()
    tokens = [str(x).strip() for x in (arg1, arg2, arg3) if str(x or "").strip()]
    mode = DEFAULT_PLOT_MODE
    date_tokens: list[str] = []

    for token in tokens:
        maybe_mode = normalize_plot_mode(token)
        if maybe_mode is not None and not date_tokens:
            mode = maybe_mode
            continue
        date_tokens.append(token)

    if not date_tokens:
        return {
            "success": True,
            "mode": mode,
            "start_date": today,
            "end_date": today,
            "dates": [today],
            "soft_hint": False,
        }

    if len(date_tokens) == 1:
        d = parse_date_token(date_tokens[0])
        if d is None:
            # 可能用户把未知投影类型当成了参数
            if normalize_plot_mode(date_tokens[0]) is None and not is_plot_mode_token(
                date_tokens[0]
            ):
                return {
                    "success": False,
                    "error": f"无法解析参数：{date_tokens[0]}",
                    "usage": [
                        "JMA震央分布绘图",
                        "JMA震央分布绘图 经度深度",
                        "JMA震央分布绘图 经度纬度 2025-01-01",
                        "JMA震央分布绘图 深度时间 2025-01-01 2025-01-31",
                    ],
                }
            return {
                "success": False,
                "error": f"无法解析日期：{date_tokens[0]}",
            }
        return {
            "success": True,
            "mode": mode,
            "start_date": d,
            "end_date": d,
            "dates": [d],
            "soft_hint": False,
        }

    start = parse_date_token(date_tokens[0])
    end = parse_date_token(date_tokens[1])
    if start is None or end is None:
        return {
            "success": False,
            "error": f"无法解析日期区间：{' '.join(date_tokens[:2])}",
            "usage": [
                "JMA震央分布绘图 经度纬度 2025-01-01 2025-01-31",
            ],
        }
    dates = expand_date_range(start, end)
    if not dates:
        return {
            "success": False,
            "error": f"日期跨度过大（最多 {HARD_MAX_DAYS} 天）",
        }
    if end < start:
        start, end = end, start
    return {
        "success": True,
        "mode": mode,
        "start_date": start,
        "end_date": end,
        "dates": dates,
        "soft_hint": len(dates) > SOFT_RANGE_HINT_DAYS,
    }


def format_date_range_text(start: date, end: date) -> str:
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} 至 {end.isoformat()}"


__all__ = [
    "ALL_PLOT_MODES",
    "DEFAULT_PLOT_MODE",
    "HARD_MAX_DAYS",
    "PLOT_DEP_TIME",
    "PLOT_LAT_DEP",
    "PLOT_LAT_TIME",
    "PLOT_LON_DEP",
    "PLOT_LON_LAT",
    "PLOT_LON_TIME",
    "SOFT_RANGE_HINT_DAYS",
    "format_date_range_text",
    "is_plot_mode_token",
    "normalize_plot_mode",
    "parse_date_token",
    "parse_jma_hypo_list_args",
    "parse_jma_hypo_plot_args",
]
