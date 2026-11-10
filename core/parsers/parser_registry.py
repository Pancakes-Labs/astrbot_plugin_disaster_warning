"""
统一解析器注册入口。
负责根据数据源目录解析解析器类型，并集中维护解析器名称到解析器类的静态映射。
"""

from __future__ import annotations

# 导入所有具体的解析器子类
from ..sources.source_catalog import SOURCE_CATALOG, get_source_entry
from .china_earthquake_parser import (
    CencEarthquakeJianProjectParser,
    CencEarthquakeWolfxParser,
)
from .china_eew_parser import (
    CeaEewJianProjectParser,
    CEAEEWWolfxParser,
)
from .china_intensity_report_eqsc_parser import CencIntensityReportEqscParser
from .global_sources_parser import (
    GlobalQuakeParser,
    UsgsEarthquakeJianProjectParser,
    UsgsPancakesParser,
)
from .japan_earthquake_parser import (
    JmaEarthquakeP2PParser,
    JmaEarthquakeWolfxParser,
    JmaEqlistPancakesParser,
)
from .japan_eew_parser import (
    JmaEewJianProjectParser,
    JmaEewP2PParser,
    JmaEewPancakesParser,
    JmaEewWolfxParser,
)
from .jian_project_extra_parsers import (
    BeijingReportJianProjectParser,
    CeaPrEewJianProjectParser,
    CwaReportJianProjectParser,
    EmscReportJianProjectParser,
    JmaLpgmJianProjectParser,
    JmaReportJianProjectParser,
    JmaTsunamiJianProjectParser,
    NingxiaReportJianProjectParser,
    SaEewJianProjectParser,
    ShanxiReportJianProjectParser,
    YunnanReportJianProjectParser,
)
from .snet_parser import SnetParser
from .taiwan_eew_parser import CwaEewJianProjectParser, CwaEewWolfxParser
from .tsunami_parser import (
    ChinaTsunamiJianProjectParser,
    JmaTsunamiEqscParser,
    JmaTsunamiP2PParser,
)
from .weather_parser import WeatherAlarmJianProjectParser

# 静态映射：配置中的解析器名(parser_name) -> 数据源解析器类(Parser Class)
# 同名多源由 create_parser_for_source 再按 source_id 细化分派。
PARSER_CLASS_BY_NAME = {
    "china_eew_parser": CeaEewJianProjectParser,
    "china_report_parser": CencEarthquakeJianProjectParser,
    "china_intensity_report_eqsc_parser": CencIntensityReportEqscParser,
    "taiwan_eew_parser": CwaEewJianProjectParser,
    "taiwan_report_parser": CwaReportJianProjectParser,
    "japan_eew_parser": JmaEewJianProjectParser,
    "japan_report_parser": JmaEarthquakeP2PParser,
    "china_tsunami_parser": ChinaTsunamiJianProjectParser,
    "japan_tsunami_parser": JmaTsunamiP2PParser,
    "japan_tsunami_eqsc_parser": JmaTsunamiEqscParser,
    "jma_tsunami_jian_parser": JmaTsunamiJianProjectParser,
    "weather_alarm_parser": WeatherAlarmJianProjectParser,
    "global_report_parser": UsgsEarthquakeJianProjectParser,
    "shakealert_eew_parser": SaEewJianProjectParser,
    "global_quake_parser": GlobalQuakeParser,
    "jma_pancakes_parser": JmaEewPancakesParser,
    "jma_eqlist_pancakes_parser": JmaEqlistPancakesParser,
    "usgs_pancakes_parser": UsgsPancakesParser,
    "snet_parser": SnetParser,
    "regional_report_parser": YunnanReportJianProjectParser,
    "jma_lpgm_parser": JmaLpgmJianProjectParser,
    "emsc_report_parser": EmscReportJianProjectParser,
}


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

    # 细化分派 1：中国地震预警
    if entry.parser_name == "china_eew_parser":
        parser_class = {
            "cea_wolfx": CEAEEWWolfxParser,
            "cea_jianproject": CeaEewJianProjectParser,
            "cea_pr_jianproject": CeaPrEewJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 2：气象预警
    if entry.parser_name == "weather_alarm_parser":
        if source_id == "china_weather_jianproject":
            return WeatherAlarmJianProjectParser(*args, **kwargs)
        return None

    # 细化分派 3：日本地震预警
    if entry.parser_name == "japan_eew_parser":
        parser_class = {
            "jma_p2p": JmaEewP2PParser,
            "jma_wolfx": JmaEewWolfxParser,
            "jma_jianproject": JmaEewJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 4：日本地震情报
    if entry.parser_name == "japan_report_parser":
        parser_class = {
            "jma_p2p_info": JmaEarthquakeP2PParser,
            "jma_wolfx_info": JmaEarthquakeWolfxParser,
            "jma_report_jianproject": JmaReportJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 5：中国地震台网测定
    if entry.parser_name == "china_report_parser":
        parser_class = {
            "cenc_wolfx": CencEarthquakeWolfxParser,
            "cenc_jianproject": CencEarthquakeJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 6：台湾地震预警
    if entry.parser_name == "taiwan_eew_parser":
        parser_class = {
            "cwa_wolfx": CwaEewWolfxParser,
            "cwa_jianproject": CwaEewJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 7：台湾地震速报
    if entry.parser_name == "taiwan_report_parser":
        if source_id == "cwa_report_jianproject":
            return CwaReportJianProjectParser(*args, **kwargs)
        return None

    # 细化分派 8：中国海啸
    if entry.parser_name == "china_tsunami_parser":
        if source_id == "china_tsunami_jianproject":
            return ChinaTsunamiJianProjectParser(*args, **kwargs)
        return None

    # 细化分派 9：全球地震测定
    if entry.parser_name == "global_report_parser":
        if source_id == "usgs_jianproject":
            return UsgsEarthquakeJianProjectParser(*args, **kwargs)
        return None

    # 细化分派 10：ShakeAlert
    if entry.parser_name == "shakealert_eew_parser":
        if source_id == "sa_jianproject":
            return SaEewJianProjectParser(*args, **kwargs)
        return None

    # 细化分派 11：地方局速报
    if entry.parser_name == "regional_report_parser":
        parser_class = {
            "yunnan_jianproject": YunnanReportJianProjectParser,
            "shanxi_jianproject": ShanxiReportJianProjectParser,
            "beijing_jianproject": BeijingReportJianProjectParser,
            "ningxia_jianproject": NingxiaReportJianProjectParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 12：JMA 长周期 / EMSC / JMA 海啸 Jian
    if entry.parser_name == "jma_lpgm_parser":
        if source_id == "jma_lpgm_jianproject":
            return JmaLpgmJianProjectParser(*args, **kwargs)
        return None
    if entry.parser_name == "emsc_report_parser":
        if source_id == "emsc_jianproject":
            return EmscReportJianProjectParser(*args, **kwargs)
        return None
    if entry.parser_name == "jma_tsunami_jian_parser":
        if source_id == "jma_tsunami_jianproject":
            return JmaTsunamiJianProjectParser(*args, **kwargs)
        return None

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
    missing_names = {
        entry.parser_name
        for entry in SOURCE_CATALOG.values()
        if (entry.parser_name or "").strip()
        and entry.parser_name not in PARSER_CLASS_BY_NAME
    }
    if missing_names:
        missing_text = ", ".join(sorted(missing_names))
        raise RuntimeError(
            f"未注册的 parser_name: {missing_text}；请检查 source_catalog 与 parser_registry 的一致性"
        )
