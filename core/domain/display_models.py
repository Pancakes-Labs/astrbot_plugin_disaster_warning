"""
展示模型。

这一层是“已经整理好、适合直接展示”的轻量结构，
通常由展示上下文或展示构建逻辑产出，
供消息构建器、卡片渲染器或文本展示逻辑直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EarthquakeDisplayModel:
    """地震展示模型。"""

    # 标题通常是该次展示最醒目的主文案。
    title: str
    # 正文按行拆分，便于文本消息、卡片模板等多种展示方式复用。
    lines: list[str] = field(default_factory=list)
    # 额外信息用于承接模板渲染、附加标记等非核心展示字段。
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TsunamiDisplayModel:
    """海啸展示模型。"""

    # 展示主标题文案
    title: str
    # 行级内容明细
    lines: list[str] = field(default_factory=list)
    # 附加的渲染定制化参数
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WeatherDisplayModel:
    """气象展示模型。"""

    # 气象预警的主标题文案
    title: str
    # 格式化后的通知行级内容
    lines: list[str] = field(default_factory=list)
    # 模板和特定气象渠道所需的其他自定义数据
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TyphoonDisplayModel:
    """台风展示模型。"""

    # 台风展示的主标题文案
    title: str
    # 格式化后的通知行级内容
    lines: list[str] = field(default_factory=list)
    # 附加的渲染定制化参数（如轨迹数据供地图渲染使用）
    extras: dict[str, Any] = field(default_factory=dict)
