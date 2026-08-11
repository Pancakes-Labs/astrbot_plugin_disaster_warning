"""
海啸展示器。

该模块负责把海啸展示上下文转换为适合发送的文本内容，
同时覆盖通用海啸文本展示与日本气象厅海啸预报展示。
"""

from __future__ import annotations

from typing import Any

from ....utils.severity_emoji import (
    CN_TSUNNAMI_LEVEL_EMOJI,
    JP_TSUNNAMI_LEVEL_EMOJI,
    cn_tsunami_level_emoji,
)
from ....utils.time_converter import TimeConverter
from ...domain.event_context import TsunamiDisplayContext
from ...domain.tsunami.jma_tsunami_normalize import GRADE_TITLE_MAP, coerce_bool
from ...domain.tsunami.tsunami_levels import (
    normalize_cn_tsunami_level,
    normalize_jp_tsunami_level,
)
from ...sources.source_catalog import get_source_entry
from .base_presenter import BasePresenter


def _cn_level_emoji(level: Any) -> str:
    """返回中国海啸等级对应的颜色圆点 emoji。

    先按归一化等级查统一映射表，未命中时回退到纯文本匹配。
    """
    normalized = normalize_cn_tsunami_level(level)
    if normalized in CN_TSUNNAMI_LEVEL_EMOJI:
        return CN_TSUNNAMI_LEVEL_EMOJI[normalized]
    return cn_tsunami_level_emoji(level)


def _jp_level_emoji(level: Any, *, cancelled: bool = False) -> str:
    """返回日本海啸等级对应的颜色圆点 emoji（解除/未知不着色）。"""
    normalized = normalize_jp_tsunami_level(level, cancelled=cancelled)
    return JP_TSUNNAMI_LEVEL_EMOJI.get(normalized, "")


def _jp_level_label(level: Any, *, cancelled: bool = False) -> str:
    """返回日本海啸等级的展示标签（日文官方用语，未知值原样回退）。"""
    normalized = normalize_jp_tsunami_level(level, cancelled=cancelled)
    if cancelled or normalized == "解除":
        return GRADE_TITLE_MAP.get("解除", "津波予報（解除）")
    return GRADE_TITLE_MAP.get(normalized, str(level or "").strip() or "津波予報")


class TsunamiAlertPresenter(BasePresenter):
    """通用海啸文本展示器（中国自然资源部）。"""

    presenter_name = "tsunami_alert_presenter"

    @staticmethod
    def _format_coordinates(latitude: float, longitude: float) -> str:
        """把经纬度格式化为带方向标识的文本。"""
        lat_dir = "N" if latitude >= 0 else "S"
        lon_dir = "E" if longitude >= 0 else "W"
        return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"

    @classmethod
    def _resolve_timezone(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict[str, Any],
    ) -> str:
        """解析展示时区：优先用户配置，缺省按数据源（日本 UTC+9 / 其他 UTC+8）。"""
        timezone = options.get("timezone")
        if timezone:
            return str(timezone)
        if display_context.source_id:
            source_entry = get_source_entry(display_context.source_id)
            display_name = source_entry.display_name if source_entry is not None else ""
            if "日本" in display_name or "日本气象厅" in display_name:
                return "UTC+9"
        return "UTC+8"

    @classmethod
    def format_message(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict | None = None,
    ) -> str:
        """格式化通用海啸预警/信息消息文本。"""
        options = options or {}
        target_timezone = cls._resolve_timezone(display_context, options)

        normalized_level = normalize_cn_tsunami_level(display_context.level)
        is_info = display_context.message_type == "info" or normalized_level == "信息"
        org_unit = str(display_context.org_unit or "自然资源部海啸预警中心").strip()
        header_tag = "海啸信息" if is_info else "海啸预警"
        lines = [f"🌊[{header_tag}] {org_unit}"]

        level_emoji = _cn_level_emoji(display_context.level)
        if display_context.title:
            title_line = f"📋{display_context.title}"
            if level_emoji:
                title_line += level_emoji
            lines.append(title_line)
        elif display_context.level:
            # 使用归一化等级，避免「海啸信息/info」等变体被拼成“海啸xxx警报”
            if normalized_level == "信息":
                title_line = "📋海啸信息"
            elif normalized_level in {"蓝色", "黄色", "橙色", "红色"}:
                title_line = f"📋海啸{normalized_level}警报"
            elif normalized_level == "解除":
                title_line = "📋海啸解除"
            else:
                level_text = str(display_context.level)
                title_line = f"📋海啸{level_text}警报"
            if level_emoji:
                title_line += level_emoji
            lines.append(title_line)

        time_value = display_context.updated_at or display_context.issued_at
        if time_value:
            lines.append(
                f"🕒最近更新时间：{TimeConverter.format_time(time_value, target_timezone)}"
            )

        place_name = display_context.place_name or display_context.subtitle
        lat = display_context.latitude
        lon = display_context.longitude
        if place_name:
            if lat is not None and lon is not None:
                try:
                    coords = cls._format_coordinates(float(lat), float(lon))
                    lines.append(f"🌍震源：{place_name} ({coords})")
                except (TypeError, ValueError):
                    lines.append(f"🌍震源：{place_name}")
            else:
                lines.append(f"🌍震源：{place_name}")

        shock_parts: list[str] = []
        if display_context.magnitude is not None:
            shock_parts.append(f"M {display_context.magnitude}")
        if display_context.depth is not None:
            depth = display_context.depth
            depth_text = int(depth) if float(depth).is_integer() else depth
            shock_parts.append(f"深度 {depth_text}km")
        if shock_parts:
            lines.append(f"🧭参数：{' / '.join(shock_parts)}")

        forecasts = [
            item for item in (display_context.forecasts or []) if isinstance(item, dict)
        ]
        if forecasts:
            lines.append(f"📈沿海预报：{len(forecasts)} 个区域")
            # 海啸事件默认不折叠区域，尽量全部展示
            for forecast in forecasts:
                area_name = (
                    forecast.get("forecastArea")
                    or forecast.get("forecastPoint")
                    or forecast.get("name")
                    or ""
                )
                area_name = str(area_name).strip()
                if not area_name:
                    continue
                area_info = f"  • {area_name}"
                grade = forecast.get("warningLevel") or forecast.get("grade") or ""
                grade_text = str(grade).strip()
                if grade_text:
                    grade_emoji = _cn_level_emoji(grade_text)
                    area_info += f" [{grade_text}]"
                    if grade_emoji:
                        area_info += grade_emoji
                arrival_time = str(forecast.get("estimatedArrivalTime") or "").strip()
                if arrival_time:
                    area_info += f" 预计{arrival_time}到达"
                max_wave = str(forecast.get("maxWaveHeight") or "").strip()
                if max_wave:
                    # 文档样例多为 cm 数值区间；若已带单位则原样
                    if any(
                        token in max_wave for token in ("cm", "CM", "米", "m", "ｍ")
                    ):
                        area_info += f" 波高 🌊 {max_wave}"
                    else:
                        area_info += f" 波高 🌊 {max_wave}cm"
                lines.append(area_info)

        monitoring_stations = [
            item
            for item in (display_context.monitoring_stations or [])
            if isinstance(item, dict)
        ]
        if monitoring_stations:
            lines.append(f"📡监测实况：{len(monitoring_stations)} 个站点")
            for station in monitoring_stations:
                station_name = (
                    station.get("stationName") or station.get("name") or "监测站"
                )
                location = str(station.get("location") or "").strip()
                wave = str(station.get("maxWaveHeight") or "").strip()
                station_line = f"  • {station_name}"
                if location:
                    station_line += f"({location})"
                if wave:
                    if any(token in wave for token in ("cm", "CM", "米", "m", "ｍ")):
                        station_line += f" 最大波幅 🌊 {wave}"
                    else:
                        station_line += f" 最大波幅 🌊 {wave}cm"
                lines.append(station_line)

        if display_context.details_url:
            lines.append("🔗详情：")
            lines.append(str(display_context.details_url).strip())

        map_name_mapping = {
            "earthquake": "震中图",
            "amplitude": "最大波幅图",
            "coastal": "沿岸预报图",
        }
        for map_key, map_url in (display_context.map_urls or {}).items():
            if isinstance(map_url, str) and map_url.strip():
                map_label = map_name_mapping.get(map_key, map_key)
                lines.append(f"🗺️{map_label}：")
                lines.append(map_url.strip())

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示入口：合并上下文默认选项后格式化消息。"""
        merged_options = dict(display_context.options or {})
        if options:
            merged_options.update(options)
        return cls.format_message(display_context, merged_options)


class JmaTsunamiPresenter(BasePresenter):
    """日本气象厅海啸预报文本展示器。

    同时服务 P2P 与 EQSC，输出风格统一：
    - 标题行带 [解除]/[训练报] 标记
    - 等级圆形 emoji
    - 区域默认全部展示
    """

    presenter_name = "jma_tsunami_presenter"

    # 等级展示：优先日文官方用语，未知值原样回退
    LEVEL_MAPPING = {
        **GRADE_TITLE_MAP,
        "Unknown": "不明",
        "None": "なし",
    }

    @staticmethod
    def _meta(display_context: TsunamiDisplayContext) -> dict[str, Any]:
        """提取上下文元数据字典（非 dict 时返回空字典）。"""
        metadata = display_context.metadata
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _format_dt(value: Any, timezone: str) -> str:
        """格式化时间供展示。

        解析语义：
        - 无时区（naive）的 JMA/EQSC 时间按 UTC+9（JST）解释；
        - 已有时区则保留。

        展示语义：
        - 最终文本一律按调用方传入的 timezone（用户 display_timezone）输出，
          复用 TimeConverter.format_time 的统一样式。
        """
        if value is None:
            return ""
        if hasattr(value, "tzinfo"):
            display_time = value
            if display_time.tzinfo is None:
                display_time = display_time.replace(
                    tzinfo=TimeConverter.TIMEZONES["JST"]
                )
            return TimeConverter.format_time(display_time, timezone)
        text = str(value).strip()
        if not text:
            return ""
        parsed = TimeConverter.parse_datetime(text)
        if parsed is None:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TimeConverter.TIMEZONES["JST"])
        return TimeConverter.format_time(parsed, timezone)

    @classmethod
    def _resolve_timezone(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict[str, Any],
    ) -> str:
        """解析展示时区：优先用户配置，缺省回退 UTC+8。"""
        del display_context
        timezone = options.get("timezone")
        if timezone:
            return str(timezone)
        return "UTC+8"

    @classmethod
    def _build_header(
        cls,
        *,
        cancelled: bool,
        is_training: bool,
    ) -> str:
        """构建标题行。

        示例：
        - 🌊[津波予報] 日本气象厅
        - 🌊[津波予報] [解除] 日本气象厅
        - 🌊[津波予報] [训练报] 日本气象厅
        """
        tags: list[str] = []
        if cancelled:
            tags.append("[解除]")
        if is_training:
            tags.append("[训练报]")
        if tags:
            return f"🌊[津波予報] {' '.join(tags)} 日本气象厅"
        return "🌊[津波予報] 日本气象厅"

    @classmethod
    def _append_hypocenter_lines(
        cls,
        lines: list[str],
        display_context: TsunamiDisplayContext,
        metadata: dict[str, Any],
        timezone: str,
    ) -> None:
        """追加关联地震信息。"""
        place_name = (
            display_context.place_name
            or display_context.subtitle
            or str(metadata.get("place_name") or "").strip()
        )
        magnitude = display_context.magnitude
        if magnitude is None:
            magnitude = metadata.get("magnitude")
        shock_time = metadata.get("shock_time") or metadata.get("origin_time_raw")

        # 示例：🌍震源参数：三陸沖 Mj 8.2
        if place_name and magnitude is not None and magnitude != "":
            lines.append(f"🌍震源参数：{place_name} Mj {magnitude}")
        elif place_name:
            lines.append(f"🌍震源参数：{place_name}")
        elif magnitude is not None and magnitude != "":
            lines.append(f"🌍震源参数：Mj {magnitude}")

        if shock_time:
            shock_text = cls._format_dt(shock_time, timezone)
            if shock_text:
                lines.append(f"⏱️发震时刻：{shock_text}")

    @classmethod
    def _append_area_lines(
        cls,
        lines: list[str],
        forecasts: list[dict[str, Any]],
        metadata: dict[str, Any],
        *,
        cancelled: bool,
    ) -> None:
        """追加区域预报详情（默认全部展示，不折叠）。

        即使 forecasts 为空，仍输出 grade_counts / max_wave_height 等摘要字段。
        """
        grade_counts = metadata.get("grade_counts")
        if isinstance(grade_counts, dict) and grade_counts:
            summary_parts = []
            for grade in ("MajorWarning", "Warning", "Watch", "Minor"):
                count = grade_counts.get(grade)
                if count:
                    label = cls.LEVEL_MAPPING.get(grade, grade)
                    summary_parts.append(f"{label} {count}")
            if summary_parts:
                lines.append(f"📊级别分布：{' / '.join(summary_parts)}")

        max_wave = str(metadata.get("max_wave_height") or "").strip()
        max_wave_area = str(metadata.get("max_wave_height_area") or "").strip()
        if max_wave:
            if max_wave_area:
                lines.append(f"🌊全域最大预估波高：{max_wave}（{max_wave_area}）")
            else:
                lines.append(f"🌊全域最大预估波高：{max_wave}")

        if not forecasts:
            return

        immediate_areas: list[dict[str, Any]] = []
        normal_areas: list[dict[str, Any]] = []
        for forecast in forecasts:
            if not isinstance(forecast, dict):
                continue
            area_name = str(forecast.get("name") or "").strip()
            if not area_name:
                continue
            if coerce_bool(forecast.get("immediate"), default=False):
                immediate_areas.append(forecast)
            else:
                normal_areas.append(forecast)

        if immediate_areas:
            lines.append("🚨预测将立即发生海啸的区域：")
            for area in immediate_areas:
                lines.append(cls._format_area_line(area, cancelled=cancelled))

        if normal_areas:
            lines.append(f"📍津波予報区域（{len(normal_areas)}）：")
            for area in normal_areas:
                lines.append(cls._format_area_line(area, cancelled=cancelled))

    @classmethod
    def _format_area_line(
        cls,
        forecast: dict[str, Any],
        *,
        cancelled: bool = False,
    ) -> str:
        """格式化单个预报区。

        示例：  • 🟠福島県 [津波警報] (14:25頃) 🌊５ｍ
        """
        area_name = str(forecast.get("name") or "").strip()
        grade = str(forecast.get("grade") or "").strip()
        grade_label = _jp_level_label(grade, cancelled=cancelled) if grade else ""
        grade_emoji = _jp_level_emoji(grade, cancelled=cancelled)

        # emoji 放在区域名前
        if grade_emoji:
            area_info = f"  • {grade_emoji}{area_name}"
        else:
            area_info = f"  • {area_name}"
        if grade_label:
            area_info += f" [{grade_label}]"

        time_info: list[str] = []
        arrival_time = str(forecast.get("estimatedArrivalTime") or "").strip()
        condition = str(forecast.get("condition") or "").strip()
        if arrival_time:
            time_info.append(arrival_time)
        if condition and condition != arrival_time:
            time_info.append(condition)
        if time_info:
            area_info += f" ({' / '.join(time_info)})"

        max_wave = str(
            forecast.get("maxWaveHeight") or forecast.get("maxHeightDescription") or ""
        ).strip()
        if max_wave:
            area_info += f" 🌊{max_wave}"
        return area_info

    @classmethod
    def format_message(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict | None = None,
    ) -> str:
        """格式化日本气象厅海啸预报消息。"""
        options = options or {}
        timezone = cls._resolve_timezone(display_context, options)
        metadata = cls._meta(display_context)

        cancelled = bool(
            display_context.level == "解除"
            or coerce_bool(metadata.get("cancelled"), default=False)
        )
        is_training = coerce_bool(metadata.get("is_training"), default=False)

        lines = [
            cls._build_header(
                cancelled=cancelled,
                is_training=is_training,
            )
        ]

        # 标题 + 等级 emoji（解除通常不附圆形色）
        title = str(display_context.title or "").strip()
        level = str(display_context.level or "").strip()
        level_label = _jp_level_label(level, cancelled=cancelled)
        level_emoji = _jp_level_emoji(level, cancelled=cancelled)
        if title:
            title_line = f"📋{title}"
            if level_emoji and not cancelled:
                title_line += level_emoji
            lines.append(title_line)
        elif level_label:
            title_line = f"📋{level_label}"
            if level_emoji and not cancelled:
                title_line += level_emoji
            lines.append(title_line)

        if display_context.issued_at:
            lines.append(
                f"⏰发表时间：{cls._format_dt(display_context.issued_at, timezone)}"
            )
        elif metadata.get("issue_time_raw"):
            lines.append(
                f"⏰发表时间：{cls._format_dt(metadata.get('issue_time_raw'), timezone)}"
            )

        cls._append_hypocenter_lines(lines, display_context, metadata, timezone)

        # 解除：精简正文，突出无需担心
        if cancelled:
            lines.append("✅津波の心配はありません（无需担心海啸）")
            return "\n".join(lines)

        # 非解除：区域与波高详情；标题与区域块之间可空一行增强可读性
        forecasts = [
            item for item in (display_context.forecasts or []) if isinstance(item, dict)
        ]
        if forecasts or metadata.get("max_wave_height") or metadata.get("grade_counts"):
            # 震源块与区域块之间空一行（与示例一致）
            if any(line.startswith("🌍") or line.startswith("⏱️") for line in lines):
                lines.append("")
            cls._append_area_lines(lines, forecasts, metadata, cancelled=False)

        expires_at = metadata.get("expires_at") or metadata.get("expires_at_raw")
        if expires_at:
            expires_text = cls._format_dt(expires_at, timezone)
            if expires_text:
                lines.append(f"⌛有效期至：{expires_text}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: TsunamiDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示入口：合并上下文默认选项后格式化消息。"""
        merged_options = dict(display_context.options or {})
        if options:
            merged_options.update(options)
        return cls.format_message(display_context, merged_options)
