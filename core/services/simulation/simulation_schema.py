"""
模拟参数 Schema 推导器。

核心思想：以 SOURCE_CATALOG 为唯一事实源，按灾种大类给出通用字段，
再按 source_id 的特征（intensity_mode / parser_name / report_policy 等）
补充源特有字段。前端据此动态渲染参数表单，后端据此校验入参。

字段分组（每个字段带 group 标注，驱动前端两列布局）：
- base:          灾种通用基础参数（左列核心，如地震的经纬度/震级/深度）
- time:          时间参数（发震时间 / 事件时间回退 / 更新时间回退）
- source:        数据源特有参数（右列）
- orchestration: 事件编排参数（报数 / 事件键 / 最终报）

输出结构：
{
    "disaster_types": {
        "earthquake": {
            "label": "地震", "icon": "🌍",
            "sources": [
                {
                    "source_id": "cea_fanstudio",
                    "label": "中国地震预警网 (CEA)",
                    "family_label": "FAN Studio",
                    "supports_report_semantics": true,
                    "fields": [ ...全部字段（合并顺序：base→time→source→orchestration）... ],
                    "base_fields": [...],      // 分组视图，前端优先使用
                    "time_fields": [...],
                    "source_fields": [...],
                    "orchestration_fields": [...]
                }
            ]
        }
    },
    "target_sessions": [...],
    "timestamp": "..."
}
"""

from __future__ import annotations

from typing import Any

from ...message.presenters.weather_alarm_code_map import (
    suggest_compact_weather_code,
)
from ...sources.source_catalog import (
    SOURCE_CATALOG,
    get_source_entry,
    get_source_ids_by_type,
)
from ...sources.source_entry import ProviderFamily, SourceType
from .flow_models import (
    DISASTER_TYPE_EARTHQUAKE,
    DISASTER_TYPE_META,
    DISASTER_TYPE_TSUNAMI,
    DISASTER_TYPE_TYPHOON,
    DISASTER_TYPE_WEATHER,
)

# SourceType 枚举 -> 灾种字符串键
_SOURCE_TYPE_TO_DISASTER_TYPE: dict[SourceType, str] = {
    SourceType.EARTHQUAKE_WARNING: DISASTER_TYPE_EARTHQUAKE,
    SourceType.EARTHQUAKE_INFO: DISASTER_TYPE_EARTHQUAKE,
    SourceType.TSUNAMI: DISASTER_TYPE_TSUNAMI,
    SourceType.WEATHER: DISASTER_TYPE_WEATHER,
    SourceType.TYPHOON: DISASTER_TYPE_TYPHOON,
}

# 提供方家族 -> 中文后缀（前端下拉框用于区分同名键）
_FAMILY_LABELS: dict[ProviderFamily, str] = {
    ProviderFamily.FAN_STUDIO: "FAN Studio",
    ProviderFamily.P2P: "P2P",
    ProviderFamily.WOLFX: "Wolfx",
    ProviderFamily.GLOBAL_QUAKE: "OpenQuakeAPI",
    ProviderFamily.EQSC: "EQSC",
    ProviderFamily.DIRECT_HTTP: "直连 HTTP",
}

# 提供方家族 -> 组内排序权重（数值越大越靠后）。
_FAMILY_SORT_RANK: dict[ProviderFamily, int] = {
    ProviderFamily.FAN_STUDIO: 0,
    ProviderFamily.P2P: 1,
    ProviderFamily.WOLFX: 2,
    ProviderFamily.GLOBAL_QUAKE: 3,
    ProviderFamily.EQSC: 4,
    ProviderFamily.DIRECT_HTTP: 5,
}

# 具备真实"报数/报次"语义的报次策略。
# 报数语义 = 该数据源在真实链路中会连续发布多报更新（如 EEW 第1报/第2报…最终报）。
# report_policy == "none" 的源（地震测定报告/海啸/气象/台风/S-Net 等）无报数概念，
# 前端据此自动隐藏"第几报"输入框并固定为 1。
_REPORT_SEMANTIC_POLICIES = {"jma", "cea_cwa", "global_quake"}

# 数据源地区分组：用于前端下拉框分组排序（中国 → 台湾 → 日本 → 全球）。
# 按 source_id 特征推导，避免依赖 institution_key 覆盖不全的问题。
_REGION_ORDER = ("china", "taiwan", "japan", "global")

_REGION_LABELS: dict[str, str] = {
    "china": "中国",
    "taiwan": "台湾",
    "japan": "日本",
    "global": "全球 / 美国",
}


def _resolve_source_region(source_id: str) -> str:
    """按数据源标识推导地区分组。

    优先级：台湾 → 日本 → 中国 → 全球（部分源如 S-Net 属日本、FSSN/USGS/ShakeAlert 属全球）。
    """
    if source_id.startswith("cwa_"):
        return "taiwan"
    if any(token in source_id for token in ("jma_", "snet_")) or source_id in (
        "jma_p2p",
        "jma_p2p_info",
    ):
        return "japan"
    if any(
        token in source_id
        for token in ("cea_", "cenc_", "china_", "typhoon_", "weather_")
    ):
        return "china"
    if source_id in (
        "global_quake",
        "usgs_fanstudio",
        "fssn_cmt_fanstudio",
        "sa_fanstudio",
    ):
        return "global"
    return "global"


def _num(
    key: str,
    label: str,
    *,
    default: float,
    min_value: float | None = None,
    max_value: float | None = None,
    step: float | None = None,
    required: bool = True,
    group: str = "base",
) -> dict[str, Any]:
    """构造数字类型字段定义。"""
    field_def: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": "number",
        "default": default,
        "required": required,
        "group": group,
    }
    if min_value is not None:
        field_def["min"] = min_value
    if max_value is not None:
        field_def["max"] = max_value
    if step is not None:
        field_def["step"] = step
    return field_def


def _text(
    key: str,
    label: str,
    *,
    default: str = "",
    required: bool = True,
    placeholder: str = "",
    group: str = "base",
    width: str | None = None,
) -> dict[str, Any]:
    """构造文本类型字段定义。

    width 元数据供前端布局使用（如 "half" 让短字段在同一网格行内并排）。
    """
    field_def: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": "text",
        "default": default,
        "required": required,
        "group": group,
    }
    if placeholder:
        field_def["placeholder"] = placeholder
    if width:
        field_def["width"] = width
    return field_def


def _bool_field(
    key: str, label: str, *, default: bool = False, group: str = "base"
) -> dict[str, Any]:
    """构造布尔类型字段定义。"""
    return {
        "key": key,
        "label": label,
        "type": "bool",
        "default": default,
        "group": group,
    }


def _select_field(
    key: str,
    label: str,
    options: list[tuple[str, str]],
    *,
    default: str = "",
    required: bool = True,
    group: str = "base",
) -> dict[str, Any]:
    """构造下拉枚举字段定义（前端渲染 Select 控件）。"""
    return {
        "key": key,
        "label": label,
        "type": "select",
        "default": default,
        "required": required,
        "group": group,
        "options": [{"value": value, "label": text} for value, text in options],
    }


def _json_field(
    key: str,
    label: str,
    *,
    default: str = "",
    required: bool = False,
    placeholder: str = "",
    rows: int = 3,
    json_table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 JSON 编辑字段定义（前端渲染 JSON 文本域 + 校验）。

    json_table 提供表格化编辑元数据（columns 描述数组元素的字段），
    前端可据此渲染"可视化表格 + 原始 JSON"双视图。
    """
    field_def: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": "json",
        "default": default,
        "required": required,
        "rows": rows,
        "group": "source",
    }
    if placeholder:
        field_def["placeholder"] = placeholder
    if json_table:
        field_def["json_table"] = json_table
    return field_def


def _int_field(
    key: str,
    label: str,
    *,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
    group: str = "base",
) -> dict[str, Any]:
    """构造整数类型字段定义。"""
    field_def: dict[str, Any] = {
        "key": key,
        "label": label,
        "type": "int",
        "default": default,
        "group": group,
    }
    if min_value is not None:
        field_def["min"] = min_value
    if max_value is not None:
        field_def["max"] = max_value
    return field_def


def _event_key_fields() -> list[dict[str, Any]]:
    """返回所有灾种通用的事件编排字段（报数 / 事件键 / 最终报）。"""
    return [
        _int_field(
            "report_num",
            "报数 (第几报)",
            default=1,
            min_value=1,
            max_value=99,
            group="orchestration",
        ),
        _text(
            "event_key",
            "事件键 (同一事件的连续报次共用一个键)",
            default="",
            required=False,
            placeholder="如 eq20260811a；不填则每个步骤单独算一个事件",
            group="orchestration",
        ),
        _bool_field("is_final", "最终报", group="orchestration"),
    ]


# 时间参数标签模板：按灾种数据源的实际时间语义动态命名，避免非地震页面
# 出现"发震时间"的歧义：
# - earthquake：发震时间（occurred_at）
# - tsunami：发布时间（issued_at）
# - weather：生效时间（effective_at）
# - typhoon：观测时间（updated_at）
_TIME_LABELS_DEFAULT: dict[str, str] = {
    "event_time": "事件时间",
    "time_offset": "事件时间回退 (秒)",
    "time_delay": "事件时间延迟 (秒)",
    "update_time": "更新时间",
    "update_offset": "更新时间回退 (秒)",
    "update_delay": "更新时间延迟 (秒)",
}

_TIME_LABELS_BY_DISASTER: dict[str, dict[str, str]] = {
    DISASTER_TYPE_EARTHQUAKE: {
        "event_time": "发震时间",
        "time_offset": "发震时间回退 (秒)",
        "time_delay": "发震时间延迟 (秒)",
        "update_time": "信息发布时间",
        "update_offset": "信息发布回退 (秒)",
        "update_delay": "信息发布延迟 (秒)",
    },
    DISASTER_TYPE_TSUNAMI: {
        "event_time": "发布时间",
        "time_offset": "发布时间回退 (秒)",
        "time_delay": "发布时间延迟 (秒)",
        "update_time": "信息更新时间",
        "update_offset": "信息更新回退 (秒)",
        "update_delay": "信息更新延迟 (秒)",
    },
    DISASTER_TYPE_WEATHER: {
        "event_time": "生效时间",
        "time_offset": "生效时间回退 (秒)",
        "time_delay": "生效时间延迟 (秒)",
        "update_time": "发布时间",
        "update_offset": "发布时间回退 (秒)",
        "update_delay": "发布时间延迟 (秒)",
    },
    DISASTER_TYPE_TYPHOON: {
        "event_time": "观测时间",
        "time_offset": "观测时间回退 (秒)",
        "time_delay": "观测时间延迟 (秒)",
        "update_time": "更新时间",
        "update_offset": "更新时间回退 (秒)",
        "update_delay": "更新时间延迟 (秒)",
    },
}


def _time_fields(disaster_type: str | None = None) -> list[dict[str, Any]]:
    """返回所有灾种通用的事件时间参数。

    时间字段标签按灾种数据源的实际时间语义动态生成。

    缺省行为（与 simulation_builder._resolve_event_time / _resolve_update_time 对齐）：
    1. 填写绝对时间（YYYY-MM-DD HH:MM:SS / YYYY-MM-DD HH:MM / ISO8601）→ 直接采用；
    2. 否则按"回退秒数"向前推（>0 模拟历史时刻）；
    3. 否则按"延迟秒数"向后推（>0 模拟未来时刻，叠加在绝对时间或当前时间之上）；
    4. 绝对时间 / 回退 / 延迟均为空 → 使用执行时刻（当前时间）。
    """
    labels = dict(_TIME_LABELS_DEFAULT)
    if disaster_type:
        labels.update(_TIME_LABELS_BY_DISASTER.get(disaster_type, {}))
    return [
        _text(
            "event_time",
            labels["event_time"],
            default="",
            required=False,
            placeholder="如 2026-08-12 09:30:00；不填就用现在的时间",
            group="time",
        ),
        _num(
            "time_offset_seconds",
            labels["time_offset"],
            default=0.0,
            min_value=0.0,
            max_value=86400.0,
            step=60.0,
            required=False,
            group="time",
        ),
        _num(
            "event_time_delay_seconds",
            labels["time_delay"],
            default=0.0,
            min_value=0.0,
            max_value=86400.0,
            step=60.0,
            required=False,
            group="time",
        ),
        _text(
            "update_time",
            labels["update_time"],
            default="",
            required=False,
            placeholder="如 2026-08-12 09:30:00；不填就用现在的时间",
            group="time",
        ),
        _num(
            "update_time_offset_seconds",
            labels["update_offset"],
            default=0.0,
            min_value=0.0,
            max_value=86400.0,
            step=60.0,
            required=False,
            group="time",
        ),
        _num(
            "update_time_delay_seconds",
            labels["update_delay"],
            default=0.0,
            min_value=0.0,
            max_value=86400.0,
            step=60.0,
            required=False,
            group="time",
        ),
    ]


def _build_snet_simulation_fields() -> list[dict[str, Any]]:
    """推导 S-Net 模拟参数字段（对齐 snet_parser 消费结构，不走通用两列模板）。

    S-Net 事件核心是"测站震度分布"，与通用地震两列参数（震中/震级/深度）不同：
    - 领域事件坐标取最高震度测站（非用户自定义震中）
    - 无震级/深度语义（解析器 magnitude/depth 恒为 None）
    - 震度取 max_shindo（計測震度，可负）
    - min_shindo / station_min_shindo 参与触发测站统计
    """
    fields: list[dict[str, Any]] = [
        _num(
            "latitude",
            "参考纬度 (用于缺省测站分布)",
            default=35.0,
            min_value=-90.0,
            max_value=90.0,
            required=False,
        ),
        _num(
            "longitude",
            "参考经度 (用于缺省测站分布)",
            default=140.0,
            min_value=-180.0,
            max_value=180.0,
            required=False,
        ),
        _num(
            "magnitude",
            "参考震级 (用于缺省测站分布)",
            default=5.5,
            min_value=0.0,
            max_value=10.0,
            required=False,
        ),
        _text(
            "timestamp",
            "瓦片时间戳",
            default="",
            required=False,
            placeholder="如 20260101120000（YYYYMMDDHHMM00）；留空用执行时刻",
            group="source",
        ),
        _num(
            "min_shindo",
            "最高震度门槛",
            default=1.5,
            min_value=-3.0,
            max_value=7.0,
            step=0.1,
            required=False,
            group="source",
        ),
        _num(
            "station_min_shindo",
            "测站计数门槛",
            default=0.5,
            min_value=-3.0,
            max_value=7.0,
            step=0.1,
            required=False,
            group="source",
        ),
        _num(
            "max_shindo",
            "最高震度",
            default=4.2,
            min_value=-3.0,
            max_value=7.0,
            step=0.1,
            required=False,
            group="source",
        ),
        _json_field(
            "stations",
            "测站震度分布",
            default='[{"name":"N.S5N06","shindo":4.2,"lat":41.884,"lon":145.6544},{"name":"N.S5N07","shindo":3.5,"lat":41.6637,"lon":145.5291},{"name":"N.S4N11","shindo":2.8,"lat":40.4353,"lon":143.543}]',
            required=False,
            placeholder='[{"name":"站名","shindo":計測震度,"lat":纬度,"lon":经度}]',
            rows=4,
            json_table={
                "add_label": "➕ 添加测站",
                "empty_hint": "留空则由系统按参考坐标生成默认测站分布",
                "columns": [
                    {"key": "name", "label": "站名", "type": "text"},
                    {
                        "key": "shindo",
                        "label": "計測震度",
                        "type": "number",
                        "min": -3,
                        "max": 7,
                        "step": 0.1,
                    },
                    {
                        "key": "lat",
                        "label": "纬度（N/S）",
                        "type": "number",
                        "min": -90,
                        "max": 90,
                        "step": 0.1,
                    },
                    {
                        "key": "lon",
                        "label": "经度（W/E）",
                        "type": "number",
                        "min": -180,
                        "max": 180,
                        "step": 0.1,
                    },
                ],
            },
        ),
        _text(
            "top_station",
            "最高震度测站",
            default="N.S5N06",
            required=False,
            placeholder="如 N.S5N06；留空自动取震度最高的测站",
            group="source",
        ),
    ]
    fields.extend(_time_fields(DISASTER_TYPE_EARTHQUAKE))
    fields.extend(_event_key_fields())
    return fields


def _build_earthquake_fields(source_id: str) -> list[dict[str, Any]]:
    """按地震源特征推导参数字段。

    默认值逐源对齐 docs/ 各数据源文档中的示例返回数据。
    """
    entry = get_source_entry(source_id)

    # S-Net 数据源：不走通用"两列震中参数"模板。
    # 真实链路（snet_parser）的事件以"测站震度分布"为核心：
    # 领域事件坐标取最高震度测站、无震级/深度概念、震度取 max_shindo。
    # 因此模拟字段按解析器消费结构单独推导，避免通用两列构造出的
    # 事件（假震级/假深度）与 S-Net 展示链路不兼容。
    if source_id == "snet_msil":
        return _build_snet_simulation_fields()

    # 各源文档示例默认值（place_name, latitude, longitude, magnitude, depth）
    # 数据来源索引：
    # - cea_fanstudio / cea_pr_fanstudio：FAN WebSocket /cea、/cea-pr 示例
    # - cea_wolfx：Wolfx cenc_eew
    # - cwa_fanstudio / cwa_wolfx：FAN /cwa-eew 示例（高雄市桃源區）
    # - cwa_fanstudio_report：FAN /cwa 示例（屏東縣近海）
    # - jma_fanstudio / jma_wolfx：FAN /jma 示例（能登半島沖 M5.0）
    # - jma_p2p：P2P v2 EEW(556) 示例（宗谷地方北部）
    # - global_quake：OpenQuakeAPI 示例（日本关东地区 M6.5）
    # - cenc_fanstudio / cenc_wolfx：FAN /cenc 示例（堪察加东岸附近海域）
    # - cenc_ir_fanstudio：FAN /cenc-ir 示例（新疆吐鲁番市托克逊县）
    # - cenc_ir_eqsc：EQSC intensityReportCENC 示例（青海海西州都兰县）
    # - jma_p2p_info / jma_wolfx_info：P2P v2 JMAQuake(551) 示例（宮古島近海）
    # - usgs_fanstudio：FAN /usgs 示例（Idyllwild）
    # - fssn_cmt_fanstudio：FAN /fssn-cmt 示例（斐济群岛地区）
    # - sa_fanstudio：FAN /sa 示例（Olancha）
    _source_base_defaults: dict[str, tuple[str, float, float, float, float]] = {
        "cea_fanstudio": ("四川甘孜州雅江县", 29.43, 101.09, 4.0, 8.0),
        "cea_pr_fanstudio": ("四川阿坝州红原县", 33.002, 102.89, 4.4, 5.0),
        "cea_wolfx": ("四川甘孜州雅江县", 29.43, 101.09, 4.0, 8.0),
        "cwa_fanstudio": ("高雄市桃源區", 23.33, 120.82, 4.5, 10.0),
        "cwa_wolfx": ("高雄市桃源區", 23.33, 120.82, 4.5, 10.0),
        "cwa_fanstudio_report": (
            "屏東縣政府南南東方 103.2 公里 (位於屏東縣近海)",
            21.8,
            120.8,
            4.6,
            23.9,
        ),
        "jma_fanstudio": ("能登半島沖", 37.1, 136.6, 5.0, 10.0),
        "jma_p2p": ("宗谷地方北部", 44.9, 142.1, 5.5, 10.0),
        "jma_wolfx": ("能登半島沖", 37.1, 136.6, 5.0, 10.0),
        "global_quake": ("日本关东地区", 35.0, 140.0, 6.5, 10.0),
        "cenc_fanstudio": ("堪察加东岸附近海域", 51.8, 159.5, 6.1, 30.0),
        "cenc_wolfx": ("堪察加东岸附近海域", 51.8, 159.5, 6.1, 30.0),
        "cenc_ir_fanstudio": ("新疆吐鲁番市托克逊县", 43.21, 87.71, 4.7, 25.0),
        "cenc_ir_eqsc": ("青海海西州都兰县", 36.75, 96.48, 4.0, 10.0),
        "jma_p2p_info": ("宮古島近海", 24.4, 125.2, 4.0, 50.0),
        "jma_wolfx_info": ("宮古島近海", 24.4, 125.2, 4.0, 50.0),
        "usgs_fanstudio": ("6 km SW of Idyllwild, CA", 33.7043, -116.7712, 2.62, 16.33),
        "fssn_cmt_fanstudio": ("斐济群岛地区", -21.8973, -179.5057, 6.1, 612.0),
        "sa_fanstudio": ("12 km SSW of Olancha, CA", 36.1743, -118.0322, 3.98, 2.0),
    }
    _place_name, _lat, _lon, _mag, _depth = _source_base_defaults.get(
        source_id, ("四川甘孜州雅江县", 29.43, 101.09, 4.0, 8.0)
    )

    fields: list[dict[str, Any]] = [
        _text(
            "place_name",
            "震中位置",
            default=_place_name,
            required=False,
            placeholder="如 四川甘孜州雅江县 / 青海海西州都兰县；留空则按经纬度自动翻译",
        ),
        _num("latitude", "纬度 (N/S)", default=_lat, min_value=-90.0, max_value=90.0),
        _num(
            "longitude", "经度 (W/E)", default=_lon, min_value=-180.0, max_value=180.0
        ),
        _num(
            "magnitude",
            "震级 (M)",
            default=_mag,
            min_value=0.0,
            max_value=10.0,
            step=0.1,
        ),
        _num(
            "depth",
            "深度 (km)",
            default=_depth,
            min_value=0.0,
            max_value=700.0,
            step=1.0,
        ),
    ]

    intensity_mode = (entry.intensity_mode if entry else "") or ""
    parser_name = (entry.parser_name if entry else "") or ""
    presentation_type = (entry.presentation_type if entry else "") or ""

    # 日本震度制式源：补充震度字段（源特有 → 右列）。
    if intensity_mode == "scale" or "jma" in source_id or "p2p" in source_id:
        if "p2p" in source_id:
            # P2P 源使用业务档位值（10=震度1 … 70=震度7），构建时转换为规范震度。
            # - EEW(556) areas.scaleFrom/scaleTo=45.0（5弱）
            # - 地震情报(551) example maxScale=10（震度1）
            default_scale = 45 if source_id == "jma_p2p" else 10
            fields.append(
                _num(
                    "scale",
                    "最大震度 (P2P原始值)",
                    default=default_scale,
                    min_value=0,
                    max_value=70,
                    step=1,
                    group="source",
                )
            )
        else:
            # CWA 正式地震报告（FAN /cwa）为实测报告而非 EEW 预警，
            # 标签区分"最大震度"，避免与"预估最大震度"误导混淆。
            scale_label = (
                "最大震度" if source_id == "cwa_fanstudio_report" else "预估最大震度"
            )
            fields.append(
                _num(
                    "scale",
                    scale_label,
                    default=4,
                    min_value=0,
                    max_value=7,
                    step=1,
                    group="source",
                )
            )
    # 烈度速报源：补充最大烈度（FAN 与 EQSC 两套解析器都支持；源特有 → 右列）。
    if (
        "cenc_ir" in source_id
        or parser_name == "china_intensity_report_parser"
        or parser_name == "china_intensity_report_eqsc_parser"
    ):
        # 烈度速报源：事件名称 nameByInfo 对齐文档示例。
        name_by_info_default = (
            "新疆吐鲁番市托克逊县4.7级地震"
            if source_id == "cenc_ir_fanstudio"
            else "青海海西州都兰县4.0级地震"
        )
        fields.append(
            _text(
                "name_by_info",
                "事件名称",
                default=name_by_info_default,
                required=False,
                placeholder="如 新疆吐鲁番市托克逊县4.7级地震",
                group="source",
            )
        )
        fields.append(
            _num(
                "intensity",
                "最大仪器烈度",
                default=7.0,
                min_value=1.0,
                max_value=12.0,
                step=0.1,
                group="source",
            )
        )
        # 烈度速报源特有数据：按数据源实际字段差异动态渲染。
        is_fan_ir = source_id == "cenc_ir_fanstudio"
        fields.append(
            _text(
                "intensity_info_text",
                "推测烈度说明",
                default=(
                    "基于'GB/T17742-2020中国地震烈度表', 结合台站实测仪器烈度, 本次地震推测最高烈度为7度。"
                    if is_fan_ir
                    else "基于'GB/T177422020中国地震烈度表', 结合台站实测仪器烈度, 本次地震推测最高烈度为7度。"
                ),
                required=False,
                placeholder="烈度分布的文字描述，如 本次地震推测最高烈度为7度",
                group="source",
            )
        )
        if is_fan_ir:
            fields.append(
                _json_field(
                    "intensity_contour",
                    "烈度等震线 (JSON对象)",
                    default='{"type":"FeatureCollection","features":[]}',
                    required=False,
                    placeholder='{"type":"FeatureCollection","features":[...]} 烈度等震线 GeoJSON',
                    rows=3,
                    json_table={
                        "kind": "array_in_object",
                        "array_key": "features",
                        "add_label": "➕ 添加等震线",
                        "empty_hint": "暂无等震线，点击下方按钮添加",
                        "columns": [
                            {
                                "key": "properties.intensity",
                                "label": "烈度",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "properties.INT",
                                "label": "烈度(备选)",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "geometry.type",
                                "label": "几何类型",
                                "type": "text",
                            },
                        ],
                    },
                )
            )
            fields.append(
                _json_field(
                    "intensity_stations",
                    "台站烈度明细 (JSON数组)",
                    default='[{"stName":"A0001","INT":1.3}]',
                    required=False,
                    placeholder='[{"stName":"台站名","INT":仪器烈度}]',
                    rows=4,
                    json_table={
                        "add_label": "➕ 添加台站",
                        "empty_hint": "留空则由系统按最大仪器烈度生成单个模拟台站",
                        "columns": [
                            {"key": "stName", "label": "台站名", "type": "text"},
                            {
                                "key": "INT",
                                "label": "仪器烈度",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "lat",
                                "label": "纬度（N/S）",
                                "type": "number",
                                "min": -90,
                                "max": 90,
                                "step": 0.1,
                            },
                            {
                                "key": "lon",
                                "label": "经度（W/E）",
                                "type": "number",
                                "min": -180,
                                "max": 180,
                                "step": 0.1,
                            },
                        ],
                    },
                )
            )
        else:
            # EQSC CENC 烈度速报
            fields.append(
                _json_field(
                    "intensity_contour",
                    "烈度等震线 (JSON对象)",
                    default='{"type":"FeatureCollection","features":[]}',
                    required=False,
                    placeholder='GeoJSON FeatureCollection；如 {"type":"FeatureCollection","features":[...]}',
                    rows=3,
                    json_table={
                        "kind": "array_in_object",
                        "array_key": "features",
                        "add_label": "➕ 添加等震线",
                        "empty_hint": "暂无等震线，点击下方按钮添加",
                        "columns": [
                            {
                                "key": "properties.intensity",
                                "label": "烈度",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "properties.INT",
                                "label": "烈度(备选)",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "geometry.type",
                                "label": "几何类型",
                                "type": "text",
                            },
                        ],
                    },
                )
            )
            fields.append(
                _json_field(
                    "intensity_stations",
                    "台站烈度明细 (JSON数组)",
                    default='[{"stationInfo":{"network":"QH","id":"HD031","name":"HD031","province":"青海"},"latitude":36.43,"longitude":96.46,"intensity":4.7,"distance":35.6}]',
                    required=False,
                    placeholder='[{"stationInfo":{...},"latitude":纬度,"longitude":经度,"intensity":仪器烈度,"distance":震中距km}]',
                    rows=4,
                    json_table={
                        "add_label": "➕ 添加台站",
                        "empty_hint": "留空则由系统按最大仪器烈度生成单个模拟台站",
                        "columns": [
                            {
                                "key": "stationInfo.name",
                                "label": "台站名",
                                "type": "text",
                            },
                            {
                                "key": "latitude",
                                "label": "纬度（N/S）",
                                "type": "number",
                                "min": -90,
                                "max": 90,
                                "step": 0.1,
                            },
                            {
                                "key": "longitude",
                                "label": "经度（W/E）",
                                "type": "number",
                                "min": -180,
                                "max": 180,
                                "step": 0.1,
                            },
                            {
                                "key": "intensity",
                                "label": "仪器烈度",
                                "type": "number",
                                "min": 1,
                                "max": 12,
                                "step": 0.1,
                            },
                            {
                                "key": "distance",
                                "label": "震中距(km)",
                                "type": "number",
                                "min": 0,
                                "step": 0.1,
                            },
                        ],
                    },
                )
            )
    # CEA 地震预警源：补省份（驱动标题行『XX地震局』，先于烈度）与预估烈度 epiIntensity。
    if source_id in ("cea_fanstudio", "cea_pr_fanstudio", "cea_wolfx"):
        fields.append(
            _text(
                "province",
                "省份",
                default="四川",
                required=False,
                placeholder="如 四川 展示为『四川地震局』",
                group="source",
            )
        )
        fields.append(
            _num(
                "epi_intensity",
                "预估最大烈度",
                default=5.5 if source_id != "cea_pr_fanstudio" else 6.1,
                min_value=0.0,
                max_value=12.0,
                step=0.1,
                required=False,
                group="source",
            )
        )
    # FSSN CMT 源：补充节面参数与 CMT 特有数据（源特有 → 右列）。
    if parser_name == "fssn_cmt_parser":
        fields.extend(
            [
                _text(
                    "fssn_event_id",
                    "关联 FSSN 事件ID",
                    default="FSSN2026eegb",
                    required=False,
                    placeholder="如 FSSN2026eegb（对应 /fssn 的 id）",
                    group="source",
                ),
                _json_field(
                    "all_magnitudes",
                    "震级集合 (JSON对象)",
                    default='{"M":6.1,"mB":6.2,"mb":6.1,"MLv":6.6,"Mwp":6,"Mww":6.3}',
                    required=False,
                    placeholder='{"M":主震级,"mB":面波震级,"mb":体波震级,"MLv":地方震级,"Mwp":宽频P波震级,"Mww":矩震级}',
                    rows=3,
                    json_table={
                        "kind": "object",
                        "columns": [
                            {"key": "M", "label": "M", "type": "number", "step": 0.1},
                            {"key": "mB", "label": "mB", "type": "number", "step": 0.1},
                            {"key": "mb", "label": "mb", "type": "number", "step": 0.1},
                            {
                                "key": "MLv",
                                "label": "MLv",
                                "type": "number",
                                "step": 0.1,
                            },
                            {
                                "key": "Mwp",
                                "label": "Mwp",
                                "type": "number",
                                "step": 0.1,
                            },
                            {
                                "key": "Mww",
                                "label": "Mww",
                                "type": "number",
                                "step": 0.1,
                            },
                        ],
                    },
                ),
                _text(
                    "centroid_depth",
                    "矩心深度 (km)",
                    default="582.1",
                    required=False,
                    placeholder="如 582.1",
                    group="source",
                ),
                _num(
                    "strike",
                    "节面1 走向 (strike)",
                    default=200.0,
                    min_value=0.0,
                    max_value=360.0,
                    group="source",
                ),
                _num(
                    "dip",
                    "节面1 倾角 (dip)",
                    default=77.0,
                    min_value=0.0,
                    max_value=90.0,
                    group="source",
                ),
                _num(
                    "rake",
                    "节面1 滑动角 (rake)",
                    default=74.0,
                    min_value=-180.0,
                    max_value=180.0,
                    group="source",
                ),
                _num(
                    "strike2",
                    "节面2 走向 (strike)",
                    default=73.0,
                    min_value=0.0,
                    max_value=360.0,
                    group="source",
                ),
                _num(
                    "dip2",
                    "节面2 倾角 (dip)",
                    default=21.0,
                    min_value=0.0,
                    max_value=90.0,
                    group="source",
                ),
                _num(
                    "rake2",
                    "节面2 滑动角 (rake)",
                    default=141.0,
                    min_value=-180.0,
                    max_value=180.0,
                    group="source",
                ),
                _json_field(
                    "moment_tensor",
                    "矩张量分量 (JSON对象)",
                    default='{"mnn":"-5.0526e+17","mee":"-6.9553e+17","mdd":"1.2008e+18","mne":"1.2994e+18","mnd":"-9.2356e+17","med":"2.6576e+18"}',
                    required=False,
                    placeholder='{"mnn":北-北分量,"mee":东-东分量,"mdd":垂直-垂直分量,"mne":北-东分量,"mnd":北-垂直分量,"med":东-垂直分量}（科学计数法字符串）',
                    rows=3,
                    json_table={
                        "kind": "object",
                        "columns": [
                            {"key": "mnn", "label": "mnn", "type": "text"},
                            {"key": "mee", "label": "mee", "type": "text"},
                            {"key": "mdd", "label": "mdd", "type": "text"},
                            {"key": "mne", "label": "mne", "type": "text"},
                            {"key": "mnd", "label": "mnd", "type": "text"},
                            {"key": "med", "label": "med", "type": "text"},
                        ],
                    },
                ),
            ]
        )
    # Global Quake：补充 PGA、MMI 烈度与测站统计等特有参数（源特有 → 右列）。
    if presentation_type == "global_quake":
        fields.append(
            _num(
                "max_pga",
                "最大加速度 (gal)",
                default=140.0,
                min_value=0.0,
                max_value=5000.0,
                step=10.0,
                required=False,
                group="source",
            )
        )
        fields.append(
            _num(
                "intensity",
                "MMI 烈度",
                default=8.0,
                min_value=1.0,
                max_value=12.0,
                step=1.0,
                required=False,
                group="source",
            )
        )
        fields.append(
            _bool_field(
                "fixed_depth",
                "固定深度",
                default=False,
                group="source",
            )
        )
        fields.append(
            _json_field(
                "station_count",
                "台站统计 (JSON对象)",
                default='{"total":30,"selected":20,"used":15,"matching":12}',
                required=False,
                placeholder='{"total":总台站数,"selected":选中台站数,"used":使用台站数,"matching":匹配台站数}',
                rows=3,
                json_table={
                    "kind": "object",
                    "columns": [
                        {
                            "key": "total",
                            "label": "总台站数",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                        {
                            "key": "selected",
                            "label": "选中台站数",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                        {
                            "key": "used",
                            "label": "使用台站数",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                        {
                            "key": "matching",
                            "label": "匹配台站数",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "quality",
                "定位质量 (JSON对象)",
                default='{"errOrigin":0.5,"errDepth":2.0,"errNS":5.0,"errEW":4.0,"pct":80.0,"stations":15}',
                required=False,
                placeholder='{"errOrigin":发震时间误差（s）,"errDepth":深度误差（km）,"errNS":南北误差（km）,"errEW":东西误差（km）,"pct":质量百分比,"stations":参与定位台站数}',
                rows=4,
                json_table={
                    "kind": "object",
                    "columns": [
                        {
                            "key": "errOrigin",
                            "label": "发震时间误差(s)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                        {
                            "key": "errDepth",
                            "label": "深度误差(km)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                        {
                            "key": "errNS",
                            "label": "南北误差(km)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                        {
                            "key": "errEW",
                            "label": "东西误差(km)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                        {
                            "key": "pct",
                            "label": "质量百分比(%)",
                            "type": "number",
                            "min": 0,
                            "max": 100,
                            "step": 1,
                        },
                        {
                            "key": "stations",
                            "label": "定位台站数",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "depth_confidence",
                "深度置信区间 (JSON对象)",
                default='{"minDepth":8.0,"maxDepth":12.0}',
                required=False,
                placeholder='{"minDepth":最小可能深度（km）,"maxDepth":最大可能深度（km）}',
                rows=3,
                json_table={
                    "kind": "object",
                    "columns": [
                        {
                            "key": "minDepth",
                            "label": "最小深度(km)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                        {
                            "key": "maxDepth",
                            "label": "最大深度(km)",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "cluster",
                "事件簇 (JSON对象)",
                default='{"id":"cluster-uuid","latitude":35.1,"longitude":140.1,"level":1}',
                required=False,
                placeholder='{"id":簇ID,"latitude":簇中心纬度,"longitude":簇中心经度,"level":簇层级}',
                rows=3,
                json_table={
                    "kind": "object",
                    "columns": [
                        {"key": "id", "label": "簇ID", "type": "text"},
                        {
                            "key": "latitude",
                            "label": "簇中心纬度（N/S）",
                            "type": "number",
                            "min": -90,
                            "max": 90,
                            "step": 0.1,
                        },
                        {
                            "key": "longitude",
                            "label": "簇中心经度（W/E）",
                            "type": "number",
                            "min": -180,
                            "max": 180,
                            "step": 0.1,
                        },
                        {
                            "key": "level",
                            "label": "簇层级",
                            "type": "number",
                            "min": 0,
                            "step": 1,
                        },
                    ],
                },
            )
        )
    # 说明：S-Net（snet_msil）已在 _build_earthquake_fields 开头通过
    # _build_snet_simulation_fields 单独推导，不再走此通用两列模板分支。

    # CWA EEW 源：补影响区域与震度制式（台湾；源特有 → 右列）。
    if source_id in ("cwa_fanstudio", "cwa_wolfx"):
        # locationDesc 为影响区域列表
        fields.append(
            _text(
                "location_desc",
                "影响区域",
                default="嘉義縣、嘉義市",
                required=False,
                placeholder="如 嘉義縣、嘉義市（顿号分隔）",
                group="source",
            )
        )

    # JMA EEW 源：补情报类型（源特有 → 右列）。
    if source_id in ("jma_fanstudio", "jma_p2p", "jma_wolfx"):
        info_type_options = [("予報", "予报"), ("警报", "警报")]
        info_type_default = "警报" if source_id == "jma_p2p" else "予報"
        fields.append(
            _select_field(
                "info_type",
                "情报类型",
                info_type_options,
                default=info_type_default,
                required=False,
                group="source",
            )
        )
    # JMA 地震情报源（P2P 551 / Wolfx jma_eqlist）：情报类型决定推文标题类别。
    if source_id in ("jma_p2p_info", "jma_wolfx_info"):
        fields.append(
            _select_field(
                "info_type",
                "情报类型",
                [
                    ("ScalePrompt", "震度速报"),
                    ("Destination", "震源相关情报"),
                    ("ScaleAndDestination", "震度・震源相关情报"),
                    ("DetailScale", "各地震度相关情报"),
                    ("Foreign", "远地地震相关情报"),
                    ("Other", "其他情报"),
                ],
                default="DetailScale",
                required=False,
                group="source",
            )
        )
        fields.append(
            _select_field(
                "foreign_tsunami",
                "海外津波有无",
                [
                    ("None", "无"),
                    ("Unknown", "不明"),
                    ("Checking", "调查中"),
                    ("NonEffectiveNearby", "震源附近可能有小规模海啸，无需担心受害"),
                    ("WarningNearby", "震源附近可能发生海啸"),
                    ("WarningPacific", "太平洋可能发生海啸"),
                    ("WarningPacificWide", "太平洋广域可能发生海啸"),
                    ("WarningIndian", "印度洋可能发生海啸"),
                    ("WarningIndianWide", "印度洋广域可能发生海啸"),
                    ("Potential", "此规模通常可能引发海啸"),
                ],
                default="Unknown",
                required=False,
                group="source",
            )
        )
        # 自由付加文，P2P 551 / Wolfx 均支持）
        fields.append(
            _text(
                "free_form_comment",
                "附加说明",
                default="",
                required=False,
                placeholder="如 大規模な噴火が発生しました；留空则省略",
                group="source",
            )
        )

    # CENC 报告源：补信息类型（源特有 → 右列）。
    # 默认值对齐 FAN /cenc 示例：infoTypeName=[正式测定]、placeName=堪察加东岸附近海域。
    if source_id in ("cenc_fanstudio", "cenc_wolfx"):
        fields.append(
            _select_field(
                "info_type_name",
                "信息类型",
                [("正式测定", "[正式测定]"), ("自动测定", "[自动测定]")],
                default="正式测定",
                required=False,
                group="source",
            )
        )
        # 最大烈度填写入口：Wolfx cenc_eqlist 文档含 intensity（最大烈度）；
        # FAN /cenc 文档虽未直接给出，但展示链路统一支持，故一并暴露。
        fields.append(
            _num(
                "intensity",
                "最大烈度",
                default=7.0,
                min_value=0.0,
                max_value=12.0,
                step=0.1,
                required=False,
                group="source",
            )
        )

    # CWA 正式报告源：补报告图片与等震度图附件（源特有 → 右列）。
    if source_id == "cwa_fanstudio_report":
        fields.append(
            _text(
                "image_uri",
                "报告图片 URL",
                default="https://scweb.cwa.gov.tw/webdata/OLDEQ/202601/2026012702483746011_H.png",
                required=False,
                placeholder="https://... 或留空跳过",
                group="source",
            )
        )
        fields.append(
            _text(
                "shakemap_uri",
                "等震度图 URL",
                default="https://scweb.cwa.gov.tw/webdata/drawTrace/plotContour/2026/2026011i.png",
                required=False,
                placeholder="https://... 或留空跳过",
                group="source",
            )
        )

    # JMA EEW 源（FAN）：仅补取消报标记。
    if source_id == "jma_fanstudio":
        fields.append(_bool_field("is_cancel", "取消报", group="source"))

    # JMA EEW 源（P2P 556）：训练/PLUM/取消标记 + 警报区域（areas）。
    # "各地震度详情 jma_points" 是 JMAQuake(551) 的字段，556 EEW 不包含，故此处不再暴露。
    if source_id == "jma_p2p":
        fields.extend(
            [
                _bool_field("is_training", "训练报", group="source"),
                _bool_field(
                    "is_assumption",
                    "PLUM法假定震源",
                    group="source",
                ),
                _bool_field("is_cancel", "取消报", group="source"),
                _json_field(
                    "jma_warning_areas",
                    "警报区域 (JSON数组)",
                    default='[{"pref":"北海道道北","name":"上川地方北部","scaleFrom":45.0,"scaleTo":45.0,"kindCode":"11","arrivalTime":null}]',
                    required=False,
                    placeholder='[{"pref":"府県予報区","name":"区域名","scaleFrom":震度下限,"scaleTo":震度上限,"kindCode":"10|11|19","arrivalTime":到达时刻或null}]',
                    rows=3,
                    json_table={
                        "add_label": "➕ 添加警报区域",
                        "empty_hint": "留空则不输出警报区域",
                        "columns": [
                            {"key": "pref", "label": "府県予報区", "type": "text"},
                            {"key": "name", "label": "区域名", "type": "text"},
                            {
                                "key": "scaleFrom",
                                "label": "震度下限",
                                "type": "number",
                                "min": 0,
                                "max": 70,
                                "step": 1,
                            },
                            {
                                "key": "scaleTo",
                                "label": "震度上限",
                                "type": "number",
                                "min": 0,
                                "max": 70,
                                "step": 1,
                            },
                            {"key": "kindCode", "label": "类别码", "type": "text"},
                            {"key": "arrivalTime", "label": "到达时刻", "type": "text"},
                        ],
                    },
                ),
            ]
        )

    # JMA EEW 源（Wolfx）：训练/PLUM/取消/海域标记 + 警报区域（WarnArea）。
    # Wolfx jma_eew 字段含 isTraining/isAssumption/isCancel/isSea/WarnArea(Chiiki/Shindo1/
    # Shindo2/Time/Type/Arrive)；模拟链路统一按 builder 消费的 name/scaleFrom/scaleTo/kindCode
    # 结构透传（与 P2P areas 一致），故此处 json_table 列也保持该结构，便于模拟渲染。
    if source_id == "jma_wolfx":
        fields.extend(
            [
                _bool_field("is_training", "训练报", group="source"),
                _bool_field(
                    "is_assumption",
                    "PLUM法假定震源",
                    group="source",
                ),
                _bool_field("is_cancel", "取消报", group="source"),
                _bool_field("is_sea", "海域地震", group="source"),
                _json_field(
                    "jma_warning_areas",
                    "警报区域 (JSON数组)",
                    default='[{"name":"上川地方北部","scaleFrom":45.0,"scaleTo":45.0,"kindCode":"11"}]',
                    required=False,
                    placeholder='[{"name":"区域名","scaleFrom":震度下限,"scaleTo":震度上限,"kindCode":"10|11|19"}]',
                    rows=3,
                    json_table={
                        "add_label": "➕ 添加警报区域",
                        "empty_hint": "留空则不输出警报区域",
                        "columns": [
                            {"key": "name", "label": "区域名", "type": "text"},
                            {
                                "key": "scaleFrom",
                                "label": "震度下限",
                                "type": "number",
                                "min": 0,
                                "max": 70,
                                "step": 1,
                            },
                            {
                                "key": "scaleTo",
                                "label": "震度上限",
                                "type": "number",
                                "min": 0,
                                "max": 70,
                                "step": 1,
                            },
                            {"key": "kindCode", "label": "类别码", "type": "text"},
                        ],
                    },
                ),
            ]
        )

    # JMA 地震情报源（P2P 551 / Wolfx jma_eqlist）：
    # 补津波有无（domestic_tsunami）与各地震度详情（jma_points）。
    if source_id in ("jma_p2p_info", "jma_wolfx_info"):
        fields.extend(
            [
                _select_field(
                    "domestic_tsunami",
                    "国内津波有无",
                    [
                        ("None", "无"),
                        ("Unknown", "不明"),
                        ("Checking", "调查中"),
                        ("NonEffective", "若干海面变动（无需担心受害）"),
                        ("Watch", "津波注意报"),
                        ("Warning", "津波警报/大津波警报"),
                    ],
                    default="None",
                    required=False,
                    group="source",
                ),
                _json_field(
                    "jma_points",
                    "各地震度详情 (JSON数组)",
                    # 默认值对齐 json-api-v2.yaml JMAQuake example points：
                    # addr/isArea/pref/scale（isArea=false 表示观测点而非区域名）
                    default='[{"pref":"沖縄県","addr":"宮古島市城辺福北","isArea":false,"scale":10},{"pref":"沖縄県","addr":"宮古島市伊良部長浜","isArea":false,"scale":10}]',
                    required=False,
                    placeholder='[{"pref":"都道府県","addr":"震度观测点","isArea":bool,"scale":震度}]',
                    rows=3,
                    json_table={
                        "add_label": "➕ 添加观测点",
                        "empty_hint": "留空则不输出各地震度详情",
                        "columns": [
                            {"key": "pref", "label": "都道府県", "type": "text"},
                            {"key": "addr", "label": "观测点", "type": "text"},
                            {"key": "isArea", "label": "区域标记", "type": "text"},
                            {
                                "key": "scale",
                                "label": "震度",
                                "type": "number",
                                "min": 0,
                                "max": 70,
                                "step": 1,
                            },
                        ],
                    },
                ),
            ]
        )

    # USGS 报告源：补详情 URL 与状态（源特有 → 右列）。
    # 状态展示名与 CENC 测定统一为 [自动测定] / [正式测定]
    if source_id == "usgs_fanstudio":
        fields.append(
            _text(
                "url",
                "详情 URL",
                default="https://earthquake.usgs.gov/earthquakes/eventpage/ci41026127",
                required=False,
                placeholder="如 https://earthquake.usgs.gov/earthquakes/eventpage/...",
                group="source",
            )
        )
        fields.append(
            _select_field(
                "status",
                "信息类型",
                [("reviewed", "[正式测定]"), ("automatic", "[自动测定]")],
                default="reviewed",
                required=False,
                group="source",
            )
        )

    fields.extend(_time_fields(DISASTER_TYPE_EARTHQUAKE))
    fields.extend(_event_key_fields())
    return fields


def _build_tsunami_fields(source_id: str) -> list[dict[str, Any]]:
    """按海啸源特征推导参数字段。

    默认值对齐 docs 海啸数据源文档
    """
    # 中国海啸源：level 为颜色等级制（红/橙/黄/蓝 + 信息/解除），
    # 对齐 FAN /tsunami 文档 warningInfo.level 语义（"黄色"/"橙色"…）。
    is_china = source_id == "china_tsunami_fanstudio"

    fields: list[dict[str, Any]] = [
        _text(
            "title",
            "警报标题",
            default="海啸黄色警报" if is_china else "津波警報",
            required=False,
            placeholder="如  海啸信息 / 海啸黄色警报 / 津波警報",
        ),
        _select_field(
            "level",
            "警报等级",
            (
                [
                    ("信息", "信息"),
                    ("蓝色", "蓝色"),
                    ("黄色", "黄色"),
                    ("橙色", "橙色"),
                    ("红色", "红色"),
                    ("解除", "解除"),
                ]
                if is_china
                else [
                    ("None", "津波予報"),
                    ("Unknown", "不明"),
                    ("Minor", "若干海面变动"),
                    ("Watch", "海啸注意报"),
                    ("Warning", "海啸警报"),
                    ("MajorWarning", "大海啸警报"),
                    ("解除", "解除"),
                ]
            ),
            default="黄色" if is_china else "Warning",
            required=False,
        ),
        _text(
            "place_name",
            "震源位置",
            default="堪察加东岸远海海域" if is_china else "北海道太平洋沿岸東部",
            required=False,
            placeholder="如 堪察加东岸远海海域 / 北海道太平洋沿岸東部",
        ),
        _num(
            "latitude",
            "纬度（N/S）",
            default=52.53 if is_china else 42.0,
            min_value=-90.0,
            max_value=90.0,
            required=False,
        ),
        _num(
            "longitude",
            "经度（W/E）",
            default=160.16 if is_china else 145.0,
            min_value=-180.0,
            max_value=180.0,
            required=False,
        ),
        _num(
            "magnitude",
            "关联震级 (M)",
            default=8.8 if is_china else 8.7,
            min_value=0.0,
            max_value=10.0,
            step=0.1,
            required=False,
        ),
        _num(
            "depth",
            "震源深度 (km)",
            default=20.0 if is_china else 10.0,
            min_value=0.0,
            max_value=700.0,
            required=False,
        ),
    ]
    # 中国海啸源（FAN /tsunami）：补事件编号 / 批次 / HTML 报文详情（源特有 → 右列）。
    if is_china:
        fields.append(
            _text(
                "code",
                "事件编号",
                default="202507300724",
                required=False,
                placeholder="如 202507300724（同一海啸事件多次更新编号一致）",
                group="source",
            )
        )
        fields.append(
            _text(
                "batch",
                "发布批次",
                default="4",
                required=False,
                placeholder="如 4（第4批）",
                group="source",
            )
        )
        fields.append(
            _text(
                "details_url",
                "HTML 报文详情",
                default="https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/202507300724_4.html",
                required=False,
                placeholder="https://... 官方海啸预警公告详细说明页",
                group="source",
            )
        )
        # 海啸预报区 / 水位监测站 / 图件附件（JSON 编辑）
        fields.append(
            _json_field(
                "forecasts",
                "预报区",
                default='[{"name":"花莲","warningLevel":"黄色","estimatedArrivalTime":"13:23","maxWaveHeight":"30-100"},{"name":"台东","warningLevel":"黄色","estimatedArrivalTime":"13:35","maxWaveHeight":"20-60"}]',
                required=False,
                placeholder='[{"name":"地区","warningLevel":"等级","estimatedArrivalTime":"到达时间","maxWaveHeight":"波高cm"}]',
                rows=4,
                json_table={
                    "add_label": "➕ 添加预报区",
                    "empty_hint": "留空则由系统按警报等级生成默认预报区",
                    "columns": [
                        {"key": "name", "label": "地区", "type": "text"},
                        {"key": "warningLevel", "label": "等级", "type": "text"},
                        {
                            "key": "estimatedArrivalTime",
                            "label": "预计到达",
                            "type": "text",
                        },
                        {"key": "maxWaveHeight", "label": "波高(cm)", "type": "text"},
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "monitoring_stations",
                "监测实况",
                default='[{"stationName":"浮标21416","location":"俄罗斯","coordinates":{"latitude":48.1,"longitude":163.5},"time":"08:00","maxWaveHeight":"90.0"}]',
                required=False,
                rows=3,
                json_table={
                    "add_label": "➕ 添加监测站",
                    "empty_hint": "留空则不输出水位观测",
                    "columns": [
                        {"key": "stationName", "label": "站名", "type": "text"},
                        {"key": "location", "label": "位置", "type": "text"},
                        # coordinates 为嵌套对象，拆成点路径列（保留对象结构）
                        {
                            "key": "coordinates.latitude",
                            "label": "纬度（N/S）",
                            "type": "number",
                            "min": -90,
                            "max": 90,
                            "step": 0.1,
                        },
                        {
                            "key": "coordinates.longitude",
                            "label": "经度（W/E）",
                            "type": "number",
                            "min": -180,
                            "max": 180,
                            "step": 0.1,
                        },
                        {"key": "time", "label": "观测时间", "type": "text"},
                        {
                            "key": "maxWaveHeight",
                            "label": "最大振幅(cm)",
                            "type": "text",
                        },
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "map_urls",
                "图件URL (JSON对象)",
                default='{"earthquakeMapUrl":"https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/Earthquake_Pos.jpg","amplitudeMapUrl":"","coastalMapUrl":""}',
                required=False,
                placeholder='{"earthquakeMapUrl":震中位置图,"amplitudeMapUrl":振幅图,"coastalMapUrl":沿岸预测图}',
                rows=3,
                json_table={
                    "kind": "object",
                    "columns": [
                        {
                            "key": "earthquakeMapUrl",
                            "label": "震中位置图 URL",
                            "type": "text",
                        },
                        {
                            "key": "amplitudeMapUrl",
                            "label": "振幅图 URL",
                            "type": "text",
                        },
                        {
                            "key": "coastalMapUrl",
                            "label": "沿岸预测图 URL",
                            "type": "text",
                        },
                    ],
                },
            )
        )
    # JMA 海啸源（P2P 552 / EQSC /jma_tsunami.json）：补津波予報区域 JSON。
    # 默认值按数据源区分：
    if not is_china:
        if source_id == "jma_tsunami_p2p":
            forecasts_default = '[{"name":"福島県","grade":"Warning","immediate":true,"firstHeight":{"condition":"津波到達中と推測"},"maxHeight":{"description":"３ｍ","value":3}},{"name":"青森県太平洋沿岸","grade":"Watch","immediate":false,"firstHeight":{"arrivalTime":"2019/06/18 22:40:00"},"maxHeight":{"description":"１ｍ","value":1}}]'
        else:
            forecasts_default = '[{"name":"北海道太平洋沿岸東部","grade":"Warning","immediate":false,"firstHeight":{"condition":"第１波の到達を確認"},"maxHeight":{"description":"３ｍ","value":"3"}},{"name":"北海道太平洋沿岸中部","grade":"Warning","immediate":false,"firstHeight":{"condition":"第１波の到達を確認"},"maxHeight":{"description":"３ｍ","value":"3"}},{"name":"北海道日本海沿岸南部","grade":"Minor","immediate":false,"firstHeight":{"condition":"不明"},"maxHeight":{"description":"０．２ｍ未満","value":"0.2"}}]'
        fields.append(
            _json_field(
                "forecasts",
                "津波予報区域 (JSON数组)",
                default=forecasts_default,
                required=False,
                placeholder='[{"name":"予報区名","grade":"MajorWarning|Warning|Watch|Minor","immediate":bool,"firstHeight":{"condition":"到达情况"},"maxHeight":{"description":"波高","value":数值}}]',
                rows=4,
                json_table={
                    "add_label": "➕ 添加予報区",
                    "empty_hint": "留空则由系统按警报等级生成默认予報区",
                    # 嵌套对象用点路径列：表格编辑时保留 firstHeight/maxHeight 对象结构，
                    "columns": [
                        {"key": "name", "label": "予報区", "type": "text"},
                        {"key": "grade", "label": "等级", "type": "text"},
                        {"key": "immediate", "label": "立即到达", "type": "text"},
                        {
                            "key": "firstHeight.condition",
                            "label": "第1波到达",
                            "type": "text",
                        },
                        {
                            "key": "maxHeight.description",
                            "label": "最大波高",
                            "type": "text",
                        },
                        {
                            "key": "maxHeight.value",
                            "label": "波高数值",
                            "type": "number",
                            "min": 0,
                            "step": 0.1,
                        },
                    ],
                },
            )
        )
    # JMA 海啸源：补最大波幅与预计到达时间（源特有 → 右列）。
    if source_id in ("jma_tsunami_p2p", "jma_tsunami_eqsc"):
        fields.append(
            _text(
                "max_wave_height",
                "最大波幅",
                default="３ｍ",
                required=False,
                placeholder="如 ３ｍ / ０．２ｍ未満",
                group="source",
            )
        )
        arrival_default = (
            "2019/06/18 22:40:00"
            if source_id == "jma_tsunami_p2p"
            else "2025/07/30 18:30:07"
        )
        fields.append(
            _text(
                "estimated_arrival_time",
                "预计到达时间",
                default=arrival_default,
                required=False,
                placeholder="如 2019/06/18 22:40:00",
                group="source",
            )
        )
        # 训练报标记：EQSC /jma_tsunami.json 文档含 isTraining（字符串型）；
        # P2P 552 无此字段，但模拟链路统一暴露以便透传。
        fields.append(_bool_field("is_training", "训练报", group="source"))
    fields.extend(_time_fields(DISASTER_TYPE_TSUNAMI))
    fields.extend(_event_key_fields())
    return fields


def _build_weather_fields(source_id: str) -> list[dict[str, Any]]:
    """按气象源特征推导参数字段。

    默认值逐源对齐 docs 文档示例
    """
    is_openquake = source_id == "china_weather_openquake"
    title_default = (
        "江苏省徐州市铜山区发布强对流黄色预警"
        if is_openquake
        else "靖远县气象台继续发布雷雨大风黄色预警信号"
    )
    headline_default = (
        "铜山区气象台发布强对流黄色预警[Ⅲ级/较重]"
        if is_openquake
        else "靖远县气象台继续发布雷雨大风黄色预警信号"
    )
    description_default = (
        "铜山区气象台2026年07月29日12时41分发布强对流黄色预警信号：预计今天午后到上半夜我区部分镇（街道）将出现雷电，并伴有短时强降水、局地7-9级雷暴大风等强对流天气，区应急、水务、气象联合提醒加强防范。"
        if is_openquake
        else "靖远县气象台2026年07月10日02时32分继续发布雷雨大风黄色预警信号：预计6小时内，我县部分乡镇可能受雷雨大风影响，阵风可达7级以上，并伴有短时强降水，请注意防范。"
    )
    # 预警编码：FAN 用紧凑 11B 编码（11B2002）；OQ 透传 CMA 原 type 编码（p0000003）。
    code_default = "p0000003" if is_openquake else "11B2002"
    # 经纬度：FAN 文档示例（靖远县 36.5623, 104.67786）；OQ 文档示例（铜山区 34.1929, 117.1839）。
    lat_default = 34.1929 if is_openquake else 36.5623
    lon_default = 117.1839 if is_openquake else 104.67786

    fields: list[dict[str, Any]] = [
        _text(
            "title",
            "预警标题",
            default=title_default,
            required=False,
            placeholder="如 靖远县气象台继续发布雷雨大风黄色预警信号",
        ),
        _text(
            "headline",
            "副标题",
            default=headline_default,
            required=False,
            placeholder="如 铜山区气象台发布强对流黄色预警[Ⅲ级/较重]",
        ),
        _text(
            "description",
            "预警正文",
            default=description_default,
            required=False,
            placeholder="如 靖远县气象台2026年07月10日02时32分继续发布雷雨大风黄色预警信号：预计6小时内…",
        ),
        _text(
            "weather_code",
            "预警编码",
            default=code_default,
            required=False,
            placeholder="如 11B2002 / 11B03_yellow / p0002003；可点击下方按钮从标题自动提取",
        ),
        # 经纬度对气象预警为可选项，移出核心参数区（source 组末尾展示，可留空）。
        _num(
            "latitude",
            "纬度（N/S）",
            default=lat_default,
            min_value=-90.0,
            max_value=90.0,
            required=False,
            group="source",
        ),
        _num(
            "longitude",
            "经度（W/E）",
            default=lon_default,
            min_value=-180.0,
            max_value=180.0,
            required=False,
            group="source",
        ),
    ]
    fields.extend(_time_fields(DISASTER_TYPE_WEATHER))
    fields.extend(_event_key_fields())
    return fields


def _build_typhoon_fields(source_id: str) -> list[dict[str, Any]]:
    """按台风源特征推导参数字段。"""
    fields: list[dict[str, Any]] = [
        _text(
            "typhoon_id",
            "台风编号",
            default="202609",
            required=False,
            placeholder="如 202609（年份2位+编号2位）",
        ),
        # 中文名/英文名为短文本，width=half 让前端合并到同一网格行内并排
        _text("name", "中文名", default="巴威", required=False, width="half"),
        _text("name_en", "英文名", default="BAVI", required=False, width="half"),
        _select_field(
            "typhoon_type",
            "强度等级",
            [
                ("热带低压", "热带低压"),
                ("热带风暴", "热带风暴"),
                ("强热带风暴", "强热带风暴"),
                ("台风", "台风"),
                ("强台风", "强台风"),
                ("超强台风", "超强台风"),
            ],
            default="超强台风",
            required=False,
        ),
        _num(
            "latitude",
            "中心纬度（N/S）",
            default=13.7,
            min_value=-90.0,
            max_value=90.0,
            required=False,
        ),
        _num(
            "longitude",
            "中心经度（W/E）",
            default=147.1,
            min_value=-180.0,
            max_value=180.0,
            required=False,
        ),
        _num(
            "pressure",
            "中心气压 (hPa)",
            default=915,
            min_value=850,
            max_value=1080,
            required=False,
        ),
        _num(
            "wind_speed",
            "最大风速 (m/s)",
            default=62.0,
            min_value=0.0,
            max_value=120.0,
            required=False,
        ),
        _num(
            "power", "风力级别", default=18, min_value=0, max_value=20, required=False
        ),
        _num(
            "radius7",
            "七级风圈 (km)",
            default=380,
            min_value=0,
            max_value=2000,
            required=False,
        ),
        _num(
            "radius10",
            "十级风圈 (km)",
            default=160,
            min_value=0,
            max_value=2000,
            required=False,
        ),
        _select_field(
            "move_direction",
            "移动方向",
            [
                # 16 向方位的推送展示命名（如"西北西"展示为"西北偏西"）。
                ("北", "正北"),
                ("北北东", "东北偏北"),
                ("东北", "东北"),
                ("东北偏东", "东北偏东"),
                ("东", "正东"),
                ("东南偏东", "东南偏东"),
                ("东南", "东南"),
                ("东南偏南", "东南偏南"),
                ("南", "正南"),
                ("西南偏南", "西南偏南"),
                ("西南", "西南"),
                ("西南偏西", "西南偏西"),
                ("西", "正西"),
                ("西北西", "西北偏西"),
                ("西北", "西北"),
                ("西北偏北", "西北偏北"),
            ],
            default="西北西",
            required=False,
        ),
        _num(
            "move_speed",
            "移动速度 (KM/H)",
            default=18.0,
            min_value=0.0,
            max_value=200.0,
            required=False,
        ),
    ]
    # 台风轨迹与四象限风圈（JSON 编辑；缺省由 builder 生成默认轨迹）。
    if source_id != "typhoon_fanstudio":
        fields.append(
            _json_field(
                "history_track",
                "历史轨迹",
                default='[{"time":"2025/09/22 23:00:00","latitude":19.5,"longitude":119.9,"windSpeed":58,"pressure":920,"typeNameCN":"超强台风"},{"time":"2025/09/23 05:00:00","latitude":20.2,"longitude":118.5,"windSpeed":52,"pressure":935,"typeNameCN":"强台风"},{"time":"2025/09/23 11:00:00","latitude":21.0,"longitude":117.0,"windSpeed":45,"pressure":950,"typeNameCN":"台风"}]',
                required=False,
                rows=4,
                json_table={
                    "add_label": "➕ 添加轨迹点",
                    "empty_hint": "留空则由系统按移动方向自动生成默认轨迹",
                    "columns": [
                        {"key": "time", "label": "时间", "type": "text"},
                        {"key": "latitude", "label": "纬度（N/S）", "type": "number"},
                        {"key": "longitude", "label": "经度（W/E）", "type": "number"},
                        {"key": "windSpeed", "label": "风速 m/s", "type": "number"},
                        {"key": "pressure", "label": "气压 hPa", "type": "number"},
                        {"key": "typeNameCN", "label": "强度", "type": "text"},
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "future_track",
                "预测轨迹",
                default='[{"time":"2025/09/25 23:00:00","latitude":21.9,"longitude":106.1,"windSpeed":12,"pressure":1003,"typeNameCN":"热带低压"}]',
                required=False,
                rows=3,
                json_table={
                    "add_label": "➕ 添加预测点",
                    "empty_hint": "留空则由系统按移动方向自动生成默认预测轨迹",
                    "columns": [
                        {"key": "time", "label": "时间", "type": "text"},
                        {"key": "latitude", "label": "纬度（N/S）", "type": "number"},
                        {"key": "longitude", "label": "经度（W/E）", "type": "number"},
                        {"key": "windSpeed", "label": "风速 m/s", "type": "number"},
                        {"key": "pressure", "label": "气压 hPa", "type": "number"},
                        {"key": "typeNameCN", "label": "强度", "type": "text"},
                    ],
                },
            )
        )
        fields.append(
            _json_field(
                "wind_circle",
                "四象限风圈 (JSON对象)",
                default='{"30KTS":{"NE":480,"SE":340,"SW":340,"NW":480},"50KTS":{"NE":180,"SE":160,"SW":160,"NW":180},"64KTS":{"NE":90,"SE":80,"SW":80,"NW":90}}',
                required=False,
                placeholder='{"30KTS":7级风圈,"50KTS":10级风圈,"64KTS":12级风圈（各自含 NE/SE/SW/NW 四象限半径（km））}',
                rows=4,
                json_table={
                    # 对象模式 + 嵌套键：30KTS/50KTS/64KTS 各含 NE/SE/SW/NW 四象限。
                    # 前端按列定义以点路径读写嵌套值，保留三个风圈层级的对象结构。
                    "kind": "object",
                    "columns": [
                        {
                            "key": "30KTS.NE",
                            "label": "7级风圈·东北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "30KTS.SE",
                            "label": "7级风圈·东南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "30KTS.SW",
                            "label": "7级风圈·西南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "30KTS.NW",
                            "label": "7级风圈·西北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "50KTS.NE",
                            "label": "10级风圈·东北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "50KTS.SE",
                            "label": "10级风圈·东南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "50KTS.SW",
                            "label": "10级风圈·西南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "50KTS.NW",
                            "label": "10级风圈·西北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "64KTS.NE",
                            "label": "12级风圈·东北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "64KTS.SE",
                            "label": "12级风圈·东南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "64KTS.SW",
                            "label": "12级风圈·西南",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                        {
                            "key": "64KTS.NW",
                            "label": "12级风圈·西北",
                            "type": "number",
                            "min": 0,
                            "step": 10,
                        },
                    ],
                },
            )
        )
    fields.extend(_time_fields(DISASTER_TYPE_TYPHOON))
    fields.extend(_event_key_fields())
    return fields


# 灾种 -> 字段推导函数
_FIELD_BUILDERS: dict[str, Any] = {
    DISASTER_TYPE_EARTHQUAKE: _build_earthquake_fields,
    DISASTER_TYPE_TSUNAMI: _build_tsunami_fields,
    DISASTER_TYPE_WEATHER: _build_weather_fields,
    DISASTER_TYPE_TYPHOON: _build_typhoon_fields,
}


def _resolve_disaster_type(source_id: str) -> str | None:
    """按数据源类型解析灾种键。"""
    entry = get_source_entry(source_id)
    if entry is None:
        return None
    return _SOURCE_TYPE_TO_DISASTER_TYPE.get(entry.source_type)


def _split_field_groups(
    fields: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按 group 标注拆分字段分组（base / time / source / orchestration）。"""
    groups: dict[str, list[dict[str, Any]]] = {
        "base": [],
        "time": [],
        "source": [],
        "orchestration": [],
    }
    for field in fields:
        group = str(field.get("group") or "source")
        groups.setdefault(group, []).append(field)
    return groups


def build_simulation_schema(
    config: dict[str, Any],
    session_config_manager=None,
) -> dict[str, Any]:
    """构建全灾种 × 全源模拟参数 Schema。

    由服务层集中维护，供管理端接口直接读取，避免前端硬编码参数表单。
    当传入 session_config_manager 时，target_sessions 将返回带备注名的对象列表。
    """
    raw_target_sessions = config.get("target_sessions") or []
    if session_config_manager is not None:
        target_sessions = [
            {
                "session": str(item),
                "session_name": session_config_manager.get_session_name(str(item)),
                "session_display_name": session_config_manager.get_session_display_name(
                    str(item)
                ),
            }
            for item in raw_target_sessions
        ]
    else:
        target_sessions = [str(item) for item in raw_target_sessions]

    # 聚合多个源类型到同一灾种键：EARTHQUAKE_WARNING 与 EARTHQUAKE_INFO
    # 都映射到 earthquake，需合并 sources 而非后一次覆盖前一次。
    disaster_types: dict[str, Any] = {}
    for source_type, disaster_key in _SOURCE_TYPE_TO_DISASTER_TYPE.items():
        source_ids = get_source_ids_by_type(source_type)
        if not source_ids:
            continue
        # 过滤说明：所有已注册源获取到的事件最终都能进入展示推送链路，
        # 因此模拟 schema 原则上对全部源开放。仅当某源 parser_name 为空
        # 且 SimulationBuilder 灾种工厂尚未按该源特化适配时才排除。
        # 当前目录中 parser_name 为空的只有 typhoon_eqsc（EQSC 轮询直构），
        # 其台风工厂为通用适配，可直接模拟，故不排除。
        _SIM_UNBUILDABLE_SOURCES: set[str] = set()
        source_defs = []
        for source_id in source_ids:
            entry = SOURCE_CATALOG.get(source_id)
            if entry is None:
                continue
            if source_id in _SIM_UNBUILDABLE_SOURCES:
                continue
            builder = _FIELD_BUILDERS.get(disaster_key)
            fields = builder(source_id) if builder else list(_event_key_fields())
            groups = _split_field_groups(fields)
            report_policy = (entry.report_policy or "").strip()
            family_label = _FAMILY_LABELS.get(entry.provider_family, "")
            source_defs.append(
                {
                    "source_id": source_id,
                    "label": entry.display_name or source_id,
                    "family_label": family_label,
                    "region": _resolve_source_region(source_id),
                    "region_label": _REGION_LABELS.get(
                        _resolve_source_region(source_id), "全球"
                    ),
                    "description": entry.description or "",
                    "supports_report_semantics": report_policy
                    in _REPORT_SEMANTIC_POLICIES,
                    "report_policy": report_policy,
                    # 分组视图：前端两列布局直接使用
                    "base_fields": groups.get("base", []),
                    "time_fields": groups.get("time", []),
                    "source_fields": groups.get("source", []),
                    "orchestration_fields": groups.get("orchestration", []),
                    # 全量合并视图：向后兼容（校验 / 旧逻辑）
                    "fields": fields,
                }
            )
        if not source_defs:
            continue

        meta = DISASTER_TYPE_META.get(disaster_key, {})
        existing = disaster_types.get(disaster_key)
        if existing is not None:
            # 同灾种已存在（如 earthquake 先遇到 WARNING 后遇到 INFO）：合并 sources
            existing["sources"].extend(source_defs)
        else:
            disaster_types[disaster_key] = {
                "label": meta.get("label", disaster_key),
                "icon": meta.get("icon", "🧪"),
                "sources": source_defs,
            }

    # 合并完成后统一按地区排序（WARNING/INFO 两个批次合并后再排，避免顺序错乱）。
    for disaster_key, type_def in disaster_types.items():
        srcs = type_def.get("sources") or []
        _region_rank = {region: idx for idx, region in enumerate(_REGION_ORDER)}
        srcs.sort(
            key=lambda s: (
                _region_rank.get(s.get("region", "global"), 99),
                _FAMILY_SORT_RANK.get(
                    SOURCE_CATALOG.get(s.get("source_id", "")).provider_family
                    if SOURCE_CATALOG.get(s.get("source_id", "")) is not None
                    else None,
                    9,
                ),
                str(s.get("source_id", "")),
            )
        )
        type_def["region_list"] = [
            {"key": region, "label": _REGION_LABELS[region]}
            for region in _REGION_ORDER
            if any(s.get("region") == region for s in srcs)
        ]
        type_def["sources"] = srcs

    return {
        "target_sessions": target_sessions,
        "disaster_types": disaster_types,
        "timestamp": __import__("datetime").datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# 气象预警编码自动生成：从标题文本提取灾害类型 + 颜色，组合成紧凑 11B 编码。
# 提取逻辑统一收敛到 message/presenters/weather_alarm_code_map.suggest_compact_weather_code
# （与推送链路的标题兜底同源同语义），此处仅做兼容导出。
# ---------------------------------------------------------------------------

# 兼容导出：schema 侧对外仍叫 suggest_weather_code（返回紧凑编码，如 11B2002）
suggest_weather_code = suggest_compact_weather_code


# 编排字段属于 SimulationStep 顶层字段，不参与 params 必填校验。
# 注意：time_offset_seconds 已下沉为 params 普通字段（兼容顶层旧草稿），
# 因此不再属于编排字段集合。
_ORCHESTRATION_KEYS = {
    "report_num",
    "event_key",
    "is_final",
}


def validate_step_params(step) -> list[str]:
    """校验单步参数是否符合 Schema 约束。

    返回缺失必填字段的 key 列表；空列表表示通过。
    编排字段（报数/事件键/最终报）属于 step 顶层字段，跳过校验。
    """
    entry = get_source_entry(step.source_id)
    if entry is None:
        return [f"invalid_source:{step.source_id}"]
    builder = _FIELD_BUILDERS.get(step.disaster_type)
    if builder is None:
        return [f"invalid_disaster_type:{step.disaster_type}"]
    missing = []
    for field_def in builder(step.source_id):
        if field_def.get("required") and field_def["key"] not in step.params:
            if field_def["key"] in _ORCHESTRATION_KEYS:
                continue
            missing.append(field_def["key"])
    return missing


__all__ = [
    "build_simulation_schema",
    "validate_step_params",
    "suggest_weather_code",
]
