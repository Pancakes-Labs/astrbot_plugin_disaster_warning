"""
message/render/snet_map_renderer.py — NIED S-Net 测站分布图渲染器（Playwright 版）。

用 Playwright + Canvas2D 替代 PIL：
- TopoJSON 日本都道府県多边形（Canvas 绘制）
- CAPQuake Qt 风格震度图标（Base64 内嵌，SREV 目录）
- CSS 右侧面板（三栏统计 + 测站列表）

移植自旧版 core/services/snet/snet_map_renderer.py
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import Template

from astrbot.api import logger

from ....utils.converters import ScaleConverter
from ....utils.version import get_plugin_version

# ============================================================
# 常量
# ============================================================
MAP_LON_MIN = 135.0
MAP_LON_MAX = 149.0
MAP_LAT_MIN = 32.0
MAP_LAT_MAX = 44.0

# 右侧列表展示条数（安静时也按震度降序截取，不改小数位）
SNET_LIST_LIMIT = 18

# S-Net 卡片固定画布；渲染时临时放大浏览器视口，避免 800×800 池默认值裁切
SNET_VIEWPORT = {"width": 1400, "height": 1000}

# 計測震度 -3 → #0000CD，-0.5 → #1FE460（震度0 起点；与地图 canvas 渐变一致）
_SNET_NEG3_RGB = (0, 0, 205)
_SNET_BELOW_ZERO_RGB = (31, 228, 96)  # #1FE460
_SNET_GRADIENT_END_SHINDO = -0.5  # 日本震度 0 的計測震度起点

# 图标文件映射: (shindo下限, SVG文件名)
_SNET_ICON_FILES = [
    (6.5, "C7.svg"),
    (6.0, "C6+.svg"),
    (5.5, "C6-.svg"),
    (5.0, "C5+.svg"),
    (4.5, "C5-.svg"),
    (3.5, "C4.svg"),
    (2.5, "C3.svg"),
    (1.5, "C2.svg"),
    (0.5, "C1.svg"),
    (-0.5, "C0.svg"),  # 震度0（shindo >= -0.5 且 < 0.5 用这个图标）
]

# MSIL 震度→RGB
MSIL_SHINDO_TO_RGB: dict[int, tuple[int, int, int]] = {
    -30: (0, 0, 205),
    -25: (0, 36, 227),
    -20: (0, 72, 250),
    -15: (0, 140, 194),
    -10: (0, 208, 139),
    -5: (31, 228, 96),
    0: (63, 250, 54),
    5: (125, 252, 33),
    10: (189, 255, 12),
    15: (222, 255, 5),
    20: (255, 255, 0),
    25: (255, 238, 0),
    30: (255, 221, 0),
    35: (255, 182, 0),
    40: (255, 144, 0),
    45: (255, 106, 0),
    50: (255, 68, 0),
    55: (250, 33, 0),
    60: (245, 0, 0),
    65: (208, 0, 0),
    70: (170, 0, 0),
}


# ── TopoJSON 解码 ──


def _decode_topojson_rings(topo_path: str) -> list[list[tuple[float, float]]]:
    with open(topo_path, encoding="utf-8") as f:
        topo = json.load(f)
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    decoded_arcs = []
    for arc in topo["arcs"]:
        coords = []
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
    all_rings = []
    for geom in geometries:
        arcs_data = geom.get("arcs")
        if not arcs_data:
            continue
        rc = [arcs_data] if geom["type"] == "Polygon" else arcs_data
        for pr in rc:
            for ra in pr:
                ring = []
                for idx in ra:
                    ring.extend(_get_arc(idx))
                if len(ring) >= 3:
                    all_rings.append(ring)

    visible = []
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


# ── SVG 图标加载（Base64 缓存） ──


def _load_icons_base64(icon_dir: str) -> dict[str, str]:
    """加载震度 SVG 为 Base64 映射。

    返回 dict 的 key 使用字符串（如 \"6.5\"），与前端
    th.toFixed(1) 对齐，避免 float 键在 JSON 往返后匹配失败。
    """
    result: dict[str, str] = {}
    if not os.path.isdir(icon_dir):
        logger.warning(f"[灾害预警] 震度 SVG 图标目录不存在: {icon_dir}")
        return result
    for threshold, fname in _SNET_ICON_FILES:
        svg_path = os.path.join(icon_dir, fname)
        if os.path.exists(svg_path):
            try:
                with open(svg_path, "rb") as f:
                    # key 用 .1f 格式化对齐 JS 的 .toFixed(1)
                    result[f"{float(threshold):.1f}"] = base64.b64encode(
                        f.read()
                    ).decode()
            except Exception as e:
                logger.error(f"[灾害预警] 加载 {fname} 失败: {e}")
    logger.info(f"[灾害预警] SVG 图标加载: {len(result)}/{len(_SNET_ICON_FILES)}")
    return result


# ── 工具函数 ──


def _shindo_short_label(shindo: float) -> str:
    """复用 ScaleConverter，避免重复维护震度映射。"""

    text = ScaleConverter.format_measured_intensity_display(shindo)
    if not text:
        return ""
    # 图例短标签去掉“0以下”的“0”前缀以外的前缀已是短文本
    return text.replace("强", "強")


def _shindo_css_class(shindo: float) -> str:
    classified = ScaleConverter.classify_measured_intensity(shindo)
    if classified is None:
        # 0以下：单独 CSS 类，回退到 s0 样式
        return "shindo-s0"
    if classified >= 6.5:
        return "shindo-s7"
    if classified >= 6.0:
        return "shindo-s6p"
    if classified >= 5.5:
        return "shindo-s6m"
    if classified >= 5.0:
        return "shindo-s5p"
    if classified >= 4.5:
        return "shindo-s5m"
    if classified >= 3.5:
        return "shindo-s4"
    if classified >= 2.5:
        return "shindo-s3"
    if classified >= 1.5:
        return "shindo-s2"
    if classified >= 0.5:
        return "shindo-s1"
    return "shindo-s0"


def _rgb_to_str(rgb: tuple[int, int, int] | list[int] | None) -> str:
    if not rgb or len(rgb) < 3:
        r, g, b = _SNET_BELOW_ZERO_RGB
        return f"{r},{g},{b}"
    return f"{rgb[0]},{rgb[1]},{rgb[2]}"


def _neg_to_zero_gradient_rgb(shindo: float) -> tuple[int, int, int]:
    """計測震度 -3～-0.5 线性插值到 (#0000CD → #1FE460)。"""
    # 震度 0 起点为 -0.5；区间长度 2.5
    span = 3.0 + _SNET_GRADIENT_END_SHINDO  # 2.5
    t = max(0.0, min(1.0, (float(shindo) + 3.0) / span))
    r = int(
        round(_SNET_NEG3_RGB[0] + (_SNET_BELOW_ZERO_RGB[0] - _SNET_NEG3_RGB[0]) * t)
    )
    g = int(
        round(_SNET_NEG3_RGB[1] + (_SNET_BELOW_ZERO_RGB[1] - _SNET_NEG3_RGB[1]) * t)
    )
    b = int(
        round(_SNET_NEG3_RGB[2] + (_SNET_BELOW_ZERO_RGB[2] - _SNET_NEG3_RGB[2]) * t)
    )
    return (r, g, b)


def _list_dot_bg(shindo: float) -> str:
    """列表圆点背景色（rgb(...)），经 data-bg 注入，避免 Jinja 写进 style 触发 IDE 误报。"""
    try:
        value = float(shindo)
    except (TypeError, ValueError):
        return ""
    # 仅震度 0 以下（計測震度 < -0.5）使用渐变色点
    if value >= _SNET_GRADIENT_END_SHINDO:
        return ""
    r, g, b = _neg_to_zero_gradient_rgb(value)
    return f"rgb({r},{g},{b})"


def _format_display_time(timestamp: str) -> str:
    if not timestamp:
        return "——"
    try:
        dt = datetime.strptime(str(timestamp), "%Y%m%d%H%M00")
        dt_utc8 = dt.replace(tzinfo=timezone.utc) + timedelta(hours=8)
        return dt_utc8.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return timestamp


# ── 渲染器 ──


class SnetMapRenderer:
    """NIED S-Net 测站分布图渲染器（Playwright 版）。"""

    def __init__(self, browser_manager=None, plugin_root: str = ""):
        self.browser_manager = browser_manager
        self.plugin_root = plugin_root
        self._template_cache: str | None = None
        self._japan_rings: list[list[tuple[float, float]]] | None = None
        # key 为字符串阈值（如 "6.5"），与前端 toFixed(1) 对齐
        self._icon_cache: dict[str, str] | None = None

    def _get_template(self) -> str:
        if self._template_cache is not None:
            return self._template_cache
        template_path = os.path.join(
            self.plugin_root,
            "resources",
            "card_templates",
            "SNET",
            "snet_station.html",
        )
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"SNET 模板未找到: {template_path}")
        with open(template_path, encoding="utf-8") as f:
            self._template_cache = f.read()
        return self._template_cache

    def _get_japan_rings(self) -> list[list[tuple[float, float]]]:
        """加载并缓存 TopoJSON 日本轮廓。"""
        if self._japan_rings is not None:
            return self._japan_rings
        topo_path = os.path.join(
            self.plugin_root, "resources", "snet_data", "jp.pref.topo.json"
        )
        if not os.path.exists(topo_path):
            logger.warning(f"[灾害预警] TopoJSON 未找到: {topo_path}")
            self._japan_rings = []
            return self._japan_rings
        try:
            self._japan_rings = _decode_topojson_rings(topo_path)
        except Exception as e:
            logger.error(f"[灾害预警] 加载 TopoJSON 失败: {e}")
            self._japan_rings = []
        return self._japan_rings

    def _get_icons(self) -> dict[str, str]:
        """加载并缓存 SVG 图标。"""
        if self._icon_cache is not None:
            return self._icon_cache
        icon_dir = os.path.join(self.plugin_root, "resources", "snet_data", "SREV")
        self._icon_cache = _load_icons_base64(icon_dir)
        return self._icon_cache

    async def render(
        self,
        stations: list[dict[str, Any]],
        output_path: str,
        timestamp: str = "",
    ) -> str | None:
        """渲染 SNET 测站分布图。"""
        ctx = self._build_context(stations, timestamp)

        try:
            template_str = self._get_template()
            template = Template(template_str)
            html_content = template.render(**ctx)
        except Exception as e:
            logger.error(f"[灾害预警] 模板渲染失败: {e}", exc_info=True)
            return None

        if not self.browser_manager:
            logger.warning("[灾害预警] 无浏览器管理器，跳过渲染")
            return None

        try:
            # S-Net 模板固定 1400×1000，临时放大视口后截图，避免 800×800 池默认值裁切。
            result = await self.browser_manager.render_card(
                html_content,
                output_path,
                selector="#card-wrapper",
                wait_until="networkidle",
                viewport=SNET_VIEWPORT,
                render_label="S-Net 测站分布图",
            )
            if result and os.path.exists(output_path):
                return output_path
            logger.warning("[灾害预警] 渲染未生成文件")
            return None
        except Exception as e:
            logger.error(f"[灾害预警] Playwright 渲染失败: {e}", exc_info=True)
            return None

    def _build_context(
        self, stations: list[dict[str, Any]], timestamp: str
    ) -> dict[str, Any]:
        rings_json = json.dumps(self._get_japan_rings())
        icons = self._get_icons()
        icon_svgs_json = json.dumps(icons)

        station_list = []
        for s in stations:
            name = s.get("name", "?")
            lat = s.get("lat", 0)
            lon = s.get("lon", 0)
            shindo = s.get("shindo", -999)
            try:
                shindo_f = float(shindo)
            except (TypeError, ValueError):
                shindo_f = -999.0
            rgb = s.get("rgb")
            rgb_str = _rgb_to_str(rgb)
            sc = _shindo_css_class(shindo_f)
            # < -0.5 列表圆点用 -3~-0.5 渐变；>= -0.5 走 CSS 震度色 class（含震度0）
            if shindo_f >= _SNET_GRADIENT_END_SHINDO:
                dot_class = sc.replace("shindo-", "dot-") if sc else "dot-none"
                dot_bg = ""
            else:
                dot_class = "dot-none"
                dot_bg = _list_dot_bg(shindo_f)
            station_list.append(
                {
                    "name": name,
                    "lat": lat,
                    "lon": lon,
                    "shindo": shindo_f,
                    "rgb_str": rgb_str,
                    "label": _shindo_short_label(shindo_f),
                    "shindo_class": sc,
                    "dot_class": dot_class,
                    "dot_bg": dot_bg,
                }
            )

        sorted_stations = sorted(station_list, key=lambda x: x["shindo"], reverse=True)
        # 安静时也展示全部语义：仍按震度降序截取 Top-N，保留三位小数，无额外空状态文案
        list_stations = sorted_stations[:SNET_LIST_LIMIT]
        # 触发测站：日本震度 0 起点为計測震度 -0.5（与图标/色阶一致）
        triggered = [
            s for s in sorted_stations if s["shindo"] >= _SNET_GRADIENT_END_SHINDO
        ]
        triggered_count = len(triggered)
        total_stations = len(sorted_stations)
        # 最大震度：有 >= -0.5（震度0起点）用触发最高；否则用全网最高（通常为负基线）
        top = (
            triggered[0]
            if triggered
            else (sorted_stations[0] if sorted_stations else None)
        )
        max_shindo_class = ""
        max_shindo_color = ""
        if top is not None:
            max_shindo_text = f"{top['shindo']:.3f}"
            top_station_name = top["name"]
            top_shindo = float(top["shindo"])
            if top_shindo >= _SNET_GRADIENT_END_SHINDO:
                # 震度0及以上：用与列表一致的 shindo-*（对齐 earthquake_list 色相）
                max_shindo_class = top.get("shindo_class") or _shindo_css_class(
                    top_shindo
                )
            else:
                # 震度0以下：与地图 -3~-0.5 渐变同色（内联，避免 style=Jinja 在 class 上爆红）
                r, g, b = _neg_to_zero_gradient_rgb(top_shindo)
                max_shindo_color = f"rgb({r},{g},{b})"
        else:
            max_shindo_text = "——"
            top_station_name = "——"
        display_time = _format_display_time(timestamp)
        triggered_color = "60b0ff" if triggered_count > 0 else "4a5470"
        version = get_plugin_version()
        watermark_text = (
            f"@DBJD-CR/astrbot_plugin_disaster_warning (灾害预警) {version}"
            " · NIED S-Net 海底震度分布"
        )

        return {
            "rings_json": rings_json,
            "stations_json": json.dumps(
                sorted(station_list, key=lambda x: x["shindo"])
            ),
            "icon_svgs_json": icon_svgs_json,
            # 渐变参数由 Python 常量注入 JSON payload，避免与模板 JS 双份维护漂移
            "gradient_config_json": json.dumps(
                {
                    "color_neg3": list(_SNET_NEG3_RGB),
                    "color_below_zero": list(_SNET_BELOW_ZERO_RGB),
                    "gradient_end_shindo": _SNET_GRADIENT_END_SHINDO,
                }
            ),
            "display_time": display_time,
            "triggered_count": triggered_count,
            "total_stations": total_stations,
            "max_shindo_text": max_shindo_text,
            "max_shindo_class": max_shindo_class,
            "max_shindo_color": max_shindo_color,
            "top_station_name": top_station_name,
            "sorted_stations": sorted_stations,
            "list_stations": list_stations,
            "triggered_color": triggered_color,
            "watermark_text": watermark_text,
        }
