"""
Global Quake 展示上下文构建器。

该模块负责把地震领域事件整理为卡片模板可直接消费的扁平上下文字典，
减少模板层对原始领域对象与元数据结构的直接依赖。
"""

from __future__ import annotations

from typing import Any

from ....utils.time_converter import TimeConverter
from ...domain.event_models import EarthquakeEvent, EventEnvelope


def _coerce_location_error(value: Any) -> float | None:
    """把 errOrigin / err_origin 归一化为 float，无法转换时返回 None。

    兼容两类脏数据：
    - 合法数值 0（不能被 or 误判为缺失）；
    - JSON 原始路径以字符串下发数值（如 "3.2"），直接 f"{v:.1f}" 会抛 TypeError。
    """
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num


def _format_coordinates(latitude: float, longitude: float) -> str:
    """格式化坐标显示。"""
    lat_dir = "N" if latitude >= 0 else "S"
    lon_dir = "E" if longitude >= 0 else "W"
    return f"{abs(latitude):.2f}°{lat_dir}, {abs(longitude):.2f}°{lon_dir}"


class GlobalQuakeDisplayContextBuilder:
    """构建 Global Quake 卡片展示上下文。"""

    @staticmethod
    def build(
        earthquake: EventEnvelope,
        options: dict | None = None,
    ) -> dict:
        """构建 Global Quake 卡片渲染上下文。"""
        options = options or {}
        timezone_str = options.get("timezone", "UTC+8")

        envelope = earthquake
        domain_event = envelope.event
        payload = envelope.payload
        metadata = envelope.metadata

        if not isinstance(domain_event, EarthquakeEvent):
            raise TypeError("Global Quake display context requires EarthquakeEvent")

        mag = domain_event.magnitude or 0
        # 卡片模板通过震级区间切换不同背景样式，以强化视觉层级。
        if mag < 5:
            mag_class = "bg-low"
        elif mag < 7:
            mag_class = "bg-med"
        else:
            mag_class = "bg-high"

        shock_time = domain_event.occurred_at
        if shock_time:
            time_str = TimeConverter.format_time(shock_time, timezone_str)
        else:
            time_str = "Unknown Time"

        stations_used = 0
        stations_total = 0
        event_payload = payload.to_dict() if hasattr(payload, "to_dict") else {}
        stations = {}
        if isinstance(metadata, dict):
            stations = metadata.get("stations") or {}
        if not stations:
            # 若元数据中没有台站统计，则回退到原始载荷中寻找兼容字段。
            stations = (
                event_payload.get("stationCount") or event_payload.get("stations") or {}
            )
        if isinstance(stations, dict):
            stations_used = stations.get("used", 0)
            stations_total = stations.get("total", 0)

        # 质量指标（数据拟合 / 定位误差）的提取顺序：
        # 1. metadata["quality"]：解析器已统一规范化的 dict（含 pct / err_origin 等）
        # 2. metadata["quality_pct"] / metadata["location_error"]：历史扁平键兼容
        # 3. 原始载荷 event_payload：JSON 新协议平铺在 data / payload 外层，
        #    protobuf 历史路径则封装在 raw["data"]["quality"] 中
        quality_pct = "N/A"
        location_error = "N/A"

        quality = {}
        if isinstance(metadata, dict):
            _quality = metadata.get("quality")
            if isinstance(_quality, dict):
                quality = _quality

        # 解析器已在 metadata["quality"] 中规范化好质量指标，优先消费。
        if quality:
            pct = quality.get("pct")
            if pct is not None:
                quality_pct = f"{pct}%"
            # 按键是否存在取 errOrigin / err_origin（不能依赖 or：合法值 0 会被误判缺失），
            # 并先 float() 归一化：JSON 原始路径可能以字符串下发数值。
            err_origin = (
                quality.get("errOrigin")
                if "errOrigin" in quality
                else quality.get("err_origin")
            )
            err_origin = _coerce_location_error(err_origin)
            if err_origin is not None:
                location_error = f"{err_origin:.1f} km"

        if quality_pct == "N/A" or location_error == "N/A":
            # 兼容历史扁平元数据键（早期实现写入的字段）。
            if isinstance(metadata, dict):
                if quality_pct == "N/A" and metadata.get("quality_pct") is not None:
                    quality_pct = f"{metadata.get('quality_pct')}%"
                if location_error == "N/A" and isinstance(
                    metadata.get("location_error"), (int, float)
                ):
                    location_error = f"{metadata.get('location_error'):.1f} km"
                elif location_error == "N/A" and isinstance(
                    metadata.get("location_error_km"), (int, float)
                ):
                    location_error = f"{metadata.get('location_error_km'):.1f} km"

        if quality_pct == "N/A" or location_error == "N/A":
            # 一些质量字段可能存在于更内层的原始数据结构中，这里继续向下兼容提取。
            data_inner = (
                event_payload.get("data", {}) if isinstance(event_payload, dict) else {}
            )
            inner_quality = (
                data_inner.get("quality", {}) if isinstance(data_inner, dict) else {}
            )
            if not isinstance(inner_quality, dict):
                inner_quality = {}
            # JSON 新协议 payload 平铺在外层时，quality 也可能直接挂在 event_payload 上。
            if not inner_quality and isinstance(event_payload, dict):
                outer_quality = event_payload.get("quality")
                if isinstance(outer_quality, dict):
                    inner_quality = outer_quality
            if inner_quality:
                if quality_pct == "N/A":
                    pct = inner_quality.get("pct")
                    if pct is not None:
                        quality_pct = f"{pct}%"

                if location_error == "N/A":
                    err_origin = (
                        inner_quality.get("errOrigin")
                        if "errOrigin" in inner_quality
                        else inner_quality.get("err_origin")
                    )
                    err_origin = _coerce_location_error(err_origin)
                    if err_origin is not None:
                        location_error = f"{err_origin:.1f} km"
            if location_error == "N/A" and isinstance(
                data_inner.get("locationError"), (int, float)
            ):
                location_error = f"{data_inner.get('locationError'):.1f} km"
            if location_error == "N/A" and isinstance(
                event_payload.get("locationError"), (int, float)
            ):
                location_error = f"{event_payload.get('locationError'):.1f} km"

        # 返回的字段名直接面向卡片模板，因此保持扁平、稳定且尽量避免模板层再做业务判断。
        is_final = (
            bool(metadata.get("is_final", False))
            if isinstance(metadata, dict)
            else False
        )
        return {
            "magnitude": f"{mag:.1f}",
            "mag_class": mag_class,
            "intensity": domain_event.intensity if domain_event.intensity else "",
            "region": domain_event.place_name,
            "is_final": is_final,
            "is_update": (
                (metadata.get("report_num", 1) if isinstance(metadata, dict) else 1) > 1
            ),
            "revision": metadata.get("report_num", 1)
            if isinstance(metadata, dict)
            else 1,
            "time_str": time_str,
            "depth": f"{domain_event.depth} km"
            if domain_event.depth not in (None, 0.0)
            else ("极浅" if domain_event.depth == 0.0 else "N/A"),
            "latitude": f"{domain_event.latitude:.4f}",
            "longitude": f"{domain_event.longitude:.4f}",
            "epicenter_str": _format_coordinates(
                domain_event.latitude, domain_event.longitude
            ),
            "pga": f"{metadata.get('max_pga'):.1f} gal"
            if isinstance(metadata, dict) and metadata.get("max_pga") is not None
            else "N/A",
            "location_error": location_error,
            "stations_used": stations_used,
            "stations_total": stations_total,
            "quality_pct": quality_pct,
            "event_id": envelope.identity.event_id,
        }
