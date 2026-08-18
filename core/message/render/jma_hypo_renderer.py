"""
JMA 震央分布图渲染器（PIL 版）。

设计取舍：
- 6 种投影散点图用 PIL 直接绘制，避免为查询链路强依赖 Playwright 预热。
- 经度纬度模式复用 resources/snet_data/jp.pref.topo.json 作为日本底图。
- 输入事件结构与 jma_hypo_query_service 输出对齐。
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from astrbot.api import logger

from ....utils.version import get_plugin_version
from ...services.query.jma_hypo_query_parser import (
    PLOT_DEP_TIME,
    PLOT_LAT_DEP,
    PLOT_LAT_TIME,
    PLOT_LON_DEP,
    PLOT_LON_LAT,
    PLOT_LON_TIME,
)

CANVAS_WIDTH = 1500
CANVAS_HEIGHT = 1000
MARGIN = 50
MAP_LEFT = 30
# 经度纬度底图区域
MAP_RIGHT = 1080
MAP_WIDTH = MAP_RIGHT - MAP_LEFT
MAP_HEIGHT = CANVAS_HEIGHT - 2 * MARGIN
# 其余 5 种投影：主网格尽量加宽，右侧信息卡收窄
SCATTER_MAP_RIGHT = 1290
# 超采样倍率：先 2x 绘制再缩回，提升文字/圆点清晰度
SSAA = 2

MAP_LON_MIN = 124.0
MAP_LON_MAX = 149.0
MAP_LAT_MIN = 26.0
MAP_LAT_MAX = 46.0

PANEL_LEFT = MAP_RIGHT + 10
PANEL_WIDTH = CANVAS_WIDTH - PANEL_LEFT - 20
SCATTER_PANEL_LEFT = SCATTER_MAP_RIGHT + 8
SCATTER_PANEL_WIDTH = CANVAS_WIDTH - SCATTER_PANEL_LEFT - 16
PANEL_PAD = 14

COLOR_BG = (18, 18, 22)
COLOR_PANEL_BG = (30, 32, 38)
COLOR_CARD_BG = (45, 45, 55)
COLOR_CARD_INNER = (38, 40, 48)
COLOR_BORDER = (60, 60, 70)
COLOR_TEXT = (220, 225, 230)
COLOR_TEXT_SEC = (150, 150, 160)
# 日期等次要信息：比 DIM 更亮，保证暗底可读
COLOR_TEXT_DATE = (175, 180, 190)
COLOR_TEXT_DIM = (100, 100, 110)
# 水印：略提亮保证可读，但仍保持低调
COLOR_WATERMARK = (145, 150, 160)
COLOR_OCEAN = (6, 6, 6)
COLOR_LAND = (30, 33, 42)
COLOR_COAST = (75, 80, 95)
COLOR_GRID = (55, 58, 68)
COLOR_AXIS = (120, 125, 140)
# 散点细描边：暗底上提升边缘清晰度
COLOR_DOT_OUTLINE = (18, 18, 22)

# 深度轴分段：顶部 -25 留白 + 0~100 放大 + 深部压缩
DEPTH_PAD_TOP = -25.0
DEPTH_SHALLOW_BREAK = 100.0
# 画布高度占比：-25~0 / 0~100 / 100~max（-25 留白压小）
DEPTH_FRAC_PAD = 0.06
DEPTH_FRAC_SHALLOW = 0.54
DEPTH_FRAC_DEEP = 0.40

# 震源深度色阶：对齐 JMA 官方标准色阶图例 (0, 10, 20, 30, 50, 100, 200, 700km)
DEPTH_COLORS: list[tuple[float, tuple[int, int, int], str]] = [
    (0.0, (230, 0, 0), "0 ~ 10km"),  # 红 (极浅源)
    (10.0, (255, 120, 0), "10 ~ 20km"),  # 橙
    (20.0, (255, 210, 0), "20 ~ 30km"),  # 黄
    (30.0, (150, 230, 0), "30 ~ 50km"),  # 黄绿
    (50.0, (0, 210, 140), "50 ~ 100km"),  # 绿
    (100.0, (0, 160, 255), "100 ~ 200km"),  # 浅蓝
    (200.0, (0, 30, 220), "≥ 200km"),  # 深蓝 (深源地震)
]

# 震级图例（示例气泡大小）
MAG_LEGENDS: list[tuple[float, str]] = [
    (1.0, "~1"),
    (2.0, "2"),
    (3.0, "3"),
    (4.0, "4"),
    (5.0, "5"),
    (6.0, "6"),
    (7.0, "7"),
    (8.0, "≥8"),
]

MIN_DOT_RADIUS = 1.8
MAX_DOT_RADIUS = 18.0
# 半径随震级连续增长的参考上限（≥ 此值取 MAX_DOT_RADIUS）
DOT_RADIUS_REF_MAG = 8.0
# 经度纬度地图：主图震点整体缩小一圈（散点图保持原尺寸）
MAP_DOT_SCALE = 0.58


def _get_depth_color(depth: float) -> tuple[int, int, int]:
    dep = max(0.0, float(depth or 0.0))
    for threshold, color, _ in reversed(DEPTH_COLORS):
        if dep >= threshold:
            return color
    return DEPTH_COLORS[0][1]


def _dot_radius(magnitude: float) -> float:
    """震级 → 圆点半径；拉大梯度差距使震级大小区分更明显。"""
    mag = max(0.0, float(magnitude or 0.0))
    if mag <= 0:
        return MIN_DOT_RADIUS
    ratio = (mag / DOT_RADIUS_REF_MAG) ** 0.65
    r = MIN_DOT_RADIUS + (MAX_DOT_RADIUS - MIN_DOT_RADIUS) * ratio
    return max(MIN_DOT_RADIUS, min(r, MAX_DOT_RADIUS))


def _legend_dot_radius(threshold: float) -> float:
    """图例圆点：取该档中位震级，保证 M5/M6/M7/M8 档大小可区分。"""
    if threshold >= 8.0:
        sample_mag = 8.2
    else:
        # 各档区间宽度：0.5 或 1.0
        span = 0.5 if threshold < 1.0 else 1.0
        sample_mag = float(threshold) + span * 0.5
    return _dot_radius(sample_mag)


def _sort_events_by_mag_asc(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按震级升序，使高震级最后绘制、不被小震遮挡。"""
    return sorted(events, key=lambda e: float(e.get("mag") or 0.0))


def _draw_dot(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int],
    *,
    outline: bool = True,
    scale: int = 1,
) -> None:
    """绘制带细描边的震级圆点。

    约定：
    - x/y 已是超采样画布坐标
    - radius 为逻辑半径（目标分辨率下的像素），内部再乘 scale
    - 小圆点关闭描边，避免 1px 描边把圆“切”成多边形
    """
    r = float(radius) * max(1, int(scale))
    cx, cy = float(x), float(y)
    # 半像素对齐，减少椭圆栅格化锯齿
    box = [(cx - r, cy - r), (cx + r, cy + r)]
    # 半径过小时描边占比过高，边缘会呈多边形；阈值随 scale 放宽
    min_outline_r = 2.2 * max(1, int(scale))
    if outline and r >= min_outline_r:
        # 描边宽度随超采样放大，缩回后约 1px；大圆可略粗一点
        width = max(1, int(round(1 * max(1, int(scale)))))
        draw.ellipse(box, fill=color, outline=COLOR_DOT_OUTLINE, width=width)
    else:
        draw.ellipse(box, fill=color)


def _downscale_ssaa(img: Image.Image) -> Image.Image:
    """将超采样画布缩回目标分辨率。"""
    if SSAA <= 1:
        return img
    return img.resize(
        (CANVAS_WIDTH, CANVAS_HEIGHT),
        resample=Image.Resampling.LANCZOS,
    )


def _get_font(size: int = 14, bold: bool = False):
    if bold:
        candidates = [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _watermark_text(suffix: str = "JMA 震央分布") -> str:
    """与台风路径 / S-Net 统一的水印格式。"""
    try:
        version = get_plugin_version()
    except Exception:
        version = ""
    version = str(version or "").strip()
    base = "@DBJD-CR/astrbot_plugin_disaster_warning (灾害预警)"
    if version:
        return f"{base} {version} · {suffix}"
    return f"{base} · {suffix}"


def _lonlat_to_xy(lon: float, lat: float) -> tuple[float, float]:
    x = MAP_LEFT + (lon - MAP_LON_MIN) / (MAP_LON_MAX - MAP_LON_MIN) * MAP_WIDTH
    y = MARGIN + (MAP_LAT_MAX - lat) / (MAP_LAT_MAX - MAP_LAT_MIN) * MAP_HEIGHT
    return x, y


def _decode_topojson_rings(topo_path: str) -> list[list[tuple[float, float]]]:
    with open(topo_path, encoding="utf-8") as f:
        topo = json.load(f)
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    decoded_arcs: list[list[tuple[float, float]]] = []
    for arc in topo["arcs"]:
        coords: list[tuple[float, float]] = []
        cx = cy = 0.0
        for dx, dy in arc:
            cx += dx
            cy += dy
            coords.append((cx * sx + tx, cy * sy + ty))
        decoded_arcs.append(coords)

    def _get_arc(idx: int) -> list[tuple[float, float]]:
        if idx >= 0:
            return list(decoded_arcs[idx])
        return list(reversed(decoded_arcs[~idx]))

    obj = next(iter(topo["objects"].values()))
    geometries = obj["geometries"] if obj.get("type") == "GeometryCollection" else [obj]
    all_rings: list[list[tuple[float, float]]] = []
    for geom in geometries:
        arcs_data = geom.get("arcs")
        if not arcs_data:
            continue
        rc = [arcs_data] if geom["type"] == "Polygon" else arcs_data
        for pr in rc:
            for ra in pr:
                ring: list[tuple[float, float]] = []
                for idx in ra:
                    ring.extend(_get_arc(idx))
                if len(ring) >= 3:
                    all_rings.append(ring)

    visible: list[list[tuple[float, float]]] = []
    for ring in all_rings:
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        if (
            max(lons) > MAP_LON_MIN - 2
            and min(lons) < MAP_LON_MAX + 2
            and max(lats) > MAP_LAT_MIN - 2
            and min(lats) < MAP_LAT_MAX + 2
        ):
            visible.append(ring)
    return visible


def _event_time_value(event: dict[str, Any]) -> float | None:
    occurred_at = event.get("occurred_at")
    if isinstance(occurred_at, datetime):
        return occurred_at.timestamp()
    return None


def _axis_values(
    events: list[dict[str, Any]],
    mode: str,
) -> tuple[list[float], list[float], str, str, bool]:
    """返回 x_vals, y_vals, x_label, y_label, y_inverted。"""
    xs: list[float] = []
    ys: list[float] = []
    if mode == PLOT_LON_LAT:
        for e in events:
            xs.append(float(e["lon"]))
            ys.append(float(e["lat"]))
        return xs, ys, "经度 (°E)", "纬度 (°N)", False
    if mode == PLOT_LON_DEP:
        for e in events:
            xs.append(float(e["lon"]))
            ys.append(float(e["dep"]))
        return xs, ys, "经度 (°E)", "深度 (km)", True
    if mode == PLOT_LAT_DEP:
        for e in events:
            xs.append(float(e["lat"]))
            ys.append(float(e["dep"]))
        return xs, ys, "纬度 (°N)", "深度 (km)", True
    if mode == PLOT_LON_TIME:
        for e in events:
            t = _event_time_value(e)
            if t is None:
                continue
            xs.append(float(e["lon"]))
            ys.append(t)
        return xs, ys, "经度 (°E)", "时间", False
    if mode == PLOT_LAT_TIME:
        for e in events:
            t = _event_time_value(e)
            if t is None:
                continue
            xs.append(float(e["lat"]))
            ys.append(t)
        return xs, ys, "纬度 (°N)", "时间", False
    if mode == PLOT_DEP_TIME:
        for e in events:
            t = _event_time_value(e)
            if t is None:
                continue
            xs.append(float(e["dep"]))
            ys.append(t)
        return xs, ys, "深度 (km)", "时间", False
    # 默认经度纬度
    for e in events:
        xs.append(float(e["lon"]))
        ys.append(float(e["lat"]))
    return xs, ys, "经度 (°E)", "纬度 (°N)", False


def _nice_range(
    values: list[float], fallback: tuple[float, float]
) -> tuple[float, float]:
    if not values:
        return fallback
    lo = min(values)
    hi = max(values)
    if abs(hi - lo) < 1e-9:
        pad = 1.0 if abs(lo) < 1e-9 else abs(lo) * 0.05
        return lo - pad, hi + pad
    # 默认留白缩小，避免数据两端出现大段空白
    pad = (hi - lo) * 0.06
    return lo - pad, hi + pad


def _geo_axis_setup(
    values: list[float],
    fallback: tuple[float, float],
    *,
    pad_ratio: float = 0.025,
    min_pad: float = 0.25,
    target_count: int = 6,
) -> tuple[float, float, list[float]]:
    """
    经度/纬度轴：严格按数据极值 + 小留白自适应。

    与 `_nice_ticks` 的区别：
    - 轴范围不向外扩张到“漂亮整数边界”（避免 119~154 被扩成 110~160）
    - 仅在范围内生成 nice 步长刻度
    """
    if not values:
        return _nice_ticks(
            fallback[0], fallback[1], target_count=target_count, prefer_5=False
        )

    data_lo = float(min(values))
    data_hi = float(max(values))
    span = data_hi - data_lo
    if span < 1e-9:
        pad = max(min_pad, 1.0)
    else:
        pad = max(span * pad_ratio, min_pad)

    lo = data_lo - pad
    hi = data_hi + pad
    step = _nice_number((hi - lo) / max(target_count, 1), round_up=True)
    if step <= 0:
        step = 1.0

    # 从范围内第一个 nice 刻度起，不把轴边界强行对齐到 step
    first = math.ceil((lo / step) - 1e-12) * step
    ticks: list[float] = []
    v = first
    guard = 0
    while v <= hi + step * 1e-9 and guard < 100:
        if v >= lo - 1e-9:
            # 抑制浮点毛刺（如 129.999999）
            ticks.append(round(v, 10))
        v += step
        guard += 1

    if not ticks:
        ticks = [lo, hi]
    return lo, hi, ticks


def _time_axis_setup(
    values: list[float],
    *,
    pad_ratio: float = 0.0267,
    min_pad: float = 3 * 3600.0,
    target_count: int = 6,
    max_ticks: int = 10,
) -> tuple[float, float, list[float]]:
    """时间轴：数据极值 + 小留白，不向外扩到整天边界。

    与 `_nice_ticks` 的区别：
    - 轴范围不向外扩张到整数日边界（避免时间轴两端出现大段空白日期）。
    - 仅在数据极值附近保留少量留白，并生成整天步长刻度，避免非整日刻度导致 "%m-%d" 日期标签重复。

    刻度策略（动态）：
    - 短跨度（逐天可画且不超过 max_ticks）→ 逐天画轴；
    - 逐天会超过 max_ticks → 按 max_ticks 等距取整天步长（不再逐个画）。

    Args:
        values: 时间戳列表（Unix 秒）。
        pad_ratio: 两端留白占数据跨度的比例（默认 0.0267，约为原 0.08 的 1/3）。
        min_pad: 最小留白（秒），默认 3 小时。
        target_count: 目标刻度数量（仅作为步长估算的基准）。
        max_ticks: 时间轴刻度数量上限，超过后改为等距整天步长。

    Returns:
        (lo, hi, ticks)：轴范围与刻度列表。
    """
    if not values:
        # 空数据时返回空刻度：绘图区已覆盖"该时段无地震记录"提示，
        # 避免出现 1970-01-01 起算的无意义 epoch 日期刻度。
        return 0.0, 0.0, []

    data_lo = float(min(values))
    data_hi = float(max(values))
    span = data_hi - data_lo
    if span < 1e-9:
        pad = min_pad
    else:
        pad = max(span * pad_ratio, min_pad)

    lo = data_lo - pad
    hi = data_hi + pad

    # 时间轴步长取整天倍数，避免非整日刻度导致日期标签重复。
    # 逐天可行且不超上限 → 逐天；否则按上限等距取整天步长。
    day = 86400.0
    span_days = max((hi - lo) / day, 1e-9)
    if span_days <= max(1, max_ticks):
        step = day
    else:
        raw_step = span_days / max(max_ticks, 1)
        step = max(day, math.ceil(raw_step - 1e-12) * day)

    # 起始刻度取数据下限所在整天（floor），保证覆盖完整日期序列
    first = math.floor((lo / step) - 1e-12) * step
    ticks: list[float] = []
    v = first
    guard = 0
    while v <= hi + step * 1e-9 and guard < 500:
        if v >= lo - 1e-9:
            ticks.append(round(v, 10))
        v += step
        guard += 1

    if not ticks:
        ticks = [lo, hi]
    return lo, hi, ticks


def _nice_number(span: float, round_up: bool = True) -> float:
    """经典 nice number：1/2/5 × 10^n。"""
    if span <= 0:
        return 1.0
    exp = math.floor(math.log10(span))
    frac = span / (10**exp)
    if round_up:
        if frac <= 1:
            nice_frac = 1.0
        elif frac <= 2:
            nice_frac = 2.0
        elif frac <= 5:
            nice_frac = 5.0
        else:
            nice_frac = 10.0
    else:
        if frac < 1.5:
            nice_frac = 1.0
        elif frac < 3:
            nice_frac = 2.0
        elif frac < 7:
            nice_frac = 5.0
        else:
            nice_frac = 10.0
    return nice_frac * (10**exp)


def _nice_ticks(
    lo: float,
    hi: float,
    *,
    target_count: int = 6,
    prefer_5: bool = False,
) -> tuple[float, float, list[float]]:
    """
    生成美观刻度。

    prefer_5=True 时优先使用 5/10/20/25/50... 步长，
    刻度值为 0 或 5 结尾的整数，便于深度轴判读。
    """
    if abs(hi - lo) < 1e-12:
        center = lo
        if prefer_5:
            step = 5.0
            nlo = math.floor(center / step) * step - step
            nhi = nlo + step * target_count
        else:
            step = 1.0
            nlo, nhi = center - 3, center + 3
        ticks = [nlo + i * step for i in range(int(round((nhi - nlo) / step)) + 1)]
        return nlo, nhi, ticks

    span = hi - lo
    if prefer_5:
        # 深度轴：步长锁定为 5 的倍数
        raw_step = span / max(target_count, 1)
        # 候选：5, 10, 20, 25, 50, 100, 200, 250, 500...
        exp = max(0, math.floor(math.log10(max(raw_step, 1e-9))))
        candidates: list[float] = []
        for e in range(max(0, exp - 1), exp + 3):
            base = 10**e
            for m in (1, 2, 5):
                step = m * base
                # 强制可被 5 整除（或本身是 1/2 但深度场景从 5 起）
                if step < 5:
                    continue
                candidates.append(float(step))
        if not candidates:
            candidates = [5.0, 10.0, 20.0, 25.0, 50.0, 100.0]
        # 选最接近目标刻度数的步长
        best_step = candidates[0]
        best_score = 1e18
        for step in candidates:
            count = span / step
            score = abs(count - target_count)
            if score < best_score:
                best_score = score
                best_step = step
        step = best_step
        nlo = math.floor(lo / step) * step
        nhi = math.ceil(hi / step) * step
        if abs(nhi - nlo) < step:
            nhi = nlo + step * target_count
        ticks: list[float] = []
        v = nlo
        # 防止浮点漂移
        while v <= nhi + step * 0.5:
            ticks.append(round(v))
            v += step
        return float(ticks[0]), float(ticks[-1]), [float(t) for t in ticks]

    # 通用 nice ticks
    step = _nice_number(span / max(target_count, 1), round_up=True)
    nlo = math.floor(lo / step) * step
    nhi = math.ceil(hi / step) * step
    ticks = []
    v = nlo
    guard = 0
    while v <= nhi + step * 0.5 and guard < 100:
        ticks.append(v)
        v += step
        guard += 1
    return nlo, nhi, ticks


def _map_value(v: float, lo: float, hi: float, a: float, b: float) -> float:
    if abs(hi - lo) < 1e-12:
        return (a + b) / 2.0
    return a + (v - lo) / (hi - lo) * (b - a)


def _map_depth_axis_x(
    dep: float,
    dep_max: float,
    plot_left: float,
    plot_right: float,
) -> float:
    """深度 → 像素 X（水平方向，左浅右深）。

    与 `_map_depth_axis`（垂直方向，上浅下深）共用相同的分段比例：
    - 左端留白（-25~0）→ DEPTH_FRAC_PAD
    - 浅源放大（0~100）→ DEPTH_FRAC_SHALLOW
    - 深源压缩（100~max）→ 剩余宽度

    用于深度时间投影（PLOT_DEP_TIME）的 X 轴，与 Y 轴深度投影保持
    一致的"浅源放大、深源压缩"视觉语义。
    """
    w = plot_right - plot_left
    x0 = plot_left  # dep = -25
    x1 = plot_left + w * DEPTH_FRAC_PAD  # dep = 0
    x2 = x1 + w * DEPTH_FRAC_SHALLOW  # dep = 100
    x3 = plot_right  # dep = max

    d = float(dep)
    if d <= 0:
        return _map_value(d, DEPTH_PAD_TOP, 0.0, x0, x1)
    if d <= DEPTH_SHALLOW_BREAK:
        return _map_value(d, 0.0, DEPTH_SHALLOW_BREAK, x1, x2)
    hi = max(dep_max, DEPTH_SHALLOW_BREAK + 1.0)
    return _map_value(d, DEPTH_SHALLOW_BREAK, hi, x2, x3)


def _format_axis_value(v: float, is_time: bool, *, prefer_int: bool = False) -> str:
    if is_time:
        try:
            return datetime.fromtimestamp(v).strftime("%m-%d")
        except Exception:
            return f"{v:.0f}"
    if prefer_int or abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}"
    if abs(v) >= 100:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _is_depth_axis(label: str) -> bool:
    return "深度" in label


def _depth_axis_max(values: list[float]) -> float:
    """深度轴上限：向上取整到 50/100 的整数。"""
    if not values:
        return 100.0
    hi = max(0.0, max(values))
    if hi <= 100:
        return 100.0
    # 向上取整到 50
    return float(math.ceil(hi / 50.0) * 50.0)


def _depth_axis_setup(
    values: list[float],
) -> tuple[float, float, list[float]]:
    """
    深度轴：顶部 -25 留白，0~100 放大，深部压缩。
    返回 (lo, hi, ticks)。
    """
    dep_max = _depth_axis_max(values)
    # 浅层刻度稀疏一些，避免挤
    shallow_ticks = [0.0, 25.0, 50.0, 75.0, 100.0]
    deep_ticks: list[float] = []
    if dep_max > 100:
        # 深部步长：100~300 用 50，更大用 100
        step = 50.0 if dep_max <= 400 else 100.0
        v = 100.0 + step
        while v < dep_max - 1e-6:
            deep_ticks.append(v)
            v += step
        deep_ticks.append(dep_max)
    ticks = [DEPTH_PAD_TOP] + shallow_ticks + deep_ticks
    # 去重保序
    out: list[float] = []
    for t in ticks:
        if not out or abs(out[-1] - t) > 1e-9:
            out.append(float(t))
    return DEPTH_PAD_TOP, dep_max, out


def _map_depth_axis(
    dep: float,
    dep_max: float,
    plot_top: float,
    plot_bottom: float,
) -> float:
    """
    深度 → 像素 Y（向下为正深度）。
    分段：-25~0 留白 / 0~100 放大 / 100~max 压缩。
    """
    h = plot_bottom - plot_top
    y0 = plot_top  # dep = -25
    y1 = plot_top + h * DEPTH_FRAC_PAD  # dep = 0
    y2 = y1 + h * DEPTH_FRAC_SHALLOW  # dep = 100
    y3 = plot_bottom  # dep = max

    d = float(dep)
    if d <= 0:
        return _map_value(d, DEPTH_PAD_TOP, 0.0, y0, y1)
    if d <= DEPTH_SHALLOW_BREAK:
        return _map_value(d, 0.0, DEPTH_SHALLOW_BREAK, y1, y2)
    hi = max(dep_max, DEPTH_SHALLOW_BREAK + 1.0)
    return _map_value(d, DEPTH_SHALLOW_BREAK, hi, y2, y3)


class JmaHypoRenderer:
    """JMA 震央分布图渲染器。"""

    def __init__(self, plugin_root: str = ""):
        self._plugin_root = plugin_root
        self._japan_rings: list[list[tuple[float, float]]] | None = None

    def _get_japan_rings(self) -> list[list[tuple[float, float]]]:
        if self._japan_rings is not None:
            return self._japan_rings
        topo_path = os.path.join(
            self._plugin_root, "resources", "snet_data", "jp.pref.topo.json"
        )
        if not os.path.exists(topo_path):
            logger.warning(f"[灾害预警] JMA 震央底图 TopoJSON 未找到: {topo_path}")
            self._japan_rings = []
            return self._japan_rings
        try:
            self._japan_rings = _decode_topojson_rings(topo_path)
        except Exception as exc:
            logger.error(f"[灾害预警] 加载 JMA 震央底图失败: {exc}")
            self._japan_rings = []
        return self._japan_rings

    def render(
        self,
        *,
        events: list[dict[str, Any]],
        mode: str,
        output_path: str,
        start_date: date | None = None,
        end_date: date | None = None,
        stats: dict[str, Any] | None = None,
    ) -> str | None:
        """渲染并保存 PNG，成功返回路径。"""
        mode = mode or PLOT_LON_LAT
        stats = stats or {}
        render_started = time.perf_counter()
        try:
            if mode == PLOT_LON_LAT:
                img = self._render_map(events, start_date, end_date, stats)
            else:
                img = self._render_scatter(events, mode, start_date, end_date, stats)
            img.save(output_path, "PNG")
            elapsed = time.perf_counter() - render_started
            logger.info(
                f"[灾害预警] JMA 震央分布图渲染成功（{mode}），耗时 {elapsed:.3f}秒"
            )
            return output_path
        except Exception as exc:
            logger.error(f"[灾害预警] JMA 震央分布图渲染失败: {exc}", exc_info=True)
            return None

    def _render_map(
        self,
        events: list[dict[str, Any]],
        start_date: date | None,
        end_date: date | None,
        stats: dict[str, Any],
    ) -> Image.Image:
        # 与散点图一致：2x 超采样后再缩回，避免小圆点锯成多边形
        s = SSAA
        cw, ch = CANVAS_WIDTH * s, CANVAS_HEIGHT * s
        rings = self._get_japan_rings()
        img = Image.new("RGB", (cw, ch), COLOR_OCEAN)
        draw = ImageDraw.Draw(img)

        for ring in rings:
            poly = [
                (x * s, y * s)
                for x, y in (_lonlat_to_xy(lon, lat) for lon, lat in ring)
            ]
            if len(poly) >= 3:
                draw.polygon(poly, fill=COLOR_LAND)
                draw.line(poly, fill=COLOR_COAST, width=max(1, s))

        # 低震级先画、高震级后画，避免大震被小震遮挡
        # 圆圈大小表示震级，圆圈颜色表示深度色阶
        for ev in _sort_events_by_mag_asc(events):
            x, y = _lonlat_to_xy(float(ev["lon"]), float(ev["lat"]))
            mag = float(ev.get("mag") or 0.0)
            dep = float(ev.get("dep") or 0.0)
            color = _get_depth_color(dep)
            r = max(MIN_DOT_RADIUS * 0.85, _dot_radius(mag) * MAP_DOT_SCALE)
            _draw_dot(draw, x * s, y * s, r, color, outline=True, scale=s)

        self._draw_side_panel(
            draw,
            events,
            start_date,
            end_date,
            stats,
            title_mode="经度纬度",
            layout="map",
            scale=s,
        )

        # 水印：左上角贴边，避免遮挡震点密集区
        draw.text(
            (10 * s, 8 * s),
            _watermark_text("JMA 震央分布"),
            fill=COLOR_WATERMARK,
            font=_get_font(13 * s),
            anchor="lt",
        )

        if not events:
            empty_font = _get_font(24 * s)
            empty_text = "该时段无地震记录"
            tb = draw.textbbox((0, 0), empty_text, font=empty_font)
            tw = tb[2] - tb[0]
            map_cx = ((MAP_LEFT + MAP_RIGHT) // 2) * s
            draw.text(
                (map_cx - tw // 2, (CANVAS_HEIGHT // 2 - 20) * s),
                empty_text,
                fill=COLOR_TEXT_DIM,
                font=empty_font,
            )
        return _downscale_ssaa(img)

    def _render_scatter(
        self,
        events: list[dict[str, Any]],
        mode: str,
        start_date: date | None,
        end_date: date | None,
        stats: dict[str, Any],
    ) -> Image.Image:
        # 2x 超采样绘制，缩回后文字/圆点更清晰
        s = SSAA
        cw, ch = CANVAS_WIDTH * s, CANVAS_HEIGHT * s
        img = Image.new("RGB", (cw, ch), COLOR_BG)
        draw = ImageDraw.Draw(img)

        # 左侧预留：横排 Y 轴标签放在绘图区上方，刻度数字单独占左栏
        plot_left = 110 * s
        plot_right = (SCATTER_MAP_RIGHT - 16) * s
        plot_top = 96 * s
        plot_bottom = (CANVAS_HEIGHT - 78) * s

        xs, ys, x_label, y_label, y_inverted = _axis_values(events, mode)
        aligned_events: list[dict[str, Any]] = []
        if mode in (PLOT_LON_TIME, PLOT_LAT_TIME, PLOT_DEP_TIME):
            for e in events:
                if _event_time_value(e) is not None:
                    aligned_events.append(e)
        else:
            aligned_events = list(events)

        # 经度/纬度：按数据极值 + 小留白，避免固定宽范围导致中间挤成一团
        if mode in (PLOT_LON_DEP, PLOT_LON_TIME, PLOT_LON_LAT):
            x_fallback = (MAP_LON_MIN, MAP_LON_MAX)
        elif mode in (PLOT_LAT_DEP, PLOT_LAT_TIME):
            x_fallback = (MAP_LAT_MIN, MAP_LAT_MAX)
        else:
            x_fallback = (0.0, 100.0)
        y_fallback = (0.0, 100.0)

        x_is_depth = _is_depth_axis(x_label)
        y_is_depth = _is_depth_axis(y_label)

        # 时间轴判定（用于 Y 轴的时间分支与刻度格式化，需在轴计算前定义）
        # 注意：6 种投影中时间均在 Y 轴（经度时间/纬度时间/深度时间）；
        # X 轴恒为经度/纬度/深度，不存在时间在 X 轴的情况。
        is_y_time = mode in (PLOT_LON_TIME, PLOT_LAT_TIME, PLOT_DEP_TIME)
        is_x_time = False

        # X 轴
        # 经度/纬度：用数据极值自适应，禁止 nice_ticks 把范围外扩成 110~160
        x_is_geo = mode in (
            PLOT_LON_DEP,
            PLOT_LON_TIME,
            PLOT_LON_LAT,
            PLOT_LAT_DEP,
            PLOT_LAT_TIME,
        )
        if x_is_depth:
            x_lo, x_hi, x_ticks = _depth_axis_setup(xs)
        elif x_is_geo:
            x_lo, x_hi, x_ticks = _geo_axis_setup(
                xs, x_fallback, pad_ratio=0.025, min_pad=0.25, target_count=6
            )
        else:
            x_raw_lo, x_raw_hi = _nice_range(xs, x_fallback)
            # 缩减非地理 X 轴两端留白比例
            pad = (x_raw_hi - x_raw_lo) * 0.03 if (x_raw_hi - x_raw_lo) > 1e-9 else 0.5
            x_lo, x_hi, x_ticks = _nice_ticks(
                x_raw_lo - pad, x_raw_hi + pad, target_count=6, prefer_5=False
            )

        # Y 轴
        if y_is_depth:
            y_lo, y_hi, y_ticks = _depth_axis_setup(ys)
        elif is_y_time:
            # 时间轴：数据极值 + 小留白，避免两端出现大段空白日期。
            # Y 轴方向最多 10 个刻度，超过后等距画。
            y_lo, y_hi, y_ticks = _time_axis_setup(ys, target_count=6, max_ticks=10)
        else:
            y_raw_lo, y_raw_hi = _nice_range(ys, y_fallback)
            y_lo, y_hi, y_ticks = _nice_ticks(
                y_raw_lo, y_raw_hi, target_count=6, prefer_5=False
            )

        # 坐标框
        draw.rectangle(
            [plot_left, plot_top, plot_right, plot_bottom],
            outline=COLOR_AXIS,
            width=2 * s,
        )

        # 网格与刻度
        tick_font = _get_font(14 * s)

        def _x_to_px(xv: float) -> float:
            if x_is_depth:
                # 深度作 X（深度时间投影）：与 Y 深度一致的分段压缩，左浅右深
                return _map_depth_axis_x(xv, x_hi, plot_left, plot_right)
            return _map_value(xv, x_lo, x_hi, plot_left, plot_right)

        def _y_to_px(yv: float) -> float:
            if y_is_depth:
                return _map_depth_axis(yv, y_hi, plot_top, plot_bottom)
            if y_inverted:
                return _map_value(yv, y_lo, y_hi, plot_top, plot_bottom)
            return _map_value(yv, y_lo, y_hi, plot_bottom, plot_top)

        for xv in x_ticks:
            x = _x_to_px(xv)
            draw.line(
                [(x, plot_top), (x, plot_bottom)], fill=COLOR_GRID, width=max(1, s)
            )
            draw.text(
                (x, plot_bottom + 12 * s),
                _format_axis_value(xv, is_x_time, prefer_int=x_is_depth),
                fill=COLOR_TEXT_SEC,
                font=tick_font,
                anchor="mt",
            )

        for yv in y_ticks:
            y = _y_to_px(yv)
            draw.line(
                [(plot_left, y), (plot_right, y)], fill=COLOR_GRID, width=max(1, s)
            )
            draw.text(
                (plot_left - 12 * s, y),
                _format_axis_value(yv, is_y_time, prefer_int=y_is_depth),
                fill=COLOR_TEXT_SEC,
                font=tick_font,
                anchor="rm",
            )

        # 散点：低震级先画、高震级后画；细描边提升边缘清晰度
        # 圆圈大小表示震级，圆圈颜色表示深度色阶
        for e in _sort_events_by_mag_asc(aligned_events):
            if mode == PLOT_LON_DEP:
                xv, yv = float(e["lon"]), float(e["dep"])
            elif mode == PLOT_LAT_DEP:
                xv, yv = float(e["lat"]), float(e["dep"])
            elif mode == PLOT_LON_TIME:
                t = _event_time_value(e)
                if t is None:
                    continue
                xv, yv = float(e["lon"]), t
            elif mode == PLOT_LAT_TIME:
                t = _event_time_value(e)
                if t is None:
                    continue
                xv, yv = float(e["lat"]), t
            elif mode == PLOT_DEP_TIME:
                t = _event_time_value(e)
                if t is None:
                    continue
                xv, yv = float(e["dep"]), t
            else:
                xv, yv = float(e["lon"]), float(e["lat"])
            x = _x_to_px(xv)
            y = _y_to_px(yv)
            mag = float(e.get("mag") or 0.0)
            dep = float(e.get("dep") or 0.0)
            color = _get_depth_color(dep)
            r = _dot_radius(mag)
            _draw_dot(draw, x, y, r, color, outline=True, scale=s)

        # X 轴标签
        draw.text(
            ((plot_left + plot_right) // 2, ch - 28 * s),
            x_label,
            fill=COLOR_TEXT,
            font=_get_font(18 * s, bold=True),
            anchor="mt",
        )
        # Y 轴标签：横排，放在绘图区左上角外侧，不与刻度重叠
        draw.text(
            (plot_left, plot_top - 14 * s),
            y_label,
            fill=COLOR_TEXT,
            font=_get_font(16 * s, bold=True),
            anchor="lb",
        )
        # 左侧主图标题行：标题左对齐，日期右对齐（不进入右侧信息卡）
        title = f"JMA 震央分布 · {mode}"
        draw.text(
            (plot_left, 18 * s),
            title,
            fill=COLOR_TEXT,
            font=_get_font(26 * s, bold=True),
        )
        if start_date and end_date:
            if start_date == end_date:
                date_text = start_date.isoformat()
            else:
                date_text = f"{start_date.isoformat()} ~ {end_date.isoformat()}"
            # 右边界贴主网格右缘，留 8px 内边距，绝不压进侧栏
            draw.text(
                (plot_right - 8 * s, 18 * s + 13 * s),
                date_text,
                fill=COLOR_TEXT_DATE,
                font=_get_font(15 * s),
                anchor="rm",
            )

        self._draw_side_panel(
            draw,
            events,
            start_date,
            end_date,
            stats,
            title_mode=mode,
            layout="scatter",
            scale=s,
            show_date_in_panel=False,
        )

        # 散点图水印：保持左下角贴边（仅经度纬度地图模式改左上角）
        draw.text(
            (10 * s, ch - 8 * s),
            _watermark_text("JMA 震央分布"),
            fill=COLOR_WATERMARK,
            font=_get_font(13 * s),
            anchor="lb",
        )

        if not aligned_events:
            empty_font = _get_font(24 * s)
            empty_text = "该时段无地震记录"
            tb = draw.textbbox((0, 0), empty_text, font=empty_font)
            tw = tb[2] - tb[0]
            cx = (plot_left + plot_right) // 2
            cy = (plot_top + plot_bottom) // 2
            draw.text(
                (cx - tw // 2, cy - 12 * s),
                empty_text,
                fill=COLOR_TEXT_DIM,
                font=empty_font,
            )
        return _downscale_ssaa(img)

    @staticmethod
    def _draw_side_panel(
        draw: ImageDraw.ImageDraw,
        events: list[dict[str, Any]],
        start_date: date | None,
        end_date: date | None,
        stats: dict[str, Any],
        *,
        title_mode: str,
        layout: str = "map",
        scale: int = 1,
        show_date_in_panel: bool = True,
    ) -> None:
        s = max(1, int(scale))
        is_scatter = layout == "scatter"
        if is_scatter:
            panel_left = SCATTER_PANEL_LEFT * s
            panel_width = SCATTER_PANEL_WIDTH * s
            canvas_w = CANVAS_WIDTH * s
            canvas_h = CANVAS_HEIGHT * s
        else:
            panel_left = PANEL_LEFT * s
            panel_width = PANEL_WIDTH * s
            canvas_w = CANVAS_WIDTH * s
            canvas_h = CANVAS_HEIGHT * s

        pad = PANEL_PAD * s
        draw.rectangle([panel_left, 0, canvas_w, canvas_h], fill=COLOR_PANEL_BG)
        px = panel_left + pad
        pw = panel_width - pad * 2
        content_left = px + 14 * s
        content_right = px + pw - 14 * s
        content_cx = px + pw / 2.0

        # ── 标题卡 ──
        # scatter：日期已画在左侧主图标题行，侧栏只保留标题+投影
        # map：标题左 + 日期右同一行
        cy = pad
        ch = (86 if is_scatter else 100) * s
        draw.rounded_rectangle(
            [px, cy, px + pw, cy + ch],
            radius=16 * s,
            fill=COLOR_CARD_BG,
            outline=COLOR_BORDER,
            width=max(1, s),
        )
        draw.text(
            (content_left, cy + 28 * s),
            "震央分布",
            fill=COLOR_TEXT,
            font=_get_font(26 * s, bold=True),
            anchor="lm",
        )
        draw.text(
            (content_left, cy + (58 if is_scatter else 66) * s),
            f"投影：{title_mode}",
            fill=COLOR_TEXT_SEC,
            font=_get_font(14 * s),
            anchor="lm",
        )
        if show_date_in_panel and start_date and end_date:
            if start_date == end_date:
                date_text = start_date.isoformat()
            else:
                date_text = f"{start_date.isoformat()} ~ {end_date.isoformat()}"
            draw.text(
                (content_right, cy + 28 * s),
                date_text,
                fill=COLOR_TEXT_DATE,
                font=_get_font(13 * s),
                anchor="rm",
            )

        # ── 统计数据 ──
        total = int(stats.get("total") or len(events) or 0)
        max_mag = stats.get("max_mag")
        max_dep = stats.get("max_dep")

        max_mag_text = f"M {max_mag:.1f}" if max_mag is not None else "--"
        max_dep_text = f"{max_dep:.0f} km" if max_dep is not None else "--"
        total_text = f"{total} 次"

        lcy = cy + ch + 12 * s

        # 统计卡片：总地震数 / 今日最大 / 今日最深
        items = [
            ("总地震数", total_text),
            ("最大震级", max_mag_text),
            ("最深地震", max_dep_text),
        ]

        if is_scatter:
            item_h = 58 * s
            gap = 8 * s
            outer_pad = 10 * s
            lch = outer_pad * 2 + item_h * len(items) + gap * (len(items) - 1)
            draw.rounded_rectangle(
                [px, lcy, px + pw, lcy + lch],
                radius=16 * s,
                fill=COLOR_CARD_BG,
                outline=COLOR_BORDER,
                width=max(1, s),
            )
            y_cursor = lcy + outer_pad
            for label, value in items:
                draw.rounded_rectangle(
                    [
                        content_left - 2 * s,
                        y_cursor,
                        content_right + 2 * s,
                        y_cursor + item_h,
                    ],
                    radius=12 * s,
                    fill=COLOR_CARD_INNER,
                    outline=COLOR_BORDER,
                    width=max(1, s),
                )
                draw.text(
                    (content_left + 10 * s, y_cursor + item_h * 0.30),
                    label,
                    fill=COLOR_TEXT_SEC,
                    font=_get_font(13 * s),
                    anchor="lm",
                )
                draw.text(
                    (content_left + 10 * s, y_cursor + item_h * 0.70),
                    value,
                    fill=COLOR_TEXT,
                    font=_get_font(17 * s, bold=True),
                    anchor="lm",
                )
                y_cursor += item_h + gap
        else:
            row_h = 52 * s
            gap = 8 * s
            outer_pad = 10 * s
            lch = outer_pad * 2 + row_h * len(items) + gap * (len(items) - 1)
            draw.rounded_rectangle(
                [px, lcy, px + pw, lcy + lch],
                radius=16 * s,
                fill=COLOR_CARD_BG,
                outline=COLOR_BORDER,
                width=max(1, s),
            )
            y_cursor = lcy + outer_pad
            for label, value in items:
                draw.rounded_rectangle(
                    [
                        content_left - 2 * s,
                        y_cursor,
                        content_right + 2 * s,
                        y_cursor + row_h,
                    ],
                    radius=12 * s,
                    fill=COLOR_CARD_INNER,
                    outline=COLOR_BORDER,
                    width=max(1, s),
                )
                mid_y = y_cursor + row_h / 2.0
                draw.text(
                    (content_left + 10 * s, mid_y),
                    label,
                    fill=COLOR_TEXT_SEC,
                    font=_get_font(15 * s),
                    anchor="lm",
                )
                draw.text(
                    (content_right - 10 * s, mid_y),
                    value,
                    fill=COLOR_TEXT,
                    font=_get_font(19 * s, bold=True),
                    anchor="rm",
                )
                y_cursor += row_h + gap

        # ── 图例：深度颜色 (色阶) ──
        ly = lcy + lch + 10 * s
        dep_row_h = 20 * s if is_scatter else 22 * s
        title_h = 28 * s
        bottom_pad = 8 * s
        lh = title_h + len(DEPTH_COLORS) * dep_row_h + bottom_pad

        draw.rounded_rectangle(
            [px, ly, px + pw, ly + lh],
            radius=16 * s,
            fill=COLOR_CARD_BG,
            outline=COLOR_BORDER,
            width=max(1, s),
        )
        legend_title_size = 14 * s if is_scatter else 15 * s
        draw.text(
            (content_cx, ly + title_h / 2.0),
            "震源深度 [km]",
            fill=COLOR_TEXT,
            font=_get_font(legend_title_size, bold=True),
            anchor="mm",
        )
        box_w = 18 * s
        box_h = 10 * s
        box_x = content_left + 6 * s
        label_x = box_x + box_w + 10 * s
        legend_label_size = 12 * s if is_scatter else 13 * s
        for i, (_, color, label) in enumerate(DEPTH_COLORS):
            iy = ly + title_h + i * dep_row_h + dep_row_h / 2.0
            draw.rectangle(
                [box_x, iy - box_h / 2.0, box_x + box_w, iy + box_h / 2.0],
                fill=color,
                outline=COLOR_DOT_OUTLINE,
                width=max(1, s),
            )
            draw.text(
                (label_x, iy),
                label,
                fill=COLOR_TEXT,
                font=_get_font(legend_label_size),
                anchor="lm",
            )

        # ── 图例：震级气泡大小 ──
        # 散点图模式：侧栏空间更窄，转为竖向排版；地图模式：维持横向横排
        ly_mag = ly + lh + 10 * s
        max_bottom = canvas_h - pad

        if is_scatter:
            # 考虑最大圆点直径，增大垂直行高避免气泡上下重叠
            max_r_legend = max(_dot_radius(m_val) * 0.95 for m_val, _ in MAG_LEGENDS)
            mag_row_h = max(24 * s, int(max_r_legend * 2 * s + 4 * s))
            title_h_mag = 26 * s
            bottom_pad_mag = 6 * s
            lh_mag = title_h_mag + len(MAG_LEGENDS) * mag_row_h + bottom_pad_mag

            # 若溢出画布底部，按剩余空间缩减行高
            if ly_mag + lh_mag > max_bottom:
                avail_mag = max_bottom - ly_mag - title_h_mag - bottom_pad_mag
                mag_row_h = max(
                    int(max_r_legend * 2 * s + 1 * s),
                    avail_mag // max(len(MAG_LEGENDS), 1),
                )
                lh_mag = title_h_mag + len(MAG_LEGENDS) * mag_row_h + bottom_pad_mag

            if ly_mag + lh_mag <= max_bottom:
                draw.rounded_rectangle(
                    [px, ly_mag, px + pw, ly_mag + lh_mag],
                    radius=16 * s,
                    fill=COLOR_CARD_BG,
                    outline=COLOR_BORDER,
                    width=max(1, s),
                )
                draw.text(
                    (content_cx, ly_mag + title_h_mag / 2.0),
                    "震级 M",
                    fill=COLOR_TEXT,
                    font=_get_font(13 * s, bold=True),
                    anchor="mm",
                )
                # 圆点中心及文字起始点：按照最大圆点半径留足间距
                dot_cx_mag = content_left + max_r_legend * s + 6 * s
                label_x_mag = dot_cx_mag + max_r_legend * s + 10 * s
                for i, (m_val, m_label) in enumerate(MAG_LEGENDS):
                    iy_mag = ly_mag + title_h_mag + i * mag_row_h + mag_row_h / 2.0
                    r_item = max(1.8, _dot_radius(m_val) * 0.95)
                    _draw_dot(
                        draw,
                        dot_cx_mag,
                        iy_mag,
                        r_item,
                        (180, 185, 195),
                        outline=True,
                        scale=s,
                    )
                    draw.text(
                        (label_x_mag, iy_mag),
                        m_label,
                        fill=COLOR_TEXT,
                        font=_get_font(12 * s),
                        anchor="lm",
                    )
        else:
            lh_mag = 46 * s
            if ly_mag + lh_mag <= max_bottom:
                draw.rounded_rectangle(
                    [px, ly_mag, px + pw, ly_mag + lh_mag],
                    radius=14 * s,
                    fill=COLOR_CARD_BG,
                    outline=COLOR_BORDER,
                    width=max(1, s),
                )
                cy_card = ly_mag + lh_mag / 2.0
                draw.text(
                    (content_left + 4 * s, cy_card),
                    "震级 M",
                    fill=COLOR_TEXT_SEC,
                    font=_get_font(12 * s, bold=True),
                    anchor="lm",
                )
                legend_scale = MAP_DOT_SCALE
                for i, (m_val, m_label) in enumerate(MAG_LEGENDS):
                    cx_item = (
                        content_left
                        + 54 * s
                        + i * ((pw - 74 * s) / len(MAG_LEGENDS))
                        + 6 * s
                    )
                    r_item = max(1.8, _dot_radius(m_val) * legend_scale * 0.95)
                    _draw_dot(
                        draw,
                        cx_item,
                        cy_card - 6 * s,
                        r_item,
                        (180, 185, 195),
                        outline=True,
                        scale=s,
                    )
                    draw.text(
                        (cx_item, cy_card + 10 * s),
                        m_label,
                        fill=COLOR_TEXT,
                        font=_get_font(11 * s),
                        anchor="mm",
                    )


__all__ = ["JmaHypoRenderer"]
