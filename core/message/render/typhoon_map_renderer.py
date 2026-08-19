"""
message/render/typhoon_map_renderer.py — 台风路径图渲染器（Playwright 版）。

设计原则（改模板为主，薄渲染器）：
1. 模板直接消费 EQSC 原生 history_track / future_track / windCircle 字段。
2. 本文件只做：轨迹排序清洗、面板摘要、bootstrap JSON 注入、Playwright 截图。
3. 不引入 TyphoonTrackPoint 中间模型，也不单独拆完整 adapter 模块。
4. HTML 模板刻意避免 Jinja 语法，改用占位符替换，避免 IDE 对模板误报爆红。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ....utils.map_tile_sources import get_tile_url_js
from ....utils.time_converter import TimeConverter
from ....utils.version import get_plugin_version
from ...domain.event_models import TyphoonEvent
from ...domain.typhoon.typhoon_display_format import format_coordinates
from ...domain.typhoon.typhoon_levels import level_weight
from ...domain.typhoon.typhoon_values import clean_text, to_float
from ...domain.typhoon.typhoon_winds import clean_wind_circle

# 台风卡片固定画布（与 typhoon_track.html 的 1400×1000 对齐）；
# 渲染时临时放大浏览器视口，避免 800×800 池默认值裁切。
TYPHOON_VIEWPORT = {"width": 1400, "height": 1000}

# 默认视野（西北太平洋常见台风活动区），轨迹过短时兜底。
DEFAULT_LON_MIN = 100.0
DEFAULT_LON_MAX = 170.0
DEFAULT_LAT_MIN = 5.0
DEFAULT_LAT_MAX = 45.0

# 强度等级 0-6 对应配色（与模板 CAT_COLORS 保持一致）。
CAT_COLORS: dict[int, tuple[int, int, int]] = {
    0: (120, 120, 130),
    1: (50, 175, 255),
    2: (50, 215, 115),
    3: (255, 215, 40),
    4: (255, 160, 10),
    5: (245, 95, 10),
    6: (225, 40, 40),
}

# 面板风圈展示：标签 / EQSC 键 / RGB。
RADII_META: list[tuple[str, str, tuple[int, int, int]]] = [
    ("7级", "30KTS", (100, 180, 255)),
    ("10级", "50KTS", (255, 200, 50)),
    ("12级", "64KTS", (255, 80, 0)),
]

QUADRANT_CN = {"NE": "东", "SE": "南", "SW": "西", "NW": "北"}
DEFAULT_MAP_SOURCE = "PetalMap矢量图暗"

# 模板占位符：渲染时整体替换，模板文件本身保持合法 HTML/JS。
_PLACEHOLDER_BOOTSTRAP = "__TYPHOON_BOOTSTRAP_JSON__"
_PLACEHOLDER_LEAFLET_JS = "__LEAFLET_JS_URL__"
_PLACEHOLDER_LEAFLET_CSS = "__LEAFLET_CSS_URL__"
_PLACEHOLDER_HELPER_JS = "__MAP_RENDER_HELPER_JS__"


def _parse_node_time(value: Any) -> datetime | None:
    """解析轨迹节点时间；无时区时按 UTC+8 解释（与 EQSC/FAN 业务习惯一致）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = TimeConverter.parse_datetime(str(value).strip())
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))
    return parsed


def _node_time_key(node: dict[str, Any]) -> float:
    """排序键：无效时间沉底为 0。"""
    parsed = _parse_node_time(node.get("time"))
    return parsed.timestamp() if parsed is not None else 0.0


def _node_lat_lon(node: dict[str, Any]) -> tuple[float | None, float | None]:
    """兼容 latitude/lat、longitude/lon 两套键名。"""
    lat = to_float(
        node.get("latitude") if node.get("latitude") is not None else node.get("lat")
    )
    lon = to_float(
        node.get("longitude") if node.get("longitude") is not None else node.get("lon")
    )
    return lat, lon


def _node_level_text(node: dict[str, Any]) -> str:
    """提取节点强度文案（优先中文 typeNameCN）。"""
    return clean_text(
        node.get("typeNameCN")
        or node.get("type")
        or node.get("level")
        or node.get("typhoon_type")
    )


def _node_wind_circle(node: dict[str, Any]) -> dict[str, Any]:
    """清洗节点四象限风圈，剔除 NULL/空象限。"""
    raw = (
        node.get("windCircle")
        if node.get("windCircle") is not None
        else node.get("wind_circle")
    )
    return clean_wind_circle(raw)


def _normalize_track_nodes(nodes: Any) -> list[dict[str, Any]]:
    """保留 EQSC 原生字段，仅做排序、坐标校验与风圈清洗。

    注意：这里不做“适配成 TyphoonTrackPoint”的完整转换，
    只保证模板 JS 能稳定读到 latitude/longitude/windSpeed/typeNameCN/windCircle。
    """
    if not isinstance(nodes, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        lat, lon = _node_lat_lon(node)
        if lat is None or lon is None:
            continue
        item = dict(node)
        item["latitude"] = lat
        item["longitude"] = lon
        # 统一风速别名，模板侧优先读 windSpeed。
        if item.get("windSpeed") is None and item.get("wind_speed") is not None:
            item["windSpeed"] = item.get("wind_speed")
        level = _node_level_text(item)
        if level and not item.get("typeNameCN"):
            item["typeNameCN"] = level
        circle = _node_wind_circle(item)
        if circle:
            item["windCircle"] = circle
        cleaned.append(item)
    # EQSC historyTrack 顺序不保证，统一按时间升序。
    cleaned.sort(key=_node_time_key)
    return cleaned


def _format_panel_time(value: Any) -> str:
    """面板更新时间：YYYY年MM月DD日HH:MM:SS（北京时间）。"""
    parsed = _parse_node_time(value)
    if parsed is None:
        text = clean_text(value)
        return text or ""
    local = parsed.astimezone(TimeConverter._get_timezone("UTC+8"))
    return TimeConverter._safe_strftime(local, "%Y年%m月%d日%H:%M:%S")


def _format_compact_time(value: Any) -> str:
    """紧凑时间：MM/DD HH:MM（表格行与迷你图刻度共用）。"""
    parsed = _parse_node_time(value)
    if parsed is None:
        text = clean_text(value)
        return text[:11] if text else ""
    local = parsed.astimezone(TimeConverter._get_timezone("UTC+8"))
    return TimeConverter._safe_strftime(local, "%m/%d %H:%M")


def _format_pos(lat: float | None, lon: float | None) -> str:
    """表格坐标：压缩 format_coordinates 结果，节省列宽。

    例：14.2°N, 118.5°E → 14.2N 118.5E（空格分隔，避免与风速列粘连）。
    """
    text = format_coordinates(lat, lon)
    if not text:
        return ""
    return text.replace("°", "").replace(",", " ").replace("  ", " ").strip()


def _cat_color_css(cat: int) -> str:
    rgb = CAT_COLORS.get(int(cat or 0), CAT_COLORS[0])
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _build_table_rows(
    nodes: list[dict[str, Any]],
    *,
    reverse: bool = False,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """构建左右栏表格行（预报/历史共用）。"""
    seq = list(reversed(nodes)) if reverse else list(nodes)
    if isinstance(limit, int) and limit > 0:
        seq = seq[:limit]
    rows: list[dict[str, str]] = []
    for node in seq:
        lat, lon = _node_lat_lon(node)
        level = _node_level_text(node)
        cat = level_weight(level)
        ws = to_float(
            node.get("windSpeed")
            if node.get("windSpeed") is not None
            else node.get("wind_speed")
        )
        pr = to_float(node.get("pressure"))
        rows.append(
            {
                "time": _format_compact_time(node.get("time")),
                "pos": _format_pos(lat, lon),
                "ws": f"{ws:g}" if ws is not None else "",
                "pr": f"{pr:g}" if pr is not None else "",
                "cat": level or "",
                "cat_color": _cat_color_css(cat),
            }
        )
    return rows


def _build_wind_radii_panel(wind_circle: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把四象限风圈整理成面板可读行。"""
    cleaned = clean_wind_circle(wind_circle or {})
    rows: list[dict[str, Any]] = []
    for label, key, color in RADII_META:
        circle = cleaned.get(key)
        if not isinstance(circle, dict):
            continue
        parts: list[str] = []
        for qk in ("NE", "SE", "SW", "NW"):
            val = to_float(circle.get(qk))
            if val is None or val <= 0:
                continue
            parts.append(f"{QUADRANT_CN.get(qk, qk)}{int(val)}km")
        if not parts:
            continue
        # 两两一行，避免面板过宽。
        lines = [" ".join(parts[i : i + 2]) for i in range(0, len(parts), 2)]
        rows.append(
            {
                "label": label,
                "lines": lines,
                "color_rgb": f"{color[0]},{color[1]},{color[2]}",
            }
        )
    return rows


def _compute_view_bounds(
    history: list[dict[str, Any]],
    future: list[dict[str, Any]],
) -> tuple[float, float, float, float]:
    """按路径点四至（最西/最东/最南/最北）计算地图视野。

    规则：
    1. 取 history + future 全部有效点的 lon/lat 极值；
    2. 按跨度加少量 padding（约 8%，夹在 0.3°~1.2°）；
    3. 跨度过小时给最小框，避免 zoom 过大；
    4. 结果限制在默认大区内兜底。
    """
    lons: list[float] = []
    lats: list[float] = []
    for node in history + future:
        lat, lon = _node_lat_lon(node)
        if lat is None or lon is None:
            continue
        lats.append(lat)
        lons.append(lon)
    if not lons or not lats:
        return DEFAULT_LON_MIN, DEFAULT_LON_MAX, DEFAULT_LAT_MIN, DEFAULT_LAT_MAX

    # 四至：最西/最东/最南/最北
    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)

    lon_span = max(lon_max - lon_min, 0.05)
    lat_span = max(lat_max - lat_min, 0.05)

    # 按跨度自适应 padding：短路径多留一点，长路径少留
    pad_lon = min(max(lon_span * 0.08, 0.3), 1.2)
    pad_lat = min(max(lat_span * 0.08, 0.3), 1.2)

    lo = lon_min - pad_lon
    hi = lon_max + pad_lon
    la = lat_min - pad_lat
    ha = lat_max + pad_lat

    # 短轨迹最小跨度（度）
    min_span = 2.0
    if hi - lo < min_span:
        c = (lo + hi) / 2.0
        lo, hi = c - min_span / 2.0, c + min_span / 2.0
    if ha - la < min_span:
        c = (la + ha) / 2.0
        la, ha = c - min_span / 2.0, c + min_span / 2.0

    # 兜底夹在默认大区
    lo = max(lo, DEFAULT_LON_MIN)
    hi = min(hi, DEFAULT_LON_MAX)
    la = max(la, DEFAULT_LAT_MIN)
    ha = min(ha, DEFAULT_LAT_MAX)
    if hi <= lo:
        mid = (DEFAULT_LON_MIN + DEFAULT_LON_MAX) / 2.0
        lo, hi = mid - 1.0, mid + 1.0
    if ha <= la:
        mid = (DEFAULT_LAT_MIN + DEFAULT_LAT_MAX) / 2.0
        la, ha = mid - 1.0, mid + 1.0
    return lo, hi, la, ha


def _format_signed_delta(delta: float | None, unit: str, *, digits: int = 0) -> str:
    """格式化带符号变化量，如 +2m/s / -3hPa。"""
    if delta is None:
        return ""
    if abs(delta) < 0.05:
        return f"0{unit}"
    q = round(delta, digits) if digits > 0 else int(round(delta))
    if digits > 0:
        text = f"{q:+.{digits}f}".rstrip("0").rstrip(".")
    else:
        text = f"{q:+d}"
    return f"{text}{unit}"


def _build_intensity_trend(
    *,
    wind_speed: float | None,
    pressure: float | None,
    prev_ws: float | None,
    prev_pr: float | None,
    category_name: str,
    prev_level: str,
) -> tuple[str, str]:
    """综合风速/气压/等级给出强度趋势文案与样式类。"""
    score = 0
    if wind_speed is not None and prev_ws is not None:
        d = wind_speed - prev_ws
        if d > 0.5:
            score += 1
        elif d < -0.5:
            score -= 1
    if pressure is not None and prev_pr is not None:
        d = pressure - prev_pr
        # 气压下降通常意味着增强
        if d < -0.5:
            score += 1
        elif d > 0.5:
            score -= 1
    cat_now = level_weight(category_name)
    cat_prev = level_weight(prev_level)
    if cat_now > cat_prev:
        score += 1
    elif cat_now < cat_prev:
        score -= 1

    if score >= 1:
        return "强度增强", "intensity-up"
    if score <= -1:
        return "强度减弱", "intensity-down"
    return "强度稳定", "intensity-flat"


def _resolve_source_label(
    data_source: str | None, source_label: str | None = None
) -> str:
    """统一来源角标文案（EQSC / FAN / LOCAL）。"""
    text = clean_text(source_label) or clean_text(data_source)
    low = text.lower()
    if "eqsc" in low:
        return "EQSC"
    if "fan" in low:
        return "FAN"
    if "local" in low or "本地" in text:
        return "LOCAL"
    if text:
        return text.upper()
    return "EQSC"


def _extract_payload(data: TyphoonEvent | dict[str, Any]) -> dict[str, Any]:
    """统一 TyphoonEvent / TyphoonQueryItem 为渲染输入字典。"""
    if isinstance(data, TyphoonEvent):
        return {
            "typhoon_id": data.typhoon_id,
            "name": data.name,
            "name_en": data.name_en,
            "display_name": data.name or data.name_en or data.typhoon_id,
            "typhoon_type": data.typhoon_type,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "pressure": data.pressure,
            "wind_speed": data.wind_speed,
            "move_direction": data.move_direction,
            "move_speed": data.move_speed,
            "updated_at": data.updated_at,
            "history_track": list(data.history_track or []),
            "future_track": list(data.future_track or []),
            "wind_circle": dict(data.wind_circle or {}),
            "data_source": "eqsc",
            "source_label": "EQSC",
        }
    if isinstance(data, dict):
        return data
    return {}


class TyphoonMapRenderer:
    """台风路径图渲染器（Playwright，EQSC 原生字段契约）。"""

    def __init__(self, browser_manager=None, plugin_root: str = ""):
        self.browser_manager = browser_manager
        self.plugin_root = plugin_root
        self._template_cache: str | None = None
        self._template_mtime: float | None = None
        self._helper_js_cache: str | None = None
        self._helper_js_mtime: float | None = None

    async def _track_render_template_error(
        self, exception: Exception, label: str
    ) -> None:
        """上报模板渲染阶段（render_card 之前）的失败，覆盖渲染性能指标的盲区。"""
        telemetry = getattr(
            getattr(self.browser_manager, "_telemetry", None), "enabled", False
        )
        if not telemetry:
            return
        try:
            await self.browser_manager._telemetry.track_error(
                exception,
                module=f"core.render.{label}.render_template",
            )
        except Exception as track_err:
            logger.debug(
                f"[灾害预警] 台风模板渲染错误遥测上报失败（已忽略）: {track_err}"
            )

    def _get_template(self) -> str:
        """读取 HTML 模板原文（含占位符）。

        按文件 mtime 失效缓存，避免热更新后仍用旧模板导致视野/样式不生效。
        """
        template_path = os.path.join(
            self.plugin_root,
            "resources",
            "card_templates",
            "Typhoon",
            "typhoon_track.html",
        )
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"台风模板未找到: {template_path}")
        mtime = os.path.getmtime(template_path)
        if (
            self._template_cache is not None
            and self._template_mtime is not None
            and mtime == self._template_mtime
        ):
            return self._template_cache
        with open(template_path, encoding="utf-8") as f:
            self._template_cache = f.read()
        self._template_mtime = mtime
        return self._template_cache

    def _get_helper_js(self) -> str:
        """读取 map_render_helper.js（按 mtime 缓存，避免每次渲染重复读盘）。"""
        helper_path = os.path.join(
            self.plugin_root,
            "resources",
            "card_templates",
            "map_render_helper.js",
        )
        if not os.path.exists(helper_path):
            raise FileNotFoundError(f"地图渲染辅助脚本未找到: {helper_path}")
        mtime = os.path.getmtime(helper_path)
        if (
            self._helper_js_cache is not None
            and self._helper_js_mtime is not None
            and mtime == self._helper_js_mtime
        ):
            return self._helper_js_cache
        with open(helper_path, encoding="utf-8") as f:
            self._helper_js_cache = f.read()
        self._helper_js_mtime = mtime
        return self._helper_js_cache

    def can_render(self, data: TyphoonEvent | dict[str, Any] | None) -> bool:
        """是否具备可渲染轨迹（至少一个有效历史点）。"""
        if data is None:
            return False
        payload = _extract_payload(data)
        history = _normalize_track_nodes(payload.get("history_track"))
        return len(history) >= 1

    async def render(
        self,
        data: TyphoonEvent | dict[str, Any],
        output_path: str,
        *,
        map_source: str | None = None,
        playwright_mode: str = "local",
    ) -> str | None:
        """渲染台风路径图到 output_path，成功返回路径，失败返回 None。"""
        payload = _extract_payload(data)
        history = _normalize_track_nodes(payload.get("history_track"))
        future = _normalize_track_nodes(payload.get("future_track"))
        if not history:
            logger.warning("[灾害预警] 台风路径图跳过：无有效 history_track")
            return None

        try:
            html_content = self._render_html(
                payload,
                history=history,
                future=future,
                map_source=map_source or DEFAULT_MAP_SOURCE,
                playwright_mode=playwright_mode,
            )
        except Exception as e:
            logger.error(f"[灾害预警] 台风模板渲染失败: {e}", exc_info=True)
            # 模板渲染失败发生在 render_card 之前，是 render_performance 指标的盲区，需补上报。
            await self._track_render_template_error(e, "typhoon")
            return None

        if not self.browser_manager:
            logger.warning("[灾害预警] 无浏览器管理器，跳过台风路径图渲染")
            return None

        try:
            # 与 S-Net 一致：临时放大视口后截 #card-wrapper。
            result = await self.browser_manager.render_card(
                html_content,
                output_path,
                selector="#card-wrapper",
                wait_until="domcontentloaded",
                viewport=TYPHOON_VIEWPORT,
                render_label="台风路径图",
                event_stream="typhoon",
            )
            if result and os.path.exists(output_path):
                return output_path
            logger.warning("[灾害预警] 台风路径图渲染未生成文件")
            return None
        except Exception as e:
            logger.error(
                f"[灾害预警] 台风路径图 Playwright 渲染失败: {e}",
                exc_info=True,
            )
            return None

    def _render_html(
        self,
        payload: dict[str, Any],
        *,
        history: list[dict[str, Any]],
        future: list[dict[str, Any]],
        map_source: str,
        playwright_mode: str,
    ) -> str:
        """把 bootstrap / 静态资源 URL 注入模板占位符。"""
        bootstrap = self._build_bootstrap(
            payload,
            history=history,
            future=future,
            map_source=map_source,
        )
        resources_dir = os.path.join(self.plugin_root, "resources", "card_templates")
        leaflet_js_path = Path(
            os.path.abspath(os.path.join(resources_dir, "leaflet.js"))
        )
        leaflet_css_path = Path(
            os.path.abspath(os.path.join(resources_dir, "leaflet.css"))
        )
        helper_js = self._get_helper_js()

        # remote Playwright 无法读本地 file:// 时，回退 CDN。
        if playwright_mode == "remote":
            leaflet_js_url = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
            leaflet_css_url = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        else:
            leaflet_js_url = leaflet_js_path.as_uri()
            leaflet_css_url = leaflet_css_path.as_uri()

        html = self._get_template()
        html = html.replace(
            _PLACEHOLDER_BOOTSTRAP,
            json.dumps(bootstrap, ensure_ascii=False, default=str),
        )
        html = html.replace(_PLACEHOLDER_LEAFLET_JS, leaflet_js_url)
        html = html.replace(_PLACEHOLDER_LEAFLET_CSS, leaflet_css_url)
        html = html.replace(_PLACEHOLDER_HELPER_JS, helper_js)
        return html

    def _build_bootstrap(
        self,
        payload: dict[str, Any],
        *,
        history: list[dict[str, Any]],
        future: list[dict[str, Any]],
        map_source: str,
    ) -> dict[str, Any]:
        """组装模板 bootstrap JSON（面板 + 轨迹 + 视野 + 瓦片）。"""
        latest = history[-1]
        prev = history[-2] if len(history) >= 2 else None

        # 面板优先用顶层字段，缺失时回退最新历史点。
        lat = to_float(payload.get("latitude"))
        lon = to_float(payload.get("longitude"))
        if lat is None or lon is None:
            lat, lon = _node_lat_lon(latest)

        wind_speed = to_float(payload.get("wind_speed"))
        if wind_speed is None:
            wind_speed = to_float(
                latest.get("windSpeed")
                if latest.get("windSpeed") is not None
                else latest.get("wind_speed")
            )
        pressure = to_float(payload.get("pressure"))
        if pressure is None:
            pressure = to_float(latest.get("pressure"))

        category_name = clean_text(payload.get("typhoon_type")) or _node_level_text(
            latest
        )
        cat = level_weight(category_name)
        cat_rgb = CAT_COLORS.get(cat, CAT_COLORS[0])

        # 相对上一历史点的风速/气压变化量（数值中性色 + 小标签，避免箭头语义歧义）。
        ws_delta_text = ""
        pr_delta_text = ""
        ws_delta_class = "delta-flat"
        pr_delta_class = "delta-flat"
        intensity_trend_text = "强度稳定"
        intensity_trend_class = "intensity-flat"
        prev_ws = None
        prev_pr = None
        prev_level = ""
        if prev is not None:
            prev_ws = to_float(
                prev.get("windSpeed")
                if prev.get("windSpeed") is not None
                else prev.get("wind_speed")
            )
            prev_pr = to_float(prev.get("pressure"))
            prev_level = _node_level_text(prev)
            if prev_ws is not None and wind_speed is not None:
                d_ws = wind_speed - prev_ws
                if abs(d_ws) >= 0.5:
                    ws_delta_text = _format_signed_delta(d_ws, "m/s")
                    ws_delta_class = "delta-up" if d_ws > 0 else "delta-down"
            if prev_pr is not None and pressure is not None:
                d_pr = pressure - prev_pr
                if abs(d_pr) >= 0.5:
                    pr_delta_text = _format_signed_delta(d_pr, "hPa")
                    # 气压标签只表达数值升降，用中性蓝灰，避免红色误导
                    pr_delta_class = "delta-pr-up" if d_pr > 0 else "delta-pr-down"
            intensity_trend_text, intensity_trend_class = _build_intensity_trend(
                wind_speed=wind_speed,
                pressure=pressure,
                prev_ws=prev_ws,
                prev_pr=prev_pr,
                category_name=category_name,
                prev_level=prev_level,
            )

        wind_circle = payload.get("wind_circle")
        if not isinstance(wind_circle, dict) or not wind_circle:
            wind_circle = _node_wind_circle(latest)
        wind_radii = _build_wind_radii_panel(wind_circle)

        # 迷你折线图：最近 24 个历史点 + 时间刻度。
        chart_nodes = history[-24:] if len(history) >= 2 else history
        chart_winds = [
            float(
                to_float(
                    n.get("windSpeed")
                    if n.get("windSpeed") is not None
                    else n.get("wind_speed")
                )
                or 0.0
            )
            for n in chart_nodes
        ]
        chart_press = [float(to_float(n.get("pressure")) or 0.0) for n in chart_nodes]
        chart_times = [_format_compact_time(n.get("time")) for n in chart_nodes]

        lo, hi, la, ha = _compute_view_bounds(history, future)
        event_name = (
            clean_text(
                payload.get("display_name")
                or payload.get("name")
                or payload.get("name_en")
                or payload.get("typhoon_id")
            )
            or "未知台风"
        )
        event_code = (
            clean_text(payload.get("typhoon_id") or payload.get("eqsc_id")) or "--"
        )
        source = _resolve_source_label(
            str(payload.get("data_source") or ""),
            str(payload.get("source_label") or ""),
        )
        move_dir = clean_text(
            payload.get("move_direction")
            or latest.get("directionCN")
            or latest.get("direction")
        )
        move_speed_val = to_float(payload.get("move_speed"))
        if move_speed_val is None:
            move_speed_val = to_float(latest.get("speed") or latest.get("moveSpeed"))

        version = get_plugin_version()
        watermark_text = (
            f"@DBJD-CR/astrbot_plugin_disaster_warning (灾害预警) {version} · 台风路径"
        )

        return {
            # 轨迹：模板 JS 直接 normalize EQSC 节点
            "history": history,
            "forecast": future,
            "view": {"lo": lo, "hi": hi, "la": la, "ha": ha},
            "chart_winds": chart_winds,
            "chart_pressures": chart_press,
            "chart_times": chart_times,
            "tile_url": get_tile_url_js(map_source or DEFAULT_MAP_SOURCE),
            "wind_radii": wind_radii,
            "forecast_rows": _build_table_rows(future, limit=12),
            "history_rows": _build_table_rows(history, reverse=True),
            "panel": {
                "event_name": event_name,
                "event_code": event_code,
                "source": source,
                "category_name": category_name,
                "cat_color_rgb": f"{cat_rgb[0]},{cat_rgb[1]},{cat_rgb[2]}",
                "wind_speed": (
                    f"{wind_speed:.0f}m/s" if wind_speed is not None else "--"
                ),
                "pressure": f"{pressure:.0f}hPa" if pressure is not None else "--",
                # 兼容旧字段（模板已不再依赖箭头染色）
                "ws_trend": "",
                "pr_trend": "",
                "ws_delta_text": ws_delta_text,
                "pr_delta_text": pr_delta_text,
                "ws_delta_class": ws_delta_class,
                "pr_delta_class": pr_delta_class,
                "intensity_trend_text": intensity_trend_text,
                "intensity_trend_class": intensity_trend_class,
                "position": format_coordinates(lat, lon) or "--",
                "move_dir": move_dir,
                "move_speed": (
                    f"{move_speed_val:.0f}km/h" if move_speed_val is not None else ""
                ),
                "update_time": _format_panel_time(
                    payload.get("updated_at")
                    or payload.get("updated_at_text")
                    or latest.get("time")
                ),
                "watermark_text": watermark_text,
            },
        }


__all__ = ["TyphoonMapRenderer", "TYPHOON_VIEWPORT"]
