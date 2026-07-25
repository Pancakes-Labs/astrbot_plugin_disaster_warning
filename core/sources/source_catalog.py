"""
统一数据源注册中心。

当前文件只保留数据源描述与基础索引；
机构视图等查询职责已迁出，避免目录再次膨胀为兼容仓库。
"""

from __future__ import annotations

from .source_entry import FusionRole, ProviderFamily, SourceEntry, SourceType

# 统一数据源注册表目录，保存了系统中所有支持接入的数据源及其配置、路由和展示的元数据。
SOURCE_CATALOG: dict[str, SourceEntry] = {
    # cea_fanstudio: 中国地震预警网 - 来自 FAN Studio
    "cea_fanstudio": SourceEntry(
        source_id="cea_fanstudio",
        source_enum="fan_studio_cea",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_earthquake_warning",
        parser_name="china_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="cea_eew",
        report_policy="cea_cwa",
        intensity_mode="intensity",  # 使用中国烈度标准
        priority=1,
        display_name="中国地震预警网",
        description="中国地震预警网（CEA）- FAN Studio WebSocket",
        default_timezone="Asia/Shanghai",
        publish_time_field="create_time",
        report_num_field="updates",
        fingerprint_prefix="cea",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        institution_key="china",
        institution_display_name="中国地震预警网 EEW",
        institution_active_name="中国地震预警网",
        query_group="eew",
        dispatch_family="fan_studio_eew",
        provider_source_names=("cea",),
        provider_aliases=("fan_studio_cea", "cea"),
        routing_tags=("fan_studio", "china", "eew"),
        payload_signatures=(("epiIntensity", "eventId", "updates"),),
        payload_exclusions=(("province",),),
    ),
    # cea_pr_fanstudio: 中国地震预警网(省级) - 提供省级细颗粒度的地震预警推送
    "cea_pr_fanstudio": SourceEntry(
        source_id="cea_pr_fanstudio",
        source_enum="fan_studio_cea_pr",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_earthquake_warning_provincial",
        parser_name="china_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="cea_eew",
        report_policy="cea_cwa",
        intensity_mode="intensity",
        priority=0,
        display_name="中国地震预警网(省级)",
        description="中国地震预警网（CEA）省级 - FAN Studio WebSocket",
        default_timezone="Asia/Shanghai",
        publish_time_field="create_time",
        report_num_field="updates",
        fingerprint_prefix="cea",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        institution_key="china",
        institution_display_name="中国地震预警网 EEW",
        institution_active_name="中国地震预警网",
        query_group="eew",
        dispatch_family="fan_studio_eew",
        provider_source_names=("cea-pr",),
        provider_aliases=("fan_studio_cea_pr", "cea-pr"),
        routing_tags=("fan_studio", "china", "eew", "provincial"),
        payload_signatures=(("epiIntensity", "eventId", "updates", "province"),),
    ),
    # cea_wolfx: 中国地震预警网 - 来自 Wolfx API
    "cea_wolfx": SourceEntry(
        source_id="cea_wolfx",
        source_enum="wolfx_cenc_eew",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.WOLFX,
        config_group="wolfx",
        config_key="china_cenc_eew",
        parser_name="china_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="cea_eew",
        report_policy="cea_cwa",
        intensity_mode="intensity",
        priority=2,
        display_name="中国地震预警网",
        description="中国地震预警网（CEA）- Wolfx API",
        default_timezone="Asia/Shanghai",
        publish_time_field="update_time",
        report_num_field="updates",
        fingerprint_prefix="cea",
        connection_group="wolfx_all",
        connection_handler="wolfx",
        connection_data_source="wolfx_mixed",
        connection_url="wss://ws-api.wolfx.jp/all_eew",
        institution_key="china",
        institution_display_name="中国地震预警网 EEW",
        institution_active_name="中国地震预警网",
        query_group="eew",
        dispatch_family="wolfx_eew",
        provider_message_types=("cenc_eew", "sc_eew", "fj_eew"),
        provider_aliases=("wolfx_cenc_eew", "cenc_eew", "sc_eew", "fj_eew"),
        routing_tags=("wolfx", "china", "eew"),
    ),
    # cwa_fanstudio: 台湾中央气象署地震预警 - 来自 FAN Studio
    "cwa_fanstudio": SourceEntry(
        source_id="cwa_fanstudio",
        source_enum="fan_studio_cwa",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="taiwan_cwa_earthquake",
        parser_name="taiwan_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="cwa_eew",
        report_policy="cea_cwa",
        intensity_mode="scale",  # 台湾地震震度制式
        priority=1,
        display_name="台湾中央气象署",
        description="台湾中央气象署地震预警（CWA）- FAN Studio WebSocket",
        default_timezone="Asia/Taipei",
        publish_time_field="shockTime",
        report_num_field="updates",
        fingerprint_prefix="cwa",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        institution_key="taiwan",
        institution_display_name="中央氣象署 EEW",
        institution_active_name="中央氣象署",
        query_group="eew",
        fusion_group="cwa_scale",
        fusion_role=FusionRole.SECONDARY,
        dispatch_family="fan_studio_eew",
        provider_source_names=("cwa-eew",),
        provider_aliases=("fan_studio_cwa", "cwa-eew"),
        routing_tags=("fan_studio", "taiwan", "eew"),
        payload_signatures=(("shockTime", "updates", "locationDesc"),),
    ),
    # cwa_fanstudio_report: 台湾中央气象署地震报告 - 正式地震报告(包含等震度图等)
    "cwa_fanstudio_report": SourceEntry(
        source_id="cwa_fanstudio_report",
        source_enum="fan_studio_cwa_report",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="taiwan_cwa_report",
        parser_name="taiwan_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="cwa_report",
        report_policy="none",
        intensity_mode="scale",
        priority=1,
        display_name="台湾中央气象署",
        description="台湾中央气象署（CWA）：地震报告 - FAN Studio WebSocket",
        default_timezone="Asia/Taipei",
        publish_time_field="shockTime",
        fingerprint_prefix="cwa_report",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_report",
        provider_source_names=("cwa",),
        provider_aliases=("fan_studio_cwa_report", "cwa"),
        routing_tags=("fan_studio", "taiwan", "report"),
        payload_signatures=(("imageURI", "shockTime"),),
    ),
    # cwa_wolfx: 台湾中央气象署地震预警 - 来自 Wolfx API
    "cwa_wolfx": SourceEntry(
        source_id="cwa_wolfx",
        source_enum="wolfx_cwa_eew",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.WOLFX,
        config_group="wolfx",
        config_key="taiwan_cwa_eew",
        parser_name="taiwan_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="cwa_eew",
        report_policy="cea_cwa",
        intensity_mode="scale",
        priority=2,
        display_name="台湾中央气象署",
        description="台湾中央气象署地震预警（CWA）- Wolfx API",
        default_timezone="Asia/Taipei",
        publish_time_field="shockTime",
        report_num_field="updates",
        fingerprint_prefix="cwa",
        connection_group="wolfx_all",
        connection_handler="wolfx",
        connection_data_source="wolfx_mixed",
        connection_url="wss://ws-api.wolfx.jp/all_eew",
        institution_key="taiwan",
        institution_display_name="中央氣象署 EEW",
        institution_active_name="中央氣象署",
        query_group="eew",
        fusion_group="cwa_scale",
        fusion_role=FusionRole.PRIMARY,
        dispatch_family="wolfx_eew",
        provider_message_types=("cwa_eew",),
        provider_aliases=("wolfx_cwa_eew", "cwa_eew"),
        routing_tags=("wolfx", "taiwan", "eew"),
    ),
    # jma_fanstudio: 日本气象厅紧急地震速报 - 来自 FAN Studio
    "jma_fanstudio": SourceEntry(
        source_id="jma_fanstudio",
        source_enum="fan_studio_jma",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="japan_jma_eew",
        parser_name="japan_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="jma_eew",
        report_policy="jma",  # 日本特定的 EEW 多报次策略
        intensity_mode="scale",  # 日本震度制式
        priority=1,
        display_name="日本气象厅",
        description="日本气象厅：紧急地震速报 - FAN Studio WebSocket",
        default_timezone="Asia/Tokyo",
        publish_time_field="originTime",
        report_num_field="serialNo",
        issue_type_field="info_type",
        fingerprint_prefix="jma",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        institution_key="japan",
        institution_display_name="日本気象庁 EEW",
        institution_active_name="日本気象庁",
        query_group="eew",
        dispatch_family="fan_studio_eew",
        provider_source_names=("jma",),
        provider_aliases=("fan_studio_jma", "jma"),
        routing_tags=("fan_studio", "japan", "eew"),
        payload_signatures=(("infoTypeName", "final", "epiIntensity"),),
    ),
    # jma_p2p: 日本气象厅紧急地震速报 - P2P 地震情报
    "jma_p2p": SourceEntry(
        source_id="jma_p2p",
        source_enum="p2p_eew",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.P2P,
        config_group="p2p_earthquake",
        config_key="japan_jma_eew",
        parser_name="japan_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="jma_eew",
        report_policy="jma",
        intensity_mode="scale",
        priority=1,
        display_name="日本气象厅",
        description="日本气象厅：紧急地震速报 - P2P地震情报 WebSocket",
        default_timezone="Asia/Tokyo",
        publish_time_field="originTime",
        report_num_field="serialNo",
        issue_type_field="issueType",
        fingerprint_prefix="jma",
        connection_group="p2p_main",
        connection_handler="p2p",
        connection_data_source="jma_p2p",
        connection_url="wss://api.p2pquake.net/v2/ws",
        institution_key="japan",
        institution_display_name="日本気象庁 EEW",
        institution_active_name="日本気象庁",
        query_group="eew",
        dispatch_family="p2p_eew",
        provider_message_types=("556",),
        provider_aliases=("p2p_eew",),
        routing_tags=("p2p", "japan", "eew"),
    ),
    # jma_wolfx: 日本气象厅紧急地震速报 - 来自 Wolfx API
    "jma_wolfx": SourceEntry(
        source_id="jma_wolfx",
        source_enum="wolfx_jma_eew",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.WOLFX,
        config_group="wolfx",
        config_key="japan_jma_eew",
        parser_name="japan_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="jma_eew",
        report_policy="jma",
        intensity_mode="scale",
        priority=2,
        display_name="日本气象厅",
        description="日本气象厅：紧急地震速报 - Wolfx API",
        default_timezone="Asia/Tokyo",
        publish_time_field="originTime",
        report_num_field="serialNo",
        issue_type_field="issueType",
        fingerprint_prefix="jma",
        connection_group="wolfx_all",
        connection_handler="wolfx",
        connection_data_source="wolfx_mixed",
        connection_url="wss://ws-api.wolfx.jp/all_eew",
        institution_key="japan",
        institution_display_name="日本気象庁 EEW",
        institution_active_name="日本気象庁",
        query_group="eew",
        dispatch_family="wolfx_eew",
        provider_message_types=("jma_eew",),
        provider_aliases=("wolfx_jma_eew", "jma_eew"),
        routing_tags=("wolfx", "japan", "eew"),
    ),
    # global_quake: 全球地震监测模拟系统客户端推送源 (自建或官方 WebSocket 地址)
    "global_quake": SourceEntry(
        source_id="global_quake",
        source_enum="global_quake",
        source_type=SourceType.EARTHQUAKE_WARNING,
        provider_family=ProviderFamily.GLOBAL_QUAKE,
        config_group="global_quake",
        config_key="enabled",
        parser_name="global_quake_parser",
        presentation_type="global_quake",
        text_presenter_key="global_quake",
        report_policy="global_quake",
        intensity_mode="intensity",
        priority=3,
        display_name="Global Quake",
        description="Global Quake 服务器推送 - WebSocket连接",
        default_timezone="UTC",
        publish_time_field="update_time",
        report_num_field="report_num",
        fingerprint_prefix="gq",
        connection_group="global_quake",
        connection_handler="global_quake",
        connection_data_source="global_quake",
        connection_url="wss://gqm.aloys23.link/ws",
        dispatch_family="global_quake",
        provider_aliases=("global_quake",),
        routing_tags=("global_quake", "global", "eew"),
    ),
    # cenc_fanstudio: 中国地震台网地震测定数据 - 来自 FAN Studio
    "cenc_fanstudio": SourceEntry(
        source_id="cenc_fanstudio",
        source_enum="fan_studio_cenc",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_cenc_earthquake",
        parser_name="china_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="cenc_report",
        report_policy="none",
        intensity_mode="intensity",
        priority=1,
        display_name="中国地震台网",
        description="中国地震台网（CENC）：地震测定 - FAN Studio WebSocket",
        default_timezone="Asia/Shanghai",
        publish_time_field="update_time",
        report_num_field="report_num",
        fingerprint_prefix="cenc",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        fusion_group="cenc_intensity",
        fusion_role=FusionRole.SECONDARY,
        dispatch_family="fan_studio_report",
        provider_source_names=("cenc",),
        provider_aliases=("fan_studio_cenc", "cenc"),
        routing_tags=("fan_studio", "china", "report"),
        payload_signatures=(("infoTypeName",),),
        payload_predicates=("cenc_report",),
    ),
    # cenc_ir_fanstudio: 中国地震台网烈度速报 - 来自 FAN Studio 独立路径 /cenc-ir
    # 注意：/all 默认不包含本源，必须单独建立 WebSocket 连接。
    "cenc_ir_fanstudio": SourceEntry(
        source_id="cenc_ir_fanstudio",
        source_enum="fan_studio_cenc_ir",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_cenc_intensity_report",
        parser_name="china_intensity_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="cenc_ir_report",
        report_policy="none",
        intensity_mode="intensity",
        priority=0,
        display_name="中国地震台网烈度速报",
        description="中国地震台网（CENC）：烈度速报 - FAN Studio WebSocket /cenc-ir",
        default_timezone="Asia/Shanghai",
        event_time_field="occurred_at",
        publish_time_field="gmtCreate",
        fingerprint_prefix="cenc_ir",
        connection_group="fan_studio_cenc_ir",
        connection_handler="fan_studio",
        connection_data_source="cenc_ir_fanstudio",
        connection_url="wss://ws.fanstudio.tech/cenc-ir",
        connection_backup_url="wss://ws.fanstudio.hk/cenc-ir",
        institution_key="china",
        institution_display_name="中国地震台网",
        institution_active_name="中国地震台网",
        dispatch_family="fan_studio_report",
        provider_source_names=("cenc-ir",),
        provider_aliases=(
            "fan_studio_cenc_ir",
            "cenc-ir",
            "cenc_ir",
            "china_cenc_intensity_report",
        ),
        routing_tags=("fan_studio", "china", "report", "intensity"),
        payload_signatures=(
            ("uniEventId", "intensity_info_text"),
            ("uniEventId", "instrument_intensity_json"),
            ("uniEventId", "contour_geojson"),
            ("uniEventId", "nameByInfo"),
        ),
        payload_predicates=("cenc_intensity_report",),
    ),
    # cenc_wolfx: 中国地震台网地震测定数据 - 来自 Wolfx API
    "cenc_wolfx": SourceEntry(
        source_id="cenc_wolfx",
        source_enum="wolfx_cenc_eq",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.WOLFX,
        config_group="wolfx",
        config_key="china_cenc_earthquake",
        parser_name="china_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="cenc_report",
        report_policy="none",
        intensity_mode="intensity",
        priority=2,
        display_name="中国地震台网",
        description="中国地震台网（CENC）：地震测定 - Wolfx API",
        default_timezone="Asia/Shanghai",
        publish_time_field="update_time",
        report_num_field="report_num",
        fingerprint_prefix="cenc",
        connection_group="wolfx_all",
        connection_handler="wolfx",
        connection_data_source="wolfx_mixed",
        connection_url="wss://ws-api.wolfx.jp/all_eew",
        fusion_group="cenc_intensity",
        fusion_role=FusionRole.PRIMARY,
        dispatch_family="wolfx_report",
        provider_message_types=("cenc_eqlist",),
        provider_aliases=("wolfx_cenc_eq", "cenc_eqlist"),
        routing_tags=("wolfx", "china", "report"),
    ),
    # jma_p2p_info: 日本气象厅地震情报报告 - 来自 P2P 地震情报
    "jma_p2p_info": SourceEntry(
        source_id="jma_p2p_info",
        source_enum="p2p_earthquake",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.P2P,
        config_group="p2p_earthquake",
        config_key="japan_jma_earthquake",
        parser_name="japan_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="jma_report",
        report_policy="none",
        intensity_mode="scale",
        priority=1,
        display_name="日本气象厅",
        description="日本气象厅（JMA）：地震情报 - P2P地震情报 WebSocket",
        default_timezone="Asia/Tokyo",
        publish_time_field="time",
        report_num_field="serialNo",
        issue_type_field="issueType",
        fingerprint_prefix="jma_report",
        connection_group="p2p_main",
        connection_handler="p2p",
        connection_data_source="jma_p2p",
        connection_url="wss://api.p2pquake.net/v2/ws",
        dispatch_family="p2p_report",
        provider_message_types=("551",),
        provider_aliases=("p2p_earthquake",),
        routing_tags=("p2p", "japan", "report"),
    ),
    # jma_wolfx_info: 日本气象厅地震情报报告
    "jma_wolfx_info": SourceEntry(
        source_id="jma_wolfx_info",
        source_enum="wolfx_jma_eq",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.WOLFX,
        config_group="wolfx",
        config_key="japan_jma_earthquake",
        parser_name="japan_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="jma_report",
        report_policy="none",
        intensity_mode="scale",
        priority=2,
        display_name="日本气象厅",
        description="日本气象厅（JMA）：地震情报 - Wolfx API",
        default_timezone="Asia/Tokyo",
        publish_time_field="time",
        report_num_field="serialNo",
        issue_type_field="issueType",
        fingerprint_prefix="jma_report",
        connection_group="wolfx_all",
        connection_handler="wolfx",
        connection_data_source="wolfx_mixed",
        connection_url="wss://ws-api.wolfx.jp/all_eew",
        dispatch_family="wolfx_report",
        provider_message_types=("jma_eqlist",),
        provider_aliases=("wolfx_jma_eq", "jma_eqlist"),
        routing_tags=("wolfx", "japan", "report"),
    ),
    # usgs_fanstudio: 美国地质调查局全球地震测定报告
    "usgs_fanstudio": SourceEntry(
        source_id="usgs_fanstudio",
        source_enum="fan_studio_usgs",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="usgs_earthquake",
        parser_name="global_report_parser",
        presentation_type="earthquake_report",
        text_presenter_key="usgs_report",
        report_policy="none",
        intensity_mode="magnitude",  # 默认使用震级单位呈现，因为没有烈度数据
        priority=1,
        display_name="美国地质调查局",
        description="美国地质调查局（USGS）：地震测定 - FAN Studio WebSocket",
        default_timezone="UTC",
        publish_time_field="time",
        issue_type_field="status",
        fingerprint_prefix="usgs",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_report",
        provider_source_names=("usgs",),
        provider_aliases=("fan_studio_usgs", "usgs"),
        routing_tags=("fan_studio", "global", "report"),
        payload_signatures=(("url",),),
        payload_predicates=("usgs_report",),
    ),
    # sa_fanstudio: 美国 ShakeAlert - 来自 FAN Studio
    # 推送频率较高，统计语义按地震事件（earthquake）处理，不参与 EEW 机构状态卡片
    # （与 Global Quake 一致：无 institution_key / query_group）。
    "sa_fanstudio": SourceEntry(
        source_id="sa_fanstudio",
        source_enum="fan_studio_sa",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="usa_shakealert",
        parser_name="shakealert_eew_parser",
        presentation_type="earthquake_eew",
        text_presenter_key="shakealert_eew",
        report_policy="none",
        intensity_mode="magnitude",
        priority=1,
        display_name="美国 ShakeAlert",
        description="美国 ShakeAlert - FAN Studio WebSocket（高频源，统计归入地震事件）",
        default_timezone="America/Los_Angeles",
        publish_time_field="shockTime",
        fingerprint_prefix="sa",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_report",
        provider_source_names=("sa",),
        provider_aliases=("fan_studio_sa", "sa", "shakealert", "usa_shakealert"),
        routing_tags=("fan_studio", "usa", "report", "shakealert"),
        # 与 USGS / FSSN 区分：ShakeAlert 无 url、infoTypeName、createTime、placeName_zh
        payload_signatures=(("placeName", "shockTime", "magnitude", "id"),),
        payload_exclusions=(
            ("url",),
            ("infoTypeName",),
            ("createTime",),
            ("placeName_zh",),
        ),
        payload_predicates=("shakealert_eew",),
    ),
    # china_tsunami_fanstudio: 自然资源部海啸预警中心的海啸警报推送
    "china_tsunami_fanstudio": SourceEntry(
        source_id="china_tsunami_fanstudio",
        source_enum="fan_studio_tsunami",
        source_type=SourceType.TSUNAMI,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_tsunami",
        parser_name="china_tsunami_parser",
        presentation_type="tsunami",
        text_presenter_key="tsunami_cn",
        report_policy="none",
        intensity_mode="none",
        priority=1,
        display_name="中国海啸预警中心",
        description="自然资源部海啸预警中心海啸预警信息 - FAN Studio WebSocket",
        default_timezone="Asia/Shanghai",
        publish_time_field="time",
        fingerprint_prefix="cn_tsunami",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_tsunami",
        provider_source_names=("tsunami",),
        provider_aliases=("fan_studio_tsunami", "tsunami"),
        routing_tags=("fan_studio", "china", "tsunami"),
        payload_signatures=(("warningInfo", "code"),),
    ),
    # jma_tsunami_p2p: 日本气象厅海啸预报/警报 - 来自 P2P 地震情报
    "jma_tsunami_p2p": SourceEntry(
        source_id="jma_tsunami_p2p",
        source_enum="p2p_tsunami",
        source_type=SourceType.TSUNAMI,
        provider_family=ProviderFamily.P2P,
        config_group="p2p_earthquake",
        config_key="japan_jma_tsunami",
        parser_name="japan_tsunami_parser",
        presentation_type="tsunami",
        text_presenter_key="tsunami_jma",
        report_policy="none",
        intensity_mode="none",
        # EQSC 字段更完整，优先级低于 EQSC（数值越大越优先）
        priority=1,
        display_name="日本气象厅",
        description="日本气象厅：津波予報 - P2P地震情报 WebSocket",
        default_timezone="Asia/Tokyo",
        publish_time_field="time",
        fingerprint_prefix="jma_tsunami",
        connection_group="p2p_main",
        connection_handler="p2p",
        connection_data_source="jma_p2p",
        connection_url="wss://api.p2pquake.net/v2/ws",
        dispatch_family="p2p_tsunami",
        provider_message_types=("552",),
        provider_aliases=("p2p_tsunami",),
        routing_tags=("p2p", "japan", "tsunami"),
    ),
    # jma_tsunami_eqsc: 日本气象厅海啸情报 - 来自 EQSC HTTP 轮询（高优先级补充源）
    # 不挂 WebSocket 连接计划：connection_url 留空，由 EqscTsunamiPollService 独立轮询
    "jma_tsunami_eqsc": SourceEntry(
        source_id="jma_tsunami_eqsc",
        source_enum="eqsc_tsunami",
        source_type=SourceType.TSUNAMI,
        provider_family=ProviderFamily.EQSC,
        config_group="eqsc",
        config_key="jma_tsunami",
        parser_name="japan_tsunami_eqsc_parser",
        presentation_type="tsunami",
        text_presenter_key="tsunami_jma",
        report_policy="none",
        intensity_mode="none",
        # 高于 P2P：字段更完整，跨源去重时优先保留 EQSC
        priority=3,
        display_name="日本气象厅",
        description="日本气象厅：津波予報 - EQSC HTTP 轮询（P2P 高优先级补充）",
        default_timezone="Asia/Tokyo",
        publish_time_field="time",
        fingerprint_prefix="jma_tsunami",
        connection_group="eqsc",
        connection_handler="",
        connection_data_source="jma_tsunami_eqsc",
        connection_url="",
        institution_key="japan",
        institution_display_name="日本気象庁 津波",
        institution_active_name="日本気象庁",
        dispatch_family="eqsc_tsunami",
        provider_aliases=("eqsc_tsunami", "jma_tsunami_eqsc"),
        routing_tags=("eqsc", "japan", "tsunami", "http"),
    ),
    # china_weather_fanstudio: 中国气象局发布的气象预警
    "china_weather_fanstudio": SourceEntry(
        source_id="china_weather_fanstudio",
        source_enum="fan_studio_weather",
        source_type=SourceType.WEATHER,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_weather_alarm",
        parser_name="weather_alarm_parser",
        presentation_type="weather",
        text_presenter_key="weather_cn",
        report_policy="none",
        intensity_mode="none",
        priority=1,
        display_name="中国气象局",
        description="中国气象局气象预警 - FAN Studio WebSocket",
        default_timezone="Asia/Shanghai",
        publish_time_field="issue_time",
        fingerprint_prefix="cn_weather",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_weather",
        provider_source_names=("weatheralarm",),
        provider_aliases=("fan_studio_weather", "weatheralarm"),
        routing_tags=("fan_studio", "china", "weather"),
        payload_signatures=(("type",),),
        payload_predicates=("weather_alert",),
    ),
    # typhoon_fanstudio: 实时活跃台风 - FAN Studio（遗留触发源，上游已停服）
    "typhoon_fanstudio": SourceEntry(
        source_id="typhoon_fanstudio",
        source_enum="fan_studio_typhoon",
        source_type=SourceType.TYPHOON,
        provider_family=ProviderFamily.FAN_STUDIO,
        config_group="fan_studio",
        config_key="china_typhoon",
        parser_name="typhoon_parser",
        presentation_type="typhoon",
        text_presenter_key="typhoon",
        report_policy="none",
        intensity_mode="none",
        priority=0,
        display_name="中国气象局：实时活跃台风 - Fan",
        description="实时活跃台风 - FAN Studio WebSocket（遗留触发源，上游除 FSSN 外已停服）",
        default_timezone="Asia/Shanghai",
        publish_time_field="update_time",
        fingerprint_prefix="typhoon",
        connection_group="fan_studio_all",
        connection_handler="fan_studio",
        connection_data_source="fan_studio_mixed",
        connection_url="wss://ws.fanstudio.tech/all",
        connection_backup_url="wss://ws.fanstudio.hk/all",
        dispatch_family="fan_studio_typhoon",
        provider_source_names=("typhoon",),
        provider_aliases=("fan_studio_typhoon",),
        routing_tags=("fan_studio", "china", "typhoon"),
        payload_signatures=(("moveDirection", "windSpeed", "pressure"),),
        payload_predicates=("typhoon_active",),
    ),
    # typhoon_eqsc: 实时活跃台风 - EQSC HTTP 独立轮询
    # 不挂 WebSocket 连接计划：connection_url 留空，由 EqscTyphoonPollService 独立轮询。
    "typhoon_eqsc": SourceEntry(
        source_id="typhoon_eqsc",
        source_enum="eqsc_typhoon",
        source_type=SourceType.TYPHOON,
        provider_family=ProviderFamily.EQSC,
        config_group="eqsc",
        config_key="typhoon_enrichment",
        parser_name="typhoon_parser",
        presentation_type="typhoon",
        text_presenter_key="typhoon",
        report_policy="none",
        intensity_mode="none",
        priority=2,
        display_name="中国气象局：实时活跃台风 - EQSC",
        description="实时活跃台风 - EQSC HTTP 轮询（含轨迹与风圈，不依赖 FAN 触发）",
        default_timezone="Asia/Shanghai",
        publish_time_field="update_time",
        fingerprint_prefix="typhoon",
        connection_group="eqsc",
        connection_handler="",
        connection_data_source="typhoon_eqsc",
        connection_url="",
        dispatch_family="eqsc_typhoon",
        provider_aliases=("eqsc_typhoon", "typhoon_eqsc"),
        routing_tags=("eqsc", "china", "typhoon", "http"),
    ),
    # snet_msil: NIED S-Net 海底测站震度（MSIL 瓦片直连轮询）
    "snet_msil": SourceEntry(
        source_id="snet_msil",
        source_enum="snet_msil",
        source_type=SourceType.EARTHQUAKE_INFO,
        provider_family=ProviderFamily.DIRECT_HTTP,
        config_group="snet",
        config_key="enabled",
        parser_name="snet_parser",
        presentation_type="snet",
        text_presenter_key="snet",
        report_policy="none",
        intensity_mode="snet_shindo",
        priority=0,
        display_name="S-Net 海底地震计",
        description="NIED S-Net 海底观测网震度分布 - MSIL 强震动瓦片直连轮询",
        default_timezone="Asia/Tokyo",
        publish_time_field="timestamp",
        fingerprint_prefix="snet",
        connection_group="snet_msil",
        connection_handler="direct_http",
        connection_data_source="snet_msil",
        institution_key="snet",
        institution_display_name="S-net 海底地震觀測網",
        institution_active_name="S-net",
        query_group="",
        dispatch_family="snet",
        provider_aliases=("snet", "s-net", "snet_http", "snet_msil"),
        routing_tags=("direct_http", "japan", "snet", "shindo"),
    ),
}


# 下面这些辅助索引用于把“按某个维度筛选数据源”的查询成本从遍历全表降到直接查字典。
SOURCE_IDS_BY_FAMILY: dict[ProviderFamily, list[str]] = {}
SOURCE_IDS_BY_TYPE: dict[SourceType, list[str]] = {}
SOURCE_IDS_BY_CONFIG_GROUP: dict[str, list[str]] = {}
SOURCE_IDS_BY_PROVIDER_MESSAGE_TYPE: dict[str, list[str]] = {}
SOURCE_IDS_BY_PROVIDER_SOURCE_NAME: dict[str, list[str]] = {}
SOURCE_IDS_BY_ROUTING_TAG: dict[str, list[str]] = {}
SOURCE_IDS_BY_QUERY_GROUP: dict[str, list[str]] = {}
SOURCE_IDS_BY_FUSION_GROUP: dict[str, list[str]] = {}
SOURCE_IDS_BY_DISPATCH_FAMILY: dict[str, list[str]] = {}
SOURCE_IDS_BY_INSTITUTION_KEY: dict[str, list[str]] = {}
for _entry in SOURCE_CATALOG.values():
    # 先按最常见的主维度建立索引：提供方家族、事件类型、配置分组。
    SOURCE_IDS_BY_FAMILY.setdefault(_entry.provider_family, []).append(_entry.source_id)
    SOURCE_IDS_BY_TYPE.setdefault(_entry.source_type, []).append(_entry.source_id)
    SOURCE_IDS_BY_CONFIG_GROUP.setdefault(_entry.config_group, []).append(
        _entry.source_id
    )
    # 再按消息类型、来源名和路由标签建立细粒度索引，供路由器和查询层复用。
    for _message_type in _entry.provider_message_types:
        SOURCE_IDS_BY_PROVIDER_MESSAGE_TYPE.setdefault(
            _message_type.strip(), []
        ).append(_entry.source_id)
    for _source_name in _entry.provider_source_names:
        SOURCE_IDS_BY_PROVIDER_SOURCE_NAME.setdefault(_source_name.strip(), []).append(
            _entry.source_id
        )
    for _routing_tag in _entry.routing_tags:
        SOURCE_IDS_BY_ROUTING_TAG.setdefault(_routing_tag.strip(), []).append(
            _entry.source_id
        )
    if _entry.query_group:
        SOURCE_IDS_BY_QUERY_GROUP.setdefault(_entry.query_group.strip(), []).append(
            _entry.source_id
        )
    if _entry.fusion_group:
        SOURCE_IDS_BY_FUSION_GROUP.setdefault(_entry.fusion_group.strip(), []).append(
            _entry.source_id
        )
    if _entry.dispatch_family:
        SOURCE_IDS_BY_DISPATCH_FAMILY.setdefault(
            _entry.dispatch_family.strip(), []
        ).append(_entry.source_id)
    if _entry.institution_key:
        SOURCE_IDS_BY_INSTITUTION_KEY.setdefault(
            _entry.institution_key.strip(), []
        ).append(_entry.source_id)


def get_source_entry(source_id: str) -> SourceEntry | None:
    """按数据源标识获取注册项。"""
    return SOURCE_CATALOG.get(source_id)


def get_source_entries() -> list[SourceEntry]:
    """返回全部注册项列表。"""
    return list(SOURCE_CATALOG.values())


def get_source_ids_by_family(provider_family: ProviderFamily) -> list[str]:
    """按提供方家族过滤数据源。"""
    return list(SOURCE_IDS_BY_FAMILY.get(provider_family, []))


def get_source_ids_by_type(source_type: SourceType) -> list[str]:
    """按事件类型过滤数据源。"""
    return list(SOURCE_IDS_BY_TYPE.get(source_type, []))


def get_source_ids_by_config_group(config_group: str) -> list[str]:
    """按配置分组过滤数据源。"""
    return list(SOURCE_IDS_BY_CONFIG_GROUP.get((config_group or "").strip(), []))


def get_source_ids_by_provider_message_type(message_type: str) -> list[str]:
    """按提供方消息类型过滤数据源。"""
    return list(
        SOURCE_IDS_BY_PROVIDER_MESSAGE_TYPE.get((message_type or "").strip(), [])
    )


def get_source_ids_by_provider_source_name(source_name: str) -> list[str]:
    """按提供方来源名称过滤数据源。"""
    return list(SOURCE_IDS_BY_PROVIDER_SOURCE_NAME.get((source_name or "").strip(), []))


def get_source_ids_by_routing_tag(routing_tag: str) -> list[str]:
    """按路由标签过滤数据源。"""
    return list(SOURCE_IDS_BY_ROUTING_TAG.get((routing_tag or "").strip(), []))


def get_source_ids_by_query_group(query_group: str) -> list[str]:
    """按查询分组过滤数据源。"""
    return list(SOURCE_IDS_BY_QUERY_GROUP.get((query_group or "").strip(), []))


def get_source_ids_by_fusion_group(fusion_group: str) -> list[str]:
    """按融合分组过滤数据源。"""
    return list(SOURCE_IDS_BY_FUSION_GROUP.get((fusion_group or "").strip(), []))


def get_source_ids_by_dispatch_family(dispatch_family: str) -> list[str]:
    """按分发族过滤数据源。"""
    return list(SOURCE_IDS_BY_DISPATCH_FAMILY.get((dispatch_family or "").strip(), []))


def get_source_ids_by_institution_key(institution_key: str) -> list[str]:
    """按机构键过滤数据源。"""
    return list(SOURCE_IDS_BY_INSTITUTION_KEY.get((institution_key or "").strip(), []))
