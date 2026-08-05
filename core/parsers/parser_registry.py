"""
统一解析器注册入口。
负责根据数据源目录解析解析器类型，并集中维护解析器名称到解析器类的静态映射。
"""

from __future__ import annotations

# 导入所有具体的解析器子类
from ..sources.source_catalog import SOURCE_CATALOG, get_source_entry
from .china_earthquake_parser import CencEarthquakeParser, CencEarthquakeWolfxParser
from .china_eew_parser import CEAEEWParser, CEAEEWPRParser, CEAEEWWolfxParser
from .china_intensity_report_eqsc_parser import CencIntensityReportEqscParser
from .china_intensity_report_parser import CencIntensityReportParser
from .fssn_cmt_parser import FssnCmtParser
from .global_sources_parser import (
    GlobalQuakeParser,
    ShakeAlertEewParser,
    UsgsEarthquakeParser,
)
from .japan_earthquake_parser import JmaEarthquakeP2PParser, JmaEarthquakeWolfxParser
from .japan_eew_parser import JmaEewFanStudioParser, JmaEewP2PParser, JmaEewWolfxParser
from .snet_parser import SnetParser
from .taiwan_earthquake_parser import CwaReportParser
from .taiwan_eew_parser import CwaEewParser, CwaEewWolfxParser
from .tsunami_parser import JmaTsunamiEqscParser, JmaTsunamiP2PParser, TsunamiParser
from .typhoon_parser import TyphoonParser
from .weather_parser import WeatherAlarmParser

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
    "snet_parser": SnetParser,
    "fssn_cmt_parser": FssnCmtParser,
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

    # 细化分派 1：中国地震预警，按数据源拆分为 FAN Studio、省级网 或 Wolfx 版本
    if entry.parser_name == "china_eew_parser":
        parser_class = {
            "cea_fanstudio": CEAEEWParser,
            "cea_pr_fanstudio": CEAEEWPRParser,
            "cea_wolfx": CEAEEWWolfxParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 2：气象预警，按数据源注入正确的 source_id
    if entry.parser_name == "weather_alarm_parser":
        # message_logger 作为位置参数传入，需显式提取避免与 source_id 冲突
        message_logger = args[0] if args else kwargs.get("message_logger")
        return WeatherAlarmParser(source_id=source_id, message_logger=message_logger)

    # 细化分派 3：日本地震预警，拆分为 FAN Studio、P2P 还是 Wolfx 接收版本
    if entry.parser_name == "japan_eew_parser":
        parser_class = {
            "jma_fanstudio": JmaEewFanStudioParser,
            "jma_p2p": JmaEewP2PParser,
            "jma_wolfx": JmaEewWolfxParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 4：日本地震情报，拆分为 P2P 还是 Wolfx 地震列表版本
    if entry.parser_name == "japan_report_parser":
        parser_class = {
            "jma_p2p_info": JmaEarthquakeP2PParser,
            "jma_wolfx_info": JmaEarthquakeWolfxParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 5：中国地震台网地震测定，拆分为 FAN Studio 还是 Wolfx 接收版本
    if entry.parser_name == "china_report_parser":
        parser_class = {
            "cenc_fanstudio": CencEarthquakeParser,
            "cenc_wolfx": CencEarthquakeWolfxParser,
        }.get(source_id)
        if parser_class is None:
            return None
        return parser_class(*args, **kwargs)

    # 细化分派 6：台湾地震预警，拆分为 FAN Studio 还是 Wolfx 接收版本
    if entry.parser_name == "taiwan_eew_parser":
        parser_class = {
            "cwa_fanstudio": CwaEewParser,
            "cwa_wolfx": CwaEewWolfxParser,
        }.get(source_id)
        if parser_class is None:
            return None
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
