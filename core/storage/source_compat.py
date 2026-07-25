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

# 历史别名映射表：把旧来源名、展示名和外部兼容名统一折叠到规范 source_id
# 包含各种历史插件版本产生的 key 以及 WebSocket 连接中发送的 label
_ALIAS_MAP: dict[str, str] = {
    "fan_studio_cenc": "cenc_fanstudio",
    "fan_studio_cenc_ir": "cenc_ir_fanstudio",
    "fan_studio_cea": "cea_fanstudio",
    "fan_studio_cea_pr": "cea_pr_fanstudio",
    "fan_studio_cwa": "cwa_fanstudio",
    "fan_studio_cwa_report": "cwa_fanstudio_report",
    "fan_studio_usgs": "usgs_fanstudio",
    "fan_studio_sa": "sa_fanstudio",
    "fan_studio_jma": "jma_fanstudio",
    "fan_studio_weather": "china_weather_fanstudio",
    "fan_studio_tsunami": "china_tsunami_fanstudio",
    "p2p_eew": "jma_p2p",
    "p2p_earthquake": "jma_p2p_info",
    "p2p_tsunami": "jma_tsunami_p2p",
    "eqsc_tsunami": "jma_tsunami_eqsc",
    "eqsc_typhoon": "typhoon_eqsc",
    "fan_studio_typhoon": "typhoon_fanstudio",
    "wolfx_jma_eew": "jma_wolfx",
    "wolfx_cenc_eew": "cea_wolfx",
    "wolfx_cwa_eew": "cwa_wolfx",
    "wolfx_cenc_eq": "cenc_wolfx",
    "wolfx_jma_eq": "jma_wolfx_info",
    "china_earthquake_warning": "cea_fanstudio",
    "china_earthquake_warning_provincial": "cea_pr_fanstudio",
    "taiwan_cwa_earthquake": "cwa_fanstudio",
    "taiwan_cwa_report": "cwa_fanstudio_report",
    "china_cenc_earthquake": "cenc_fanstudio",
    "china_cenc_intensity_report": "cenc_ir_fanstudio",
    "cenc-ir": "cenc_ir_fanstudio",
    "cenc_ir": "cenc_ir_fanstudio",
    "usgs_earthquake": "usgs_fanstudio",
    "usa_shakealert": "sa_fanstudio",
    "sa": "sa_fanstudio",
    "shakealert": "sa_fanstudio",
    "china_weather_alarm": "china_weather_fanstudio",
    "china_tsunami": "china_tsunami_fanstudio",
    "japan_jma_eew": "jma_p2p",
    "japan_jma_earthquake": "jma_p2p_info",
    "japan_jma_tsunami": "jma_tsunami_p2p",
    "china_cenc_eew": "cea_wolfx",
    "taiwan_cwa_eew": "cwa_wolfx",
    "中国气象局：气象预警": "china_weather_fanstudio",
    "中国气象局: 气象预警": "china_weather_fanstudio",
    "台湾中央气象署：强震即时警报": "cwa_fanstudio",
    "台湾中央气象署: 强震即时警报": "cwa_fanstudio",
    "台湾中央气象署：地震报告": "cwa_fanstudio_report",
    "台湾中央气象署: 地震报告": "cwa_fanstudio_report",
    "中国地震台网（cenc）": "cenc_fanstudio",
    "中国地震台网(cenc)": "cenc_fanstudio",
    "中国地震台网（cenc）：地震测定": "cenc_fanstudio",
    "中国地震台网(cenc)：地震测定": "cenc_fanstudio",
    "中国地震台网（cenc）：烈度速报": "cenc_ir_fanstudio",
    "中国地震台网(cenc)：烈度速报": "cenc_ir_fanstudio",
    "中国地震台网烈度速报": "cenc_ir_fanstudio",
    "中国地震预警网（cea）": "cea_fanstudio",
    "中国地震预警网(cea)": "cea_fanstudio",
    "中国地震预警网（省级）": "cea_pr_fanstudio",
    "中国地震预警网(省级)": "cea_pr_fanstudio",
    "日本气象厅：紧急地震速报": "jma_fanstudio",
    "日本气象厅: 紧急地震速报": "jma_fanstudio",
    "日本气象厅：地震情报": "jma_p2p_info",
    "日本气象厅: 地震情报": "jma_p2p_info",
    # 中文冒号全角/半角 + 预报/予报 历史写法都兼容
    "日本气象厅：海啸预报": "jma_tsunami_p2p",
    "日本气象厅: 海啸预报": "jma_tsunami_p2p",
    "日本气象厅：海啸予报": "jma_tsunami_p2p",
    "日本气象厅: 海啸予报": "jma_tsunami_p2p",
    "日本气象厅：海啸予报 - P2P": "jma_tsunami_p2p",
    "日本气象厅: 海啸予报 - P2P": "jma_tsunami_p2p",
    "日本气象厅：海啸予报 - EQSC": "jma_tsunami_eqsc",
    "日本气象厅: 海啸予报 - EQSC": "jma_tsunami_eqsc",
    "日本气象厅：海啸预报 - EQSC": "jma_tsunami_eqsc",
    "日本气象厅: 海啸预报 - EQSC": "jma_tsunami_eqsc",
}

# 展示名称映射表：用于把内部规范 key 转回更友好的前端展示标签。
_DISPLAY_MAP: dict[str, str] = {
    "cenc_fanstudio": "中国地震台网 (CENC) - Fan",
    "cenc_ir_fanstudio": "中国地震台网 (CENC) - 烈度速报",
    "cea_fanstudio": "中国地震预警网 (CEA)",
    "cea_pr_fanstudio": "中国地震预警网 (省级)",
    "cwa_fanstudio": "台湾中央气象署: 强震即时警报 - Fan",
    "cwa_fanstudio_report": "台湾中央气象署: 地震报告",
    "usgs_fanstudio": "美国地质调查局 (USGS)",
    "sa_fanstudio": "美国 ShakeAlert 地震预警",
    "jma_fanstudio": "日本气象厅: 紧急地震速报 - Fan",
    "china_weather_fanstudio": "中国气象局: 气象预警",
    "china_tsunami_fanstudio": "自然资源部海啸预警中心",
    # 贡献榜默认中性名：实时通道不强制带后缀
    "typhoon_fanstudio": "中国气象局：实时活跃台风",
    "typhoon_eqsc": "中国气象局：实时活跃台风 - EQSC",
    # 仅 EQSC 历史重建在贡献统计中单独成源
    "typhoon_eqsc_rebuild": "中国气象局：台风历史 - EQSC",
    "jma_p2p": "日本气象厅: 紧急地震速报 - P2P",
    "jma_p2p_info": "日本气象厅: 地震情报 - P2P",
    "jma_tsunami_p2p": "日本气象厅: 海啸予报 - P2P",
    "jma_tsunami_eqsc": "日本气象厅: 海啸予报 - EQSC",
    "jma_wolfx": "日本气象厅: 紧急地震速报 - Wolfx",
    "cea_wolfx": "中国地震预警网 (CEA) - Wolfx",
    "cwa_wolfx": "台湾中央气象署: 强震即时警报 - Wolfx",
    "cenc_wolfx": "中国地震台网地震测定 - Wolfx",
    "jma_wolfx_info": "日本气象厅地震情报 - Wolfx",
    "global_quake": "Global Quake",
    "sc_eew": "四川地震局",
    "fj_eew": "福建地震局",
    "kma_earthquake": "韩国气象厅 (KMA)",
    "emsc_earthquake": "欧洲地中海地震中心 (EMSC)",
    "gfz_earthquake": "德国地学研究中心 (GFZ)",
    "enabled": "实时数据流",
    "unknown": "未知来源",
}


def normalize_source_name(source: str) -> str:
    """把任意来源名归一化为稳定的内部 key。"""
    raw_source = str(source or "").strip()
    if not raw_source:
        # 空来源统一折叠为 unknown，避免后续展示与筛选阶段出现空字符串分支。
        return "unknown"
    lower_source = raw_source.lower()
    # 先按原值匹配，再按小写匹配历史别名；若都未命中，则回退为小写标准形态。
    return _ALIAS_MAP.get(raw_source) or _ALIAS_MAP.get(lower_source) or lower_source


def format_source_name(source: str) -> str:
    """把来源标识格式化为更适合展示的中文标签。"""
    normalized = normalize_source_name(source)
    # 如果映射字典里找不到对应的漂亮展示名，则使用归一化后的去重字符串作为兜底
    return _DISPLAY_MAP.get(normalized) or str(source or "").strip() or "未知来源"


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
    if normalized == "cenc_ir_fanstudio":
        return True
    info_text = str(info_type or "").strip()
    return "烈度速报" in info_text


def cenc_intensity_report_source_keys() -> tuple[str, ...]:
    """返回可识别为 CENC 烈度速报的 source/source_id 键集合。

    包含规范 key 与历史别名，供 SQL 侧过滤与 Python 判定保持一致。
    统一折叠为 strip + lower 形态，避免大小写/空白导致 SQL 与 Python 分叉。
    """
    keys: set[str] = {"cenc_ir_fanstudio"}
    for alias, target in _ALIAS_MAP.items():
        if target == "cenc_ir_fanstudio":
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

    for alias, canonical in _ALIAS_MAP.items():
        # 第二轮反向补全所有历史别名，尽量覆盖旧数据库中的遗留写法。
        if canonical in canonical_keys:
            expanded.add(alias)
            expanded.add(alias.lower())

    for canonical in canonical_keys:
        # 最后补回规范标识本身及其展示名，避免结果集合缺项。
        expanded.add(canonical)
        expanded.add(format_source_name(canonical))

    return sorted(item for item in expanded if item)
