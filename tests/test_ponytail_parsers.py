"""Pin the source_id to parser class dispatch of the parser registry."""

from __future__ import annotations

import importlib

importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
_PKG = "astrbot_plugin_disaster_warning.core."
registry = importlib.import_module(_PKG + "parsers.parser_registry")
catalog = importlib.import_module(_PKG + "sources.source_catalog")

GOLDEN = {
    "cea_fanstudio": "CEAEEWParser",
    "cea_jianproject": "CeaEewJianProjectParser",
    "cea_pr_fanstudio": "CEAEEWPRParser",
    "cea_wolfx": "CEAEEWWolfxParser",
    "cenc_fanstudio": "CencEarthquakeParser",
    "cenc_ir_eqsc": "CencIntensityReportEqscParser",
    "cenc_ir_fanstudio": "CencIntensityReportParser",
    "cenc_jianproject": "CencEarthquakeJianProjectParser",
    "cenc_wolfx": "CencEarthquakeWolfxParser",
    "china_tsunami_fanstudio": "TsunamiParser",
    "china_tsunami_jianproject": "ChinaTsunamiJianProjectParser",
    "china_weather_fanstudio": "WeatherAlarmParser",
    "china_weather_jianproject": "WeatherAlarmJianProjectParser",
    "cwa_fanstudio": "CwaEewParser",
    "cwa_fanstudio_report": "CwaReportParser",
    "cwa_jianproject": "CwaEewJianProjectParser",
    "cwa_wolfx": "CwaEewWolfxParser",
    "fssn_cmt_fanstudio": "FssnCmtParser",
    "global_quake": "GlobalQuakeParser",
    "jma_eqlist_pancakes": "JmaEqlistPancakesParser",
    "jma_fanstudio": "JmaEewFanStudioParser",
    "jma_jianproject": "JmaEewJianProjectParser",
    "jma_p2p": "JmaEewP2PParser",
    "jma_p2p_info": "JmaEarthquakeP2PParser",
    "jma_pancakes": "JmaEewPancakesParser",
    "jma_tsunami_eqsc": "JmaTsunamiEqscParser",
    "jma_tsunami_p2p": "JmaTsunamiP2PParser",
    "jma_wolfx": "JmaEewWolfxParser",
    "jma_wolfx_info": "JmaEarthquakeWolfxParser",
    "sa_fanstudio": "ShakeAlertEewParser",
    "snet_msil": "SnetParser",
    "typhoon_eqsc": None,
    "typhoon_fanstudio": "TyphoonParser",
    "usgs_fanstudio": "UsgsEarthquakeParser",
    "usgs_jianproject": "UsgsEarthquakeJianProjectParser",
    "usgs_pancakes": "UsgsPancakesParser",
}


def test_dispatch_matches_golden():
    """Check that each catalog source gets the same parser class as before the refactor."""
    actual = {}
    for source_id in catalog.SOURCE_CATALOG:
        parser = registry.create_parser_for_source(source_id)
        actual[source_id] = None if parser is None else type(parser).__name__
    assert actual == GOLDEN


def test_unknown_source_is_none():
    """Check that an unknown source gives no parser."""
    assert registry.create_parser_for_source("no_such_source") is None
