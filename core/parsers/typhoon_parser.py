"""
台风解析器。
负责把 FAN Studio 来源的实时活跃台风数据转换为统一领域事件。

FAN Studio 台风数据以数组形式推送（支持多台风共舞），
解析器会遍历数组为每个活跃台风生成独立的 EventEnvelope。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EventEnvelope, TyphoonEvent
from ..domain.event_payload import SourcePayload
from ..domain.typhoon.typhoon_values import clean_text
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class TyphoonParser(BaseParser):
    """FAN Studio 实时活跃台风解析器。"""

    def __init__(self, message_logger=None):
        """初始化台风解析器。"""
        super().__init__("typhoon_fanstudio", message_logger)

    @staticmethod
    def _normalize_radius(value: Any) -> int | None:
        """规范化 FAN Studio 风圈半径字段。

        原始接口在未达到对应等级时返回 null；日志可读化后会显示为“无数据”。
        这里统一收敛为 int | None，避免脏字符串进入领域模型。
        """
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text in {"无数据", "NULL", "null", "None", "-"}:
                return None
            try:
                number = float(text)
            except ValueError:
                return None
            return int(number) if number > 0 else None
        if isinstance(value, (int, float)):
            return int(value) if value > 0 else None
        return None

    @staticmethod
    def _normalize_number(value: Any) -> float | int | None:
        """规范化数值字段，吸收 int/float/字符串差异，供去重指纹稳定比较。"""
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value != value:  # NaN
                return None
            return int(value) if value == int(value) else value
        if isinstance(value, str):
            text = value.strip()
            if not text or text in {"无数据", "NULL", "null", "None", "-"}:
                return None
            try:
                number = float(text)
            except ValueError:
                return None
            if number != number:
                return None
            return int(number) if number == int(number) else number
        return None

    def _build_envelope(self, typhoon_data: dict[str, Any]) -> EventEnvelope | None:
        """把单个台风原始字典封装为统一事件包裹体。"""
        if not isinstance(typhoon_data, dict):
            return None

        # 提取台风标识与命名
        typhoon_id = str(typhoon_data.get("id", "") or "").strip()
        if not typhoon_id:
            plugin_logger.debug(f"[灾害预警] {self.source_id} 台风消息缺少ID，跳过处理")
            return None

        # 使用 clean_text 清洗名称/类型：无名低压的 name 可能是占位字符串
        # "None"/"NULL"，若不清洗会写入领域事件，阻断后续富化覆盖有效名称。
        name = clean_text(typhoon_data.get("name"))
        name_en = clean_text(typhoon_data.get("name_en"))
        typhoon_type = clean_text(typhoon_data.get("type"))

        # 若名称和类型全部缺失，视为无效数据
        if not name and not name_en and not typhoon_type:
            plugin_logger.debug(
                f"[灾害预警] {self.source_id} 台风 {typhoon_id} 缺少名称和类型信息，跳过"
            )
            return None

        # 解析更新时间
        update_time_str = str(typhoon_data.get("updateTime", "") or "").strip()
        updated_at = self._parse_datetime(update_time_str) if update_time_str else None

        # 提取中心位置与参数（统一数值归一化，避免去重指纹因 62/62.0 抖动）
        latitude = self._normalize_number(typhoon_data.get("latitude"))
        longitude = self._normalize_number(typhoon_data.get("longitude"))
        pressure = self._normalize_number(typhoon_data.get("pressure"))
        if isinstance(pressure, float) and pressure == int(pressure):
            pressure = int(pressure)
        wind_speed = self._normalize_number(typhoon_data.get("windSpeed"))
        power = self._normalize_number(typhoon_data.get("power"))
        if isinstance(power, float) and power == int(power):
            power = int(power)
        move_direction = str(typhoon_data.get("moveDirection", "") or "").strip()
        move_speed = self._normalize_number(typhoon_data.get("moveSpeed"))
        # FAN Studio 未达等级时返回 null（日志可读化显示为“无数据”）
        radius7 = self._normalize_radius(typhoon_data.get("radius7"))
        radius10 = self._normalize_radius(typhoon_data.get("radius10"))

        source_entry = get_source_entry(self.source_id)
        # 流水线元数据只保留形态/来源标记；规范化业务字段只写入 TyphoonEvent。
        pipeline_metadata = {
            "data_source": "fan_studio",
            "info_type": "fan",
            "typhoon_data_mode": "fan",
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "typhoon",
        }

        # 实例化台风领域模型（唯一业务状态真源）
        domain_event = TyphoonEvent(
            typhoon_id=typhoon_id,
            name=name,
            name_en=name_en,
            typhoon_type=typhoon_type,
            latitude=latitude,
            longitude=longitude,
            pressure=pressure,
            wind_speed=wind_speed,
            power=power,
            move_direction=move_direction,
            move_speed=move_speed,
            radius7=radius7,
            radius10=radius10,
            is_active=True,
            updated_at=updated_at,
            metadata={},
        )

        # 构造事件身份对象
        identity = EventIdentity(
            event_id=typhoon_id,
            source_id=self.source_id,
            event_type="typhoon",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            published_at=updated_at,
            aliases=tuple(item for item in (name, name_en) if item),
            attributes={
                "parser_name": self.source_entry.parser_name
                if self.source_entry
                else "",
                "config_key": source_entry.config_key if source_entry else "",
            },
        )

        return EventEnvelope(
            identity=identity,
            event=domain_event,
            received_at=datetime.now(timezone.utc),
            payload=SourcePayload(
                source_id=self.source_id,
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                message_type="typhoon",
                raw=dict(typhoon_data),
                # 原始输入只保留 raw；attributes 不再作为规范化字段第二份存储。
                attributes={},
            ),
            metadata=pipeline_metadata,
        )

    def _parse_data(
        self, data: dict[str, Any]
    ) -> list[EventEnvelope] | EventEnvelope | None:
        """解析 FAN Studio 台风数据。

        FAN Studio 台风数据以数组形式推送，支持多台风共舞。
        解析器遍历数组，返回所有有效台风的事件包裹列表。
        上层路由器会负责对数组中每个台风分别调度。
        """
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                return None

            # 台风数据固定为数组格式
            typhoon_list: list[dict[str, Any]] = []
            if isinstance(msg_data, list):
                typhoon_list = msg_data
            elif isinstance(msg_data, dict):
                # 兼容单对象格式
                typhoon_list = [msg_data]

            if not typhoon_list:
                # 空数组（当前无活跃台风）为轮询常态，不逐一记录
                return None

            envelopes = []
            for typhoon in typhoon_list:
                envelope = self._build_envelope(typhoon)
                if envelope is not None:
                    plugin_logger.info(
                        f"[灾害预警] 台风解析成功: {envelope.event.name}({envelope.event.name_en}) "
                        f"ID: {envelope.event.typhoon_id}，类型为{envelope.event.typhoon_type}",
                        is_event_linked=True,
                        event_stream="typhoon",
                        is_silent_window=True,
                    )
                    envelopes.append(envelope)

            if not envelopes:
                return None

            return envelopes
        except Exception as exc:
            plugin_logger.error(
                f"[灾害预警] {self.source_id} 解析台风数据失败: {exc}, 数据内容: {data}"
            )
            return None
