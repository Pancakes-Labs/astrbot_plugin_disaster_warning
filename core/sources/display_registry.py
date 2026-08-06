"""
数据源展示名称统一注册表（事实层）。

设计原则：事实层 + 场景投影层 分离。
- 事实层（本文件）：同一份数据只在唯一位置维护，供所有模块读取。
  · SOURCE_ALIAS_MAP       数据源历史别名归一化表（alias -> 规范 source_id）
  · SOURCE_DISPLAY_MAP     数据源通道展示名表（source_id -> 完整展示名）
  · CONNECTION_DISPLAY_NAMES  物理连接组展示名表（连接组 key -> 展示名）
  · CONNECTION_GROUP_ORDER    连接组展示顺序
  · CONNECTION_GROUP_ALIAS    提供方家族 -> 连接组 key
  · DISPLAY_NAME_ALIASES      展示名 -> 连接组 key 的反向别名（历史兼容）

- 场景投影层：banner / 离线通知 / 管理命令 / 前端子源列表等场景会在事实
  基础上追加自己的展示规则（加后缀、折叠到通道粒度、剥通道后缀等）。
  这些投影属于有意设计，保留在各消费点，并注明与事实层的对应关系，
  修改事实层时只需检查各投影点是否需要同步调整。

历史背景：
- 连接组展示名曾散落各个模块，内容完全一致却各自维护，改动容易漏改；现统一收编。
- 历史别名表与展示名表曾内联在 core/storage/source_compat.py，现迁移至此，
  source_compat.py 保留为兼容层门面（通过别名导入继续暴露同名数据）。

同步契约：
- 前端启动时经管理端 /api/sources/meta 动态拉取，仅保留极少量场景投影覆盖
- 修改本文件别名或展示名时，后端会自动生效，前端无需改动。
（新 key 自动补全；既有 key 若与投影覆盖冲突，以前端覆盖为准，修改时请核对前端覆盖表）
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. 数据源历史别名归一化表
# ---------------------------------------------------------------------------
# 把旧来源名、展示名和外部兼容名统一折叠到规范 source_id。
# 包含各种历史插件版本产生的 key 以及 WebSocket 连接中发送的 label。
SOURCE_ALIAS_MAP: dict[str, str] = {
    "fan_studio_cenc": "cenc_fanstudio",
    "fan_studio_cenc_ir": "cenc_ir_fanstudio",
    "fan_studio_cea": "cea_fanstudio",
    "fan_studio_cea_pr": "cea_pr_fanstudio",
    "fan_studio_cwa": "cwa_fanstudio",
    "fan_studio_cwa_report": "cwa_fanstudio_report",
    "fan_studio_usgs": "usgs_fanstudio",
    "fan_studio_fssn_cmt": "fssn_cmt_fanstudio",
    "fssn-cmt": "fssn_cmt_fanstudio",
    "fssn_cmt": "fssn_cmt_fanstudio",
    "fan_studio_sa": "sa_fanstudio",
    "fan_studio_jma": "jma_fanstudio",
    "fan_studio_weather": "china_weather_fanstudio",
    "fan_studio_tsunami": "china_tsunami_fanstudio",
    "p2p_eew": "jma_p2p",
    "p2p_earthquake": "jma_p2p_info",
    "p2p_tsunami": "jma_tsunami_p2p",
    "eqsc_tsunami": "jma_tsunami_eqsc",
    "eqsc_typhoon": "typhoon_eqsc",
    "eqsc_cenc_ir": "cenc_ir_eqsc",
    "cenc_ir_eqsc": "cenc_ir_eqsc",
    "eqsc_intensity_report": "cenc_ir_eqsc",
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
    "openquake_cma": "china_weather_openquake",
    "cma_weather": "china_weather_openquake",
    "cma": "china_weather_openquake",
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

# ---------------------------------------------------------------------------
# 2. 数据源通道展示名表
# ---------------------------------------------------------------------------
# 把内部规范 key 转回更友好的前端展示标签。
# 注意：这是"通道级完整展示名"；机构级短名（如"中国地震预警网"）由
# source_catalog.py 的 SourceEntry.institution_display_name 维护，两者粒度不同。
SOURCE_DISPLAY_MAP: dict[str, str] = {
    "cenc_fanstudio": "中国地震台网 (CENC) - Fan",
    "cenc_ir_fanstudio": "中国地震台网 (CENC) - 烈度速报 - Fan",
    "cenc_ir_eqsc": "中国地震台网 (CENC) - 烈度速报 - EQSC",
    "cea_fanstudio": "中国地震预警网 (CEA)",
    "cea_pr_fanstudio": "中国地震预警网 (省级)",
    "cwa_fanstudio": "台湾中央气象署: 强震即时警报 - Fan",
    "cwa_fanstudio_report": "台湾中央气象署: 地震报告",
    "usgs_fanstudio": "美国地质调查局 (USGS)",
    "fssn_cmt_fanstudio": "FSSN 矩心矩张量解 (CMT)",
    "sa_fanstudio": "美国 ShakeAlert 地震预警",
    "jma_fanstudio": "日本气象厅: 紧急地震速报 - Fan",
    "china_weather_fanstudio": "中国气象局: 气象预警 - Fan",
    "china_weather_openquake": "中国气象局: 气象预警 - OQ",
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
    "unknown": "未知来源",
}

# ---------------------------------------------------------------------------
# 3. 物理连接组展示名表
# ---------------------------------------------------------------------------
# 供管理后台、健康监控、连接载荷构建、离线通知等场景使用。
CONNECTION_DISPLAY_NAMES: dict[str, str] = {
    "fan_studio_all": "FAN Studio",
    "fan_studio_cenc_ir": "FAN Studio（烈度速报）",
    "p2p_main": "P2P地震情報",
    "wolfx_all": "Wolfx",
    "openquake_api": "OpenQuakeAPI",
    "snet_msil": "NIED S-Net",
    "eqsc": "EQSC API",
}

# ---------------------------------------------------------------------------
# 4. 连接组展示顺序
# ---------------------------------------------------------------------------
# 与 ConnectionsGrid 列语义对齐，供健康监控按固定顺序遍历。
CONNECTION_GROUP_ORDER: tuple[str, ...] = (
    "fan_studio_all",
    "fan_studio_cenc_ir",
    "p2p_main",
    "wolfx_all",
    "openquake_api",
    "snet_msil",
    "eqsc",
)

# ---------------------------------------------------------------------------
# 5. 提供方家族 -> 连接组 key
# ---------------------------------------------------------------------------
# 供 SourceRuntimeQueryService 在数据源未显式声明 connection_group 时，
# 按 provider_family 解析默认连接分组。
CONNECTION_GROUP_ALIAS: dict[str, str] = {
    "fan_studio": "fan_studio_all",
    "p2p": "p2p_main",
    "wolfx": "wolfx_all",
    "global_quake": "openquake_api",
    "direct_http": "snet_msil",
}

# ---------------------------------------------------------------------------
# 6. 展示名 -> 连接组 key 的反向别名（历史兼容）
# ---------------------------------------------------------------------------
# ConnectionsPayloadBuilder / SourceRuntimeQuery 可能使用的展示名别名，
# 包含历史上出现过的括号/空格写法，用于把展示名归一化回连接组 key。
DISPLAY_NAME_ALIASES: dict[str, str] = {
    "FAN Studio": "fan_studio_all",
    "FAN Studio 烈度速报": "fan_studio_cenc_ir",
    "FAN Studio（烈度速报）": "fan_studio_cenc_ir",
    "Fan Studio（烈度速报）": "fan_studio_cenc_ir",
    "P2P地震情報": "p2p_main",
    "Wolfx": "wolfx_all",
    "OpenQuakeAPI": "openquake_api",
    "NIED S-Net": "snet_msil",
    "EQSC API": "eqsc",
}


__all__ = [
    "SOURCE_ALIAS_MAP",
    "SOURCE_DISPLAY_MAP",
    "CONNECTION_DISPLAY_NAMES",
    "CONNECTION_GROUP_ORDER",
    "CONNECTION_GROUP_ALIAS",
    "DISPLAY_NAME_ALIASES",
]
