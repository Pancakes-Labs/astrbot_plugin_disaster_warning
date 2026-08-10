"""
通用文本格式化工具。

集中维护查询/展示链路常用的纯文本格式化原语：
- 显示宽度估算（中文 2 列 / ASCII 1 列）与按宽度填充对齐
- 缺测/异常数值归一化（None / 9999 / NA / NaN 等统一显示「-」）
- ISO 8601 时间字符串可读化（去掉 T，去掉秒）

供实况排行、气象站、AQI 等查询模块复用，避免各自重复实现。
"""

from __future__ import annotations

import math
import re
from typing import Any

# 常用缺测标记：NMC / FAN 等接口用 9999 表示缺测。
MISSING_VALUE = 9999.0

# 全角空格（U+3000）：聊天平台会压缩连续半角空格导致对齐失效，
# 全角空格不会被压缩，且显示宽度固定为 2 列，适合用来做列对齐。
_FULLWIDTH_SPACE = "\u3000"


def display_width(s: Any) -> int:
    """估算字符串显示宽度：中文按 2 字符宽、ASCII 按 1 字符宽。"""
    w = 0
    for ch in str(s):
        w += 2 if ord(ch) > 127 else 1
    return w


def pad_display_width(s: Any, width: int, align: str = "left") -> str:
    """按显示宽度填充/截断字符串到指定宽度。

    终端等宽字体下中文字符占 2 列，直接用 str.ljust/rjust 会因
    字符数与显示宽度不一致导致列错位；且聊天平台会压缩连续半角空格，
    因此这里用「全角空格为主、半角空格兜奇数」的方式补齐显示宽度：
    - 全角空格占 2 显示宽，不会被平台压缩；
    - 若需要补奇数列宽，用 1 个半角空格兜底（夹在全角空格之间，
      不会触发平台连续空格折叠）。

    Args:
        s: 原始字符串。
        width: 目标显示宽度。
        align: 对齐方式，left/right。

    Returns:
        填充后的字符串（按显示宽度对齐）。
    """
    s = str(s)
    cur = display_width(s)
    if cur >= width:
        return s
    pad_count = width - cur
    full = pad_count // 2
    half = pad_count % 2
    pad = _FULLWIDTH_SPACE * full + " " * half
    return s + pad if align == "left" else pad + s


def norm_value(
    v: Any,
    unit: str = "",
    decimals: int | None = None,
    *,
    missing_markers: tuple[Any, ...] = (None, "", "9999", "9999.0", "NA", "N/A", "-"),
) -> str:
    """把数值格式化为可读文本；缺测/异常显示「-」。

    Args:
        v: 原始数值。
        unit: 单位后缀。
        decimals: 固定小数位；None 时温度（℃）保留 1 位、其余整数不带小数。
        missing_markers: 缺测标记集合（None/空串/9999/NA 等）。

    Returns:
        格式化后的文本；缺测/异常时返回「-」。
    """
    if v is None or v == "":
        return "-"
    if unit == "℃" and str(v) in ("9999", "9999.0"):
        return "-"
    # 字符串缺测标记（NA 等）
    sv = str(v).strip()
    if sv.upper() in {str(m).upper() for m in missing_markers if m is not None}:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f) or f == MISSING_VALUE:
        return "-"
    if decimals is None:
        # 温度统一保留 1 位小数（如 29.5 ℃、36.0 ℃），其余整数不带小数
        if unit == "℃":
            decimals = 1
        else:
            decimals = 0 if f == int(f) else 1
    text = f"{f:.{decimals}f}"
    return f"{text} {unit}".strip() if unit else text


def format_iso_time(s: Any, *, keep_seconds: bool = False) -> str:
    """把 ISO 8601 时间字符串格式化为可读文本。

    实测 FAN AQI 接口返回形如 "2026-08-10T21:00:00"：
    - 去掉中间的 T -> "2026-08-10 21:00:00"
    - 默认去掉秒 -> "2026-08-10 21:00"

    Args:
        s: 原始时间字符串；None/空返回空串。
        keep_seconds: 是否保留秒（默认去掉）。

    Returns:
        格式化后的时间文本。
    """
    text = str(s or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    if not keep_seconds and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
        text = text[:-3]
    return text


__all__ = [
    "MISSING_VALUE",
    "display_width",
    "pad_display_width",
    "norm_value",
    "format_iso_time",
]
