"""
海啸列表标题构建。

统一生成管理端事件列表、时间轴与入库 description 使用的短标题，
避免解析器、统计工厂与前端各自维护一套拼接规则。

设计目标：
- 一眼可读：级别 + 震中 + 震级
- 中日语义分离，列表默认中文等级
- 字段缺失时优雅降级（兼容升级前旧记录）
"""

from __future__ import annotations

import re
from typing import Any

from .tsunami_levels import (
    JP_TSUNAMI_LEVEL_DISPLAY,
    normalize_cn_tsunami_level,
    normalize_jp_tsunami_level,
    resolve_tsunami_region,
    to_optional_float,
)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {
        "null",
        "none",
        "unknown",
        "未知",
        "未知地点",
        "未知位置",
    }:
        return ""
    return text


def is_generic_tsunami_title(text: str) -> bool:
    """判断是否为无信息量的泛化标题（不应作为地点展示）。"""
    cleaned = _clean_text(text)
    if not cleaned:
        return True
    generics = {
        "海啸信息",
        "海啸情报",
        "海啸预警",
        "海啸警报",
        "海啸解除",
        "海啸解除通告",
        "津波予報",
        "津波注意報",
        "津波警報",
        "大津波警報",
        "津波予報（解除）",
        "若干の海面変動",
        "若干海面变动",
        "海啸注意报",
        "大海啸警报",
    }
    if cleaned in generics:
        return True
    # “海啸黄色警报”这类纯等级标题
    if cleaned.startswith("海啸") and any(
        token in cleaned for token in ("信息", "警报", "预警", "解除", "注意报")
    ):
        # 若包含明显地名特征则不算泛化（极少见）
        if "·" in cleaned or "M" in cleaned or "Mj" in cleaned:
            return False
        # 短标题且无空格分隔地点
        if len(cleaned) <= 12:
            return True
    return False


def format_tsunami_level_label(
    level: Any,
    *,
    region: str = "unknown",
    cancelled: bool = False,
    raw_title: str = "",
) -> str:
    """把标准化等级转为列表用中文主标签。"""
    if cancelled:
        return "海啸解除"

    region_key = (region or "unknown").strip().lower()
    raw_level = _clean_text(level)

    if region_key == "japan":
        normalized = normalize_jp_tsunami_level(raw_level, cancelled=False)
        if normalized == "解除":
            return "海啸解除"
        display = JP_TSUNAMI_LEVEL_DISPLAY.get(normalized)
        if display:
            # JP_TSUNAMI_LEVEL_DISPLAY 已是中文：若干海面变动 / 海啸注意报…
            if display.startswith("海啸") or display.startswith("若干"):
                return display
            return f"海啸{display}"
        if normalized in {"None", "Unknown"}:
            return "海啸预报"
        return raw_level or "海啸预报"

    if region_key == "china":
        normalized = normalize_cn_tsunami_level(raw_level)
        if normalized == "解除":
            return "海啸解除"
        if normalized == "信息":
            return "海啸信息"
        if normalized in {"蓝色", "黄色", "橙色", "红色"}:
            return f"海啸{normalized}警报"
        # 回退：原始 title 若已是完整警报名则复用
        title = _clean_text(raw_title)
        if title and not is_generic_tsunami_title(title):
            # title 可能是“海啸黄色警报”，可直接用
            if title.startswith("海啸"):
                return title
        if raw_level:
            return (
                f"海啸{raw_level}警报"
                if "警报" not in raw_level
                else f"海啸{raw_level}"
            )
        return title or "海啸情报"

    # unknown：尽量从 title / level 推断
    title = _clean_text(raw_title)
    if title and title.startswith("海啸"):
        return title
    if raw_level:
        cn = normalize_cn_tsunami_level(raw_level)
        if cn == "信息":
            return "海啸信息"
        if cn in {"蓝色", "黄色", "橙色", "红色"}:
            return f"海啸{cn}警报"
        if cn == "解除":
            return "海啸解除"
        jp = normalize_jp_tsunami_level(raw_level)
        display = JP_TSUNAMI_LEVEL_DISPLAY.get(jp)
        if display:
            return display if display.startswith(("海啸", "若干")) else f"海啸{display}"
        return raw_level
    return title or "海啸情报"


def format_tsunami_magnitude_token(
    magnitude: Any,
    *,
    region: str = "unknown",
) -> str:
    """格式化列表用震级 token：中国 M6.6 / 日本 Mj8.2。"""
    value = to_optional_float(magnitude)
    if value is None:
        return ""
    if float(value).is_integer():
        mag_text = f"{int(value)}.0"
    else:
        mag_text = f"{value:.1f}".rstrip("0").rstrip(".")
        if "." not in mag_text:
            mag_text = f"{mag_text}.0"
    prefix = "Mj" if (region or "").strip().lower() == "japan" else "M"
    return f"{prefix}{mag_text}"


def parse_tsunami_batch_num(batch: Any) -> int | None:
    """解析海啸业务报次为整数。

    支持：
    - 纯数字：3 / "3" / 3.0
    - 中文报次：第3报 / 第 3 报 / 第3批
    - 摘要片段：批次 3

    无法解析时返回 None（不是系统 update_count）。
    """
    if batch is None:
        return None
    if isinstance(batch, bool):
        return None
    if isinstance(batch, (int, float)):
        try:
            number = int(batch)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    text = _clean_text(batch)
    if not text:
        return None
    # 纯数字
    try:
        number = int(float(text))
        if number > 0:
            return number
    except (TypeError, ValueError):
        pass

    match = re.search(r"第\s*(\d+)\s*[报批]", text) or re.search(r"批次\s*(\d+)", text)
    if not match:
        return None
    try:
        number = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def format_tsunami_batch_token(batch: Any) -> str:
    """格式化批次/报次：第3报。"""
    parsed = parse_tsunami_batch_num(batch)
    if parsed is not None:
        return f"第{parsed}报"
    text = _clean_text(batch)
    if not text:
        return ""
    # 已是“第n报/批”
    if text.startswith("第") and ("报" in text or "批" in text):
        return text
    return f"第{text}报"


def build_tsunami_list_title(
    *,
    region: str = "unknown",
    level: str = "",
    title: str = "",
    place_name: str = "",
    magnitude: float | None = None,
    batch: Any = None,
    cancelled: bool = False,
    is_training: bool = False,
    max_wave_height: str | None = None,
    area_count: int | None = None,
) -> str:
    """构建管理端列表/入库用的海啸短标题。

    示例：
    - 海啸信息 · 中国台湾省周边海域 M6.6
    - 海啸黄色警报 · 堪察加东岸远海海域 M8.8
    - 海啸警报 · 三陸沖 Mj8.2
    - [训练] 海啸注意报 · 三陸沖
    - 海啸解除 · 中国台湾省周边海域

    字段全缺时回退：
    - 有 title 且非垃圾：用 title
    - 否则「海啸情报」
    """
    region_key = (region or "unknown").strip().lower() or "unknown"
    level_label = format_tsunami_level_label(
        level,
        region=region_key,
        cancelled=cancelled or _clean_text(level) == "解除",
        raw_title=title,
    )

    place = _clean_text(place_name)
    if is_generic_tsunami_title(place):
        place = ""

    mag_token = format_tsunami_magnitude_token(magnitude, region=region_key)
    # 业务报次统一由卡片徽章展示，标题内不再内嵌「（第N报）」，避免与徽章语义打架。
    batch_token = ""

    # 主段：级别
    head = level_label
    if is_training and not head.startswith("[训练]"):
        head = f"[训练] {head}"

    body_parts: list[str] = []
    if place:
        body_parts.append(place)
    if mag_token:
        # 有地点时震级紧跟地点；无地点时单独一段
        if body_parts:
            body_parts[-1] = f"{body_parts[-1]} {mag_token}"
        else:
            body_parts.append(mag_token)

    # 无地点/震级时，用波高或预报区数补一点辨识度（避免只剩级别）
    if not body_parts:
        wave = _clean_text(max_wave_height)
        if wave:
            body_parts.append(f"最大波高 {wave}")
        elif area_count is not None:
            try:
                count = int(area_count)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                body_parts.append(f"预报区 {count}")

    if batch_token:
        body_parts.append(f"（{batch_token}）")

    if body_parts:
        # 批次用括号贴在末尾，其余用 · 连接
        core: list[str] = []
        suffix = ""
        for part in body_parts:
            if part.startswith("（") and part.endswith("）"):
                suffix = part
            else:
                core.append(part)
        middle = " · ".join(core) if core else ""
        if middle and suffix:
            return f"{head} · {middle}{suffix}"
        if middle:
            return f"{head} · {middle}"
        if suffix:
            return f"{head}{suffix}"
        return head

    # 彻底无附加信息：尽量用原始 title，避免再拼 “(level)”
    raw_title = _clean_text(title)
    if raw_title and not is_generic_tsunami_title(raw_title):
        return (
            f"[训练] {raw_title}"
            if is_training and "[训练]" not in raw_title
            else raw_title
        )
    return head or "海啸情报"


def build_tsunami_list_title_from_mapping(
    data: dict[str, Any] | None,
    *,
    source_id: str = "",
    default_title: str = "",
    default_level: str = "",
) -> str:
    """从 dict / DB 行 / metadata 构建列表标题（前后端共用语义入口）。"""
    row = data if isinstance(data, dict) else {}
    region = resolve_tsunami_region(
        source_id or row.get("source_id") or row.get("source"), row
    )

    level = _clean_text(row.get("level") or default_level)
    title = _clean_text(row.get("title") or row.get("description") or default_title)
    place_name = _clean_text(
        row.get("place_name") or row.get("placeName") or row.get("subtitle") or ""
    )
    # subtitle 若其实是泛化标题，不当作地点
    if is_generic_tsunami_title(place_name):
        place_name = _clean_text(row.get("place_name") or row.get("placeName") or "")

    magnitude = to_optional_float(row.get("magnitude"))
    cancelled = bool(
        row.get("is_cancelled")
        or row.get("cancelled")
        or level == "解除"
        or "解除" in title
    )
    is_training = bool(row.get("is_training") or row.get("isTraining"))
    batch = row.get("batch")
    max_wave = _clean_text(
        row.get("max_wave_height_text")
        or row.get("maxWaveHeight")
        or (
            f"{row.get('max_wave_height')}m"
            if isinstance(row.get("max_wave_height"), (int, float))
            else row.get("max_wave_height")
        )
        or ""
    )
    area_count = row.get("area_count")
    try:
        area_count_int = int(area_count) if area_count is not None else None
    except (TypeError, ValueError):
        area_count_int = None

    return build_tsunami_list_title(
        region=region,
        level=level,
        title=title,
        place_name=place_name,
        magnitude=magnitude,
        batch=batch,
        cancelled=cancelled,
        is_training=is_training,
        max_wave_height=max_wave or None,
        area_count=area_count_int,
    )


def is_legacy_tsunami_description(description: Any, level: Any = None) -> bool:
    """识别升级前的低质量 description：如「海啸信息 (信息)」。"""
    text = _clean_text(description)
    if not text:
        return True
    # title (level)
    if " (" in text and text.endswith(")"):
        head, _, tail = text.partition(" (")
        level_part = tail[:-1].strip() if tail.endswith(")") else ""
        if head and level_part:
            # 等级括号与主标题高度同义
            if level_part in head or head in {
                f"海啸{level_part}",
                f"海啸{level_part}警报",
                "海啸信息",
            }:
                return True
            if _clean_text(level) and level_part == _clean_text(level):
                return True
    if is_generic_tsunami_title(text):
        return True
    return False


__all__ = [
    "build_tsunami_list_title",
    "build_tsunami_list_title_from_mapping",
    "format_tsunami_batch_token",
    "format_tsunami_level_label",
    "format_tsunami_magnitude_token",
    "is_generic_tsunami_title",
    "is_legacy_tsunami_description",
    "parse_tsunami_batch_num",
]
