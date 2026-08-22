"""地震领域辅助能力。"""

from .cmt_normalize import (
    CMT_INFO_TYPE,
    FSSN_CMT_SOURCE_ID,
    classify_fault_mechanism,
    format_fault_type_label,
    is_fssn_cmt_source,
    looks_like_fssn_cmt_payload,
    normalize_all_magnitudes,
    parse_depth_with_error,
    parse_moment_tensor_component,
    parse_nodal_plane,
    pick_display_magnitude,
    pick_stats_magnitude,
    resolve_fssn_cmt_event_ids,
)

__all__ = [
    "CMT_INFO_TYPE",
    "FSSN_CMT_SOURCE_ID",
    "classify_fault_mechanism",
    "format_fault_type_label",
    "is_fssn_cmt_source",
    "looks_like_fssn_cmt_payload",
    "normalize_all_magnitudes",
    "parse_depth_with_error",
    "parse_moment_tensor_component",
    "parse_nodal_plane",
    "pick_display_magnitude",
    "pick_stats_magnitude",
    "resolve_fssn_cmt_event_ids",
]
