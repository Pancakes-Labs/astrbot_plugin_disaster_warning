"""
统一解析器注册入口。
负责根据数据源目录解析解析器类型，并集中维护解析器名称到解析器类的静态映射。
"""

from __future__ import annotations

# 导入所有具体的解析器子类
from ..sources.source_catalog import SOURCE_CATALOG, get_source_entry
from .china_earthquake_parser import (
    CencEarthquakeJianProjectParser,
    CencEarthquakeParser,
    CencEarthquakeWolfxParser,
)
from .china_eew_parser import (
    CeaEewJianProjectParser,
    CEAEEWParser,
    CEAEEWPRParser,
    CEAEEWWolfxParser,
)
from .china_intensity_report_eqsc_parser import CencIntensityReportEqscParser
from .china_intensity_report_parser import CencIntensityReportParser
from .fssn_cmt_parser import FssnCmtParser
from .global_sources_parser import (
    GlobalQuakeParser,
    ShakeAlertEewParser,
    UsgsEarthquakeJianProjectParser,
    UsgsEarthquakeParser,
    UsgsPancakesParser,
)
from .japan_earthquake_parser import (
    JmaEarthquakeP2PParser,
    JmaEarthquakeWolfxParser,
    JmaEqlistPancakesParser,
)
from .japan_eew_parser import (
    JmaEewFanStudioParser,
    JmaEewJianProjectParser,
    JmaEewP2PParser,
    JmaEewPancakesParser,
    JmaEewWolfxParser,
)
from .snet_parser import SnetParser
from .taiwan_earthquake_parser import CwaReportParser
from .taiwan_eew_parser import CwaEewJianProjectParser, CwaEewParser, CwaEewWolfxParser
from .tsunami_parser import (
    ChinaTsunamiJianProjectParser,
    JmaTsunamiEqscParser,
    JmaTsunamiP2PParser,
    TsunamiParser,
)
from .typhoon_parser import TyphoonParser
from .weather_parser import WeatherAlarmJianProjectParser, WeatherAlarmParser

# 静态映射：配置中的解析器名(parser_name) -> 数据源解析器类(Parser Class)
PARSER_CLASS_BY_NAME = {
    "china_eew_parser": CEAEEWParser,
    "china_report_parser": CencEarthquakeParser,
    "china_intensity_report_parser": CencIntensityReportParser,
    "china_intensity_report_eqsc_parser": CencIntensityReportEqscParser,
    "taiwan_eew_parser": CwaEewParser,
    "taiwan_report_parser": CwaReportParser,
    "japan_eew_parser": JmaEewFanStudioParser,
    "japan_report_parser": JmaEarthquakeP2PParser,
    "china_tsunami_parser": TsunamiParser,
    "japan_tsunami_parser": JmaTsunamiP2PParser,
    "japan_tsunami_eqsc_parser": JmaTsunamiEqscParser,
    "weather_alarm_parser": WeatherAlarmParser,
    "typhoon_parser": TyphoonParser,
    "global_report_parser": UsgsEarthquakeParser,
    "shakealert_eew_parser": ShakeAlertEewParser,
    "global_quake_parser": GlobalQuakeParser,
    "jma_pancakes_parser": JmaEewPancakesParser,
    "jma_eqlist_pancakes_parser": JmaEqlistPancakesParser,
    "usgs_pancakes_parser": UsgsPancakesParser,
    "snet_parser": SnetParser,
    "fssn_cmt_parser": FssnCmtParser,
}


# parser_name -> {source_id: parser class}. A source that is not in the table gets no parser.
_STRICT_PARSER_CLASS_BY_SOURCE = {
    # 中国地震预警：FAN Studio、省级网、Wolfx、Jian Project
    "china_eew_parser": {
        "cea_fanstudio": CEAEEWParser,
        "cea_pr_fanstudio": CEAEEWPRParser,
        "cea_wolfx": CEAEEWWolfxParser,
        "cea_jianproject": CeaEewJianProjectParser,
    },
    # 日本地震预警：FAN Studio、P2P、Wolfx、Jian Project
    "japan_eew_parser": {
        "jma_fanstudio": JmaEewFanStudioParser,
        "jma_p2p": JmaEewP2PParser,
        "jma_wolfx": JmaEewWolfxParser,
        "jma_jianproject": JmaEewJianProjectParser,
    },
    # 日本地震情报：P2P、Wolfx
    "japan_report_parser": {
        "jma_p2p_info": JmaEarthquakeP2PParser,
        "jma_wolfx_info": JmaEarthquakeWolfxParser,
    },
    # 中国地震台网地震测定：FAN Studio、Wolfx、Jian Project
    "china_report_parser": {
        "cenc_fanstudio": CencEarthquakeParser,
        "cenc_wolfx": CencEarthquakeWolfxParser,
        "cenc_jianproject": CencEarthquakeJianProjectParser,
    },
    # 台湾地震预警：FAN Studio、Wolfx、Jian Project
    "taiwan_eew_parser": {
        "cwa_fanstudio": CwaEewParser,
        "cwa_wolfx": CwaEewWolfxParser,
        "cwa_jianproject": CwaEewJianProjectParser,
    },
}

# parser_name -> {source_id: parser class}. A source that is not in the table uses the default class.
_OVERRIDE_PARSER_CLASS_BY_SOURCE = {
    "china_tsunami_parser": {
        "china_tsunami_jianproject": ChinaTsunamiJianProjectParser
    },
    "global_report_parser": {"usgs_jianproject": UsgsEarthquakeJianProjectParser},
}


# Owner of each extension parser: parser_name -> extension id.
_PARSER_OWNERS: dict[str, str] = {}


def register_parser(parser_name: str, parser_class, *, owner: str) -> None:
    """Register a parser of an extension. Raise ValueError if parser_name exists."""
    if parser_name in PARSER_CLASS_BY_NAME:
        raise ValueError(f"解析器 {parser_name} 已存在，无法重复注册")
    PARSER_CLASS_BY_NAME[parser_name] = parser_class
    _PARSER_OWNERS[parser_name] = owner


def unregister_parsers(owner: str) -> None:
    """Remove all parsers that an extension registered."""
    for parser_name in [n for n, o in _PARSER_OWNERS.items() if o == owner]:
        PARSER_CLASS_BY_NAME.pop(parser_name, None)
        del _PARSER_OWNERS[parser_name]


def resolve_parser_class(parser_name: str):
    """按解析器名称解析对应的解析器类。"""
    normalized_name = (parser_name or "").strip()
    if not normalized_name:
        return None
    return PARSER_CLASS_BY_NAME.get(normalized_name)


def create_parser_for_source(source_id: str, *args, **kwargs):
    """按数据源标识创建解析器实例。"""
    entry = get_source_entry(source_id)
    if entry is None:
        return None

    # 细化分派 2：气象预警，按数据源注入正确的 source_id 或专用解析器
    if entry.parser_name == "weather_alarm_parser":
        if source_id == "china_weather_jianproject":
            return WeatherAlarmJianProjectParser(*args, **kwargs)
        # message_logger 作为位置参数传入，需显式提取避免与 source_id 冲突
        message_logger = args[0] if args else kwargs.get("message_logger")
        return WeatherAlarmParser(source_id=source_id, message_logger=message_logger)

    # Sources of these parsers must be in the table. An unknown source gets no parser.
    strict_classes = _STRICT_PARSER_CLASS_BY_SOURCE.get(entry.parser_name)
    if strict_classes is not None:
        parser_class = strict_classes.get(source_id)
        return parser_class(*args, **kwargs) if parser_class else None

    # A source can use its own class. Other sources use the class of the parser name.
    parser_class = _OVERRIDE_PARSER_CLASS_BY_SOURCE.get(entry.parser_name, {}).get(
        source_id
    )
    if parser_class is not None:
        return parser_class(*args, **kwargs)

    # 其他无细化子类分发的常规数据源解析器，直接按映射返回
    parser_class = resolve_parser_class(entry.parser_name)
    if parser_class is None:
        return None
    return parser_class(*args, **kwargs)


def validate_catalog_parser_names() -> None:
    """校验数据源目录中引用的解析器名称都能被解析。

    空 parser_name 表示该源不通过消息解析器接入（例如 EQSC 轮询源，
    事件由轮询服务直接构建），属于合法状态，跳过校验。
    """
    # 抽取静态数据源配置名录中所有引用的解析器名称，与已注册的解析器表做一致性对齐
    missing_names = {
        entry.parser_name
        for entry in SOURCE_CATALOG.values()
        if (entry.parser_name or "").strip()
        and entry.parser_name not in PARSER_CLASS_BY_NAME
    }
    # 若存在名录中有定义，但实际上解析器静态字典内未注册的配置，强行抛出运行时异常
    if missing_names:
        missing_text = ", ".join(sorted(missing_names))
        raise RuntimeError(
            f"未注册的 parser_name: {missing_text}；请检查 source_catalog 与 parser_registry 的一致性"
        )
