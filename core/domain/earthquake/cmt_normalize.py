"""FSSN CMT 规范化与断层机制辅助。

职责：
- 解析 depth / 节面 / 矩张量字符串
- 从 rake 推导断层类型与倾滑/走滑占比
- 统一 CMT 补充产品识别与震级选取口径

不依赖网络、存储或消息层，供解析器、展示器、统计共同复用。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from typing import Any

from ....utils.converters import safe_float_convert

FSSN_CMT_SOURCE_ID = "fssn_cmt_fanstudio"
CMT_INFO_TYPE = "CMT"

# 展示用主震级优先序（文案括号内补充项）
_DISPLAY_MAG_PRIORITY: tuple[str, ...] = (
    "Mww",
    "Mw(Mwp)",
    "Mw(mB)",
    "Mwp",
    "mB",
    "mb",
    "MLv",
    "M",
)

# 近纯倾滑/走滑折叠阈值（对齐常见 CMT 文案风格，偏宽松）
_PURE_DIP_SLIP_MIN = 0.90
_PURE_STRIKE_SLIP_MIN = 0.90
_PURE_SECONDARY_MAX = 0.30

_DEPTH_ERROR_RE = re.compile(
    r"""
    ^\s*
    (?P<depth>[-+]?\d+(?:\.\d+)?)
    (?:
        \s*
        (?:
            \(\s*(?:\+/?\-|\±)\s*(?P<error1>\d+(?:\.\d+)?)\s*\)
            |
            (?:\+/?\-|\±)\s*(?P<error2>\d+(?:\.\d+)?)
        )
    )?
    \s*$
    """,
    re.VERBOSE,
)

_NODAL_PLANE_RE = re.compile(
    r"""
    ^\s*
    (?P<strike>[-+]?\d+(?:\.\d+)?)
    \s*/\s*
    (?P<dip>[-+]?\d+(?:\.\d+)?)
    \s*/\s*
    (?P<rake>[-+]?\d+(?:\.\d+)?)
    \s*$
    """,
    re.VERBOSE,
)

_MOMENT_KEYS: tuple[str, ...] = ("mnn", "mee", "mdd", "mne", "mnd", "med")


def is_fssn_cmt_source(
    source: str | None = None,
    *,
    info_type: str | None = None,
) -> bool:
    """判断是否为 FSSN CMT 补充产品。

    CMT 与 CENC 烈度速报类似：保留 by_source / 事件列表，
    但不计入 total_events、by_type、震级分布与时间序列。
    """
    text = str(source or "").strip().lower()
    if text in {
        FSSN_CMT_SOURCE_ID,
        "fan_studio_fssn_cmt",
        "fssn-cmt",
        "fssn_cmt",
    }:
        return True
    info_text = str(info_type or "").strip().upper()
    return info_text == CMT_INFO_TYPE or "矩心矩张量" in str(info_type or "")


def looks_like_fssn_cmt_payload(
    payload: Mapping[str, Any] | None,
    *,
    get_value: Callable[[str], Any] | None = None,
) -> bool:
    """载荷是否呈现 FSSN CMT 特征。"""
    if not isinstance(payload, dict):
        return False

    def _get(key: str) -> Any:
        if get_value is not None:
            return get_value(key)
        return payload.get(key)

    event_id = str(_get("eventId") or "").strip()
    has_fssn_event = event_id.upper().startswith("FSSN")
    has_plane = _get("nodalPlane1") is not None or _get("nodalPlane2") is not None
    has_tensor = any(_get(key) is not None for key in _MOMENT_KEYS)
    has_mags = isinstance(_get("allMagnitudes"), dict)
    has_centroid = _get("centroidDepth") is not None
    if has_fssn_event and (has_plane or has_tensor or has_mags):
        return True
    if has_plane and has_tensor:
        return True
    if has_mags and has_centroid and has_plane:
        return True
    return False


def parse_depth_with_error(raw: Any) -> tuple[float | None, float | None]:
    """解析 depth 字符串，例如 612(+/- 8) → (612.0, 8.0)。"""
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None
    text = str(raw).strip()
    if not text:
        return None, None
    match = _DEPTH_ERROR_RE.match(text)
    if not match:
        # 兜底：提取首个数字
        number = safe_float_convert(re.search(r"[-+]?\d+(?:\.\d+)?", text))
        return (float(number) if number is not None else None), None
    depth = safe_float_convert(match.group("depth"))
    error = safe_float_convert(match.group("error1") or match.group("error2"))
    return (
        float(depth) if depth is not None else None,
        float(error) if error is not None else None,
    )


def parse_nodal_plane(raw: Any) -> dict[str, Any] | None:
    """解析 strike/dip/rake 节面字符串。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        strike = safe_float_convert(raw.get("strike"))
        dip = safe_float_convert(raw.get("dip"))
        rake = safe_float_convert(raw.get("rake"))
        if strike is None or dip is None or rake is None:
            return None
        plane = {
            "strike": float(strike),
            "dip": float(dip),
            "rake": float(rake),
            "raw": str(raw.get("raw") or "").strip(),
        }
        mechanism = classify_fault_mechanism(plane["rake"])
        plane.update(mechanism)
        return plane

    text = str(raw).strip()
    if not text:
        return None
    match = _NODAL_PLANE_RE.match(text)
    if not match:
        return None
    strike = safe_float_convert(match.group("strike"))
    dip = safe_float_convert(match.group("dip"))
    rake = safe_float_convert(match.group("rake"))
    if strike is None or dip is None or rake is None:
        return None
    plane = {
        "strike": float(strike),
        "dip": float(dip),
        "rake": float(rake),
        "raw": text,
    }
    mechanism = classify_fault_mechanism(plane["rake"])
    plane.update(mechanism)
    return plane


def parse_moment_tensor_component(raw: Any) -> float | None:
    """解析科学计数法矩张量分量字符串。"""
    value = safe_float_convert(raw)
    if value is None:
        return None
    return float(value)


def normalize_moment_tensor(raw_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """提取矩张量数值与原始字符串。"""
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    values: dict[str, float] = {}
    raw_values: dict[str, str] = {}
    for key in _MOMENT_KEYS:
        raw = payload.get(key)
        if raw is None:
            continue
        raw_text = str(raw).strip()
        if raw_text:
            raw_values[key] = raw_text
        parsed = parse_moment_tensor_component(raw)
        if parsed is not None:
            values[key] = parsed
    return {"values": values, "raw": raw_values}


def normalize_all_magnitudes(raw: Any) -> dict[str, float]:
    """规范化 allMagnitudes 字典。"""
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        mag = safe_float_convert(value)
        if mag is None:
            continue
        result[name] = round(float(mag), 1)
    return result


def pick_stats_magnitude(all_magnitudes: Mapping[str, float] | None) -> float | None:
    """统计/最大地震仅使用 allMagnitudes.M。"""
    if not isinstance(all_magnitudes, Mapping):
        return None
    value = safe_float_convert(all_magnitudes.get("M"))
    if value is None:
        return None
    return round(float(value), 1)


def pick_display_magnitude(
    all_magnitudes: Mapping[str, float] | None,
    *,
    fallback: float | None = None,
) -> tuple[float | None, str]:
    """选取展示用主震级与类型标签。

    文案主显示优先 M；若缺失再按优先序回退。
    """
    mags = normalize_all_magnitudes(all_magnitudes)
    if "M" in mags:
        return mags["M"], "M"
    for key in _DISPLAY_MAG_PRIORITY:
        if key in mags:
            return mags[key], key
    if mags:
        first_key = next(iter(mags))
        return mags[first_key], first_key
    if fallback is not None:
        return round(float(fallback), 1), "M"
    return None, "M"


def classify_fault_mechanism(rake: float | None) -> dict[str, Any]:
    """根据 rake 计算断层类型与倾滑/走滑占比。

    占比定义为投影强度：
    - dip_slip_pct = |sin(rake)| * 100
    - strike_slip_pct = |cos(rake)| * 100
    二者之和通常大于 100，不是归一化成分比。
    """
    if rake is None:
        return {
            "kind": "unknown",
            "label": "未知",
            "is_oblique": False,
            "dip_slip_pct": None,
            "strike_slip_pct": None,
            "dip_slip_name": "",
            "strike_slip_name": "",
        }

    rake_value = float(rake)
    # 归一到 (-180, 180]
    while rake_value <= -180.0:
        rake_value += 360.0
    while rake_value > 180.0:
        rake_value -= 360.0

    rake_rad = math.radians(rake_value)
    sin_r = math.sin(rake_rad)
    cos_r = math.cos(rake_rad)
    dip_ratio = abs(sin_r)
    strike_ratio = abs(cos_r)
    dip_pct = round(dip_ratio * 100.0, 1)
    strike_pct = round(strike_ratio * 100.0, 1)

    dip_name = "逆冲" if sin_r >= 0 else "正断"
    # Aki-Richards：cos>0 左旋，cos<0 右旋
    strike_name = "左旋走滑" if cos_r >= 0 else "右旋走滑"

    pure_dip = dip_ratio >= _PURE_DIP_SLIP_MIN and strike_ratio <= _PURE_SECONDARY_MAX
    pure_strike = (
        strike_ratio >= _PURE_STRIKE_SLIP_MIN and dip_ratio <= _PURE_SECONDARY_MAX
    )

    if pure_dip:
        kind = "reverse" if sin_r >= 0 else "normal"
        label = "逆断层" if sin_r >= 0 else "正断层"
        return {
            "kind": kind,
            "label": label,
            "is_oblique": False,
            "dip_slip_pct": dip_pct,
            "strike_slip_pct": strike_pct,
            "dip_slip_name": dip_name,
            "strike_slip_name": strike_name,
        }

    if pure_strike:
        kind = "strike_slip_left" if cos_r >= 0 else "strike_slip_right"
        label = "左旋走滑断层" if cos_r >= 0 else "右旋走滑断层"
        return {
            "kind": kind,
            "label": label,
            "is_oblique": False,
            "dip_slip_pct": dip_pct,
            "strike_slip_pct": strike_pct,
            "dip_slip_name": dip_name,
            "strike_slip_name": strike_name,
        }

    return {
        "kind": "oblique",
        "label": "斜滑断层",
        "is_oblique": True,
        "dip_slip_pct": dip_pct,
        "strike_slip_pct": strike_pct,
        "dip_slip_name": dip_name,
        "strike_slip_name": strike_name,
    }


def format_fault_type_label(mechanism: Mapping[str, Any] | None) -> str:
    """格式化断层类型展示文案（含仅供参考）。"""
    if not isinstance(mechanism, Mapping):
        return "未知[仅供参考]"

    if mechanism.get("is_oblique"):
        dip_name = str(mechanism.get("dip_slip_name") or "").strip() or "倾滑"
        strike_name = str(mechanism.get("strike_slip_name") or "").strip() or "走滑"
        dip_pct = mechanism.get("dip_slip_pct")
        strike_pct = mechanism.get("strike_slip_pct")
        dip_text = (
            f"{dip_name}[{dip_pct:.1f}%]"
            if isinstance(dip_pct, (int, float))
            else dip_name
        )
        strike_text = (
            f"{strike_name}[{strike_pct:.1f}%]"
            if isinstance(strike_pct, (int, float))
            else strike_name
        )
        return f"斜滑断层（{dip_text} + {strike_text}）[仅供参考]"

    label = str(mechanism.get("label") or "未知").strip() or "未知"
    return f"{label}[仅供参考]"


def resolve_fssn_cmt_event_ids(
    raw_payload: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """返回 (cmt_id, fssn_event_id)。"""
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    cmt_id = str(payload.get("id") or "").strip()
    fssn_event_id = str(payload.get("eventId") or "").strip()
    return cmt_id, fssn_event_id


def build_cmt_metadata(
    *,
    raw_payload: Mapping[str, Any],
    source_enum: str = "",
    source_type: str = "earthquake_info",
    source_family: str = "fan_studio",
) -> dict[str, Any]:
    """从原始 CMT 载荷构建标准化 metadata。"""
    cmt_id, fssn_event_id = resolve_fssn_cmt_event_ids(raw_payload)
    all_magnitudes = normalize_all_magnitudes(raw_payload.get("allMagnitudes"))
    stats_magnitude = pick_stats_magnitude(all_magnitudes)
    display_magnitude, display_mag_type = pick_display_magnitude(
        all_magnitudes,
        fallback=stats_magnitude,
    )

    depth, depth_error = parse_depth_with_error(raw_payload.get("depth"))
    centroid_depth = safe_float_convert(raw_payload.get("centroidDepth"))
    if centroid_depth is not None:
        centroid_depth = float(centroid_depth)

    plane1 = parse_nodal_plane(raw_payload.get("nodalPlane1"))
    plane2 = parse_nodal_plane(raw_payload.get("nodalPlane2"))
    tensor = normalize_moment_tensor(raw_payload)

    beachball_ready = bool(
        (plane1 and plane2)
        or len(tensor.get("values") or {}) >= 6
        or (plane1 and len(tensor.get("values") or {}) >= 4)
    )

    return {
        "source_family": source_family,
        "source_enum": source_enum,
        "source_type": source_type,
        "info_type": CMT_INFO_TYPE,
        "cmt_id": cmt_id,
        "fssn_event_id": fssn_event_id,
        "all_magnitudes": all_magnitudes,
        "stats_magnitude": stats_magnitude,
        "display_magnitude": display_magnitude,
        "display_magnitude_type": display_mag_type,
        "primary_magnitude_type": "M",
        "depth": depth,
        "depth_error": depth_error,
        "centroid_depth": centroid_depth,
        "nodal_plane1": plane1,
        "nodal_plane2": plane2,
        "moment_tensor": tensor.get("values") or {},
        "moment_tensor_raw": tensor.get("raw") or {},
        "beachball_ready": beachball_ready,
        "is_supplement_product": True,
    }


__all__ = [
    "CMT_INFO_TYPE",
    "FSSN_CMT_SOURCE_ID",
    "build_cmt_metadata",
    "classify_fault_mechanism",
    "format_fault_type_label",
    "is_fssn_cmt_source",
    "looks_like_fssn_cmt_payload",
    "normalize_all_magnitudes",
    "normalize_moment_tensor",
    "parse_depth_with_error",
    "parse_moment_tensor_component",
    "parse_nodal_plane",
    "pick_display_magnitude",
    "pick_stats_magnitude",
    "resolve_fssn_cmt_event_ids",
]
