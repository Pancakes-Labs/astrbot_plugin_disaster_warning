"""
领域事件模型。

这一层描述“已经被解析并完成规范化”的业务事件，
它位于原始载荷与展示上下文之间，
主要供规则、去重、统计、推送编排等核心流程使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .event_identity import EventIdentity
from .event_payload import SourcePayload


@dataclass(slots=True)
class EarthquakeEvent:
    """地震领域事件。"""

    occurred_at: datetime | None  # 震中发生时间
    latitude: float | None  # 纬度
    longitude: float | None  # 经度
    place_name: str  # 发生地点/震中位置
    magnitude: float | None = None  # 震级 M
    depth: float | None = None  # 深度 (km)
    intensity: float | None = None  # 预警本地预估烈度或最大烈度
    scale: float | None = None  # 震度等级
    headline: str = ""  # 事件简短标题/摘要
    province: str | None = None  # 省份/行政区划
    # metadata 用于承接不适合上升为固定字段的附加信息。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TsunamiEvent:
    """海啸领域事件。"""

    title: str  # 海啸事件标题
    level: str  # 警报等级
    issued_at: datetime | None = None  # 发布时间
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WeatherEvent:
    """气象领域事件。"""

    title: str  # 预警名称/灾种名称
    headline: str  # 预警简报正文
    effective_at: datetime | None = None  # 生效起始时间
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TyphoonEvent:
    """台风/热带气旋领域事件。"""

    typhoon_id: str  # 台风国际编号标识
    name: str  # 台风中文命名
    name_en: str  # 台风英文命名
    typhoon_type: str  # 当前强度等级（如"超强台风"、"热带风暴"）
    latitude: float | None = None  # 中心纬度
    longitude: float | None = None  # 中心经度
    pressure: int | None = None  # 中心最低气压 (hPa)
    wind_speed: float | None = None  # 中心附近最大风速 (m/s)
    power: int | None = None  # 中心附近最大风力级别
    move_direction: str = ""  # 移动方向
    move_speed: float | None = None  # 移动速度 (km/h)
    radius7: int | None = None  # 七级风圈半径 (km)
    radius10: int | None = None  # 十级风圈半径 (km)
    is_active: bool = True  # 是否活跃
    updated_at: datetime | None = None  # 数据观测/更新时间
    # EQSC 富化数据：历史轨迹、预测路径、四象限风圈
    history_track: list[dict[str, Any]] = field(default_factory=list)
    future_track: list[dict[str, Any]] = field(default_factory=list)
    wind_circle: dict[str, Any] = field(default_factory=dict)
    # metadata 用于承接不适合上升为固定字段的附加信息。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventEnvelope:
    """统一事件包裹层。"""

    # identity 负责描述“这是谁”，event 负责描述“发生了什么”。
    identity: EventIdentity
    event: EarthquakeEvent | TsunamiEvent | WeatherEvent | TyphoonEvent
    # received_at 是本系统接收到该事件的时间，不一定等于事件发生时间或发布时间。
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # payload 保留原始载荷包装，用于追踪、日志和必要的回溯场景。
    payload: SourcePayload | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """返回事件主标识。"""
        return self.identity.event_id

    @property
    def source_id(self) -> str:
        """返回来源标识。"""
        return self.identity.source_id

    @property
    def event_type(self) -> str:
        """返回事件类型。"""
        return self.identity.event_type

    @property
    def report_num(self) -> int | None:
        """返回第几报信息。"""
        return self.identity.report_num
