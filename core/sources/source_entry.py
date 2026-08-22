"""
统一数据源注册项模型定义。
负责描述数据源类型、连接信息、路由标签、展示方式与融合策略等元数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceType(Enum):
    """统一数据源领域类型。"""

    EARTHQUAKE_WARNING = "earthquake_warning"  # 地震预警 (EEW)
    EARTHQUAKE_INFO = "earthquake_info"  # 地震测定与历史报告
    TSUNAMI = "tsunami"  # 海啸预警/情报
    WEATHER = "weather"  # 气象预警
    TYPHOON = "typhoon"  # 台风/热带气旋


class ProviderFamily(Enum):
    """连接与提供方家族。"""

    FAN_STUDIO = "fan_studio"  # FAN Studio WebSocket 聚合源
    P2P = "p2p"  # 日本 P2P 地震情报网
    WOLFX = "wolfx"  # Wolfx API
    GLOBAL_QUAKE = "global_quake"  # Global Quake 自建或公共服务器
    EQSC = "eqsc"  # EQSC HTTP API（海啸/台风/火山等富化数据源）
    DIRECT_HTTP = "direct_http"  # 直连 HTTP 轮询源（如 NIED S-Net / MSIL 瓦片）


class FusionRole(Enum):
    """融合策略中的数据源角色。

    当同一区域、同一级别警报存在多个渠道源时，用于决定主备及融合控制逻辑。
    """

    PRIMARY = "primary"  # 主通道源，优先采信其首发与更新数据
    SECONDARY = "secondary"  # 次通道源，作冗余备份与校验补充


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """统一数据源注册项。

    单个实例完整描述一个可接入数据源在配置、路由、展示、查询和融合链路中的定位。
    """

    # 统一身份字段：用于跨模块稳定引用同一数据源。
    source_id: str  # 唯一标识符
    source_enum: str  # 映射的枚举名
    source_type: SourceType  # 领域数据类型
    provider_family: ProviderFamily  # 协议提供方家族类型

    # 配置定位字段：用于映射到 data_sources 下的具体开关位置。
    config_group: str  # 对应外部主配置组
    config_key: str  # 主配置组内具体通道开关的 Key

    # 解析与展示字段：决定消息进入哪类解析器和展示链路。
    parser_name: str  # 注册的解析器类名
    presentation_type: str  # 展示呈现分类
    text_presenter_key: str  # 对应渲染器的注册 Key

    # 规则相关字段：控制报次策略、强度模式与排序优先级。
    report_policy: str  # 报次策略模式
    intensity_mode: str  # 烈度制式
    priority: int  # 在同类数据源中的优先级顺序 (数值越大越优先)
    display_name: str  # 中文友好显示名称

    description: str = ""  # 详细的功能与数据源描述
    default_timezone: str = "Asia/Shanghai"  # 事件发布源的本地时区 (用于时间转换)
    event_time_field: str = "occurred_at"  # 原始载荷中表示发震时刻的字段名
    publish_time_field: str = ""  # 原始载荷中表示发布/更新时刻的字段名
    report_num_field: str = ""  # 表示报数更新次数的字段名
    final_flag_field: str = "is_final"  # 表示是否为最终报的布尔字段名
    issue_type_field: str = ""  # 表示警报变更类型或级别变更的字段名
    fingerprint_prefix: str = ""  # 去重时指纹生成的特有前缀

    # 连接相关字段：用于生成 WebSocket 或其他接入通道的连接计划。
    connection_group: str = ""  # 连接组别
    connection_handler: str = ""  # 对应的底层 WebSocket 接收处理器类型
    connection_data_source: str = ""  # 连接数据源标识
    connection_url: str = ""  # 主服务器 WebSocket 地址
    connection_backup_url: str = ""  # 备份服务器 WebSocket 地址

    # 提供方映射字段：用于来源名称、消息类型和路由标签匹配。
    provider_message_types: tuple[str, ...] = ()  # 该源支持匹配的原始协议消息列表
    provider_source_names: tuple[
        str, ...
    ] = ()  # FAN Studio 等聚合平台定义的底层原始来源标识
    provider_aliases: tuple[str, ...] = ()  # 解析与路由时支持的别名集合
    routing_tags: tuple[str, ...] = ()  # 供决策树和日志分类使用的特征标签
    payload_signatures: tuple[tuple[str, ...], ...] = ()  # 载荷特征签名
    payload_exclusions: tuple[tuple[str, ...], ...] = ()  # 载荷排除特征
    payload_predicates: tuple[str, ...] = ()  # 辅助的自定义复杂规则匹配谓词

    # 查询与融合字段：用于机构分组、查询视图、融合策略和分发族。
    institution_key: str = ""  # 统一机构标识键名
    institution_display_name: str = ""  # 机构完整显示名称
    institution_active_name: str = ""  # 机构日常简称
    query_group: str = ""  # 查询的分组大类
    fusion_group: str = ""  # 参与数据融合的分组名
    fusion_role: FusionRole | None = None  # 在数据融合中的主备角色
    dispatch_family: str = ""  # 内部消息分发流派
    metadata: dict[str, str] = field(
        default_factory=dict
    )  # 预留字典，存放未来扩展元数据

    @property
    def config_path(self) -> tuple[str, str]:
        """返回数据源配置路径。"""
        return self.config_group, self.config_key

    @property
    def timezone_name(self) -> str:
        """返回数据源默认时区名称，做防空字符串保护并退化到 UTC。"""
        return (self.default_timezone or "UTC").strip() or "UTC"

    @property
    def identity_fingerprint_prefix(self) -> str:
        """返回统一事件指纹前缀。"""
        return (self.fingerprint_prefix or self.source_id).strip()

    def resolve_metadata_field(self, field_name: str, default: str = "") -> str:
        """解析当前注册项对应的元数据字段名。"""
        value = getattr(self, field_name, default)
        # 某些字段允许在注册表中留空，这里统一做字符串化兜底，避免上层重复判空。
        if not isinstance(value, str):
            return default
        return value.strip() or default

    def build_connection_plan(self) -> dict[str, str]:
        """构建当前数据源所属连接组的连接计划片段。

        若连接信息不完整，则返回空字典，表示该数据源不能独立形成连接计划。
        """
        group_key = (self.connection_group or "").strip()
        handler = (self.connection_handler or "").strip()
        data_source = (self.connection_data_source or self.source_id).strip()
        url = (self.connection_url or "").strip()
        backup_url = (self.connection_backup_url or "").strip()
        # 连接分组、处理器和主地址任一缺失时，都视为该注册项无法独立产出连接计划。
        if not group_key or not handler or not url:
            return {}

        result = {
            "group_key": group_key,
            "url": url,
            "handler": handler,
            "data_source": data_source,
        }
        if backup_url:
            result["backup_url"] = backup_url
        return result
