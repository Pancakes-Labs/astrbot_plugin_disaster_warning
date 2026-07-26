"""
地震展示器。

该模块负责把地震展示上下文转换为适合发送的文本内容，
覆盖中国、台湾、日本、全球地震等多类来源。
其中既包含通用格式化辅助函数，也包含各来源独立的展示器实现。
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone as dt_timezone

from ....utils.converters import ScaleConverter
from ....utils.time_converter import TimeConverter
from ...domain.event_context import EarthquakeDisplayContext
from ...services.geo.cn_district_intensity_service import (
    CnDistrictIntensityService,
)
from ...services.geo.intensity_service import IntensityCalculator
from ...services.geo.jma_seis_int_loc_loader import get_sect_map
from .base_presenter import BasePresenter


def _format_coordinates(latitude: float, longitude: float) -> str:
    """把经纬度格式化为带方向标识的文本。"""
    lat_dir = "N" if latitude >= 0 else "S"
    lon_dir = "E" if longitude >= 0 else "W"
    return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"


def _get_intensity_emoji(value, is_eew: bool = True, is_shindo: bool = False) -> str:
    """根据烈度或震度值选择对应的颜色图标。"""
    if value is None:
        return ""

    # 预警场景与普通情报场景使用两套图形，便于视觉区分。
    circles = ["⚪", "🔵", "🟢", "🟡", "🟠", "🔴", "🟣"]
    squares = ["⬜", "🟦", "🟩", "🟨", "🟧", "🟥", "🟪"]
    emojis = circles if is_eew else squares

    try:
        val_str = str(value)
        num_val = None

        # 必须捕获可选负号，否则 S-Net 計測震度 -2.8 会被当成 2.8
        match = re.search(r"(-?\d+(?:\.\d+)?)", val_str)
        if match:
            num_val = float(match.group(1))

        idx = 0
        if is_shindo:
            # 日本、台湾震度体系既可能传入数值，也可能传入带符号的字符串，
            # 因此这里同时兼容数字阈值判断与字符串兜底识别。
            if num_val is not None:
                # 判断日本气象厅的十级 shindo 震度标尺
                if num_val >= 9:
                    if num_val < 20:
                        idx = 0
                    elif num_val < 30:
                        idx = 1
                    elif num_val < 40:
                        idx = 2
                    elif num_val < 45:
                        idx = 3
                    elif num_val < 55:
                        idx = 4
                    elif num_val < 65:
                        idx = 5
                    else:
                        idx = 6
                else:
                    # 0 以下 / 震度 0~1 ：最低档（白）
                    if num_val < 1.5:
                        idx = 0
                    elif num_val < 2.5:
                        idx = 1
                    elif num_val < 3.5:
                        idx = 2
                    elif num_val < 4.5:
                        idx = 3
                    elif num_val < 5.5:
                        idx = 4
                    elif num_val < 6.5:
                        idx = 5
                    else:
                        idx = 6
            elif "7" in val_str:
                idx = 6
            elif "6" in val_str:
                idx = 5
            elif "5" in val_str:
                idx = 4
            elif "4" in val_str:
                idx = 3
            elif "3" in val_str:
                idx = 2
            elif "2" in val_str:
                idx = 1
            else:
                idx = 0
        else:
            # 中国 CENC 的十二级烈度体系映射判断
            if num_val is not None:
                if num_val < 2.5:
                    idx = 0
                elif num_val < 4.5:
                    idx = 1
                elif num_val < 5.5:
                    idx = 2
                elif num_val < 6.5:
                    idx = 3
                elif num_val < 8.5:
                    idx = 4
                elif num_val < 10.5:
                    idx = 5
                else:
                    idx = 6
            else:
                idx = 0
        return emojis[idx]
    except Exception:
        return ""


def _format_depth(depth: float) -> str:
    """格式化震源深度文本。"""
    if depth == 0.0:
        return "极浅"
    return f"{depth} km"


def _resolve_options(
    display_context: EarthquakeDisplayContext,
    options: dict | None = None,
) -> dict:
    """合并上下文内置选项与调用时传入选项。"""
    merged = dict(display_context.options or {})
    if options:
        merged.update(options)
    return merged


def _is_earthquake_view(data) -> bool:
    """判断输入对象是否具备地震展示所需的基础字段。"""
    return all(
        hasattr(data, attr)
        for attr in ["title", "latitude", "longitude", "magnitude", "depth"]
    )


def _resolve_report_num(data: EarthquakeDisplayContext) -> int:
    """获取合法的第几报值。"""
    if isinstance(data.report_num, int) and data.report_num > 0:
        return data.report_num
    return 1


def _resolve_shock_time(display_context: EarthquakeDisplayContext):
    """解析展示时使用的发震时间。"""
    return display_context.occurred_at


def _append_local_estimation(
    lines: list[str],
    display_context: EarthquakeDisplayContext,
    *,
    include_travel_time: bool = True,
) -> None:
    """把本地影响预估信息附加到文本尾部。

    Args:
        lines: 待追加的消息行列表。
        display_context: 地震展示上下文。
        include_travel_time: 是否附加 P/S 波预计到达时间。
            仅地震预警（EEW）场景有意义；正式测定/情报类应传 False。
    """
    local_est = display_context.local_estimation
    if not local_est:
        return

    dist = local_est.get("distance", 0.0)
    inte = local_est.get("intensity", 0.0)
    place = local_est.get("place_name", "本地")
    desc = IntensityCalculator.get_intensity_description(inte)

    lines.append("")
    lines.append(f"📍{place}预估：")
    lines.append(f"距离震中 {dist:.1f} km，预估最大烈度 {inte:.1f} ({desc})")

    # P/S 波预计到达时间仅对预警类消息有意义（震后情报已无预警价值）
    if not include_travel_time:
        return

    p_sec = local_est.get("p_travel_sec")
    s_sec = local_est.get("s_travel_sec")
    if p_sec is not None:
        lines.append(f"⏱️预计P波到达：约 {p_sec:.0f} 秒")
    if s_sec is not None:
        lines.append(f"⏱️预计S波到达：约 {s_sec:.0f} 秒")


def _append_cn_district_estimation(
    lines: list[str],
    display_context: EarthquakeDisplayContext,
) -> None:
    """把中国影响区县预估列表附加到文本尾部。

    仅用于中国地震预警展示；正式测定不附加。
    仅在震中位于中国大陆附近、且能解析出受影响区县时输出。
    资源加载失败或无命中区县时静默跳过，不影响主推送链路。
    """
    lat = display_context.latitude
    lon = display_context.longitude
    mag = display_context.magnitude
    depth = display_context.depth
    # 缺少必要参数时跳过
    if lat is None or lon is None or mag is None or depth is None:
        return

    try:
        estimates = CnDistrictIntensityService.estimate_affected_districts(
            float(lat), float(lon), float(mag), float(depth)
        )
    except Exception:
        return

    if not estimates:
        return

    # 按烈度整数分组
    groups = CnDistrictIntensityService.group_by_intensity(estimates)
    if not groups:
        return

    lines.append("")
    lines.append("📡预估影响区县（仅供参考）：")
    for level, names in groups.items():
        emoji = _get_intensity_emoji(float(level), is_eew=True, is_shindo=False)
        # 每行最多展示 5 个区县名，超出部分用「等N处」省略
        max_show = 5
        if len(names) > max_show:
            loc_str = "、".join(names[:max_show]) + f" 等{len(names)}处"
        else:
            loc_str = "、".join(names)
        lines.append(f"  {emoji}[烈度{level}] {loc_str}")


class CeaEewPresenter(BasePresenter):
    """中国地震预警网展示器。"""

    presenter_name = "cea_eew_presenter"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建地震预警的基础中文文本消息内容。"""
        if not _is_earthquake_view(data):
            return "🚨[地震预警] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")

        # 省级来源存在时，优先展示更具体的属地机构名称；缺失或占位值回退到全国机构名。
        source_name = "中国地震预警网"
        province_name = str(data.province or "").strip()
        if province_name and province_name.lower() not in {"none", "null", "unknown"}:
            source_name = f"{province_name}地震局"

        lines = [f"🚨[地震预警] {source_name}"]

        report_num = _resolve_report_num(data)
        is_final = data.is_final
        report_info = f"第 {report_num} 报"
        if is_final:
            report_info += "(最终报)"
        lines.append(f"📋{report_info}")

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")

        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")

        if data.intensity is not None:
            emoji = _get_intensity_emoji(data.intensity, is_eew=True, is_shindo=False)
            lines.append(f"💥预估最大烈度：{data.intensity} {emoji}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 CeaEew 消息入口，并附带本地影响距离估值与影响区县列表。

        本地预估（距离/烈度/P-S 波）跟随会话级 local_monitoring 配置：
        仅当该配置启用且上下文携带 local_estimation 时才会输出。
        影响区县列表为独立增强，不依赖本地监控开关。
        """
        rendered = cls.format_message(
            display_context, _resolve_options(display_context, options)
        )
        if not _is_earthquake_view(display_context):
            return rendered
        lines = rendered.split("\n") if rendered else []
        # 本地预估仅在上下文携带 local_estimation 时输出（跟随会话级配置）
        _append_local_estimation(lines, display_context)
        # 追加中国影响区县预估列表（独立于本地监控配置）
        if not any("预估影响区县" in line for line in lines):
            _append_cn_district_estimation(lines, display_context)
        return "\n".join(lines)


class CwaEewPresenter(BasePresenter):
    """台湾中央气象署地震预警展示器。"""

    presenter_name = "cwa_eew_presenter"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建台湾 CWA 地震预警中文基础文本。"""
        if not _is_earthquake_view(data):
            return "🚨[地震预警] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        lines = ["🚨[地震预警] 台湾中央气象署"]

        report_num = _resolve_report_num(data)
        is_final = data.is_final
        report_info = f"第 {report_num} 报"
        if is_final:
            report_info += "(最终报)"
        lines.append(f"📋{report_info}")

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")

        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")

        if data.scale is not None:
            scale_display = ScaleConverter.format_jma_cwa_scale_display(data.scale)
            emoji = _get_intensity_emoji(data.scale, is_eew=True, is_shindo=True)
            lines.append(f"💥预估最大震度：{scale_display} {emoji}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示入口，并拼接影响区域及本地最大震级预估。"""
        rendered = cls.format_message(
            display_context, _resolve_options(display_context, options)
        )
        if not _is_earthquake_view(display_context):
            return rendered

        lines = rendered.split("\n") if rendered else []
        impact_area = display_context.impact_area
        impact_area_text = str(impact_area).strip() if impact_area is not None else ""
        if impact_area_text.lower() in {"none", "null", "undefined"}:
            impact_area_text = ""
        if impact_area_text and not any(
            line.startswith("⚠️影响区域：") for line in lines
        ):
            inserted = False
            for idx, line in enumerate(lines):
                if line.startswith("💥预估最大震度："):
                    lines.insert(idx + 1, f"⚠️影响区域：{impact_area_text}")
                    inserted = True
                    break
            if not inserted:
                lines.append(f"⚠️影响区域：{impact_area_text}")

        _append_local_estimation(lines, display_context)
        return "\n".join(lines)


class JmaEewPresenter(BasePresenter):
    """日本气象厅紧急地震速报展示器。"""

    presenter_name = "jma_eew_presenter"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """格式化日本紧急地震速报的文本摘要。"""
        if not _is_earthquake_view(data):
            return "🚨[紧急地震速报] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        if data.is_cancel:
            # 取消报单独走极简格式，避免保留无效的震中与震级信息。
            updates = _resolve_report_num(data)
            return (
                f"🚨[紧急地震速报] [取消] 日本气象厅\n"
                f"📋第 {updates} 报 (取消报)\n"
                "📝之前的紧急地震速报已取消"
            )

        warning_type = data.jma_issue_type or "予报"
        if not data.jma_issue_type and data.scale is not None and data.scale >= 4.5:
            warning_type = "警报"

        header_tags = []
        if data.is_training:
            header_tags.append("训练")
        if data.is_assumption:
            header_tags.append("PLUM法所得假定震源")
        # 训练报、假定震源等附加标签统一拼接在标题中，便于用户第一眼识别消息性质。
        tag_str = f" [{'/'.join(header_tags)}]" if header_tags else ""
        lines = [f"🚨[紧急地震速报] [{warning_type}]{tag_str} 日本气象厅"]

        report_num = _resolve_report_num(data)
        is_final = data.is_final
        report_info = f"第 {report_num} 报"
        if is_final:
            report_info += "(最终报)"
        lines.append(f"📋{report_info}")

        shock_time = _resolve_shock_time(data)
        if shock_time:
            display_time = shock_time
            if getattr(display_time, "tzinfo", None) is None:
                display_time = TimeConverter.parse_datetime(display_time).replace(
                    tzinfo=TimeConverter._get_timezone("Asia/Tokyo")
                )
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(display_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")

        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")

        if data.scale is not None:
            scale_display = ScaleConverter.format_jma_cwa_scale_display(data.scale)
            emoji = _get_intensity_emoji(data.scale, is_eew=True, is_shindo=True)
            lines.append(f"💥预估最大震度：{scale_display} {emoji}")
        elif data.intensity is not None:
            intensity_display = ScaleConverter.format_jma_cwa_scale_display(
                data.intensity
            )
            emoji = _get_intensity_emoji(data.intensity, is_eew=True, is_shindo=True)
            lines.append(f"💥预估最大震度：{intensity_display} {emoji}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示速报，并拼装细分的警报覆盖县市与预估范围。"""
        rendered = cls.format_message(
            display_context, _resolve_options(display_context, options)
        )
        if not _is_earthquake_view(display_context):
            return rendered

        lines = rendered.split("\n") if rendered else []

        jma_warning_areas = display_context.jma_warning_areas
        if (
            isinstance(jma_warning_areas, list)
            and jma_warning_areas
            and not any(line.startswith("⚠️警报区域：") for line in lines)
        ):
            lines.append("⚠️警报区域：")
            # 多区域场景按固定数量分行，避免单行过长影响阅读。
            chunk_size = 3
            for i in range(0, len(jma_warning_areas), chunk_size):
                chunk = [
                    str(item).strip()
                    for item in jma_warning_areas[i : i + chunk_size]
                    if str(item).strip()
                ]
                if chunk:
                    lines.append("  " + "、".join(chunk))

        jma_warn_area = display_context.jma_warn_area
        if (
            isinstance(jma_warn_area, str)
            and jma_warn_area.strip()
            and not any(line.startswith("⚠️警报区域：") for line in lines)
        ):
            lines.append(f"⚠️警报区域：{jma_warn_area.strip()}")

        jma_warning_area_ranges = display_context.jma_warning_area_ranges
        if isinstance(jma_warning_area_ranges, list):
            for shindo_range in jma_warning_area_ranges:
                if (
                    isinstance(shindo_range, str)
                    and shindo_range.strip()
                    and not any(
                        line.startswith("💥预估震度范围：")
                        and shindo_range.strip() in line
                        for line in lines
                    )
                ):
                    lines.append(f"💥预估震度范围：{shindo_range.strip()}")

        _append_local_estimation(lines, display_context)
        return "\n".join(lines)


class CencEarthquakePresenter(BasePresenter):
    """中国地震台网地震测定展示器。"""

    presenter_name = "cenc_earthquake_presenter"

    @staticmethod
    def _format_coordinates(latitude: float, longitude: float) -> str:
        lat_dir = "N" if latitude >= 0 else "S"
        lon_dir = "E" if longitude >= 0 else "W"
        return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"

    @staticmethod
    def _format_depth(depth: float) -> str:
        if depth == 0.0:
            return "极浅"
        return f"{depth} km"

    @staticmethod
    def _determine_measurement_type(data: EarthquakeDisplayContext) -> str:
        """判断当前测定属于自动测定还是正式测定。"""
        info_type = data.jma_issue_type.strip() or str(
            data.metadata.get("info_type") or data.metadata.get("infoTypeName") or ""
        )
        info_type_lower = info_type.lower()
        if "正式测定" in info_type or "reviewed" in info_type_lower:
            return "正式测定"
        if "自动测定" in info_type or "automatic" in info_type_lower:
            return "自动测定"
        return "自动测定"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """格式化 CENC 信息为展示文本。"""
        if not _is_earthquake_view(data):
            return "🚨[地震情报] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        # 中国地震台网测定结果会区分自动测定与正式测定，直接体现在标题中。
        measurement_type = cls._determine_measurement_type(data)
        lines = [f"🚨[地震情报] 中国地震台网 [{measurement_type}]"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )
        if data.title and data.latitude is not None and data.longitude is not None:
            coords = cls._format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")
        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")
        if data.depth is not None:
            lines.append(f"🏔️深度：{cls._format_depth(data.depth)}")
        if data.intensity is not None:
            emoji = _get_intensity_emoji(data.intensity, is_eew=False, is_shindo=False)
            lines.append(f"💥最大烈度：{data.intensity} {emoji}")
        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示中国地震台网测定。

        测定类型（自动测定/正式测定等地震情报类）不附加本地预估信息。
        """
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class UsgsEarthquakePresenter(BasePresenter):
    """美国地质调查局地震情报展示器。"""

    presenter_name = "usgs_earthquake_presenter"

    @staticmethod
    def _format_coordinates(latitude: float, longitude: float) -> str:
        lat_dir = "N" if latitude >= 0 else "S"
        lon_dir = "E" if longitude >= 0 else "W"
        return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"

    @staticmethod
    def _format_depth(depth: float) -> str:
        if depth == 0.0:
            return "极浅"
        return f"{depth} km"

    @staticmethod
    def _determine_measurement_type(data: EarthquakeDisplayContext) -> str:
        """根据附加信息判断测定类型。"""
        info_type = data.jma_issue_type.strip() or str(
            data.metadata.get("info_type") or data.metadata.get("infoTypeName") or ""
        )
        info_type_lower = info_type.lower()
        if info_type_lower == "reviewed":
            return "正式测定"
        if info_type_lower == "automatic":
            return "自动测定"
        return "自动测定"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """格式化 USGS 地震事件情报。"""
        if not _is_earthquake_view(data):
            return "🚨[地震情报] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        # 美国地质调查局同样可能区分自动结果与复核结果。
        measurement_type = cls._determine_measurement_type(data)
        lines = [f"🚨[地震情报] 美国地质调查局(USGS) [{measurement_type}]"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )
        if data.title and data.latitude is not None and data.longitude is not None:
            coords = cls._format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")
        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")
        if data.depth is not None:
            lines.append(f"🏔️深度：{cls._format_depth(data.depth)}")
        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 USGS 测定。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class FssnCmtPresenter(BasePresenter):
    """FSSN 矩心矩张量解 (CMT) 展示器。"""

    presenter_name = "fssn_cmt_presenter"

    @staticmethod
    def _format_depth_error(depth: float | None, error: float | None) -> str:
        if depth is None:
            return "未知"
        if depth == 0.0:
            return "极浅"
        if error is not None:
            return f"{depth} km (±{error})"
        return f"{depth} km"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建 FSSN CMT 中文基础文本。"""
        if not _is_earthquake_view(data):
            return "🚨[CMT] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")

        # 标题固定使用 [CMT] FSSN 矩心矩张量解
        lines = ["🌐[CMT] FSSN 矩心矩张量解"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        # 整理 CMT 震级格式：M xxx (Mww yyy / mB zzz ...)
        meta = data.metadata if isinstance(data.metadata, dict) else {}
        all_mags = meta.get("all_magnitudes") or {}

        # 主显示使用 display_magnitude_type，默认取 stats_magnitude (M)
        display_mag = data.magnitude
        display_mag_type = meta.get("display_magnitude_type", "M")

        # 补充震级列表
        mag_tokens = []
        for key in ("Mww", "mB", "mb", "MLv", "Mwp"):
            val = all_mags.get(key)
            if val is not None and key != display_mag_type:
                mag_tokens.append(f"{key} {val:.1f}")

        if mag_tokens:
            lines.append(
                f"📊震级：{display_mag_type} {display_mag:.1f}（{' / '.join(mag_tokens)}）"
            )
        else:
            lines.append(f"📊震级：{display_mag_type} {display_mag:.1f}")

        # 震源深度与矩心深度
        depth_val = meta.get("depth") if meta.get("depth") is not None else data.depth
        depth_err = meta.get("depth_error")
        centroid_val = meta.get("centroid_depth")

        depth_text = cls._format_depth_error(depth_val, depth_err)
        if centroid_val is not None:
            lines.append(f"🏔️深度：{depth_text}｜矩心深度：{centroid_val} km")
        else:
            lines.append(f"🏔️深度：{depth_text}")

        # 两个节面断层走向、倾角与滑动角
        plane1 = meta.get("nodal_plane1")
        plane2 = meta.get("nodal_plane2")
        from ...domain.earthquake.cmt_normalize import format_fault_type_label

        if plane1:
            lines.append(
                f"🧭节面1：走向 {plane1.get('strike')}° / 倾角 {plane1.get('dip')}° / 滑动角 {plane1.get('rake')}°"
            )
            lines.append(f"    类型：{format_fault_type_label(plane1)}")
        if plane2:
            lines.append(
                f"🧭节面2：走向 {plane2.get('strike')}° / 倾角 {plane2.get('dip')}° / 滑动角 {plane2.get('rake')}°"
            )
            lines.append(f"    类型：{format_fault_type_label(plane2)}")

        lines.append("📋备注：左/右旋最终确定需依赖实际发震断层面")
        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 FSSN CMT。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class ShakeAlertEewPresenter(BasePresenter):
    """美国 ShakeAlert 地震预警展示器。"""

    presenter_name = "shakealert_eew_presenter"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建 ShakeAlert 地震预警中文基础文本。"""
        if not _is_earthquake_view(data):
            return "🚨[地震预警] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        lines = ["🚨[地震预警] 美国 ShakeAlert"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")

        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 ShakeAlert 预警（不附加本地预估）。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class JmaEarthquakeInfoPresenter(BasePresenter):
    """日本气象厅地震情报展示器。"""

    presenter_name = "jma_earthquake_info_presenter"

    @staticmethod
    def _determine_info_type(data: EarthquakeDisplayContext) -> str:
        """推断日本气象厅地震情报的展示类别。"""
        info_type = data.jma_issue_type or ""
        type_mapping = {
            "ScalePrompt": "震度速报",
            "Destination": "震源相关情报",
            "ScaleAndDestination": "震度・震源相关情报",
            "DetailScale": "各地震度相关情报",
            "Foreign": "远地地震相关情报",
            "Other": "其他情报",
        }

        if info_type in type_mapping:
            return type_mapping[info_type]
        if info_type and any("\u4e00" <= char <= "\u9fff" for char in info_type):
            return info_type
        # 当震中与震级尚未明确时，更倾向视为震度速报场景。
        if (data.title == "未知地点" or not data.title) and (
            data.magnitude is None or data.magnitude == -1.0
        ):
            return "震度速报"
        if data.scale is None:
            return "震源相关情报"
        return "震源・震度情报"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """拼装日本气象厅正式地震速报/情报基础文本。"""
        if not _is_earthquake_view(data):
            return "🚨[地震情报] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        info_type = cls._determine_info_type(data)

        revision_text = (
            data.revision.strip()
            if hasattr(data, "revision") and isinstance(data.revision, str)
            else ""
        )
        if revision_text.lower() == "none":
            revision_text = ""

        # 更正、订正等修订标记直接跟在标题后，便于识别本条是否为后续修正情报。
        correct_tag = f" [{revision_text}]" if revision_text else ""

        lines = [f"🚨[{info_type}]{correct_tag} 日本气象厅"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            display_time = shock_time
            if getattr(display_time, "tzinfo", None) is None:
                display_time = TimeConverter.parse_datetime(display_time).replace(
                    tzinfo=TimeConverter._get_timezone("Asia/Tokyo")
                )
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(display_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")
        elif info_type == "震度速报":
            lines.append("📍震中：调查中")

        if data.magnitude is not None and data.magnitude != -1.0:
            lines.append(f"📊震级：M {data.magnitude:.1f}")
        elif info_type == "震度速报":
            lines.append("📊震级：调查中")

        if data.depth is not None and data.depth != -1.0:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")
        elif info_type != "震度速报":
            lines.append("🏔️深度：调查中")

        if data.scale is not None:
            scale_display = ScaleConverter.format_jma_cwa_scale_display(data.scale)
            emoji = _get_intensity_emoji(data.scale, is_eew=False, is_shindo=True)
            lines.append(f"💥最大震度：{scale_display} {emoji}")

        domestic_tsunami = data.domestic_tsunami
        if domestic_tsunami:
            # 日本情报里的海啸字段直接影响风险理解，因此一并翻译成可读文本。
            tsunami_mapping = {
                "None": "无需担心海啸",
                "Unknown": "不明",
                "Checking": "调查中",
                "NonEffective": "预计会有若干海面变动，无须担心受害",
                "Watch": "正在/已经发布津波注意报",
                "Warning": "正在/已经发布津波警报/大津波警报",
            }
            tsunami_info = tsunami_mapping.get(domestic_tsunami, domestic_tsunami)
            lines.append(f"🌊津波：{tsunami_info}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示日本地震情报，可根据配置展开显示每个具体观测点的详细震度。"""
        merged_options = _resolve_options(display_context, options)
        rendered = cls.format_message(display_context, merged_options)
        if not _is_earthquake_view(display_context):
            return rendered

        lines = rendered.split("\n") if rendered else []
        jma_points = display_context.jma_points
        if (
            isinstance(jma_points, list)
            and jma_points
            and not any(
                line.startswith("📡各地震度详情：") or line.startswith("📡震度 ")
                for line in lines
            )
        ):
            scale_groups: dict[object, list[str]] = {}
            for point in jma_points:
                if not isinstance(point, dict):
                    continue
                scale = point.get("scale", 0)
                addr = str(point.get("addr", "") or "").strip()
                if not addr:
                    continue
                scale_groups.setdefault(scale, []).append(addr)

            if scale_groups:
                if merged_options.get("jma_region_intensity", True):
                    # 地域汇总模式：将町丁目按 sect 聚合，
                    # 每个 sect 的震度取其内所有町丁目的最大震度，
                    # 然后按震度从高到低分组展示地域列表，不做截断。
                    sect_map = get_sect_map()
                    if sect_map:
                        # 每个 sect 的最大震度
                        # scale_key 来自 jma_points 原始数据，可能是 int（P2P）
                        # 或 str（Wolfx），避免异质类型比较使用 in 检查
                        sect_max_scale: dict[str, object] = {}
                        for scale_key, addrs in scale_groups.items():
                            for addr in addrs:
                                sect = sect_map.get(addr)
                                if not sect:
                                    continue
                                if (
                                    sect not in sect_max_scale
                                    or scale_key > sect_max_scale[sect]
                                ):
                                    sect_max_scale[sect] = scale_key

                        if sect_max_scale:
                            # 按震度从高到低分组展示地域
                            region_scale_groups: dict[object, list[str]] = {}
                            for sect, s_scale in sect_max_scale.items():
                                region_scale_groups.setdefault(s_scale, []).append(sect)

                            sorted_scales = sorted(
                                region_scale_groups.keys(), reverse=True
                            )
                            lines.append("📡各地震度详情：")
                            for scale_key in sorted_scales:
                                scale_disp = (
                                    ScaleConverter.format_jma_cwa_scale_display(
                                        scale_key
                                    )
                                )
                                emoji = _get_intensity_emoji(
                                    scale_key, is_eew=False, is_shindo=True
                                )
                                # 地域级不做截断，完整展示所有地域
                                locs = sorted(region_scale_groups[scale_key])
                                loc_str = "、".join(locs)
                                lines.append(f"  {emoji}[震度{scale_disp}] {loc_str}")
                        else:
                            # sect 映射全部未命中时回退到町丁目模式
                            cls._render_town_scale_groups(
                                merged_options, scale_groups, lines
                            )
                    else:
                        # 映射表加载失败时回退到町丁目模式
                        cls._render_town_scale_groups(
                            merged_options, scale_groups, lines
                        )
                else:
                    cls._render_town_scale_groups(merged_options, scale_groups, lines)

        jma_comment = display_context.jma_comment
        if (
            isinstance(jma_comment, str)
            and jma_comment.strip()
            and not any(line.startswith("📝备注：") for line in lines)
        ):
            lines.append(f"📝备注：{jma_comment.strip()}")

        return "\n".join(lines)

    @staticmethod
    def _render_town_scale_groups(
        merged_options: dict,
        scale_groups: dict[object, list[str]],
        lines: list[str],
    ) -> None:
        """按町丁目级震度分组渲染（原始模式，带截断）。

        当 detailed_jma_intensity 开启时逐级展示所有震度的町丁目，
        否则仅展示最大震度的代表观测点。町丁目列表会做截断处理以控制文本长度。
        """
        if merged_options.get("detailed_jma_intensity", False):
            # 详细模式下按震度从高到低逐级展开展示。
            sorted_scales = sorted(scale_groups.keys(), reverse=True)
            lines.append("📡各地震度详情：")
            for scale_key in sorted_scales:
                scale_disp = ScaleConverter.format_jma_cwa_scale_display(scale_key)
                emoji = _get_intensity_emoji(scale_key, is_eew=False, is_shindo=True)
                locs = scale_groups[scale_key]
                max_show = 20
                loc_str = "、".join(locs[:max_show])
                if len(locs) > max_show:
                    loc_str += f" 等{len(locs)}处"
                lines.append(f"  {emoji}[震度{scale_disp}] {loc_str}")
        else:
            # 简略模式仅展示最大震度对应的代表观测点，控制文本长度。
            max_scale_key = max(scale_groups.keys())
            scale_disp = ScaleConverter.format_jma_cwa_scale_display(max_scale_key)
            emoji = _get_intensity_emoji(max_scale_key, is_eew=False, is_shindo=True)
            locs = scale_groups[max_scale_key][:5]
            suffix = "等" if len(scale_groups[max_scale_key]) > 5 else ""
            lines.append(
                f"📡震度 {scale_disp} {emoji} 观测点：{'、'.join(locs)}{suffix}"
            )


class GlobalQuakeTextPresenter(BasePresenter):
    """Global Quake 文本展示器。"""

    presenter_name = "global_quake_text_presenter"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建 GlobalQuake 测定情报文本消息。"""
        if data.is_cancel:
            # 适配 Global Quake 取消报
            report_num = _resolve_report_num(data)
            return (
                f"🚨[地震预警] [取消] Global Quake\n"
                f"📋第 {report_num} 报 (取消报)\n"
                "📝该地震的预警信息已被撤销/删除"
            )

        if not _is_earthquake_view(data):
            return "🚨[地震预警] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        lines = ["🚨[地震预警] Global Quake"]

        report_num = _resolve_report_num(data)
        is_final = data.is_final
        report_info = f"第 {report_num} 报"
        if is_final:
            report_info += "(最终报)"
        lines.append(f"📋{report_info}")

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")

        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")

        if data.intensity is not None:
            emoji = _get_intensity_emoji(data.intensity, is_eew=True, is_shindo=False)
            lines.append(f"💥预估最大烈度：{data.intensity} {emoji}")

        if data.max_pga is not None:
            lines.append(f"📈最大加速度：{data.max_pga:.1f} gal")

        stations = data.stations
        if isinstance(stations, dict):
            # Global Quake 常会附带触发测站统计，用于体现当前解算基础。
            total = stations.get("total", 0)
            used = stations.get("used", 0)
            lines.append(f"📡触发测站：{used}/{total}")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 GlobalQuake 消息。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class CwaReportPresenter(BasePresenter):
    """台湾中央气象署地震报告展示器。"""

    presenter_name = "cwa_report_presenter"

    @staticmethod
    def _format_depth(depth: float) -> str:
        if depth == 0.0:
            return "极浅"
        return f"{depth} km"

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建台湾 CWA 地震报告的富文本/图片链接消息。"""
        if not _is_earthquake_view(data):
            return "🚨[地震报告] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        # 台湾报告类消息会额外带上报告图片和等震度图地址。
        image_uri = merged_options.get("image_uri")
        shakemap_uri = merged_options.get("shakemap_uri")
        lines = ["🚨[地震报告] 台湾中央气象署"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )
        if data.title and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{data.title} ({coords})")
        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")
        if data.depth is not None:
            lines.append(f"🏔️深度：{cls._format_depth(data.depth)}")
        if isinstance(image_uri, str) and image_uri.strip():
            lines.append("🖼️报告图片：")
            lines.append(image_uri.strip())
        if isinstance(shakemap_uri, str) and shakemap_uri.strip():
            lines.append("🗺️等震度图：")
            lines.append(shakemap_uri.strip())
        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示报告，并优先从上下文载入等震度图。"""
        merged_options = _resolve_options(display_context, options)
        # 若调用方未显式覆盖图片地址，则优先使用展示上下文自带的链接。
        merged_options.setdefault("image_uri", display_context.image_uri)
        merged_options.setdefault("shakemap_uri", display_context.shakemap_uri)
        return cls.format_message(display_context, merged_options)


class CencIntensityReportPresenter(BasePresenter):
    """中国地震台网烈度速报展示器。"""

    presenter_name = "cenc_ir_report_presenter"
    _TOP_N = 5

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建中国地震台网烈度速报文本。"""
        if not _is_earthquake_view(data):
            return "🚨[烈度速报] 数据类型错误"

        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        metadata = data.metadata if isinstance(data.metadata, dict) else {}
        lines = ["🚨[烈度速报] 中国地震台网"]

        shock_time = _resolve_shock_time(data)
        if shock_time:
            lines.append(
                f"⏰发震时间：{TimeConverter.format_time(shock_time, timezone)}"
            )

        place_name = str(data.title or "").strip()
        if place_name and data.latitude is not None and data.longitude is not None:
            coords = _format_coordinates(data.latitude, data.longitude)
            lines.append(f"📍震中：{place_name} ({coords})")
        elif place_name:
            lines.append(f"📍震中：{place_name}")

        if data.magnitude is not None:
            lines.append(f"📊震级：M {data.magnitude:.1f}")
        if data.depth is not None:
            lines.append(f"🏔️深度：{_format_depth(data.depth)}")
        if data.intensity is not None:
            emoji = _get_intensity_emoji(data.intensity, is_eew=False, is_shindo=False)
            lines.append(f"💥最大仪器烈度：{data.intensity} {emoji}")

        intensity_info_text = str(
            metadata.get("intensity_info_text")
            or merged_options.get("intensity_info_text")
            or ""
        ).strip()
        if intensity_info_text:
            lines.append(f"📝烈度概述：{intensity_info_text}")

        stations = metadata.get("stations") or data.stations or []
        if isinstance(stations, dict):
            stations = list(stations.values())
        if not isinstance(stations, list):
            stations = []
        station_rows = [item for item in stations if isinstance(item, dict)]
        if station_rows:
            lines.append(f"📡台站实测 Top{min(cls._TOP_N, len(station_rows))}：")
            for row in station_rows[: cls._TOP_N]:
                name = str(row.get("name") or "未知台站").strip() or "未知台站"
                intensity = row.get("intensity")
                if intensity is None:
                    lines.append(f"  · {name}  烈度 --")
                    continue
                try:
                    intensity_text = f"{float(intensity):.1f}"
                except (TypeError, ValueError):
                    intensity_text = str(intensity)
                emoji = _get_intensity_emoji(intensity, is_eew=False, is_shindo=False)
                lines.append(f"  · {name}  烈度 {intensity_text} {emoji}".rstrip())

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示烈度速报。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )


class SnetPresenter(BasePresenter):
    """NIED S-Net 海底震度分布展示器。"""

    presenter_name = "snet_presenter"
    _TOP_N = 5

    @classmethod
    def format_message(
        cls, data: EarthquakeDisplayContext, options: dict | None = None
    ) -> str:
        """构建 S-Net 文本消息。

        样例（手机窄屏尽量单行）：
        🚨[S-Net震度分布] NIED
        ⏰更新时间：2026年07月15日 20时29分00秒 (UTC+8)
        📊震度降序前 5 测站：
          N.S5N06  ⚪震度0以下 (-0.792)
        """
        merged_options = dict(options or {})
        timezone = merged_options.get("timezone", "UTC+8")
        metadata = data.metadata if isinstance(data.metadata, dict) else {}

        stations = metadata.get("stations") or data.stations or []
        if isinstance(stations, dict):
            stations = list(stations.values())
        if not isinstance(stations, list):
            stations = []
        station_rows = [s for s in stations if isinstance(s, dict)]

        display_time = "未知时间"
        shock_time = _resolve_shock_time(data)
        if shock_time:
            display_time = TimeConverter.format_time(shock_time, timezone)
        else:
            timestamp = str(metadata.get("timestamp") or "").strip()
            if timestamp:
                try:
                    dt = datetime.strptime(timestamp, "%Y%m%d%H%M00").replace(
                        tzinfo=dt_timezone.utc
                    )
                    display_time = TimeConverter.format_time(dt, timezone)
                except (ValueError, TypeError):
                    display_time = timestamp

        sorted_stations = sorted(
            station_rows,
            key=lambda s: float(s.get("shindo", -999.0)),
            reverse=True,
        )
        top_n = int(merged_options.get("top_n", cls._TOP_N) or cls._TOP_N)
        top_n = max(1, min(top_n, 20))
        top_stations = sorted_stations[:top_n]

        lines = [
            "🚨[S-Net震度分布] NIED",
            f"⏰更新时间：{display_time}",
            f"📊震度降序前 {len(top_stations)} 测站：",
        ]
        for station in top_stations:
            name = str(station.get("name") or "?").strip() or "?"
            try:
                shindo = float(station.get("shindo", 0.0))
            except (TypeError, ValueError):
                shindo = 0.0
            # 复用项目统一震度文本与圆形 emoji 指示器
            scale_text = ScaleConverter.format_measured_intensity_display(shindo)
            if not scale_text:
                scale_text = "?"
            # 0以下仍走最低档白色圆形指示；其余按計測震度阈值选色
            emoji = _get_intensity_emoji(shindo, is_eew=True, is_shindo=True) or "⚪"
            # 两个空格 + 站名 + 两个空格 + emoji + 震度描述 + 空格 + 半角括号数值
            lines.append(f"  {name}  {emoji}震度{scale_text} ({shindo:.3f})")

        return "\n".join(lines)

    @classmethod
    def present(
        cls,
        display_context: EarthquakeDisplayContext,
        options: dict | None = None,
    ) -> str:
        """展示 S-Net 消息入口。"""
        return cls.format_message(
            display_context, _resolve_options(display_context, options)
        )
