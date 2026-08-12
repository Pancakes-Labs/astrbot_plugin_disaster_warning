"""
模拟事件流数据模型。

承载"事件流编排"的数据结构：
- SimulationStep：单步模拟指令（灾种 / 数据源 / 参数 / 报数 / 事件键 / 延迟）
- SimulationFlow：一次完整的模拟编排（草稿单元），由多步骤顺序组成

设计目标：
- 步骤模型可完整描述"多数据源、多事件、多报数混排"的事件流
- 序列化 / 反序列化保持纯 dict 友好，便于草稿持久化与 REST API 传输
- 事件键 (event_key) 用于把连续步骤归并为同一事件的报次演进
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 进程内模拟流递增计数器：用于生成稳定且不重复的 flow / step 标识
_flow_sequence = 0
_flow_sequence_lock = threading.Lock()


def _next_flow_sequence() -> int:
    """获取下一个模拟流序号（线程安全，单调递增）。"""
    global _flow_sequence
    with _flow_sequence_lock:
        _flow_sequence += 1
        return _flow_sequence


def generate_sim_id(prefix: str = "sim") -> str:
    """生成稳定的模拟标识（时间戳 + 序号 + 短随机，避免跨重启冲突）。"""
    return f"{prefix}_{int(datetime.now(timezone.utc).timestamp())}_{_next_flow_sequence()}"


@dataclass(slots=True)
class SimulationStep:
    """单步模拟指令。"""

    step_id: str  # 步骤唯一标识
    disaster_type: str  # earthquake / tsunami / weather / typhoon
    source_id: str  # 必须为 SOURCE_CATALOG 中已注册的数据源
    params: dict[str, Any] = field(default_factory=dict)  # 灾种 + 源特有参数
    # 报次演进：同一 event_key 下连续步骤视为同一事件的第 N 报
    report_num: int = 1
    is_final: bool = False
    event_key: str = ""  # 事件键；空串时每步独立成事件
    # 发震/事件时间（绝对时间字符串 "YYYY-MM-DD HH:MM:SS"，为空则按 time_offset/delay 计算）
    event_time: str = ""
    # 事件时间回退秒数（模拟历史时刻事件；0 表示使用当前时间；event_time 优先级更高）
    time_offset_seconds: float = 0.0
    # 事件时间延迟秒数（在绝对时间或当前时间上向后推，模拟未来时刻事件）
    event_time_delay_seconds: float = 0.0
    # 信息更新时间（绝对时间字符串 "YYYY-MM-DD HH:MM:SS"，为空则按 update_time_offset/delay 计算）
    update_time: str = ""
    # 更新时间回退秒数（相对当前执行时刻回退；0 表示消息发布时间 = 执行时刻）
    update_time_offset_seconds: float = 0.0
    # 更新时间延迟秒数（在绝对时间或当前时间上向后推，模拟未来发布时刻）
    update_time_delay_seconds: float = 0.0

    @classmethod
    def create(
        cls,
        disaster_type: str,
        source_id: str,
        params: dict[str, Any] | None = None,
        *,
        report_num: int = 1,
        is_final: bool = False,
        event_key: str = "",
        event_time: str = "",
        time_offset_seconds: float = 0.0,
        event_time_delay_seconds: float = 0.0,
        update_time: str = "",
        update_time_offset_seconds: float = 0.0,
        update_time_delay_seconds: float = 0.0,
        step_id: str | None = None,
    ) -> SimulationStep:
        """便捷工厂：自动生成 step_id。"""
        return cls(
            step_id=step_id or uuid.uuid4().hex[:12],
            disaster_type=disaster_type,
            source_id=source_id,
            params=dict(params or {}),
            report_num=max(1, int(report_num or 1)),
            is_final=bool(is_final),
            event_key=str(event_key or "").strip(),
            event_time=str(event_time or "").strip(),
            time_offset_seconds=max(0.0, float(time_offset_seconds or 0.0)),
            event_time_delay_seconds=max(0.0, float(event_time_delay_seconds or 0.0)),
            update_time=str(update_time or "").strip(),
            update_time_offset_seconds=max(
                0.0, float(update_time_offset_seconds or 0.0)
            ),
            update_time_delay_seconds=max(0.0, float(update_time_delay_seconds or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return {
            "step_id": self.step_id,
            "disaster_type": self.disaster_type,
            "source_id": self.source_id,
            "params": dict(self.params),
            "report_num": self.report_num,
            "is_final": self.is_final,
            "event_key": self.event_key,
            "event_time": self.event_time,
            "time_offset_seconds": self.time_offset_seconds,
            "event_time_delay_seconds": self.event_time_delay_seconds,
            "update_time": self.update_time,
            "update_time_offset_seconds": self.update_time_offset_seconds,
            "update_time_delay_seconds": self.update_time_delay_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationStep:
        """从字典恢复步骤（兼容缺省字段）。"""
        data = dict(data or {})
        return cls.create(
            step_id=str(data.get("step_id") or uuid.uuid4().hex[:12]),
            disaster_type=str(data.get("disaster_type") or "earthquake"),
            source_id=str(data.get("source_id") or ""),
            params=dict(data.get("params") or {}),
            report_num=int(data.get("report_num") or 1),
            is_final=bool(data.get("is_final", False)),
            event_key=str(data.get("event_key") or ""),
            event_time=str(data.get("event_time") or ""),
            time_offset_seconds=float(data.get("time_offset_seconds") or 0.0),
            event_time_delay_seconds=float(data.get("event_time_delay_seconds") or 0.0),
            update_time=str(data.get("update_time") or ""),
            update_time_offset_seconds=float(
                data.get("update_time_offset_seconds") or 0.0
            ),
            update_time_delay_seconds=float(
                data.get("update_time_delay_seconds") or 0.0
            ),
        )


@dataclass(slots=True)
class SimulationFlow:
    """一次完整的模拟编排（草稿单元）。"""

    flow_id: str
    name: str
    steps: list[SimulationStep] = field(default_factory=list)
    description: str = ""
    target_session: str = ""  # 空串 = 回退默认会话
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(
        cls,
        name: str,
        steps: list[SimulationStep] | None = None,
        *,
        description: str = "",
        target_session: str = "",
        flow_id: str | None = None,
    ) -> SimulationFlow:
        """便捷工厂：自动生成 flow_id 与时间戳。"""
        now = datetime.now(timezone.utc)
        return cls(
            flow_id=flow_id or generate_sim_id("flow"),
            name=str(name or "未命名模拟流").strip() or "未命名模拟流",
            steps=list(steps or []),
            description=str(description or ""),
            target_session=str(target_session or ""),
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return {
            "flow_id": self.flow_id,
            "name": self.name,
            "description": self.description,
            "target_session": self.target_session,
            "steps": [step.to_dict() for step in self.steps],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationFlow:
        """从字典恢复模拟流（兼容缺省字段）。"""
        data = dict(data or {})
        steps = [SimulationStep.from_dict(item) for item in (data.get("steps") or [])]

        def _parse_dt(value: Any) -> datetime:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except (ValueError, TypeError):
                    pass
            return datetime.now(timezone.utc)

        return cls(
            flow_id=str(data.get("flow_id") or generate_sim_id("flow")),
            name=str(data.get("name") or "未命名模拟流").strip() or "未命名模拟流",
            steps=steps,
            description=str(data.get("description") or ""),
            target_session=str(data.get("target_session") or ""),
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
        )


# 支持模拟的灾种常量（与 SOURCE_CATALOG 的 SourceType 对齐的字符串键）
DISASTER_TYPE_EARTHQUAKE = "earthquake"
DISASTER_TYPE_TSUNAMI = "tsunami"
DISASTER_TYPE_WEATHER = "weather"
DISASTER_TYPE_TYPHOON = "typhoon"

# 灾种展示元数据（供前端 /api/simulation/schema 与命令侧复用）
DISASTER_TYPE_META: dict[str, dict[str, str]] = {
    DISASTER_TYPE_EARTHQUAKE: {"label": "地震", "icon": "🌍"},
    DISASTER_TYPE_TSUNAMI: {"label": "海啸", "icon": "🌊"},
    DISASTER_TYPE_WEATHER: {"label": "气象预警", "icon": "🌦️"},
    DISASTER_TYPE_TYPHOON: {"label": "台风", "icon": "🌀"},
}


__all__ = [
    "SimulationStep",
    "SimulationFlow",
    "generate_sim_id",
    "DISASTER_TYPE_EARTHQUAKE",
    "DISASTER_TYPE_TSUNAMI",
    "DISASTER_TYPE_WEATHER",
    "DISASTER_TYPE_TYPHOON",
    "DISASTER_TYPE_META",
]
