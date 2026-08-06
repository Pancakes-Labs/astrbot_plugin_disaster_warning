"""
数据源历史兼容工具。

职责：
- 统一 source/source_id 的历史别名到规范 key
- 生成前端展示标签
- 为数据库筛选展开同义别名集合

这是一个临时兼容层，用于避免将大量历史兼容逻辑堆进 core/storage/database_manager.py 本体。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.typhoon.typhoon_modes import resolve_data_mode
from ..sources.display_registry import SOURCE_ALIAS_MAP, SOURCE_DISPLAY_MAP

# 历史别名映射表与展示名称映射表已统一收编至
# core/sources/display_registry.py（SOURCE_ALIAS_MAP / SOURCE_DISPLAY_MAP），
# 本兼容层直接引用事实层常量，后续修改请前往 display_registry.py。


def normalize_source_name(source: str) -> str:
    """把任意来源名归一化为稳定的内部 key。"""
    raw_source = str(source or "").strip()
    if not raw_source:
        # 空来源统一折叠为 unknown，避免后续展示与筛选阶段出现空字符串分支。
        return "unknown"
    lower_source = raw_source.lower()
    # 先按原值匹配，再按小写匹配历史别名；若都未命中，则回退为小写标准形态。
    return (
        SOURCE_ALIAS_MAP.get(raw_source)
        or SOURCE_ALIAS_MAP.get(lower_source)
        or lower_source
    )


def format_source_name(source: str) -> str:
    """把来源标识格式化为更适合展示的中文标签。"""
    normalized = normalize_source_name(source)
    # 如果映射字典里找不到对应的漂亮展示名，则使用归一化后的去重字符串作为兜底
    return SOURCE_DISPLAY_MAP.get(normalized) or str(source or "").strip() or "未知来源"


def is_cenc_intensity_report(
    source: str | None = None,
    *,
    info_type: str | None = None,
) -> bool:
    """判断是否为中国地震台网烈度速报。

    烈度速报是同一物理地震的补充产品，不应计入全局地震事件数、
    震级分布与时间序列；但仍保留来源贡献统计与事件列表落库。
    """
    normalized = normalize_source_name(source or "")
    if normalized in {"cenc_ir_fanstudio", "cenc_ir_eqsc"}:
        return True
    info_text = str(info_type or "").strip()
    return "烈度速报" in info_text


def is_fssn_cmt_report(
    source: str | None = None,
    *,
    info_type: str | None = None,
) -> bool:
    """判断是否为 FSSN CMT 补充产品。

    与烈度速报同口径：保留 by_source / 事件列表，不计入 total_events、by_type、震级分布与时间序列。
    """
    normalized = normalize_source_name(source or "")
    if normalized == "fssn_cmt_fanstudio":
        return True
    info_text = str(info_type or "").strip().upper()
    return info_text == "CMT" or "矩心矩张量" in str(info_type or "")


def is_earthquake_supplement_product(
    source: str | None = None,
    *,
    info_type: str | None = None,
) -> bool:
    """判断是否为地震补充产品（烈度速报 / CMT 等）。"""
    return is_cenc_intensity_report(source, info_type=info_type) or is_fssn_cmt_report(
        source, info_type=info_type
    )


def fssn_cmt_report_source_keys() -> tuple[str, ...]:
    """返回可识别为 FSSN CMT 的 source/source_id 键集合。"""
    keys: set[str] = {"fssn_cmt_fanstudio"}
    for alias, target in SOURCE_ALIAS_MAP.items():
        if target == "fssn_cmt_fanstudio":
            keys.add(str(alias).strip().lower())
    return tuple(sorted(keys))


def build_earthquake_supplement_sql_predicate(
    *,
    source_expr: str = "source",
    source_id_expr: str = "source_id",
    info_type_expr: str = "info_type",
) -> str:
    """构建 SQLite 侧“是否为地震补充产品”布尔表达式。

    覆盖 CENC 烈度速报与 FSSN CMT，供 total_events 去重统计复用。
    """
    intensity_keys = ", ".join(
        "'" + key.replace("'", "''") + "'"
        for key in cenc_intensity_report_source_keys()
    )
    cmt_keys = ", ".join(
        "'" + key.replace("'", "''") + "'" for key in fssn_cmt_report_source_keys()
    )
    source_key_expr = (
        "LOWER(TRIM(COALESCE("
        f"NULLIF(TRIM({source_id_expr}), ''), "
        f"NULLIF(TRIM({source_expr}), ''), "
        "'')))"
    )
    return (
        f"({source_key_expr} IN ({intensity_keys}) "
        f"OR {source_key_expr} IN ({cmt_keys}) "
        f"OR INSTR(COALESCE({info_type_expr}, ''), '烈度速报') > 0 "
        f"OR UPPER(TRIM(COALESCE({info_type_expr}, ''))) = 'CMT' "
        f"OR INSTR(COALESCE({info_type_expr}, ''), '矩心矩张量') > 0)"
    )


def cenc_intensity_report_source_keys() -> tuple[str, ...]:
    """返回可识别为 CENC 烈度速报的 source/source_id 键集合。

    包含规范 key 与历史别名，供 SQL 侧过滤与 Python 判定保持一致。
    统一折叠为 strip + lower 形态，避免大小写/空白导致 SQL 与 Python 分叉。
    """
    keys: set[str] = {"cenc_ir_fanstudio", "cenc_ir_eqsc"}
    for alias, target in SOURCE_ALIAS_MAP.items():
        if target in {"cenc_ir_fanstudio", "cenc_ir_eqsc"}:
            keys.add(str(alias).strip().lower())
    return tuple(sorted(keys))


def build_cenc_intensity_report_sql_predicate(
    *,
    source_expr: str = "source",
    source_id_expr: str = "source_id",
    info_type_expr: str = "info_type",
) -> str:
    """构建 SQLite 侧“是否为 CENC 烈度速报”布尔表达式。

    仅拼接内部列名与静态别名字面量，不接受外部用户输入。
    对 source/source_id 使用 LOWER(TRIM(...))，与 normalize_source_name 对齐。
    """
    quoted_keys = ", ".join(
        "'" + key.replace("'", "''") + "'"
        for key in cenc_intensity_report_source_keys()
    )
    # 与 Python 侧 normalize_source_name 一致：优先 source_id，其次 source，并做 trim/lower。
    source_key_expr = (
        "LOWER(TRIM(COALESCE("
        f"NULLIF(TRIM({source_id_expr}), ''), "
        f"NULLIF(TRIM({source_expr}), ''), "
        "'')))"
    )
    return (
        f"({source_key_expr} IN ({quoted_keys}) "
        f"OR INSTR(COALESCE({info_type_expr}, ''), '烈度速报') > 0)"
    )


def build_source_stats_key(
    source: str | None = None,
    *,
    event_type: str | None = None,
    info_type: str | None = None,
) -> str:
    """构建数据源贡献统计键。

    策略：
    - 普通源：规范 source_id
    - 台风 FAN 实时 / Fan+EQSC 富化：typhoon_fanstudio
    - 台风 EQSC 实时轮询：typhoon_eqsc
    - 台风 EQSC 历史重建：typhoon_eqsc_rebuild
    """
    normalized = normalize_source_name(source or "")
    type_key = str(event_type or "").strip().lower()
    is_typhoon = normalized in {
        "typhoon_fanstudio",
        "typhoon_eqsc",
        "typhoon_eqsc_rebuild",
    } or (type_key == "typhoon")
    if not is_typhoon:
        return normalized or "unknown"

    mode = resolve_data_mode(info_type, default="")
    if mode == "eqsc_rebuild" or normalized == "typhoon_eqsc_rebuild":
        return "typhoon_eqsc_rebuild"
    if mode == "eqsc" or normalized == "typhoon_eqsc":
        return "typhoon_eqsc"
    return "typhoon_fanstudio"


def format_typhoon_source_name(
    source: str | None = None,
    *,
    info_type: str | None = None,
) -> str:
    """台风来源展示名：事件详情按数据形态追加后缀。

    注意：贡献榜仅用 format_source_name；
    本函数用于事件列表/详情，可显示 Fan / Fan+EQSC / EQSC 实时 / EQSC 历史。
    """
    normalized = normalize_source_name(source or "typhoon_fanstudio")
    mode = resolve_data_mode(info_type, default="")
    if mode == "enriched":
        return "中国气象局：实时活跃台风 - Fan+EQSC"
    # 与 build_source_stats_key 一致：先判历史重建，再判实时 EQSC。
    if mode == "eqsc_rebuild" or normalized == "typhoon_eqsc_rebuild":
        return "中国气象局：台风历史 - EQSC"
    if mode == "eqsc" or normalized == "typhoon_eqsc":
        return "中国气象局：实时活跃台风 - EQSC"
    # fan 或缺省：事件详情仍标明 Fan 触发
    return "中国气象局：实时活跃台风 - Fan"


def format_event_source_name(
    source: str | None = None,
    *,
    event_type: str | None = None,
    info_type: str | None = None,
) -> str:
    """事件级来源展示名；台风会结合 info_type 细分数据形态。"""
    normalized = normalize_source_name(source or "")
    type_key = str(event_type or "").strip().lower()
    if (
        normalized in {"typhoon_fanstudio", "typhoon_eqsc", "typhoon_eqsc_rebuild"}
        or type_key == "typhoon"
    ):
        return format_typhoon_source_name(source, info_type=info_type)
    return format_source_name(source or "")


def expand_source_aliases(sources: Iterable[str]) -> list[str]:
    """展开一组来源名对应的全部别名与展示名。

    这样数据库查询时可以同时兼容旧字段值、规范 key 与展示标签，
    降低历史数据格式不统一带来的筛选遗漏。
    """
    # canonical_keys 保存规范来源标识，expanded 保存可用于查询兼容的全部候选值。
    canonical_keys: set[str] = set()
    expanded: set[str] = set()

    for source in sources:
        # 第一轮先把原始输入、规范标识和展示名称都纳入候选集合。
        raw = str(source or "").strip()
        if not raw:
            continue
        canonical = normalize_source_name(raw)
        canonical_keys.add(canonical)
        expanded.add(raw)
        expanded.add(canonical)
        expanded.add(format_source_name(raw))

    for alias, canonical in SOURCE_ALIAS_MAP.items():
        # 第二轮反向补全所有历史别名，尽量覆盖旧数据库中的遗留写法。
        if canonical in canonical_keys:
            expanded.add(alias)
            expanded.add(alias.lower())

    for canonical in canonical_keys:
        # 最后补回规范标识本身及其展示名，避免结果集合缺项。
        expanded.add(canonical)
        expanded.add(format_source_name(canonical))

    return sorted(item for item in expanded if item)
