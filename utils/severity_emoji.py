"""
统一严重程度/等级视觉指示器。

集中维护各领域的颜色等级 emoji 映射，避免各模块各自硬编码色板导致展示口径漂移。

设计约定：
- 所有指示器 emoji 均落在 SEVERITY_INDICATOR_EMOJIS 白名单内，
  推送「简洁」模式下不会被误删（见 utils/emoji_filter.py）。

该模块不依赖消息展示器，查询层与推送层均可安全引用。
"""

from __future__ import annotations

import math
from typing import Any, Final

# ============================================================================
# 一、统一色板
# ============================================================================

# 圆形颜色指示器（EEW / 气象颜色 / 台风等级 / AQI / 实况排行）
CIRCLE_EMOJIS: Final[tuple[str, ...]] = (
    "⚪",
    "⚫",
    "🔴",
    "🟠",
    "🟡",
    "🟢",
    "🔵",
    "🟣",
    "🟤",
)

# 方形颜色指示器（地震情报场景）
SQUARE_EMOJIS: Final[tuple[str, ...]] = (
    "⬜",
    "⬛",
    "🟥",
    "🟧",
    "🟨",
    "🟩",
    "🟦",
    "🟪",
    "🟫",
)

# 全部严重性指示 emoji（供 emoji_filter 简洁模式白名单派生）。
SEVERITY_INDICATOR_EMOJIS: Final[frozenset[str]] = frozenset(
    CIRCLE_EMOJIS + SQUARE_EMOJIS
)

# 通用缺测指示器（AQI 无数据 / 排行无效值等）
MISSING_EMOJI: Final[str] = "⬜"

# ============================================================================
# 二、AQI 空气质量（参考 HJ 633-2012）
# ============================================================================

# AQI 等级圆点：按数值区间分档（优→严重）。
AQI_LEVEL_DOT: Final[list[tuple[int, int, str]]] = [
    (0, 51, "🟢"),  # 优 0-50
    (51, 101, "🟡"),  # 良 51-100
    (101, 151, "🟠"),  # 轻度污染 101-150
    (151, 201, "🔴"),  # 中度污染 151-200
    (201, 301, "🟣"),  # 重度污染 201-300
    (301, 10**9, "🟤"),  # 严重污染 301+
]


def aqi_level_emoji(aqi: Any) -> str:
    """按 AQI 数值返回等级圆点；缺测返回 ⬜。"""
    try:
        num = int(float(aqi))
    except (TypeError, ValueError):
        return MISSING_EMOJI
    for lo, hi, dot in AQI_LEVEL_DOT:
        if lo <= num < hi:
            return dot
    return MISSING_EMOJI


# ============================================================================
# 三、台风强度等级（蓝 → 绿 → 黄 → 橙 → 红 → 紫，由弱到强）
# ============================================================================

TYPHOON_LEVEL_EMOJI: Final[dict[str, str]] = {
    "热带低压": "🔵",
    "热带风暴": "🟢",
    "强热带风暴": "🟡",
    "台风": "🟠",
    "强台风": "🔴",
    "超强台风": "🟣",
}

# 台风等级匹配顺序（强→弱），避免“强台风”被“台风”抢先命中。
_TYPHOON_MATCH_ORDER: Final[tuple[str, ...]] = (
    "超强台风",
    "强台风",
    "强热带风暴",
    "热带风暴",
    "热带低压",
    "台风",
)


def typhoon_level_emoji(typhoon_type: str | None) -> str:
    """按台风强度等级返回圆形颜色 emoji；无法识别返回空串。"""
    level = str(typhoon_type or "").strip()
    if not level:
        return ""
    if level in TYPHOON_LEVEL_EMOJI:
        return TYPHOON_LEVEL_EMOJI[level]
    for key in _TYPHOON_MATCH_ORDER:
        if key in level:
            return TYPHOON_LEVEL_EMOJI[key]
    return ""


# ============================================================================
# 四、气象预警颜色等级（与中央气象台预警颜色一致）
# ============================================================================

WEATHER_COLOR_LEVEL_EMOJI: Final[dict[str, str]] = {
    "红色": "🔴",
    "橙色": "🟠",
    "黄色": "🟡",
    "蓝色": "🔵",
    "白色": "⚪",
}


# ============================================================================
# 五、海啸等级（中国 / 日本）
# ============================================================================

CN_TSUNNAMI_LEVEL_EMOJI: Final[dict[str, str]] = {
    "信息": "⚪",
    "蓝色": "🔵",
    "黄色": "🟡",
    "橙色": "🟠",
    "红色": "🔴",
    "解除": "",
}

JP_TSUNNAMI_LEVEL_EMOJI: Final[dict[str, str]] = {
    "Minor": "⚪",
    "Watch": "🟡",
    "Warning": "🟠",
    "MajorWarning": "🔴",
    "None": "",
    "Unknown": "",
    "解除": "",
}

# 中国海啸颜色文本 → emoji（文本兜底匹配顺序）
_CN_TSUNNAMI_TEXT_MATCH: Final[tuple[tuple[str, str], ...]] = (
    ("红色", "🔴"),
    ("橙色", "🟠"),
    ("黄色", "🟡"),
    ("蓝色", "🔵"),
    ("信息", "⚪"),
)


def cn_tsunami_level_emoji(level: Any) -> str:
    """按中国海啸等级文本返回圆形 emoji；无法识别返回空串。"""
    text = str(level or "").strip()
    if not text:
        return ""
    for color, emoji in _CN_TSUNNAMI_TEXT_MATCH:
        if color in text:
            return emoji
    return ""


# ============================================================================
# 六、地震烈度 / 震度色序
#   预警场景（EEW）与普通情报场景使用两套图形，便于视觉区分。
#   色序由弱到强：白 → 蓝 → 绿 → 黄 → 橙 → 红 → 紫。
# ============================================================================

INTENSITY_CIRCLE_EMOJIS: Final[tuple[str, ...]] = (
    "⚪",
    "🔵",
    "🟢",
    "🟡",
    "🟠",
    "🔴",
    "🟣",
)

INTENSITY_SQUARE_EMOJIS: Final[tuple[str, ...]] = (
    "⬜",
    "🟦",
    "🟩",
    "🟨",
    "🟧",
    "🟥",
    "🟪",
)


def intensity_level_emoji(*, index: int, is_eew: bool = True) -> str:
    """按档位索引返回地震烈度/震度色序 emoji（越界回退到最低档）。"""
    seq = INTENSITY_CIRCLE_EMOJIS if is_eew else INTENSITY_SQUARE_EMOJIS
    try:
        idx = int(index)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0:
        idx = 0
    if idx >= len(seq):
        idx = len(seq) - 1
    return seq[idx]


# ============================================================================
# 七、实况排行指示器（气温 / 最低气温 / 降水 / 风速）
# ============================================================================

# 最高气温（℃）：冷区蓝 → 暖区红紫。
# (下界含, 上界不含, emoji)；None 表示开区间。
RANK_MAXTEMP_LEVELS: Final[list[tuple[float | None, float | None, str]]] = [
    (None, 15.0, "🔵"),  # <15 凉爽
    (15.0, 22.0, "🟢"),  # 15~22 温和
    (22.0, 28.0, "🟡"),  # 22~28 温暖
    (28.0, 32.0, "🟠"),  # 28~32 偏热
    (32.0, 35.0, "🔴"),  # 32~35 高温
    (35.0, 38.0, "🟣"),  # 35~38 酷热（≥35℃ 高温预警线）
    (38.0, None, "⚫"),  # ≥38 极端
]

# 最低气温（℃）：暖区橙 → 冷区蓝紫。
RANK_MINTEMP_LEVELS: Final[list[tuple[float | None, float | None, str]]] = [
    (20.0, None, "🟠"),  # ≥20 温暖
    (10.0, 20.0, "🟡"),  # 10~20 温和
    (0.0, 10.0, "🟢"),  # 0~10 偏凉
    (-10.0, 0.0, "🔵"),  # -10~0 寒冷
    (-20.0, -10.0, "🟣"),  # -20~-10 严寒
    (None, -20.0, "⚫"),  # <-20 极寒
]

# 降水（mm）色标。
RAIN_LEVEL_EMOJIS: Final[tuple[str, ...]] = (
    "🟢",  # 小雨
    "🔵",  # 中雨
    "🟡",  # 大雨
    "🟠",  # 暴雨
    "🔴",  # 大暴雨
    "🟣",  # 特大暴雨
)

# 降水分级阈值（mm），按时间跨度区分：
# - 1h：短时强降水业务习惯（5/15/30/60/120）
# - 6h：6 小时降水量等级（10/25/50/100/200）
# - 24h：国标 GB/T 28592-2012（10/25/50/100/250）
RAIN_HOUR_THRESHOLDS: Final[dict[int, tuple[float, ...]]] = {
    1: (5.0, 15.0, 30.0, 60.0, 120.0),
    6: (10.0, 25.0, 50.0, 100.0, 200.0),
    24: (10.0, 25.0, 50.0, 100.0, 250.0),
}

# 风速（m/s）：按蒲福风级换算，色带对齐台风强度六级划分。
RANK_WIND_LEVELS: Final[list[tuple[float | None, float | None, str]]] = [
    (None, 3.4, "⚪"),  # 轻风
    (3.4, 10.8, "🔵"),  # 和风/清风
    (10.8, 17.2, "🟢"),  # 强风
    (17.2, 24.5, "🟡"),  # 大风
    (24.5, 32.7, "🟠"),  # 狂风
    (32.7, 41.5, "🔴"),  # 台风级
    (41.5, None, "🟣"),  # 强台风及以上
]


def _bracket_level_emoji(
    value: Any,
    levels: list[tuple[float | None, float | None, str]],
    *,
    default: str = "",
) -> str:
    """按区间表返回对应档位 emoji（区间下界/上界均可为 None 表示开区间）。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    for lo, hi, emoji in levels:
        if lo is not None and num < lo:
            continue
        if hi is not None and num >= hi:
            continue
        return emoji
    return default


def rank_maxtemp_emoji(value: Any) -> str:
    """最高气温指示器。"""
    return _bracket_level_emoji(value, RANK_MAXTEMP_LEVELS)


def rank_mintemp_emoji(value: Any) -> str:
    """最低气温指示器。"""
    return _bracket_level_emoji(value, RANK_MINTEMP_LEVELS)


def rank_rain_emoji(value: Any, *, hour: int = 1) -> str:
    """降水指示器（阈值随时间跨度切换）。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return MISSING_EMOJI
    if not math.isfinite(num):
        return MISSING_EMOJI
    thresholds = RAIN_HOUR_THRESHOLDS.get(int(hour), RAIN_HOUR_THRESHOLDS[1])
    for i, thr in enumerate(thresholds):
        if num < thr:
            return RAIN_LEVEL_EMOJIS[i]
    return RAIN_LEVEL_EMOJIS[-1]


def rank_wind_emoji(value: Any) -> str:
    """风速指示器。"""
    return _bracket_level_emoji(value, RANK_WIND_LEVELS, default=MISSING_EMOJI)


# 实况排行要素 -> 指示器函数
RANK_EMOJI_FUNCS: Final[dict[str, Any]] = {
    "maxtemp": rank_maxtemp_emoji,
    "mintemp": rank_mintemp_emoji,
    "rain": rank_rain_emoji,
    "wind": rank_wind_emoji,
}


def rank_level_emoji(rank_type: str, value: Any, *, hour: int = 1) -> str:
    """实况排行统一入口：按要素与跨度返回颜色指示器。

    Args:
        rank_type: 接口 type（maxtemp/mintemp/rain/wind）。
        value: 数值。
        hour: 时间跨度（仅降水使用，1/6/24）。

    Returns:
        颜色圆点 emoji；无法识别要素/缺测时返回空串或 ⬜。
    """
    func = RANK_EMOJI_FUNCS.get(rank_type)
    if func is None:
        return ""
    if rank_type == "rain":
        return func(value, hour=hour)
    return func(value)


__all__ = [
    "CIRCLE_EMOJIS",
    "SQUARE_EMOJIS",
    "SEVERITY_INDICATOR_EMOJIS",
    "MISSING_EMOJI",
    "AQI_LEVEL_DOT",
    "aqi_level_emoji",
    "TYPHOON_LEVEL_EMOJI",
    "typhoon_level_emoji",
    "WEATHER_COLOR_LEVEL_EMOJI",
    "CN_TSUNNAMI_LEVEL_EMOJI",
    "JP_TSUNNAMI_LEVEL_EMOJI",
    "cn_tsunami_level_emoji",
    "INTENSITY_CIRCLE_EMOJIS",
    "INTENSITY_SQUARE_EMOJIS",
    "intensity_level_emoji",
    "RANK_MAXTEMP_LEVELS",
    "RANK_MINTEMP_LEVELS",
    "RAIN_LEVEL_EMOJIS",
    "RAIN_HOUR_THRESHOLDS",
    "RANK_WIND_LEVELS",
    "rank_maxtemp_emoji",
    "rank_mintemp_emoji",
    "rank_rain_emoji",
    "rank_wind_emoji",
    "rank_level_emoji",
]
