"""
海啸警报等级权重与归一化。

统一中国（自然资源部）与日本（JMA / EQSC / P2P）的阈值语义，
供推送过滤、入库摘要与后续统计共用，避免各层各自维护一套映射。
"""

from __future__ import annotations

from typing import Any

# 中国海啸：信息 < 蓝色 < 黄色 < 橙色 < 红色
# 解除单独处理：过滤时默认放行（便于收到解除通告）。
CN_TSUNAMI_LEVEL_ORDER: list[str] = [
    "信息",
    "蓝色",
    "黄色",
    "橙色",
    "红色",
]

CN_TSUNAMI_LEVEL_ALIASES: dict[str, str] = {
    "信息": "信息",
    "海啸信息": "信息",
    "info": "信息",
    "message": "信息",
    "蓝色": "蓝色",
    "蓝色警报": "蓝色",
    "蓝色预警": "蓝色",
    "blue": "蓝色",
    "黄色": "黄色",
    "黄色警报": "黄色",
    "黄色预警": "黄色",
    "yellow": "黄色",
    "橙色": "橙色",
    "橙色警报": "橙色",
    "橙色预警": "橙色",
    "orange": "橙色",
    "红色": "红色",
    "红色警报": "红色",
    "红色预警": "红色",
    "red": "红色",
    "解除": "解除",
    "取消": "解除",
    "cancel": "解除",
    "cancelled": "解除",
}

# 日本海啸：Minor < Watch < Warning < MajorWarning
# None / Unknown 视为最低有效等级以下的“预报/未知”。
JP_TSUNAMI_LEVEL_ORDER: list[str] = [
    "None",
    "Unknown",
    "Minor",
    "Watch",
    "Warning",
    "MajorWarning",
]

# 配置项/展示用中文名（与 _conf_schema 一致）
JP_TSUNAMI_LEVEL_DISPLAY: dict[str, str] = {
    "Minor": "若干海面变动",
    "Watch": "海啸注意报",
    "Warning": "海啸警报",
    "MajorWarning": "大海啸警报",
}

JP_TSUNAMI_LEVEL_ALIASES: dict[str, str] = {
    "none": "None",
    "unknown": "Unknown",
    "minor": "Minor",
    "若干の海面変動": "Minor",
    "若干的海面变动": "Minor",
    "若干海面变动": "Minor",
    "watch": "Watch",
    "津波注意報": "Watch",
    "海啸注意报": "Watch",
    "warning": "Warning",
    "津波警報": "Warning",
    "海啸警报": "Warning",
    "majorwarning": "MajorWarning",
    "大津波警報": "MajorWarning",
    "大海啸警报": "MajorWarning",
    "解除": "解除",
    "cancel": "解除",
    "cancelled": "解除",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return ""
    return text


def normalize_cn_tsunami_level(raw: Any) -> str:
    """归一化中国海啸级别。"""
    text = _clean_text(raw)
    if not text:
        return ""
    # 去掉常见后缀
    stripped = text.replace("级", "").replace("警报", "").replace("预警", "").strip()
    lowered = text.lower()
    if text in CN_TSUNAMI_LEVEL_ALIASES:
        return CN_TSUNAMI_LEVEL_ALIASES[text]
    if stripped in CN_TSUNAMI_LEVEL_ALIASES:
        return CN_TSUNAMI_LEVEL_ALIASES[stripped]
    if lowered in CN_TSUNAMI_LEVEL_ALIASES:
        return CN_TSUNAMI_LEVEL_ALIASES[lowered]
    # 标题内嵌颜色
    for color in ("红色", "橙色", "黄色", "蓝色"):
        if color in text:
            return color
    if "信息" in text:
        return "信息"
    if "解除" in text or "取消" in text:
        return "解除"
    return text


def normalize_jp_tsunami_level(raw: Any, *, cancelled: bool = False) -> str:
    """归一化日本海啸级别。"""
    if cancelled:
        return "解除"
    text = _clean_text(raw)
    if not text:
        return "Unknown"
    if text == "解除":
        return "解除"
    lowered = text.lower()
    if lowered in JP_TSUNAMI_LEVEL_ALIASES:
        return JP_TSUNAMI_LEVEL_ALIASES[lowered]
    if text in JP_TSUNAMI_LEVEL_ALIASES:
        return JP_TSUNAMI_LEVEL_ALIASES[text]
    # 已是标准枚举
    if text in JP_TSUNAMI_LEVEL_ORDER:
        return text
    return text


def cn_tsunami_level_weight(raw: Any) -> int:
    """中国海啸级别权重；未知返回 0，解除返回 -1。"""
    level = normalize_cn_tsunami_level(raw)
    if not level:
        return 0
    if level == "解除":
        return -1
    try:
        return CN_TSUNAMI_LEVEL_ORDER.index(level) + 1
    except ValueError:
        return 0


def jp_tsunami_level_weight(raw: Any, *, cancelled: bool = False) -> int:
    """日本海啸级别权重；未知返回 0，解除返回 -1。"""
    level = normalize_jp_tsunami_level(raw, cancelled=cancelled)
    if not level:
        return 0
    if level == "解除":
        return -1
    try:
        return JP_TSUNAMI_LEVEL_ORDER.index(level) + 1
    except ValueError:
        return 0


def resolve_tsunami_region(
    source_id: str | None, metadata: dict[str, Any] | None = None
) -> str:
    """判定海啸区域：china / japan / unknown。"""
    meta = metadata if isinstance(metadata, dict) else {}
    family = str(meta.get("source_family") or "").strip().lower()
    sid = str(source_id or "").strip().lower()
    if family in {"eqsc", "p2p"} or "jma" in sid or "japan" in sid:
        return "japan"
    if family in {"fan_studio", "fanstudio"} or "china" in sid or "fan" in sid:
        return "china"
    # 回退：看等级形态
    level = str(meta.get("level") or "").strip()
    if level in JP_TSUNAMI_LEVEL_ORDER or level in {"解除"} and meta.get("forecasts"):
        if any(
            key in meta
            for key in ("content_fingerprint", "grade_counts", "issue_hypocenter")
        ):
            return "japan"
    cn_level = normalize_cn_tsunami_level(level)
    if cn_level in CN_TSUNAMI_LEVEL_ORDER or cn_level == "解除":
        return "china"
    return "unknown"


def to_optional_float(value: Any) -> float | None:
    """宽松解析浮点。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:  # NaN
            return None
        return number
    text = _clean_text(value)
    if not text:
        return None
    # 去掉常见单位
    for token in ("m", "ｍ", "米", "Ｍ"):
        text = text.replace(token, "")
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        return None


def _format_highlight_items(items: list[str] | None, *, limit: int = 5) -> str:
    """把亮点列表压成可读短串。"""
    cleaned: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return "；".join(cleaned)


def build_tsunami_weather_detail(
    *,
    region: str,
    level: str,
    area_count: int | None = None,
    immediate_area_count: int | None = None,
    max_wave_height: str | None = None,
    max_wave_height_value: float | None = None,
    max_wave_height_area: str | None = None,
    station_count: int | None = None,
    cancelled: bool = False,
    is_training: bool = False,
    batch: Any = None,
    magnitude: float | None = None,
    depth: float | None = None,
    place_name: str | None = None,
    grade_counts: dict[str, Any] | None = None,
    forecast_highlights: list[str] | None = None,
    station_highlights: list[str] | None = None,
) -> str:
    """构建入库用的海啸摘要文本（复用 weather_detail 列）。

    对齐推送展示器语义：区域数、波高、监测站、级别分布与重点亮点。
    前端列表可解析该文本，旧记录字段缺失时也能优雅降级。
    """
    parts: list[str] = []
    if region == "japan":
        parts.append("日本海啸")
    elif region == "china":
        parts.append("中国海啸")
    if level:
        parts.append(f"级别 {level}")
    if cancelled or level == "解除":
        parts.append("已解除")
    if is_training:
        parts.append("训练报")
    if place_name:
        parts.append(f"震中 {place_name}")
    if magnitude is not None:
        mag_text = int(magnitude) if float(magnitude).is_integer() else magnitude
        prefix = "Mj" if region == "japan" else "M"
        parts.append(f"{prefix}{mag_text}")
    if depth is not None:
        depth_text = int(depth) if float(depth).is_integer() else depth
        parts.append(f"深度 {depth_text}km")
    if area_count is not None and area_count > 0:
        parts.append(f"预报区 {area_count}")
    if immediate_area_count is not None and immediate_area_count > 0:
        parts.append(f"立即到达 {immediate_area_count}")
    if max_wave_height:
        if max_wave_height_area:
            parts.append(f"最大波高 {max_wave_height}（{max_wave_height_area}）")
        else:
            parts.append(f"最大波高 {max_wave_height}")
    elif max_wave_height_value is not None:
        if max_wave_height_area:
            parts.append(f"最大波高 {max_wave_height_value}m（{max_wave_height_area}）")
        else:
            parts.append(f"最大波高 {max_wave_height_value}m")
    if station_count is not None and station_count > 0:
        parts.append(f"监测站 {station_count}")
    if batch not in (None, ""):
        parts.append(f"批次 {batch}")

    # 级别分布：MajorWarning 2 / Warning 5 ...
    if isinstance(grade_counts, dict) and grade_counts:
        order = ("MajorWarning", "Warning", "Watch", "Minor")
        grade_labels = {
            "MajorWarning": "大海啸警报",
            "Warning": "海啸警报",
            "Watch": "海啸注意报",
            "Minor": "若干海面变动",
        }
        grade_parts: list[str] = []
        for key in order:
            count = grade_counts.get(key)
            try:
                count_int = int(count) if count is not None else 0
            except (TypeError, ValueError):
                count_int = 0
            if count_int > 0:
                grade_parts.append(f"{grade_labels.get(key, key)} {count_int}")
        if grade_parts:
            parts.append(f"级别分布 {' / '.join(grade_parts)}")

    forecast_text = _format_highlight_items(forecast_highlights, limit=6)
    if forecast_text:
        parts.append(f"重点预报 {forecast_text}")

    station_text = _format_highlight_items(station_highlights, limit=4)
    if station_text:
        parts.append(f"监测实况 {station_text}")

    return "，".join(parts)


__all__ = [
    "CN_TSUNAMI_LEVEL_ORDER",
    "JP_TSUNAMI_LEVEL_DISPLAY",
    "JP_TSUNAMI_LEVEL_ORDER",
    "build_tsunami_weather_detail",
    "cn_tsunami_level_weight",
    "jp_tsunami_level_weight",
    "normalize_cn_tsunami_level",
    "normalize_jp_tsunami_level",
    "resolve_tsunami_region",
    "to_optional_float",
]
