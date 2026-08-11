"""
模拟预警服务主入口。
承载模拟预警参数解析、目标会话选择与地震模拟构建能力。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ...domain.earthquake.cmt_normalize import parse_nodal_plane
from ...domain.event_identity import EventIdentity
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...domain.event_payload import SourcePayload
from ...message.runtime.local_monitor import LocalMonitor
from ...rules.base_rule import RuleContext
from ...rules.intensity_rule import EarthquakeThresholdRule
from ...rules.local_rule import LocalIntensityRule
from ...sources.source_catalog import SOURCE_CATALOG, get_source_entry
from ..geo.region_service import region_service


@dataclass(slots=True)
class SimulationBuildResult:
    """地震模拟构建结果。

    同时携带模拟事件本体、包裹体、规则测试报告与判定结果。
    """

    earthquake: EarthquakeEvent
    disaster_event: EventEnvelope
    report_lines: list[str]
    global_pass: bool
    local_pass: bool


@dataclass(slots=True)
class SimulationParamsDefaults:
    """前后端统一使用的默认模拟参数。"""

    # 默认纬度：北京
    latitude: float = 39.9
    # 默认经度：北京
    longitude: float = 116.4
    # 默认震级
    magnitude: float = 5.5
    # 默认震源深度
    depth: float = 10.0
    # 默认数据源
    source: str = "cea_fanstudio"


# 进程内模拟事件递增计数器：用于避免同秒多次触发时 ID 冲突
_sim_event_sequence = 0
_sim_event_sequence_lock = threading.Lock()


def _next_sim_event_sequence() -> int:
    """获取下一个模拟事件序号（线程安全，单调递增）。"""
    global _sim_event_sequence
    with _sim_event_sequence_lock:
        _sim_event_sequence += 1
        return _sim_event_sequence


def get_simulation_params(
    config: dict[str, Any],
    session_config_manager=None,
) -> dict[str, Any]:
    """获取模拟预警可用参数。

    由服务层集中维护，供管理端接口直接读取，避免前端硬编码可选来源列表。
    当传入 session_config_manager 时，target_sessions 将返回带备注名的对象列表。
    """
    raw_target_sessions = config.get("target_sessions") or []
    if session_config_manager is not None:
        # 返回带备注名的对象列表，前端可优先展示备注名
        target_sessions = [
            {
                "session": str(item),
                "session_name": session_config_manager.get_session_name(str(item)),
                "session_display_name": session_config_manager.get_session_display_name(
                    str(item)
                ),
            }
            for item in raw_target_sessions
        ]
    else:
        target_sessions = [str(item) for item in raw_target_sessions]

    defaults = SimulationParamsDefaults()

    # 当前版本仅开放 earthquake，避免前端额外硬编码过滤。
    disaster_types = {
        "earthquake": {
            "label": "地震",
            "icon": "🌍",
            # 前台模拟参数面板支持的模拟渠道表，确保与静态数据源目录保持同步映射
            "formats": [
                {
                    "value": "cea_fanstudio",
                    "label": "FAN Studio - 中国地震预警网 (CEA)",
                },
                {
                    "value": "cea_pr_fanstudio",
                    "label": "FAN Studio - 中国地震预警网 (省级)",
                },
                {
                    "value": "cenc_fanstudio",
                    "label": "FAN Studio - 中国地震台网 (CENC)",
                },
                {
                    "value": "cenc_ir_fanstudio",
                    "label": "FAN Studio - 中国地震台网 (烈度速报)",
                },
                {
                    "value": "fssn_cmt_fanstudio",
                    "label": "FAN Studio - FSSN 矩心矩张量解 (CMT)",
                },
                {
                    "value": "cwa_fanstudio",
                    "label": "FAN Studio - 台湾中央气象署 (强震即时警报)",
                },
                {
                    "value": "cwa_fanstudio_report",
                    "label": "FAN Studio - 台湾中央气象署 (地震报告)",
                },
                {"value": "jma_fanstudio", "label": "FAN Studio - 日本气象厅 (JMA)"},
                {"value": "usgs_fanstudio", "label": "FAN Studio - USGS"},
                {
                    "value": "sa_fanstudio",
                    "label": "FAN Studio - 美国 ShakeAlert 地震预警",
                },
                {"value": "jma_wolfx", "label": "Wolfx - 日本 JMA 紧急地震速报"},
                {"value": "cea_wolfx", "label": "Wolfx - 中国 CENC 地震预警"},
                {"value": "cwa_wolfx", "label": "Wolfx - 台湾 CWA 地震预警"},
                {"value": "cenc_wolfx", "label": "Wolfx - 中国 CENC 地震情报"},
                {"value": "jma_wolfx_info", "label": "Wolfx - 日本 JMA 地震情报"},
                {"value": "jma_p2p", "label": "P2P - 日本 JMA 紧急地震速报"},
                {"value": "jma_p2p_info", "label": "P2P - 日本 JMA 地震情报"},
                {"value": "global_quake", "label": "Global Quake"},
            ],
            "defaults": {
                "latitude": defaults.latitude,
                "longitude": defaults.longitude,
                "magnitude": defaults.magnitude,
                "depth": defaults.depth,
                "source": defaults.source,
            },
        }
    }

    return {
        "target_sessions": target_sessions,
        "disaster_types": disaster_types,
        "timestamp": datetime.now().isoformat(),
    }


def resolve_target_session(
    config: dict[str, Any], target_session: str = ""
) -> str | None:
    """解析模拟发送目标会话。

    显式指定时优先使用指定会话，否则退回到配置中的首个目标会话。
    """
    if target_session:
        return target_session

    target_sessions = config.get("target_sessions", [])
    if target_sessions:
        return target_sessions[0]
    return None


def build_earthquake_simulation(
    manager: Any,
    *,
    lat: float,
    lon: float,
    magnitude: float,
    depth: float,
    source: str,
    runtime_config: dict[str, Any] | None = None,
) -> SimulationBuildResult:
    """构建地震模拟数据并执行规则测试。

    该函数不会直接发送消息，而是先产出模拟事件和规则评估结果，供上层决定后续动作。
    """
    source_entry = get_source_entry(source)
    if source_entry is None:
        valid_sources = ", ".join(sorted(SOURCE_CATALOG.keys()))
        raise ValueError(f"无效的数据源: {source}，可用数据源: {valid_sources}")

    now = datetime.now(timezone.utc)
    # 同秒多次触发时，依靠递增序号拼接模拟标识，避免事件键冲突。
    ts = int(now.timestamp())
    seq = _next_sim_event_sequence()
    sim_id_suffix = f"{ts}_{seq}"
    # 调用行政区划网格服务解析并翻译模拟震中的位置地名
    final_place_name = region_service.translate_place_name("模拟震中", lat, lon)

    payload_attributes: dict[str, Any] = {"test": True, "source_id": source}
    if source == "usgs_fanstudio":
        payload_attributes["update_time"] = datetime.now(timezone.utc).isoformat()

    metadata: dict[str, Any] = {
        "source_enum": source_entry.source_enum,
        "source_type": source_entry.source_type.value,
        "test": True,
        # 该特权标志允许模拟事件在核心路由链路中绕过对时效、去重等强物理属性的硬过滤限制
        "simulation_bypass_regular_filters": True,
    }
    if source == "usgs_fanstudio":
        metadata["update_time"] = payload_attributes["update_time"]

    earthquake = EarthquakeEvent(
        occurred_at=now,
        latitude=lat,
        longitude=lon,
        depth=depth,
        magnitude=magnitude,
        place_name=final_place_name,
        metadata=metadata,
    )

    # 日本来源通常更依赖震度字段展示，因此按震级粗略补一个模拟震度。
    if source in ["jma_p2p", "jma_wolfx", "jma_p2p_info"]:
        earthquake.scale = max(0, min(7, int(magnitude - 2)))
    # 烈度速报模拟：补最高烈度与全文概述，便于验证展示链路。
    if source == "cenc_ir_fanstudio":
        simulated_intensity = max(1.0, min(12.0, round(magnitude + 1.0, 1)))
        earthquake.intensity = simulated_intensity
        earthquake.headline = f"{final_place_name}{magnitude:.1f}级地震"
        metadata.update(
            {
                "info_type": "烈度速报",
                "headline": earthquake.headline,
                "name_by_info": earthquake.headline,
                "intensity_info_text": (
                    f"基于'GB/T17742-2020中国地震烈度表'，结合台站实测仪器烈度，"
                    f"本次地震推测最高烈度为{simulated_intensity:.0f}度。"
                ),
                "station_count": 1,
                "stations": [
                    {
                        "name": "模拟台站",
                        "intensity": simulated_intensity,
                        "lat": lat,
                        "lon": lon,
                    }
                ],
                "stations_topn": [
                    {
                        "name": "模拟台站",
                        "intensity": simulated_intensity,
                        "lat": lat,
                        "lon": lon,
                    }
                ],
            }
        )
        earthquake.metadata = dict(metadata)
    elif source == "fssn_cmt_fanstudio":
        # FSSN CMT 模拟：补充节面、多震级等特征
        earthquake.headline = "FSSN CMT 模拟地震"
        # 默认使用走向200/倾角77/滑动角74的斜滑逆冲类型作为模拟样本
        metadata.update(
            {
                "info_type": "CMT",
                "cmt_id": f"sim_cmt_{sim_id_suffix}",
                "fssn_event_id": f"FSSN_SIM_{sim_id_suffix}",
                "all_magnitudes": {
                    "M": magnitude,
                    "Mww": magnitude + 0.2,
                    "mB": magnitude + 0.1,
                    "Mwp": magnitude - 0.1,
                },
                "display_magnitude": magnitude,
                "display_magnitude_type": "M",
                "depth": depth,
                "depth_error": 5.0,
                "centroid_depth": depth - 2.0 if depth > 2.0 else depth,
                "nodal_plane1": {
                    "strike": 200.0,
                    "dip": 77.0,
                    "rake": 74.0,
                },
                "nodal_plane2": {
                    "strike": 73.0,
                    "dip": 21.0,
                    "rake": 141.0,
                },
                "moment_tensor": {
                    "mnn": -5.0526e17,
                    "mee": -6.9553e17,
                    "mdd": 1.2008e18,
                    "mne": 1.2994e18,
                    "mnd": -9.2356e17,
                    "med": 2.6576e18,
                },
                "beachball_ready": True,
                "is_supplement_product": True,
            }
        )
        metadata["nodal_plane1"] = parse_nodal_plane(metadata["nodal_plane1"])
        metadata["nodal_plane2"] = parse_nodal_plane(metadata["nodal_plane2"])
        earthquake.metadata = dict(metadata)

    identity = EventIdentity(
        event_id=f"sim_{sim_id_suffix}",
        source_id=source,
        event_type="earthquake",
        provider_family=source_entry.provider_family.value,
        source_enum=source_entry.source_enum,
        report_num=1,
        published_at=now,
        attributes={"test": True},
    )
    disaster_event = EventEnvelope(
        identity=identity,
        event=earthquake,
        payload=SourcePayload(
            source_id=source,
            provider_family=source_entry.provider_family.value,
            raw=dict(payload_attributes),
            attributes=dict(payload_attributes),
        ),
        metadata=metadata,
        received_at=now,
    )

    report_lines = [
        "🧪 灾害预警模拟报告",
        f"Input: M{magnitude} @ ({lat}, {lon}), Depth {depth}km\n",
    ]

    runtime_config = dict(runtime_config or getattr(manager, "config", {}) or {})
    # 模拟链路用于验证展示、渲染、发送与本地预估，不应被震级/烈度/报次等过滤器拦截。
    runtime_config["__simulation_bypass_regular_filters"] = True
    build_policy_state = getattr(manager, "_build_policy_state", None)
    if callable(build_policy_state):
        policy_state = build_policy_state(runtime_config)
    else:
        local_monitoring_config = runtime_config.get("local_monitoring", {})
        policy_state = {"local_monitor": LocalMonitor(local_monitoring_config)}

    # 模拟评估不提交状态，避免污染真实运行时规则链缓存。
    simulation_context = RuleContext(
        event=disaster_event,
        runtime_config=runtime_config,
        policy_state=policy_state,
        commit_state=False,
    )

    # 评估全局过滤判定结果并收集报告
    global_decision = EarthquakeThresholdRule().evaluate(simulation_context)
    global_pass = global_decision.accepted
    if global_pass:
        report_lines.append("✅ 全局过滤: 通过")
    else:
        report_lines.append("❌ 全局过滤: 拦截 (不满足最小震级/烈度要求)")

    # 评估本地烈度监控触发规则并收集报告
    local_decision = LocalIntensityRule().evaluate(simulation_context)
    local_pass = local_decision.accepted
    local_monitor = policy_state.get("local_monitor")
    local_result = simulation_context.extras.get("local_estimation")

    if local_monitor is None:
        report_lines.append("ℹ️ 本地监控: 未配置")
    elif local_result is None:
        report_lines.append("ℹ️ 本地监控: 未启用")
    else:
        dist = local_result.get("distance")
        inte = local_result.get("intensity")

        if local_pass:
            report_lines.append("✅ 本地监控: 触发")
        else:
            report_lines.append("❌ 本地监控: 拦截 (严格模式生效中)")

        report_lines.append(
            f"   ⦁ 严格模式: {'开启' if local_monitor.strict_mode else '关闭 (仅计算不拦截)'}"
        )

        dist_str = f"{dist:.1f} km" if dist is not None else "未知"
        inte_str = f"{inte:.1f}" if inte is not None else "未知"
        p_sec = local_result.get("p_travel_sec")
        s_sec = local_result.get("s_travel_sec")
        p_str = f"{p_sec:.0f} 秒" if p_sec is not None else "未知"
        s_str = f"{s_sec:.0f} 秒" if s_sec is not None else "未知"
        report_lines.extend(
            [
                f"   ⦁ 距本地: {dist_str}",
                f"   ⦁ 预估最大本地烈度: {inte_str}",
                f"   ⦁ 本地烈度阈值: {local_monitor.threshold}",
                f"   ⦁ 预计P波走时: {p_str}",
                f"   ⦁ 预计S波走时: {s_str}",
            ]
        )

    return SimulationBuildResult(
        earthquake=earthquake,
        disaster_event=disaster_event,
        report_lines=report_lines,
        global_pass=global_pass,
        local_pass=local_pass,
    )


__all__ = [
    "SimulationBuildResult",
    "SimulationParamsDefaults",
    "get_simulation_params",
    "resolve_target_session",
    "build_earthquake_simulation",
]
