"""
台风展示器。

该模块负责把台风展示上下文转换为适合发送的文本内容。
支持两种数据来源：
1. EQSC 富化数据（含四象限风圈、移动速度方向等丰富信息）
2. FAN Studio 基础数据（回退用，仅含单值风圈半径）
"""

from __future__ import annotations

import math
from typing import Any

from ....utils.time_converter import TimeConverter
from ...domain.event_context import TyphoonDisplayContext
from ...domain.typhoon.typhoon_display_format import (
    format_coordinates,
    format_move_direction,
    format_wind_circle,
    format_wind_speed,
    get_typhoon_level_emoji,
    is_valid_radius_value,
)
from ...services.geo.region_service import region_service
from ...sources.source_catalog import get_source_entry
from .base_presenter import BasePresenter


class TyphoonPresenter(BasePresenter):
    """台风文本展示器。

    根据 data_source 字段自动选择 EQSC 富化版或 FAN Studio 回退版展示逻辑。
    """

    presenter_name = "typhoon_presenter"

    @staticmethod
    def _format_coordinates(latitude: float, longitude: float) -> str:
        """把经纬度格式化为带方向标识的文本。"""
        return format_coordinates(latitude, longitude)

    @staticmethod
    def _is_valid_radius_value(value: Any) -> bool:
        """判断单值风圈是否可展示。"""
        return is_valid_radius_value(value)

    @staticmethod
    def _format_wind_circle(wind_circle: dict[str, Any]) -> list[str]:
        """格式化 EQSC 四象限风圈数据为紧凑表格文本行。"""
        return format_wind_circle(wind_circle)

    @staticmethod
    def _format_reference_place(latitude: Any, longitude: Any) -> str:
        """根据经纬度解析 F-E 区域近似地名（如“菲律宾海附近”）。

        拒绝非有限坐标（inf/NaN），避免传给 region_service 时触发
        int(lat + 90) 的 OverflowError。
        """
        try:
            lat_num = float(latitude)
            lon_num = float(longitude)
        except (TypeError, ValueError):
            return ""
        if not math.isfinite(lat_num) or not math.isfinite(lon_num):
            return ""
        return region_service.get_fe_name(lat_num, lon_num) or ""

    @staticmethod
    def _format_wind_speed(wind_speed: float | None, power: int | None) -> str | None:
        """把风速与风力合并为「最大风速：20 m/s (8级)」格式。"""
        return format_wind_speed(wind_speed, power)

    @staticmethod
    def _get_source_org_name(display_context: TyphoonDisplayContext) -> str:
        """根据来源返回发布机构显示名。"""
        if display_context.source_id:
            source_entry = get_source_entry(display_context.source_id)
            if source_entry and source_entry.display_name:
                # catalog 可能是“中国气象局：实时活跃台风 - Fan”，推送标题固定机构名为“中国气象局”
                name = source_entry.display_name
                for token in (
                    "：实时活跃台风",
                    ": 实时活跃台风",
                    " - Fan+EQSC",
                    " - Fan",
                    " - EQSC",
                ):
                    name = name.replace(token, "")
                name = name.replace("台风", "").strip(" ：:")
                return name or "中国气象局"
        return "中国气象局"

    @classmethod
    def _get_typhoon_level_emoji(cls, typhoon_type: str | None) -> str:
        """根据台风强度等级返回圆形颜色 emoji。"""
        return get_typhoon_level_emoji(typhoon_type)

    @classmethod
    def _format_move_direction(cls, direction: str | None) -> str:
        """把源侧移动方向本地化为日常可读写法（仅展示层）。"""
        return format_move_direction(direction)

    @classmethod
    def format_message(
        cls,
        display_context: TyphoonDisplayContext,
        options: dict | None = None,
    ) -> str:
        """格式化台风消息文本。"""
        options = options or {}
        target_timezone = options.get("timezone")

        # 若调用方未指定时区，则按来源默认时区设置
        if not target_timezone and display_context.source_id:
            source_entry = get_source_entry(display_context.source_id)
            target_timezone = source_entry.timezone_name if source_entry else "UTC+8"
        elif not target_timezone:
            target_timezone = "UTC+8"

        # 构建标题行（固定使用[台风报文]，等级单独展示）
        type_emoji = "🌀"
        header = f"{type_emoji}[台风报文] 中国气象局"

        lines = [header]

        # 台风名称与英文（模板：巴威（BAVI））
        name_display = display_context.name or display_context.name_en or ""
        name_en = display_context.name_en or ""
        if name_display and name_en and name_en != name_display:
            lines.append(f"{name_display}（{name_en}）")
        elif name_display:
            lines.append(name_display)
        # 空行分隔名称与参数
        if name_display:
            lines.append("")

        # 编号
        id_display = display_context.typhoon_id or ""
        if id_display:
            # 按模板展示为 4 位编号（如 2609）
            short_id = id_display[-4:] if len(id_display) >= 4 else id_display
            lines.append(f"📌编号：{short_id}")

        # 等级（后附圆形颜色emoji指示器）
        if display_context.typhoon_type:
            level_emoji = cls._get_typhoon_level_emoji(display_context.typhoon_type)
            level_text = display_context.typhoon_type
            if level_emoji:
                level_text = f"{level_text}{level_emoji}"
            lines.append(f"⚠️等级：{level_text}")

        # 当前状态
        if not display_context.is_active:
            lines.append("✅该台风已停止编报")

        # 中心位置
        lat = display_context.latitude
        lon = display_context.longitude
        if lat is not None and lon is not None:
            coords = cls._format_coordinates(lat, lon)
            lines.append(f"🌍中心位置：({coords})")
            reference = cls._format_reference_place(lat, lon)
            if reference:
                lines.append(f"📍参考位置：{reference}")

        # 最大风速
        wind_speed_str = cls._format_wind_speed(
            display_context.wind_speed, display_context.power
        )
        if wind_speed_str:
            lines.append(f"💨最大风速：{wind_speed_str}")

        # 中心气压
        if display_context.pressure is not None:
            lines.append(f"🎈中心气压：{display_context.pressure} hPa")

        # 移动信息（方向仅在展示层本地化，不改原始字段）
        move_parts: list[str] = []
        if display_context.move_direction:
            move_parts.append(
                cls._format_move_direction(display_context.move_direction)
            )
        elif display_context.move_speed is not None:
            move_parts.append("—")
        if display_context.move_speed is not None:
            move_parts.append(f"({display_context.move_speed} KM/H)")
        if move_parts:
            lines.append(f"🧭移动方向：{' '.join(move_parts)}")

        # 本地距离 / 预报逼近（由 TyphoonRule 写入 metadata）
        local_lines = cls._format_local_estimation_lines(display_context, options)
        if local_lines:
            lines.append("")
            lines.extend(local_lines)

        # 风圈信息
        # 优先使用 EQSC 四象限风圈数据
        circle_lines = cls._format_wind_circle(display_context.wind_circle)
        if circle_lines:
            lines.append("")
            lines.append(f"🌪️风圈半径：{circle_lines[0]}")
            lines.extend(circle_lines[1:])
        elif cls._is_valid_radius_value(
            display_context.radius7
        ) or cls._is_valid_radius_value(display_context.radius10):
            # 回退到 FAN Studio 单值风圈，紧凑对齐展示
            lines.append("")
            lines.append("🌪️风圈半径：")
            if cls._is_valid_radius_value(display_context.radius7):
                lines.append(f"7 级：{display_context.radius7} (KM)")
            if cls._is_valid_radius_value(display_context.radius10):
                lines.append(f"10级：{display_context.radius10} (KM)")

        # 更新时间
        if display_context.updated_at:
            lines.append("")
            lines.append(
                f"🕒更新时间：{TimeConverter.format_time(display_context.updated_at, target_timezone)}"
            )

        return "\n".join(lines)

    @classmethod
    def _format_local_estimation_lines(
        cls,
        display_context: TyphoonDisplayContext,
        options: dict | None = None,
    ) -> list[str]:
        """格式化本地距离与预报逼近信息。"""
        options = options or {}
        typhoon_config = options.get("typhoon_config")
        if not isinstance(typhoon_config, dict):
            typhoon_config = {}
        # 展示开关关闭时，即使 metadata 残留估算结果也不输出到消息正文。
        if not bool(typhoon_config.get("show_local_estimation", False)):
            return []

        metadata = (
            display_context.metadata
            if isinstance(display_context.metadata, dict)
            else {}
        )
        estimation = metadata.get("typhoon_local_estimation")
        if not isinstance(estimation, dict) or not estimation:
            # 兼容直接从 options 透传的情况
            estimation = options.get("typhoon_local_estimation")
        if not isinstance(estimation, dict) or not estimation:
            return []

        lines: list[str] = []
        place = str(estimation.get("place_name") or "本地").strip() or "本地"
        distance_km = estimation.get("distance_km")
        if isinstance(distance_km, (int, float)):
            line = f"📍距{place}：约 {float(distance_km):.1f} km"
            if estimation.get("within_wind_circle"):
                line += "（位于风圈影响范围内）"
            lines.append(line)

        if estimation.get("approach_evaluated") and estimation.get("approach_hit"):
            approach_distance = estimation.get("approach_min_distance_km")
            horizon = estimation.get("approach_horizon_hours")
            if isinstance(approach_distance, (int, float)):
                horizon_text = (
                    f"{int(horizon)}h" if isinstance(horizon, (int, float)) else "预报"
                )
                lines.append(
                    f"🔭预报逼近：未来{horizon_text}路径最近约 "
                    f"{float(approach_distance):.1f} km（{place}）"
                )
            else:
                lines.append(f"🔭预报逼近：未来路径将靠近{place}")

        return lines

    @classmethod
    def present(
        cls,
        display_context: TyphoonDisplayContext,
        options: dict | None = None,
    ) -> str:
        merged_options = dict(display_context.options or {})
        if options:
            merged_options.update(options)
        return cls.format_message(display_context, merged_options)
