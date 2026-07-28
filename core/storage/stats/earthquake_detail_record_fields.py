"""
地震情报类事件的 weather_detail 落库辅助。

把可能含大量补充信息的地震情报摘要写入 weather_detail，
便于管理端事件列表像气象预警一样展开查看正文：
- P2P / Wolfx 各地震度相关情报（jma_points）
- FSSN 矩心矩张量解（CMT 节面 / 多震级）
- CENC 烈度速报仍由 cenc_intensity_record_fields 负责
"""

from __future__ import annotations

from typing import Any

from ....utils.converters import ScaleConverter
from ...domain.earthquake.cmt_normalize import (
    format_fault_type_label,
    is_fssn_cmt_source,
)
from ...domain.event_models import EventEnvelope
from ...domain.event_payload import SourcePayload
from ..source_compat import is_cenc_intensity_report, is_fssn_cmt_report

# weather_detail 文本长度上限（命名常量，便于统一调整）
DEFAULT_DETAIL_LIMIT = 2048
JMA_COMMENT_LIMIT = 256
JMA_DETAIL_LIMIT = 4096
FSSN_CMT_DETAIL_LIMIT = 1024


def truncate_text(text: str, *, limit: int = DEFAULT_DETAIL_LIMIT) -> str:
    """截断长文本，避免 weather_detail 过大。"""
    content = str(text or "").strip()
    if not content or len(content) <= limit:
        return content
    if limit <= 1:
        return content[:limit]
    return content[: max(0, limit - 1)].rstrip() + "…"


def _pick_from_sources(*sources: Any, keys: tuple[str, ...]) -> Any:
    """从多层字典中按候选键取值。"""
    for key in keys:
        for source in sources:
            if not isinstance(source, dict) or key not in source:
                continue
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _format_scale_display(scale: Any) -> str:
    """统一震度展示文本。"""
    if scale is None or scale == "":
        return ""
    # P2P 业务整数优先走专用格式化
    raw_int = ScaleConverter.normalize_p2p_scale_value(scale)
    if raw_int is not None and raw_int >= 10:
        text = ScaleConverter.format_p2p_scale_display(raw_int)
        if text:
            return text
    text = ScaleConverter.format_jma_cwa_scale_display(scale)
    if text:
        return text
    return str(scale).strip()


def _scale_sort_key(scale: Any) -> float:
    """震度排序键：数值越大越靠前。"""
    raw_int = ScaleConverter.normalize_p2p_scale_value(scale)
    if raw_int is not None and raw_int >= 0:
        converted = ScaleConverter.convert_p2p_scale(raw_int)
        if converted is not None:
            return float(converted)
        return float(raw_int)
    try:
        return float(scale)
    except (TypeError, ValueError):
        parsed = ScaleConverter.parse_jma_cwa_scale(scale)
        if parsed is not None:
            return float(parsed)
        return -1.0


def _format_jma_info_type_label(info_type: str) -> str:
    """日本情报类型中文标签。"""
    mapping = {
        "ScalePrompt": "震度速报",
        "Destination": "震源相关情报",
        "ScaleAndDestination": "震度・震源相关情报",
        "DetailScale": "各地震度相关情报",
        "Foreign": "远地地震相关情报",
        "Other": "其他情报",
    }
    text = str(info_type or "").strip()
    if not text:
        return ""
    if text in mapping:
        return mapping[text]
    # 已是中文标题时直接返回
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return text
    return text


def _collect_jma_points(*sources: Any) -> list[dict[str, Any]]:
    """从 metadata / payload 提取 jma_points。"""
    raw = _pick_from_sources(
        *sources,
        keys=("jma_points", "points", "Points"),
    )
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _build_jma_points_summary(
    points: list[dict[str, Any]],
    *,
    max_scales: int = 6,
    max_addrs_per_scale: int = 8,
    max_total_addrs: int = 36,
) -> str:
    """把观测点按震度分组压成可读摘要。"""
    if not points:
        return ""

    scale_groups: dict[Any, list[str]] = {}
    for point in points:
        addr = str(
            point.get("addr")
            or point.get("Addr")
            or point.get("name")
            or point.get("Name")
            or point.get("location")
            or ""
        ).strip()
        if not addr:
            continue
        scale = point.get("scale")
        if scale is None:
            scale = point.get("Scale") or point.get("shindo") or point.get("intensity")
        scale_groups.setdefault(scale, []).append(addr)

    if not scale_groups:
        return ""

    sorted_scales = sorted(scale_groups.keys(), key=_scale_sort_key, reverse=True)
    lines: list[str] = []
    used_addrs = 0
    for scale in sorted_scales[:max_scales]:
        addrs = scale_groups.get(scale) or []
        if not addrs:
            continue
        remaining = max(0, max_total_addrs - used_addrs)
        if remaining <= 0:
            extra_scales = len(sorted_scales) - len(lines)
            if extra_scales > 0:
                lines.append(f"…另有 {extra_scales} 档震度")
            break
        show_n = min(max_addrs_per_scale, remaining, len(addrs))
        shown = addrs[:show_n]
        used_addrs += show_n
        scale_text = _format_scale_display(scale) or str(scale)
        loc_str = "、".join(shown)
        if len(addrs) > show_n:
            loc_str += f" 等{len(addrs)}处"
        lines.append(f"[震度{scale_text}] {loc_str}")

    if not lines:
        return ""
    return "各地震度详情：\n" + "\n".join(f"  {line}" for line in lines)


def _build_jma_earthquake_detail(
    *,
    info_type: str,
    event_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
    payload_attributes: dict[str, Any],
    payload_raw: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """构建日本地震情报（含各地震度）正文摘要。"""
    sources = (
        event_metadata,
        envelope_metadata,
        payload_attributes,
        payload_raw,
        payload,
    )
    points = _collect_jma_points(*sources)
    comment = str(
        _pick_from_sources(*sources, keys=("jma_comment", "freeFormComment", "comment"))
        or ""
    ).strip()
    domestic_tsunami = str(
        _pick_from_sources(
            *sources,
            keys=("domestic_tsunami", "domesticTsunami", "info"),
        )
        or ""
    ).strip()
    revision = str(
        _pick_from_sources(*sources, keys=("revision", "correct")) or ""
    ).strip()
    if revision.lower() in {"none", "null"}:
        revision = ""

    # 无观测点、无备注时不写 weather_detail，避免普通测定误出展开入口
    if not points and not comment:
        return ""

    parts: list[str] = []
    type_label = _format_jma_info_type_label(info_type)
    if type_label:
        parts.append(type_label)
    if revision:
        parts.append(f"订正：{revision}")

    tsunami_mapping = {
        "None": "无需担心海啸",
        "Unknown": "不明",
        "Checking": "调查中",
        "NonEffective": "预计会有若干海面变动，无须担心受害",
        "Watch": "正在/已经发布津波注意报",
        "Warning": "正在/已经发布津波警报/大津波警报",
    }
    if domestic_tsunami:
        tsunami_text = tsunami_mapping.get(domestic_tsunami, domestic_tsunami)
        parts.append(f"津波：{tsunami_text}")

    if points:
        parts.append(f"观测点 {len(points)}")
        points_summary = _build_jma_points_summary(points)
        if points_summary:
            parts.append(points_summary)

    if comment:
        parts.append("备注：" + truncate_text(comment, limit=JMA_COMMENT_LIMIT))

    # 用换行拼接多段，前端 pre-wrap 可直接展开阅读
    return truncate_text("\n".join(parts), limit=JMA_DETAIL_LIMIT)


def _format_mag_token(mag_type: str, value: Any) -> str:
    """格式化震级片段。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = str(value or "").strip()
        return f"{mag_type} {text}" if text else ""
    return f"{mag_type} {number:.1f}"


def _format_plane_line(label: str, plane: Any) -> str:
    """格式化节面一行。"""
    if not isinstance(plane, dict):
        return ""
    strike = plane.get("strike")
    dip = plane.get("dip")
    rake = plane.get("rake")
    if strike is None or dip is None or rake is None:
        raw = str(plane.get("raw") or "").strip()
        if raw:
            fault = format_fault_type_label(plane)
            return f"{label}：{raw}（{fault}）" if fault else f"{label}：{raw}"
        return ""
    fault = format_fault_type_label(plane)
    base = f"{label}：走向 {strike}° / 倾角 {dip}° / 滑动角 {rake}°"
    return f"{base}（{fault}）" if fault else base


def _build_fssn_cmt_detail(
    *,
    event_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
    payload_attributes: dict[str, Any],
    payload_raw: dict[str, Any],
    payload: dict[str, Any],
    domain_event: Any,
) -> str:
    """构建 FSSN CMT 正文摘要。"""
    sources = (
        event_metadata,
        envelope_metadata,
        payload_attributes,
        payload_raw,
        payload,
    )
    all_mags = _pick_from_sources(*sources, keys=("all_magnitudes", "allMagnitudes"))
    if not isinstance(all_mags, dict):
        all_mags = {}

    display_mag = _pick_from_sources(
        *sources,
        keys=("display_magnitude", "displayMagnitude"),
    )
    if display_mag is None and domain_event is not None:
        display_mag = getattr(domain_event, "magnitude", None)
    display_mag_type = (
        str(
            _pick_from_sources(
                *sources,
                keys=("display_magnitude_type", "displayMagnitudeType"),
            )
            or "M"
        ).strip()
        or "M"
    )

    depth = _pick_from_sources(*sources, keys=("depth",))
    if depth is None and domain_event is not None:
        depth = getattr(domain_event, "depth", None)
    depth_error = _pick_from_sources(*sources, keys=("depth_error", "depthError"))
    centroid_depth = _pick_from_sources(
        *sources,
        keys=("centroid_depth", "centroidDepth"),
    )
    plane1 = _pick_from_sources(
        *sources,
        keys=("nodal_plane1", "nodalPlane1"),
    )
    plane2 = _pick_from_sources(
        *sources,
        keys=("nodal_plane2", "nodalPlane2"),
    )
    cmt_id = str(_pick_from_sources(*sources, keys=("cmt_id", "id")) or "").strip()
    fssn_event_id = str(
        _pick_from_sources(*sources, keys=("fssn_event_id", "eventId")) or ""
    ).strip()

    # 至少要有节面 / 多震级 / 矩心深度之一，才值得展开
    if not plane1 and not plane2 and not all_mags and centroid_depth is None:
        return ""

    lines: list[str] = ["CMT 矩心矩张量解"]

    mag_tokens: list[str] = []
    if display_mag is not None:
        mag_tokens.append(_format_mag_token(display_mag_type, display_mag))
    for key in ("Mww", "mB", "mb", "MLv", "Mwp", "M", "Mw(Mwp)", "Mw(mB)"):
        if key == display_mag_type:
            continue
        if key not in all_mags:
            continue
        token = _format_mag_token(key, all_mags.get(key))
        if token and token not in mag_tokens:
            mag_tokens.append(token)
    if mag_tokens:
        lines.append("震级：" + " / ".join(mag_tokens[:6]))

    depth_parts: list[str] = []
    if depth is not None and depth != "":
        try:
            depth_num = float(depth)
            if depth_num == 0:
                depth_text = "极浅"
            else:
                depth_text = f"{depth_num:g} km"
        except (TypeError, ValueError):
            depth_text = str(depth).strip()
        if depth_error is not None and depth_error != "":
            try:
                err_num = float(depth_error)
                depth_text = f"{depth_text} (±{err_num:g})"
            except (TypeError, ValueError):
                depth_text = f"{depth_text} (±{depth_error})"
        depth_parts.append(f"震源深度 {depth_text}")
    if centroid_depth is not None and centroid_depth != "":
        try:
            centroid_num = float(centroid_depth)
            depth_parts.append(f"矩心深度 {centroid_num:g} km")
        except (TypeError, ValueError):
            depth_parts.append(f"矩心深度 {centroid_depth}")
    if depth_parts:
        lines.append("深度：" + "｜".join(depth_parts))

    plane1_line = _format_plane_line("节面1", plane1)
    plane2_line = _format_plane_line("节面2", plane2)
    if plane1_line:
        lines.append(plane1_line)
    if plane2_line:
        lines.append(plane2_line)

    id_parts: list[str] = []
    if cmt_id:
        id_parts.append(f"CMT ID {cmt_id}")
    if fssn_event_id and fssn_event_id != cmt_id:
        id_parts.append(f"事件 {fssn_event_id}")
    if id_parts:
        lines.append("标识：" + " · ".join(id_parts))

    lines.append("备注：左/右旋最终确定需依赖实际发震断层面")
    return truncate_text("\n".join(lines), limit=FSSN_CMT_DETAIL_LIMIT)


def apply_earthquake_detail_record_fields(
    record: dict[str, Any],
    event: EventEnvelope,
    *,
    info_type: str,
    event_metadata: dict[str, Any],
    envelope_metadata: dict[str, Any],
) -> None:
    """为含大量补充信息的地震情报写入 weather_detail。

    注意：CENC 烈度速报由 apply_cenc_intensity_report_fields 单独处理，这里跳过。
    若记录已有 weather_detail（例如 CENC 已写入），也不覆盖。
    """
    if str(record.get("weather_detail") or "").strip():
        return

    source_id = str(
        event.source_id or record.get("source_id") or record.get("source") or ""
    ).strip()
    if is_cenc_intensity_report(source_id, info_type=info_type):
        return

    # SourcePayload.to_dict() 仅返回 raw 浅拷贝，不含 attributes。
    # 这里直接读 payload 对象字段，避免 attributes 丢失导致写不出正文。
    if isinstance(event.payload, SourcePayload):
        payload_raw = (
            dict(event.payload.raw) if isinstance(event.payload.raw, dict) else {}
        )
        payload_attributes = (
            dict(event.payload.attributes)
            if isinstance(event.payload.attributes, dict)
            else {}
        )
        # 兼容旧调用方：部分字段可能直接挂在 raw 顶层
        payload = payload_raw
    elif isinstance(event.payload, dict):
        payload = dict(event.payload)
        raw_candidate = payload.get("raw")
        payload_raw = raw_candidate if isinstance(raw_candidate, dict) else payload
        attrs_candidate = payload.get("attributes")
        payload_attributes = (
            attrs_candidate if isinstance(attrs_candidate, dict) else {}
        )
    else:
        payload = {}
        payload_raw = {}
        payload_attributes = {}

    detail = ""
    if is_fssn_cmt_report(source_id, info_type=info_type) or is_fssn_cmt_source(
        source_id, info_type=info_type
    ):
        detail = _build_fssn_cmt_detail(
            event_metadata=event_metadata,
            envelope_metadata=envelope_metadata,
            payload_attributes=payload_attributes,
            payload_raw=payload_raw,
            payload=payload,
            domain_event=getattr(event, "event", None),
        )
    else:
        # 日本地震情报：有 jma_points / 备注时写入
        detail = _build_jma_earthquake_detail(
            info_type=info_type,
            event_metadata=event_metadata,
            envelope_metadata=envelope_metadata,
            payload_attributes=payload_attributes,
            payload_raw=payload_raw,
            payload=payload,
        )

    if detail:
        record["weather_detail"] = detail


__all__ = [
    "apply_earthquake_detail_record_fields",
    "truncate_text",
]
