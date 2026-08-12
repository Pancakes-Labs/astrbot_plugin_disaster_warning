"""
模拟事件构建器。

核心职责：把 SimulationStep（灾种 / 数据源 / 参数 / 报数 / 事件键）转换为
合法、可进入展示推送链路的 EventEnvelope。

设计要点：
- 对齐各灾种解析器 _build_envelope 的字段语义，确保展示层能正常渲染
- 统一注入模拟标记（test / simulation / simulation_bypass_regular_filters）
- 事件时间支持 time_offset_seconds 参数（可模拟历史时刻事件）
- 同 event_key 的多报步骤共享事件前缀、递增 report_num，复现真实"第 N 报"演进
- 支持各数据源特有字段。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ....utils.converters import ScaleConverter
from ...domain.event_identity import EventIdentity
from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...domain.event_payload import SourcePayload
from ...sources.source_catalog import SOURCE_CATALOG, get_source_entry
from ..geo.region_service import region_service
from .flow_models import (
    DISASTER_TYPE_EARTHQUAKE,
    DISASTER_TYPE_TSUNAMI,
    DISASTER_TYPE_TYPHOON,
    DISASTER_TYPE_WEATHER,
    SimulationStep,
    generate_sim_id,
)

# 进程内模拟事件递增计数器：避免同秒多次触发时 ID 冲突
_sim_event_sequence = 0
_sim_event_sequence_lock = threading.Lock()


def _next_sim_event_sequence() -> int:
    """获取下一个模拟事件序号（线程安全，单调递增）。"""
    global _sim_event_sequence
    with _sim_event_sequence_lock:
        _sim_event_sequence += 1
        return _sim_event_sequence


def _safe_float(value: Any, default: float | None = None) -> float | None:
    """安全转 float。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int | None = None) -> int | None:
    """安全转 int。"""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_json_list(raw: Any) -> list[dict[str, Any]]:
    """解析 JSON 数组形态的参数字段（支持字符串与列表两种形态）。"""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            items = parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    else:
        return []
    return [item for item in items if isinstance(item, dict)]


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    """解析 JSON 对象形态的参数字段（支持字符串与字典两种形态）。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


# S-Net 测站震度 → RGB 反推表（对齐 snet_map_renderer.MSIL_SHINDO_TO_RGB 键语义：震度 × 10）
# 真实解析链路从 MSIL 瓦片解码 RGB 再反推震度；模拟链路无法产瓦片，
# 因此按震度正推 RGB，保证 snet_station.html 渲染器能正常着色。
_SNET_SHINDO_RGB_KEYS: tuple[tuple[int, tuple[int, int, int]], ...] = (
    (-30, (0, 0, 205)),
    (-25, (0, 36, 227)),
    (-20, (0, 72, 250)),
    (-15, (0, 140, 194)),
    (-10, (0, 208, 139)),
    (-5, (31, 228, 96)),
    (0, (63, 250, 54)),
    (5, (125, 252, 33)),
    (10, (189, 255, 12)),
    (15, (222, 255, 5)),
    (20, (255, 255, 0)),
    (25, (255, 238, 0)),
    (30, (255, 221, 0)),
    (35, (255, 182, 0)),
    (40, (255, 144, 0)),
    (45, (255, 106, 0)),
    (50, (255, 68, 0)),
    (55, (250, 33, 0)),
    (60, (245, 0, 0)),
    (65, (208, 0, 0)),
    (70, (170, 0, 0)),
)


def _snet_shindo_to_rgb(shindo: float) -> list[int]:
    """把計測震度反推为 RGB 列表（向下取整到最近档位）。"""
    try:
        scaled = int(float(shindo) * 10.0)
    except (TypeError, ValueError):
        scaled = 0
    best_key = -30
    best_rgb = (0, 0, 205)
    for key, rgb in _SNET_SHINDO_RGB_KEYS:
        if key <= scaled:
            best_key = key
            best_rgb = rgb
    if scaled < best_key:
        best_rgb = (0, 0, 205)
    return list(best_rgb)


# 台风移动方向 → (lat_delta, lon_delta) 方位向量。
# 覆盖 schema 下拉框全部 16+ 方位（含"西北西/北北东"等复合方位），
# 避免用户选择复合方位时静默回退到默认"西北"导致轨迹方向错误。
_TYPHOON_DIRECTION_VECTORS: dict[str, tuple[float, float]] = {
    "北": (+1.0, 0.0),
    "北北东": (+0.92, +0.38),
    "东北偏北": (+0.92, +0.38),
    "东北": (+0.71, +0.71),
    "东北偏东": (+0.38, +0.92),
    "东": (0.0, +1.0),
    "东南偏东": (-0.38, +0.92),
    "东南": (-0.71, +0.71),
    "东南偏南": (-0.92, +0.38),
    "南": (-1.0, 0.0),
    "西南偏南": (-0.92, -0.38),
    "西南": (-0.71, -0.71),
    "西南偏西": (-0.38, -0.92),
    "西": (0.0, -1.0),
    "西北偏西": (+0.38, -0.92),
    "西北西": (+0.71, -0.71),
    "西北": (+0.71, -0.71),
    "西北偏北": (+0.92, -0.38),
}


def _build_default_typhoon_track(
    lat: float, lon: float, now: datetime, *, direction: str = "西北"
) -> list[dict[str, Any]]:
    """为模拟台风生成 3 点历史轨迹（驱动 typhoon_track.html 路径图渲染）。

    轨迹节点字段对齐 EQSC 富化数据（time / latitude / longitude / windSpeed / pressure / typeNameCN），
    时间按 6 小时回退，位置沿移动方向线性外推。
    """
    lat_delta, lon_delta = _TYPHOON_DIRECTION_VECTORS.get(direction, (0.71, -0.71))
    track = []
    for idx in range(3):
        offset = 3 - idx  # 历史点距当前越远
        track.append(
            {
                "time": (now - timedelta(hours=6 * offset)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "latitude": round(lat - lat_delta * offset, 2),
                "longitude": round(lon - lon_delta * offset, 2),
                "windSpeed": max(15.0, 35.0 - 4.0 * offset),
                "pressure": 1000 - 10 * (3 - offset),
                "typeNameCN": "热带风暴" if offset > 1 else "台风",
            }
        )
    return track


def _build_default_future_track(
    lat: float, lon: float, now: datetime, *, direction: str = "西北"
) -> list[dict[str, Any]]:
    """为模拟台风生成 2 点预测轨迹（对齐 EQSC futureTrack 字段语义）。"""
    lat_delta, lon_delta = _TYPHOON_DIRECTION_VECTORS.get(direction, (0.71, -0.71))
    track = []
    for idx in range(1, 3):
        track.append(
            {
                "time": (now + timedelta(hours=12 * idx)).strftime("%Y-%m-%d %H:%M:%S"),
                "latitude": round(lat + lat_delta * 2 * idx, 2),
                "longitude": round(lon + lon_delta * 2 * idx, 2),
                "windSpeed": max(15.0, 35.0 - 5.0 * idx),
                "pressure": 1000 - 5 * idx,
                "typeNameCN": "热带风暴" if idx > 1 else "台风",
            }
        )
    return track


def _parse_event_time_text(text: Any) -> datetime | None:
    """解析绝对事件时间字符串（支持 "YYYY-MM-DD HH:MM:SS" 与 ISO8601）。"""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _resolve_event_time(step: SimulationStep) -> datetime:
    """解析事件发生时间（发震时间）。

    时间参数在 schema 中属于 time 分组，由前端编辑器写入 params；
    step 顶层字段用于便捷工厂/命令行直接构造。两处均兼容：
    1. params.event_time 或 step.event_time 绝对时间字符串
    2. params.time_offset_seconds 或 step.time_offset_seconds 回退秒数
    3. params.event_time_delay_seconds 或 step.event_time_delay_seconds 延迟秒数
       （叠加在绝对时间 / 当前时间之上，模拟未来时刻）
    4. 当前时间

    优先级：绝对时间 > 回退 > 当前时间；延迟在最终基准上向后叠加。
    """
    base = datetime.now(timezone.utc)
    params = getattr(step, "params", None) or {}

    # 1. 绝对时间：优先 params（前端编辑器写入位置），其次 step 顶层
    raw_event_time = params.get("event_time")
    if raw_event_time in (None, ""):
        raw_event_time = getattr(step, "event_time", "")
    parsed = _parse_event_time_text(raw_event_time)
    if parsed is not None:
        base = parsed
    else:
        # 2. 回退秒数：优先 params，其次 step 顶层（仅在无绝对时间时生效）
        offset = _safe_float(params.get("time_offset_seconds"), None)
        if offset is None:
            offset = _safe_float(getattr(step, "time_offset_seconds", 0.0), None)
        offset = _safe_float(offset, 0.0) or 0.0
        if offset:
            base = base - timedelta(seconds=abs(offset))

    # 3. 延迟秒数：在绝对时间 / 回退结果 / 当前时间之上统一向后叠加
    delay = _safe_float(params.get("event_time_delay_seconds"), None)
    if delay is None:
        delay = _safe_float(getattr(step, "event_time_delay_seconds", 0.0), None)
    delay = _safe_float(delay, 0.0) or 0.0
    if delay:
        base = base + timedelta(seconds=abs(delay))
    return base


def _resolve_update_time(
    step: SimulationStep, fallback: datetime | None = None
) -> datetime:
    """解析消息发布时间（更新时间）。

    时间参数在 schema 中属于 time 分组，由前端编辑器写入 params；
    step 顶层字段用于便捷工厂/命令行直接构造。两处均兼容：
    1. params.update_time 或 step.update_time 绝对时间字符串（优先级最高）
    2. params.update_time_offset_seconds 或 step.update_time_offset_seconds 回退秒数
    3. params.update_time_delay_seconds 或 step.update_time_delay_seconds 延迟秒数
       （叠加在绝对时间 / 当前时间之上，模拟未来发布时刻）
    4. fallback（发震时间，用于未配置更新时间偏移时的语义统一）
    5. 当前时间

    优先级：绝对时间 > 回退 > 当前时间；延迟在最终基准上向后叠加。
    """
    base = datetime.now(timezone.utc)
    params = getattr(step, "params", None) or {}

    # 1. 绝对时间：优先 params（前端编辑器写入位置），其次 step 顶层
    raw_update_time = params.get("update_time")
    if raw_update_time in (None, ""):
        raw_update_time = getattr(step, "update_time", "")
    parsed = _parse_event_time_text(raw_update_time)
    if parsed is not None:
        base = parsed
    else:
        # 2. 回退秒数：优先 params，其次 step 顶层（仅在无绝对时间时生效）
        offset = _safe_float(params.get("update_time_offset_seconds"), None)
        if offset is None:
            offset = _safe_float(getattr(step, "update_time_offset_seconds", 0.0), None)
        offset = _safe_float(offset, 0.0) or 0.0
        if offset:
            base = base - timedelta(seconds=abs(offset))

    # 3. 延迟秒数：在绝对时间 / 回退结果 / 当前时间之上统一向后叠加
    delay = _safe_float(params.get("update_time_delay_seconds"), None)
    if delay is None:
        delay = _safe_float(getattr(step, "update_time_delay_seconds", 0.0), None)
    delay = _safe_float(delay, 0.0) or 0.0
    if delay:
        base = base + timedelta(seconds=abs(delay))
    return base


@dataclass(frozen=True, slots=True)
class _StepBuildResult:
    """灾种工厂回调的返回载体：领域事件 + 事件类型 + 消息类型 + 附加元数据。"""

    domain_event: Any  # 领域事件实例（Earthquake/Tsunami/Weather/Typhoon）
    event_type: str  # 统一事件类型字符串
    message_type: str  # 原始消息类型（展示层与日志使用）
    extra_metadata: dict[str, Any]  # 灾种特有元数据（会并入统一模拟元数据）


class SimulationBuilder:
    """模拟事件构建器。

    采用模板方法模式：
    - 模板方法 build_step_envelope 负责统一的校验、元数据注入、事件 ID 解析与包裹组装
    - 各灾种工厂回调只负责构建"领域事件 + 灾种特有元数据"，消除重复样板
    """

    def __init__(self):
        # 记录同 event_key 已分配的事件前缀，保证多报步骤共享同一事件标识。
        # 注意：该映射仅用于"单次构建会话"（一次整流执行），每次执行前必须 reset，
        # 避免跨 run 复用导致同一 event_key 二次执行时事件 ID 重复。
        self._event_key_prefixes: dict[str, str] = {}

    def reset(self) -> None:
        """重置事件键前缀映射（每次整流执行前调用，隔离事件标识空间）。"""
        self._event_key_prefixes.clear()

    def _resolve_event_id(self, step: SimulationStep) -> str:
        """解析事件 ID：同 event_key 步骤共享前缀，否则每步独立。"""
        if step.event_key:
            prefix = self._event_key_prefixes.get(step.event_key)
            if not prefix:
                prefix = generate_sim_id("sim")
                self._event_key_prefixes[step.event_key] = prefix
            return f"{prefix}"
        return generate_sim_id("sim")

    def build_step_envelope(self, step: SimulationStep) -> EventEnvelope:
        """模板方法：把模拟步骤构建为合法事件包裹。"""
        source_entry = get_source_entry(step.source_id)
        if source_entry is None:
            valid_sources = ", ".join(sorted(SOURCE_CATALOG.keys()))
            raise ValueError(
                f"无效的数据源: {step.source_id}，可用数据源: {valid_sources}"
            )

        # 按灾种分派到领域事件工厂回调
        factories = {
            DISASTER_TYPE_EARTHQUAKE: self._build_earthquake_payload,
            DISASTER_TYPE_TSUNAMI: self._build_tsunami_payload,
            DISASTER_TYPE_WEATHER: self._build_weather_payload,
            DISASTER_TYPE_TYPHOON: self._build_typhoon_payload,
        }
        factory = factories.get(step.disaster_type)
        if factory is None:
            raise ValueError(f"暂不支持的模拟灾种: {step.disaster_type}")

        now = _resolve_event_time(step)
        # 更新时间默认取当前执行时刻（消息发布时刻），与发震时间独立。
        # 仅在显式配置 update_time_offset_seconds 时才回退到过去。
        update_time = _resolve_update_time(step, fallback=None)
        build_result = factory(step, source_entry, now, update_time=update_time)

        # 统一模拟元数据 + 灾种特有元数据
        metadata = self._build_simulation_metadata(source_entry, step)
        metadata.update(build_result.extra_metadata)
        # 时间语义注入：发震时间 / 更新时间（展示层据此区分两套时间）
        metadata["event_time"] = now
        metadata["update_time"] = update_time

        event_id = self._resolve_event_id(step)
        raw_payload = dict(step.params)
        raw_payload["test"] = True
        raw_payload["simulation"] = True

        identity = EventIdentity(
            event_id=event_id,
            source_id=step.source_id,
            event_type=build_result.event_type,
            provider_family=source_entry.provider_family.value if source_entry else "",
            source_enum=source_entry.source_enum if source_entry else "",
            report_num=step.report_num,
            published_at=update_time,
            is_final=step.is_final,
            attributes={"test": True, "simulation": True},
        )
        return EventEnvelope(
            identity=identity,
            event=build_result.domain_event,
            received_at=datetime.now(timezone.utc),
            payload=SourcePayload(
                source_id=step.source_id,
                provider_family=source_entry.provider_family.value
                if source_entry
                else "",
                message_type=build_result.message_type,
                raw=raw_payload,
                attributes=dict(metadata),
            ),
            metadata=metadata,
        )

    @staticmethod
    def _build_simulation_metadata(
        source_entry, step: SimulationStep
    ) -> dict[str, Any]:
        """构造统一模拟元数据（所有灾种强制注入的模拟标记）。"""
        return {
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value if source_entry else "",
            "test": True,
            "simulation": True,
            # 特权标志：允许模拟事件在核心路由链路中绕过对时效、去重等强物理属性的硬过滤限制
            "simulation_bypass_regular_filters": True,
            # 模拟事件跳过外部富化（如台风 EQSC 富化），避免拉取外部 API / 改写模拟模式
            "skip_enrich": True,
            "report_num": step.report_num,
            "is_final": step.is_final,
        }

    # ------------------------------------------------------------------
    # 灾种领域事件工厂回调（只负责领域事件 + 特有元数据）
    # ------------------------------------------------------------------
    def _build_earthquake_payload(
        self,
        step: SimulationStep,
        source_entry,
        now: datetime,
        update_time: datetime | None = None,
    ) -> _StepBuildResult:
        """构建地震领域事件与特有元数据。"""
        params = step.params
        lat = _safe_float(params.get("latitude"), 39.9)
        lon = _safe_float(params.get("longitude"), 116.4)
        magnitude = _safe_float(params.get("magnitude"), 5.5)
        depth = _safe_float(params.get("depth"), 10.0)
        place_name = str(params.get("place_name") or "").strip()
        if not place_name:
            place_name = region_service.translate_place_name("模拟震中", lat, lon)

        domain_event = EarthquakeEvent(
            occurred_at=now,
            latitude=lat,
            longitude=lon,
            depth=depth,
            magnitude=magnitude,
            place_name=place_name,
            metadata={},
        )

        extra = {}
        presentation_type = (source_entry.presentation_type or "").strip()

        # 日本震度制式源：补震度与情报类型（info_type）。
        # info_type 用于生成推文标题中的 [警报] / [予報] / [震度速报] 等标记；
        if (
            source_entry.intensity_mode == "scale"
            or "jma" in step.source_id
            or "p2p" in step.source_id
        ):
            scale = _safe_int(params.get("scale"), None)
            if scale is not None:
                if "p2p" in step.source_id:
                    # P2P 源使用业务档位值（10=震度1 … 70=震度7），需转换为规范震度。
                    # 对齐真实解析链路 ScaleConverter.convert_p2p_scale。
                    scale = ScaleConverter.convert_p2p_scale(scale)
                    domain_event.scale = max(0.0, min(7.0, scale or 0.0))
                else:
                    domain_event.scale = max(0, min(7, scale))
            info_type = str(params.get("info_type") or "").strip()
            if info_type:
                extra["info_type"] = info_type
            # 布尔标记
            if params.get("is_training"):
                extra["is_training"] = True
            if params.get("is_assumption"):
                extra["is_assumption"] = True
                extra["magnitude_is_placeholder"] = True
            if params.get("is_cancel"):
                extra["is_cancel"] = True
            # 警报区域：优先使用 jma_warning_areas JSON，转换成展示器读取的格式。
            raw_areas = params.get("jma_warning_areas")
            if raw_areas:
                areas_list = _parse_json_list(raw_areas)
                if areas_list:
                    warning_areas: list[str] = []
                    warning_ranges: list[str] = []
                    groups: list[dict[str, Any]] = []
                    for area in areas_list:
                        name = str(area.get("name") or "").strip()
                        if not name:
                            continue
                        from_raw = area.get("scaleFrom")
                        to_raw = area.get("scaleTo")
                        try:
                            scale_from = (
                                int(float(from_raw))
                                if from_raw not in (None, "")
                                else None
                            )
                        except (TypeError, ValueError):
                            scale_from = None
                        try:
                            scale_to = (
                                int(float(to_raw)) if to_raw not in (None, "") else None
                            )
                        except (TypeError, ValueError):
                            scale_to = None
                        range_text = (
                            f"{scale_from}~{scale_to}"
                            if scale_from is not None and scale_to is not None
                            else (
                                f"{scale_from}以上"
                                if scale_from is not None
                                else "震度"
                            )
                        )
                        status = (
                            "已到达" if str(area.get("kindCode")) == "11" else "未到达"
                        )
                        area_label = f"{name}({status})"
                        warning_areas.append(area_label)
                        if range_text not in warning_ranges:
                            warning_ranges.append(range_text)
                        # 按范围文本归组
                        group = next(
                            (g for g in groups if g["range_text"] == range_text), None
                        )
                        if group is None:
                            group = {
                                "range_text": range_text,
                                "scale_from": scale_from or 0,
                                "emoji": "⚪",
                                "areas": [],
                            }
                            groups.append(group)
                        if area_label not in group["areas"]:
                            group["areas"].append(area_label)
                    extra["jma_warning_areas"] = warning_areas
                    extra["jma_warning_area_ranges"] = warning_ranges
                    extra["jma_warning_area_groups"] = groups
            # 各地震度详情
            raw_points = params.get("jma_points")
            if raw_points:
                points = _parse_json_list(raw_points)
                if points:
                    extra["jma_points"] = [
                        {
                            "pref": str(p.get("pref") or "").strip(),
                            "addr": str(p.get("addr") or "").strip(),
                            "scale": int(float(p.get("scale", 0)))
                            if p.get("scale") not in (None, "")
                            else 0,
                        }
                        for p in points
                        if str(p.get("addr") or "").strip()
                    ]

        # 烈度速报源：补最高烈度与全文概述
        if (
            "cenc_ir" in step.source_id
            or source_entry.parser_name == "china_intensity_report_parser"
            or source_entry.parser_name == "china_intensity_report_eqsc_parser"
        ):
            intensity = _safe_float(params.get("intensity"), None)
            if intensity is not None:
                domain_event.intensity = max(1.0, min(12.0, round(intensity, 1)))
                domain_event.headline = f"{place_name}{magnitude:.1f}级地震"
                # 推测烈度说明：优先使用用户填写的文本，缺省按最高烈度生成默认描述。
                intensity_info_text = str(
                    params.get("intensity_info_text") or ""
                ).strip()
                if not intensity_info_text:
                    intensity_info_text = (
                        f"基于'GB/T17742-2020中国地震烈度表'，结合台站实测仪器烈度，"
                        f"本次地震推测最高烈度为{domain_event.intensity:.0f}度。"
                    )
                # 台站烈度明细：优先用户 JSON，缺省生成单个模拟台站；字段结构对齐解析器。
                raw_stations = _parse_json_list(params.get("intensity_stations"))
                stations_list = []
                for item in raw_stations:
                    name = str(
                        item.get("stName")
                        or item.get("name")
                        or (item.get("stationInfo") or {}).get("name")
                        or ""
                    ).strip()
                    try:
                        st_intensity = float(
                            item.get("INT")
                            or item.get("intensity")
                            or domain_event.intensity
                        )
                    except (TypeError, ValueError):
                        st_intensity = float(domain_event.intensity)
                    stations_list.append(
                        {
                            "name": name or "模拟台站",
                            "intensity": st_intensity,
                            "lat": _safe_float(
                                item.get("lat") or item.get("latitude") or lat,
                                lat,
                            ),
                            "lon": _safe_float(
                                item.get("lon") or item.get("longitude") or lon,
                                lon,
                            ),
                        }
                    )
                if not stations_list:
                    stations_list = [
                        {
                            "name": "模拟台站",
                            "intensity": domain_event.intensity,
                            "lat": lat,
                            "lon": lon,
                        }
                    ]
                extra.update(
                    {
                        "info_type": "烈度速报",
                        "headline": domain_event.headline,
                        "name_by_info": domain_event.headline,
                        "intensity_info_text": intensity_info_text,
                        "station_count": len(stations_list),
                        "stations": stations_list,
                        "stations_topn": list(stations_list),
                    }
                )
                # 烈度等震线 GeoJSON：透传原字段（展示层按需消费）
                contour = _parse_json_dict(params.get("intensity_contour"))
                if contour:
                    extra["intensity_contour"] = contour
                    extra["contour_geojson"] = contour

        # FSSN CMT 源：补节面、多震级、矩心深度与矩张量分量
        if source_entry.parser_name == "fssn_cmt_parser":
            strike = _safe_float(params.get("strike"), 200.0)
            dip = _safe_float(params.get("dip"), 77.0)
            rake = _safe_float(params.get("rake"), 74.0)
            strike2 = _safe_float(params.get("strike2"), round((strike + 180) % 360, 1))
            dip2 = _safe_float(params.get("dip2"), round(90 - dip, 1))
            rake2 = _safe_float(params.get("rake2"), round(-rake, 1))
            domain_event.headline = "FSSN CMT 模拟地震"
            # 震级集合：优先用 schema 的 all_magnitudes JSON，缺省按主震级推导
            all_magnitudes = _parse_json_dict(params.get("all_magnitudes")) or {
                "M": magnitude,
                "Mww": round(magnitude + 0.2, 1),
                "mB": round(magnitude + 0.1, 1),
                "Mwp": round(magnitude - 0.1, 1),
            }
            # 矩张量分量：优先用 schema 的 moment_tensor JSON（科学计数法字符串）
            moment_tensor = _parse_json_dict(params.get("moment_tensor"))
            extra.update(
                {
                    "info_type": "CMT",
                    "cmt_id": f"sim_cmt_{now.timestamp():.0f}",
                    "fssn_event_id": str(
                        params.get("fssn_event_id") or f"FSSN_SIM_{now.timestamp():.0f}"
                    ).strip(),
                    "all_magnitudes": all_magnitudes,
                    "display_magnitude": magnitude,
                    "display_magnitude_type": "M",
                    "depth": depth,
                    "depth_error": 5.0,
                    "centroid_depth": str(
                        params.get("centroid_depth") or max(0.0, depth - 2.0)
                    ).strip(),
                    "nodal_plane1": {"strike": strike, "dip": dip, "rake": rake},
                    "nodal_plane2": {
                        "strike": strike2,
                        "dip": dip2,
                        "rake": rake2,
                    },
                    "beachball_ready": True,
                    "is_supplement_product": True,
                }
            )
            if moment_tensor:
                extra["moment_tensor"] = moment_tensor

        # Global Quake：补 PGA、MMI 烈度、台站统计、定位质量、深度置信区间与事件簇。
        if presentation_type == "global_quake":
            max_pga = _safe_float(params.get("max_pga"), 100.0)
            extra["max_pga"] = max_pga
            mmi_intensity = _safe_float(params.get("intensity"), None)
            if mmi_intensity is not None:
                # 内部统一按数值烈度传递，展示器 _get_intensity_emoji 支持数值。
                extra["intensity"] = mmi_intensity
                domain_event.intensity = mmi_intensity

            # 台站统计：缺省回退默认分布（与解析器 stationCount 结构一致）。
            station_count = _parse_json_dict(params.get("station_count"))
            extra["stations"] = station_count or {
                "total": 30,
                "selected": 30,
                "used": 25,
                "matching": 20,
            }

            # 定位质量：透传原字段（errOrigin/errDepth/errNS/errEW/pct/stations），
            # 展示上下文 _coerce_location_error 兼容数值与字符串两种形态。
            quality = _parse_json_dict(params.get("quality"))
            if quality:
                extra["quality"] = quality

            # 深度置信区间 / 事件簇：透传原始结构，供卡片/日志展示。
            depth_confidence = _parse_json_dict(params.get("depth_confidence"))
            if depth_confidence:
                extra["depth_confidence"] = depth_confidence
            cluster = _parse_json_dict(params.get("cluster"))
            if cluster:
                extra["cluster"] = cluster

            # 固定深度标记。
            extra["fixed_depth"] = bool(params.get("fixed_depth"))

            extra["is_final"] = bool(step.is_final or extra.get("is_final"))

        # S-Net：按 snet_parser 消费结构补测站震度分布（驱动 S-Net 测站分布图渲染）。
        # 事件核心是"测站震度分布"：最高震度测站作为领域事件坐标，无震级/深度语义。
        # snet_timestamp 使用更新时间（真实 S-Net 轮询链路即使用瓦片更新时间）。
        if presentation_type == "snet":
            stations = self._parse_snet_stations(params.get("stations"))
            # 未显式提供测站时，以参考坐标为中心生成 5 站模拟分布
            if not stations:
                mag = magnitude or 5.5
                stations = self._parse_snet_stations(
                    [
                        {
                            "name": "模拟站1",
                            "shindo": max(0.0, min(7.0, mag - 2.0)),
                            "lat": round(lat + 0.3, 2),
                            "lon": round(lon - 0.3, 2),
                        },
                        {
                            "name": "模拟站2",
                            "shindo": max(0.0, min(7.0, mag - 2.5)),
                            "lat": round(lat - 0.4, 2),
                            "lon": round(lon + 0.2, 2),
                        },
                        {
                            "name": "模拟站3",
                            "shindo": max(0.0, min(7.0, mag - 3.0)),
                            "lat": round(lat + 0.6, 2),
                            "lon": round(lon + 0.5, 2),
                        },
                        {
                            "name": "模拟站4",
                            "shindo": max(0.0, min(7.0, mag - 3.5)),
                            "lat": round(lat - 0.2, 2),
                            "lon": round(lon - 0.6, 2),
                        },
                        {
                            "name": "模拟站5",
                            "shindo": max(0.0, min(7.0, mag - 4.0)),
                            "lat": round(lat + 0.1, 2),
                            "lon": round(lon + 0.8, 2),
                        },
                    ]
                )
            # 最高震度：优先取用户指定值，否则取测站分布中的最大震度
            max_shindo = _safe_float(params.get("max_shindo"), None)
            if max_shindo is None and stations:
                try:
                    max_shindo = max(float(s.get("shindo") or 0.0) for s in stations)
                except (TypeError, ValueError):
                    max_shindo = 0.0
            if max_shindo is None:
                max_shindo = 0.0
            # 最高震度测站：优先取用户指定站名，否则取震度最高的测站
            top_station = str(params.get("top_station") or "").strip()
            top = None
            for s in sorted(
                stations, key=lambda x: float(x.get("shindo") or 0.0), reverse=True
            ):
                if not top_station or str(s.get("name") or "") == top_station:
                    top = s
                    break
            if top is None and stations:
                top = sorted(
                    stations, key=lambda x: float(x.get("shindo") or 0.0), reverse=True
                )[0]
            # min_shindo / station_min_shindo：参与触发测站统计（对齐 snet_filter_constants）
            min_shindo = _safe_float(params.get("min_shindo"), 1.5)
            station_min_shindo = _safe_float(params.get("station_min_shindo"), 0.5)
            triggered = [
                s
                for s in stations
                if float(s.get("shindo") or 0.0) >= min(station_min_shindo, min_shindo)
            ]
            # 领域事件坐标取最高震度测站（对齐 snet_parser：latitude/longitude 取 top）
            if top is not None:
                domain_event.latitude = float(top.get("lat") or 0.0)
                domain_event.longitude = float(top.get("lon") or 0.0)
                domain_event.place_name = "日本海沟 S-Net 海底观测网"
            domain_event.magnitude = None
            domain_event.depth = None
            domain_event.scale = max_shindo
            domain_event.headline = f"S-Net 最大震度 {ScaleConverter.format_measured_intensity_display(max_shindo) or '不明'}"
            # timestamp：对齐 snet_parser 的 YYYYMMDDHHMM00 形态；缺省用更新时间格式化
            snet_ts = str(params.get("timestamp") or "").strip()
            if not snet_ts:
                snet_ts = (update_time or now).strftime("%Y%m%d%H%M00")
            extra["stations"] = stations
            extra["triggered"] = triggered
            extra["timestamp"] = snet_ts
            extra["snet_timestamp"] = (update_time or now).isoformat()
            extra["min_shindo"] = min_shindo
            extra["station_min_shindo"] = station_min_shindo
            extra["max_shindo"] = max_shindo
            extra["triggered_count"] = len(triggered)
            extra["triggered_station_count"] = len(triggered)
            extra["total_stations"] = len(stations)
            extra["top_station"] = str(top.get("name") or "") if top is not None else ""

        # CWA 正式报告源：补报告图片与等震度图附件。
        if source_entry.source_id == "cwa_fanstudio_report":
            image_uri = str(params.get("image_uri") or "").strip()
            shakemap_uri = str(params.get("shakemap_uri") or "").strip()
            extra["image_uri"] = image_uri
            extra["shakemap_uri"] = shakemap_uri

        # USGS 报告源：补详情 URL 与状态。
        if source_entry.source_id == "usgs_fanstudio":
            url = str(params.get("url") or "").strip()
            status = str(params.get("status") or "").strip()
            if url:
                extra["url"] = url
                extra["event_url"] = url
            if status:
                extra["status"] = status
                extra["info_type"] = status

        # CENC 报告源：补信息类型与名称。
        if source_entry.source_id in ("cenc_fanstudio", "cenc_wolfx"):
            info_type_name = str(params.get("info_type_name") or "地震测定").strip()
            name_by_info = str(params.get("name_by_info") or "").strip()
            if info_type_name:
                extra["info_type_name"] = info_type_name
                extra["infoTypeName"] = info_type_name
                extra["info_type"] = info_type_name
            if name_by_info:
                extra["name_by_info"] = name_by_info
            # 最大烈度：Wolfx cenc_eqlist 文档含 intensity；展示链路统一消费
            intensity = _safe_float(params.get("intensity"), None)
            if intensity is not None:
                extra["intensity"] = intensity
                domain_event.intensity = intensity

        # CWA EEW 源：影响区域 locationDesc → impact_area（CwaEewPresenter 展示“影响区域”）。
        if source_entry.source_id in ("cwa_fanstudio", "cwa_wolfx"):
            location_desc = str(params.get("location_desc") or "").strip()
            if location_desc:
                # 展示器按影响区域单值文本读取
                extra["impact_area"] = location_desc
                extra["location_desc"] = location_desc

        # JMA 地震情报源（P2P 551 / Wolfx jma_eqlist）：
        if source_entry.source_id in ("jma_p2p_info", "jma_wolfx_info"):
            tsunami = str(params.get("domestic_tsunami") or "").strip()
            if tsunami:
                extra["domestic_tsunami"] = tsunami
            foreign_tsunami = str(params.get("foreign_tsunami") or "").strip()
            if foreign_tsunami:
                extra["foreign_tsunami"] = foreign_tsunami
            free_form = str(params.get("free_form_comment") or "").strip()
            if free_form:
                extra["free_form_comment"] = free_form
            raw_points = params.get("jma_points")
            if raw_points:
                points = _parse_json_list(raw_points)
                if points:
                    extra["jma_points"] = [
                        {
                            "pref": str(p.get("pref") or "").strip(),
                            "addr": str(p.get("addr") or "").strip(),
                            "isArea": bool(p.get("isArea", False)),
                            "scale": int(float(p.get("scale", 0)))
                            if p.get("scale") not in (None, "")
                            else 0,
                        }
                        for p in points
                        if str(p.get("addr") or "").strip()
                    ]

        # CEA 地震预警源：补预估烈度 epiIntensity。
        if source_entry.source_id in ("cea_fanstudio", "cea_pr_fanstudio", "cea_wolfx"):
            epi_intensity = _safe_float(params.get("epi_intensity"), None)
            if epi_intensity is not None:
                extra["epi_intensity"] = epi_intensity
                extra["intensity"] = epi_intensity

        return _StepBuildResult(
            domain_event=domain_event,
            event_type="earthquake",
            message_type="simulation",
            extra_metadata=extra,
        )

    @staticmethod
    def _parse_snet_stations(raw: Any) -> list[dict[str, Any]]:
        """解析 S-Net 测站震度 JSON（支持字符串与列表两种形态）。"""
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
                items = parsed if isinstance(parsed, list) else []
            except (ValueError, TypeError):
                return []
        else:
            return []
        stations = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "模拟站").strip()
            try:
                shindo = round(float(item.get("shindo", 0)), 1)
            except (TypeError, ValueError):
                shindo = 0.0
            lat = _safe_float(item.get("lat"), None)
            lon = _safe_float(item.get("lon"), None)
            rgb = item.get("rgb")
            if isinstance(rgb, (tuple, list)) and len(rgb) >= 3:
                rgb = [int(c) for c in rgb[:3]]
            else:
                # 真实解析链路 station 带 rgb 做震度着色；模拟链路按震度反推
                rgb = _snet_shindo_to_rgb(shindo)
            stations.append(
                {
                    "name": name,
                    "shindo": shindo,
                    "lat": lat,
                    "lon": lon,
                    "rgb": rgb,
                }
            )
        return stations

    def _build_tsunami_payload(
        self,
        step: SimulationStep,
        source_entry,
        now: datetime,
        update_time: datetime | None = None,
    ) -> _StepBuildResult:
        """构建海啸领域事件与特有元数据（对齐 TsunamiParser 字段语义）。"""
        params = step.params

        title = str(params.get("title") or "").strip()
        level = str(params.get("level") or "").strip()
        if not title and level:
            if level == "信息":
                title = "海啸信息"
            elif level == "解除":
                title = "海啸解除通告"
            elif level in ("MajorWarning", "Warning", "Watch", "Minor"):
                # 日本海啸等级：对齐 JmaTsunamiPresenter 语义
                title = {
                    "MajorWarning": "大津波警報",
                    "Warning": "津波警報",
                    "Watch": "津波注意報",
                    "Minor": "若干の海面変動",
                }.get(level, "津波予報")
            else:
                title = f"海啸{level}警报"
        if not title:
            title = "海啸警报"

        place_name = str(params.get("place_name") or "模拟海域").strip()
        lat = _safe_float(params.get("latitude"), 35.0)
        lon = _safe_float(params.get("longitude"), 140.0)
        magnitude = _safe_float(params.get("magnitude"), 7.5)

        message_type = "warning"
        normalized_level = level.replace("级", "")
        # 日本海啸 Minor 视为“若干海面变动”，与信息同级不触发 warning 文案
        if (
            normalized_level
            and normalized_level not in {"信息"}
            and normalized_level != "Minor"
        ):
            message_type = "warning"
        if "警报" in title or "预警" in title:
            message_type = "warning"

        # 海啸预报区 / 水位监测站 / 图件附件（前端 schema 支持 JSON 数组/字典输入）
        forecasts = _parse_json_list(params.get("forecasts"))
        monitoring_stations = _parse_json_list(params.get("monitoring_stations"))
        map_urls = _parse_json_dict(params.get("map_urls"))
        # 中国海啸源按等级生成默认预报区，避免空数组导致展示空白。
        # 字段名对齐 TsunamiAlertPresenter 读取逻辑（name / warningLevel / estimatedArrivalTime / maxWaveHeight）。
        if not forecasts and source_entry.source_id == "china_tsunami_fanstudio":
            forecasts = [
                {
                    "name": place_name,
                    "warningLevel": "红色" if "大" in level else (level or "黄色"),
                    "estimatedArrivalTime": (now + timedelta(minutes=15)).strftime(
                        "%H:%M"
                    ),
                    "maxWaveHeight": "300-500" if "大" in level else "30-100",
                }
            ]

        # JMA 海啸源：把 schema 层予報区 JSON（对齐 P2P 552/EQSC 文档）归一为展示器消费结构。
        if (
            source_entry.source_id in ("jma_tsunami_p2p", "jma_tsunami_eqsc")
            and forecasts
        ):
            normalized_forecasts: list[dict[str, Any]] = []
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                first_height = (
                    item.get("firstHeight")
                    if isinstance(item.get("firstHeight"), dict)
                    else {}
                )
                max_height = (
                    item.get("maxHeight")
                    if isinstance(item.get("maxHeight"), dict)
                    else {}
                )
                grade_raw = str(
                    item.get("grade") or item.get("warningLevel") or "Unknown"
                ).strip()
                max_desc = str(
                    max_height.get("description") or item.get("maxWaveHeight") or ""
                ).strip()
                max_value_raw = max_height.get("value")
                if max_value_raw is None:
                    max_value_raw = item.get("maxHeightValue")
                try:
                    max_value = (
                        float(max_value_raw)
                        if max_value_raw not in (None, "")
                        else None
                    )
                except (TypeError, ValueError):
                    max_value = None
                normalized_forecasts.append(
                    {
                        "name": str(
                            item.get("name")
                            or item.get("forecastArea")
                            or item.get("forecastPoint")
                            or ""
                        ).strip(),
                        "grade": grade_raw,
                        "immediate": bool(item.get("immediate")),
                        "condition": str(
                            first_height.get("condition") or item.get("condition") or ""
                        ).strip(),
                        "estimatedArrivalTime": str(
                            first_height.get("arrivalTime")
                            or item.get("estimatedArrivalTime")
                            or ""
                        ).strip(),
                        "maxWaveHeight": max_desc
                        or (str(max_value) if max_value is not None else ""),
                        "maxHeightValue": max_value,
                        "maxHeightDescription": max_desc,
                    }
                )
            forecasts = normalized_forecasts

        # 图件 URL：schema 输入使用文档原始键名（earthquakeMapUrl/amplitudeMapUrl/coastalMapUrl），
        # 展示器读取的是简写键（earthquake/amplitude/coastal），这里统一转换。
        if map_urls:
            key_map = {
                "earthquakeMapUrl": "earthquake",
                "amplitudeMapUrl": "amplitude",
                "coastalMapUrl": "coastal",
            }
            normalized_map_urls: dict[str, Any] = {}
            for raw_key, raw_value in map_urls.items():
                target_key = key_map.get(raw_key, raw_key)
                normalized_map_urls[target_key] = raw_value
            map_urls = normalized_map_urls

        # 发布机构按数据源对齐真实链路：
        if source_entry.source_id == "china_tsunami_fanstudio":
            org_unit = "自然资源部海啸预警中心"
        elif source_entry.source_id in ("jma_tsunami_p2p", "jma_tsunami_eqsc"):
            org_unit = "日本气象厅"
        else:
            org_unit = "模拟预警中心"

        extra = {
            "subtitle": f"模拟{title}",
            "org_unit": org_unit,
            "place_name": place_name,
            "latitude": lat,
            "longitude": lon,
            "magnitude": magnitude,
            "depth": _safe_float(params.get("depth"), 10.0),
            "message_type": message_type,
            "forecasts": forecasts,
            "monitoring_stations": monitoring_stations,
            "map_urls": map_urls,
            "max_wave_height": params.get("max_wave_height", ""),
            "estimated_arrival_time": params.get("estimated_arrival_time", ""),
            # JMA 海啸展示器读取 metadata.max_wave_height / metadata.max_wave_height_area
            # 与 EQSC 解析器 area_summary 输出语义保持一致，保证文本展示“全域最大预估波高”行可用。
            "max_wave_height_area": "",
            # 训练报标记：EQSC /jma_tsunami.json 文档含 isTraining（字符串型）
            "is_training": bool(params.get("is_training")),
            # 中国海啸源（FAN /tsunami）特有：事件编号 / 批次 / HTML 报文详情。
            "code": str(params.get("code") or "").strip(),
            "batch": str(params.get("batch") or "").strip(),
            "details_url": str(params.get("details_url") or "").strip(),
        }

        domain_event = TsunamiEvent(
            title=title,
            level=level or "警报",
            issued_at=update_time or now,
            metadata={},
        )
        return _StepBuildResult(
            domain_event=domain_event,
            event_type="tsunami",
            message_type=message_type,
            extra_metadata=extra,
        )

    def _build_weather_payload(
        self,
        step: SimulationStep,
        source_entry,
        now: datetime,
        update_time: datetime | None = None,
    ) -> _StepBuildResult:
        """构建气象领域事件与特有元数据（对齐 WeatherAlarmParser 字段语义）。"""
        params = step.params

        title = str(params.get("title") or "").strip()
        headline = str(params.get("headline") or "").strip()
        description = str(params.get("description") or "").strip()
        if not title:
            title = headline or "模拟气象预警"
        if not headline:
            headline = title

        weather_code = str(
            params.get("weather_code") or params.get("weather_type") or ""
        ).strip()

        extra = {
            "issue_time": update_time or now,
            "weather_type": weather_code,
            "weather_code": weather_code,
            "type": weather_code,
            "alert_code": weather_code,
            "code": weather_code,
            "longitude": _safe_float(params.get("longitude"), 116.4),
            "latitude": _safe_float(params.get("latitude"), 39.9),
            "title": title,
            "headline": headline,
            "description": description,
        }

        domain_event = WeatherEvent(
            title=title,
            headline=headline,
            effective_at=update_time or now,
            metadata={},
        )
        return _StepBuildResult(
            domain_event=domain_event,
            event_type="weather_alarm",
            message_type="weatheralert",
            extra_metadata=extra,
        )

    def _build_typhoon_payload(
        self,
        step: SimulationStep,
        source_entry,
        now: datetime,
        update_time: datetime | None = None,
    ) -> _StepBuildResult:
        """构建台风领域事件与特有元数据（对齐 TyphoonParser 字段语义）。"""
        params = step.params

        typhoon_id = str(params.get("typhoon_id") or "").strip()
        if not typhoon_id:
            typhoon_id = f"SIM{now.strftime('%y%m')}"

        name = str(params.get("name") or "模拟台风").strip()
        name_en = str(params.get("name_en") or "SIM").strip()
        typhoon_type = str(params.get("typhoon_type") or "台风").strip()

        lat = _safe_float(params.get("latitude"), 20.0)
        lon = _safe_float(params.get("longitude"), 125.0)
        move_direction = str(params.get("move_direction") or "西北").strip()

        # 台风轨迹与风圈按数据源动态化，与真实解析器一致。缺省时生成默认轨迹驱动路径图、风圈补默认四象限。
        is_fan_source = step.source_id == "typhoon_fanstudio"
        history_track = _parse_json_list(params.get("history_track"))
        future_track = _parse_json_list(params.get("future_track"))
        wind_circle = _parse_json_dict(params.get("wind_circle"))
        if not is_fan_source:
            if not history_track:
                history_track = _build_default_typhoon_track(
                    lat, lon, now, direction=move_direction
                )
            if not future_track:
                future_track = _build_default_future_track(
                    lat, lon, now, direction=move_direction
                )
            if not wind_circle:
                wind_circle = {
                    "30KTS": {"NE": 300, "SE": 280, "SW": 260, "NW": 300},
                    "50KTS": {"NE": 100, "SE": 90, "SW": 80, "NW": 100},
                }

        # 按数据源家族区分台风元数据：FAN 走 fan_studio 语义，
        # EQSC 轮询源（typhoon_eqsc）复用其直构链路的 data_source 语义。
        data_source = "fan_studio"
        source_family = "fan_studio"
        if step.source_id == "typhoon_eqsc":
            data_source = "eqsc"
            source_family = "eqsc"
        elif step.source_id.startswith("typhoon_"):
            data_source = "eqsc"
            source_family = "eqsc"

        extra = {
            "data_source": data_source,
            "info_type": "fan",
            "typhoon_data_mode": "sim",
            "source_family": source_family,
        }
        # FAN 源不输出轨迹/风圈（与真实解析器 metadata 一致）；EQSC 源透传
        if not is_fan_source:
            extra["history_track"] = history_track
            extra["future_track"] = future_track
            extra["wind_circle"] = wind_circle

        domain_event = TyphoonEvent(
            typhoon_id=typhoon_id,
            name=name,
            name_en=name_en,
            typhoon_type=typhoon_type,
            latitude=lat,
            longitude=lon,
            pressure=_safe_int(params.get("pressure"), 960),
            wind_speed=_safe_float(params.get("wind_speed"), 35.0),
            power=_safe_int(params.get("power"), 12),
            move_direction=move_direction,
            move_speed=_safe_float(params.get("move_speed"), 20.0),
            radius7=_safe_int(params.get("radius7"), 300),
            radius10=_safe_int(params.get("radius10"), 100),
            is_active=True,
            updated_at=update_time or now,
            history_track=history_track,
            future_track=future_track,
            wind_circle=wind_circle,
            metadata={},
        )
        return _StepBuildResult(
            domain_event=domain_event,
            event_type="typhoon",
            message_type="typhoon",
            extra_metadata=extra,
        )


# 全局构建器实例（线程安全：只保存 event_key 前缀映射，由执行器按流隔离）
_builder_instance: SimulationBuilder | None = None


def get_simulation_builder() -> SimulationBuilder:
    """获取全局模拟构建器实例。"""
    global _builder_instance
    if _builder_instance is None:
        _builder_instance = SimulationBuilder()
    return _builder_instance


__all__ = ["SimulationBuilder", "get_simulation_builder"]
