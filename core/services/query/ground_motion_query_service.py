"""
地震动预测服务。

根据震中参数（纬度/经度/震级/震源深度）与预测点坐标，估算：
- 预测点与震中距离（Haversine 球面距离）
- 预测点 CSIS（中国仪器烈度，GB/T 17742-2020 同源衰减）
- 预测点 PGA（估计峰值加速度，gal），由 CSIS 反推保证与烈度自洽
- 预测点 ARV（速度放大比/系数，无量纲），由 Vs30 场地剪切波速计算
- 预测点 JMA 计测震度（距离衰减式，参考《緊急地震速報で使われる距離減衰式による震度計算》）
- P/S 波走时与到达时间（复用 TravelTimeService，jma2001/jb 双模型）
- PKP / PKIKP 深部震相（震中距 ≥ 105°/110° 才可达，否则显示不会到达）

算法说明：
- CSIS 复用 core/services/geo/intensity_service.py 的衰减模型（105° 经度东西分界）；
- PGA 定义为与 CSIS 同源反推的峰值加速度：PGA = 10^((CSIS - 1.82) / 3.77)；
- ARV 定义为「工程基岩（Vs=400m/s）到地表的最大速度放大率」的近似：
    ARV = 10^(2.367 - 0.852 * log10(Vs30))
  该式由 Vs30 经验关系（速度放大比）推导，Vs30=600m/s 时 ARV≈1.0，
  Vs30 越小（场地越软）放大越大，物理上自洽。
- JMA 计测震度按紧急地震速报距离衰减式链路计算：
    Mw = MJMA - 0.171（宇津 1982）
    L = 10^(0.5*Mw - 1.85)（宇津 1977，断层长，半径取 L/2）
    X = max(√(D²+d²) - L/2, 3)
    PGV600 = 10^(0.58*Mw + 0.0038*D - 1.29
                - log10(X + 0.0028*10^(0.5*Mw)) - 0.002*X)（司・翠川 1999）
    PGV400 = PGV600 * 1.31（松岡・翠川 1994，Vs600→Vs400 换算）
    PGVs   = PGV400 * ARV
    I      = 2.68 + 1.72 * log10(PGVs)（计测震度）
- P/S 波走时复用 core/services/geo/travel_time_service.py 的双线性插值表；
- PKP 族按地震学事实加可达性约束，避免近震中距误报深部震相。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ....utils.converters import ScaleConverter
from ....utils.time_converter import TimeConverter
from ...services.geo.intensity_service import IntensityService
from ...services.geo.jma_shindo_service import (
    DEFAULT_VS30_MS,
    calculate_jma_shindo,
    calculate_vs30_arv,
)
from ...services.geo.travel_time_service import TravelTimeService

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# 中国仪器烈度（CSIS）分级表：罗马数字 -> 名称（GB/T 17742-2020）
CSIS_GRADE_ROMAN = (
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
)
CSIS_GRADE_NAMES = (
    "无感",
    "微有感",
    "轻微有感",
    "室内有感",
    "震感明显",
    "震感强烈",
    "惊慌逃生",
    "房屋损坏",
    "严重破坏",
    "毁灭性",
    "灾难性",
    "极度毁灭",
)

# PGA反推系数：基于 GB/T 17742-2020 仪器烈度换算关系
_PGA_A = 1.82
_PGA_B = 3.77

# Vs30 默认值（m/s）：工程基岩平均 S 波速度，日本 J-SHIS 表层面板常用 400/600；
# 用户可自行通过 GroundMotionInput.vs30 覆盖。
# 实际常量与 JMA 距离衰减式计算统一由 core.services.geo.jma_shindo_service 维护。

# PKP / PKIKP 深部震相可达的最小震中距（度）
PKP_MIN_DISTANCE_DEG = 105.0
PKIKP_MIN_DISTANCE_DEG = 110.0

# PKP 族走时近似（秒），震中距在可达区间内缓慢增长
_PKP_BASE_SEC = 1160.0
_PKP_RATE_SEC_PER_DEG = 0.9
_PKIKP_BASE_SEC = 1205.0
_PKIKP_RATE_SEC_PER_DEG = 0.8

# 地球平均半径（km）
_EARTH_RADIUS_KM = 6371.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GroundMotionInput:
    """地震动预测输入参数。"""

    lat: float  # 震中纬度
    lon: float  # 震中经度
    magnitude: float  # 震级（通用 MJMA 近似）
    depth_km: float  # 震源深度 (km)
    point_lat: float  # 预测点纬度
    point_lon: float  # 预测点经度
    vs30: float = DEFAULT_VS30_MS  # 预测点 Vs30（m/s），默认 600（工程基岩等效）
    occurred_at: datetime | None = None  # 发震时间；None 表示假定即刻发震（用当前时间）


@dataclass(slots=True)
class GroundMotionResult:
    """地震动预测结果。"""

    distance_km: float  # 预测点与震中距离
    csis: float  # 中国仪器烈度（连续值）
    csis_grade: int  # 烈度等级（1~12）
    pga_gal: float  # 估计峰值加速度 PGA（gal），与 CSIS 同源反推
    arv: float  # 速度放大比/系数（无量纲），由 Vs30 计算
    vs30: float  # 使用的 Vs30（m/s）
    jma_shindo: float | None  # JMA 计测震度（连续值），物理不可算时为 None
    p_travel_sec: float | None  # P 波走时（秒）
    s_travel_sec: float | None  # S 波走时（秒）
    pkp_travel_sec: float | None  # PKP 波走时（秒），不可达为 None
    pkikp_travel_sec: float | None  # PKIKP 波走时（秒），不可达为 None
    occurred_at: datetime | None = None  # 发震时间（UTC）
    is_instant: bool = False  # 是否假定即刻发震（无引用时间）
    model_name: str = ""  # 走时模型名
    # 透传输入参数，供格式化展示使用
    lat: float = 0.0
    lon: float = 0.0
    magnitude: float = 0.0
    depth_km: float = 0.0
    point_lat: float = 0.0
    point_lon: float = 0.0

    def format(self, *, display_timezone: str = "UTC+8") -> str:
        """格式化为展示文本。"""
        lines = ["地震动预测结果[仅供参考]："]
        lines.append(f"假定震级：M {self.magnitude:.1f}")
        lines.append(f"假定深度：{self.depth_km:.2f} km")
        lines.append(f"震中位置：({self._fmt_coords(self.lat, self.lon)})")
        lines.append(
            f"预测点位置：({self._fmt_coords(self.point_lat, self.point_lon)})"
        )
        lines.append(f"预测点与震中距离：{self.distance_km:.2f} km")
        lines.append(f"预测点CSIS：{self.csis_display()}")
        if self.jma_shindo is not None:
            lines.append(f"预测点JMA震度：{self.jma_shindo_display()}")
        else:
            lines.append("预测点JMA震度：（数据不足）")
        lines.append(f"预测点PGA：{self.pga_gal:.4g} gal")
        lines.append(f"预测点ARV：{self.arv:.3f}（Vs30={self.vs30:g} m/s）")

        lines.append("预测到达时间：")
        lines.append(
            f"    P波: {self._fmt_wave('P', self.p_travel_sec, display_timezone)}"
        )
        lines.append(
            f"    S波: {self._fmt_wave('S', self.s_travel_sec, display_timezone)}"
        )
        lines.append(
            f"    PKP波: {self._fmt_wave('PKP', self.pkp_travel_sec, display_timezone, unreachable=True)}"
        )
        lines.append(
            f"    PKIKP波: {self._fmt_wave('PKIKP', self.pkikp_travel_sec, display_timezone, unreachable=True)}"
        )

        return "\n".join(lines)

    @staticmethod
    def _fmt_coords(lat: float, lon: float) -> str:
        """格式化坐标为文本展示器同款格式"""
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        return f"{abs(lat):.2f}°{lat_dir}, {abs(lon):.2f}°{lon_dir}"

    def csis_roman(self) -> str:
        """返回 CSIS 的罗马数字等级；0 级返回空串。"""
        if self.csis_grade <= 0:
            return ""
        idx = max(0, min(len(CSIS_GRADE_ROMAN) - 1, self.csis_grade - 1))
        return CSIS_GRADE_ROMAN[idx]

    def csis_display(self) -> str:
        """CSIS 展示文本；0 级（完全无感）直接显示 0。"""
        if self.csis_grade <= 0:
            return "0（无感）"
        return f"{self.csis_roman()}（{self.csis_grade}）"

    def jma_shindo_display(self) -> str:
        """JMA 计测震度 -> 展示文本"""
        if self.jma_shindo is None:
            return "（数据不足）"
        # 计测震度低于 -0.5（0以下）：统一显示 0 [0以下]，
        # 避免远距离小震把模型外推的深负值直接暴露给用户。
        if self.jma_shindo < ScaleConverter.MEASURED_INTENSITY_BELOW_ZERO:
            return "0 [0以下]"
        classified = ScaleConverter.classify_measured_intensity(self.jma_shindo)
        label = (
            ScaleConverter.format_jma_cwa_scale_display(classified)
            if classified is not None
            else "0以下"
        )
        return f"{self.jma_shindo:.2f} [{label}]"

    def _arrival_datetime(self, travel_sec: float | None) -> datetime | None:
        """根据发震时间 + 走时计算到达时间。"""
        if travel_sec is None:
            return None
        base = (
            self.occurred_at
            if self.occurred_at is not None
            else datetime.now(timezone.utc)
        )
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return base.astimezone(timezone.utc) + timedelta(seconds=travel_sec)

    def _fmt_wave(
        self,
        name: str,
        travel_sec: float | None,
        display_timezone: str,
        *,
        unreachable: bool = False,
    ) -> str:
        """格式化单个震相到达时间行。

        Args:
            name: 震相名（P/S/PKP/PKIKP）。
            travel_sec: 走时（秒）；None 表示无走时数据。
            display_timezone: 展示时区。
            unreachable: 是否因物理原因不可达（如 PKP 需震中距 ≥105°）。
                为 True 时 None 展示为「不会到达」，否则展示为「数据不足」。
        """
        if travel_sec is None:
            return "（不会到达）" if unreachable else "（数据不足）"
        arrival = self._arrival_datetime(travel_sec)
        if arrival is None:
            return "（不会到达）" if unreachable else "（数据不足）"
        # 使用公开方法 convert_timezone 转换时区；格式串为纯 ASCII，
        # 不涉及 Windows 中文编码问题，可直接 strftime
        local = TimeConverter.convert_timezone(arrival, display_timezone)
        time_str = local.strftime("%Y-%m-%d %H:%M:%S")
        return f"{time_str}（{travel_sec:.1f} 秒）"


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------


def format_coordinates(lat: float, lon: float) -> str:
    """格式化坐标为 N/S/E/W 文本。"""
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{lat_dir}, {abs(lon):.2f}°{lon_dir}"


def csis_to_grade(csis: float) -> int:
    """连续仪器烈度 -> 中国烈度等级（0~12）。

    连续值 < 0.5 视为完全无感，归为 0 级（而非强制最低 1 级），
    避免远距离弱震被误解为"有 1 级震感"。
    """
    if csis < 0.5:
        return 0
    # 十二级烈度，每级大致区间 0.5 ~ 1.5
    # 使用 floor(x + 0.5) 替代 int(round())，规避 Python 银行家舍入
    # （round(2.5)=2、round(3.5)=4）导致的 .5 边界向下/向偶数取整不一致。
    grade = math.floor(csis + 0.5)
    return max(1, min(12, grade))


def csis_to_pga_gal(csis: float) -> float:
    """CSIS -> PGA（峰值加速度，gal）：PGA = 10^((CSIS - 1.82) / 3.77)。"""
    return 10.0 ** ((max(0.0, csis) - _PGA_A) / _PGA_B)


def estimate_pkp_travel_sec(distance_deg: float) -> float | None:
    """PKP 波走时（秒）；震中距 < 105° 时不可达返回 None。"""
    if distance_deg < PKP_MIN_DISTANCE_DEG:
        return None
    return (
        _PKP_BASE_SEC
        + (min(distance_deg, 180.0) - PKP_MIN_DISTANCE_DEG) * _PKP_RATE_SEC_PER_DEG
    )


def estimate_pkikp_travel_sec(distance_deg: float) -> float | None:
    """PKIKP 波走时（秒）；震中距 < 110° 时不可达返回 None。"""
    if distance_deg < PKIKP_MIN_DISTANCE_DEG:
        return None
    return (
        _PKIKP_BASE_SEC
        + (min(distance_deg, 180.0) - PKIKP_MIN_DISTANCE_DEG) * _PKIKP_RATE_SEC_PER_DEG
    )


def distance_km_to_degree(distance_km: float) -> float:
    """震中距 km -> 球面角（度）。"""
    return math.degrees(distance_km / _EARTH_RADIUS_KM)


def predict_ground_motion(params: GroundMotionInput) -> GroundMotionResult:
    """执行地震动预测。

    Args:
        params: 输入参数。

    Returns:
        预测结果。
    """
    # 1. 距离（Haversine）
    distance_km = IntensityService.calculate_distance(
        params.lat, params.lon, params.point_lat, params.point_lon
    )

    # 2. CSIS（中国仪器烈度，105° 东西分界）
    csis = IntensityService.calculate_estimated_intensity(
        params.magnitude,
        distance_km,
        params.depth_km,
        event_longitude=params.lon,
    )

    # 3. PGA（与 CSIS 同源反推，gal）
    pga_gal = csis_to_pga_gal(csis)

    # 4. ARV（由 Vs30 求速度放大比，无量纲）
    arv = calculate_vs30_arv(params.vs30)

    # 5. JMA 计测震度（距离衰减式）
    jma_shindo = calculate_jma_shindo(
        magnitude=params.magnitude,
        depth_km=params.depth_km,
        distance_km=distance_km,
        vs30=params.vs30,
    )

    # 6. P/S 波走时
    travel_result = TravelTimeService.lookup(params.depth_km, distance_km)

    # 7. PKP / PKIKP 深部震相
    distance_deg = distance_km_to_degree(distance_km)
    pkp_sec = estimate_pkp_travel_sec(distance_deg)
    pkikp_sec = estimate_pkikp_travel_sec(distance_deg)

    # 8. 发震时间基准
    occurred_at = params.occurred_at
    is_instant = occurred_at is None
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)
    elif occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)

    return GroundMotionResult(
        distance_km=distance_km,
        csis=csis,
        csis_grade=csis_to_grade(csis),
        pga_gal=pga_gal,
        arv=arv,
        vs30=params.vs30,
        jma_shindo=jma_shindo,
        p_travel_sec=travel_result.p_travel_sec,
        s_travel_sec=travel_result.s_travel_sec,
        pkp_travel_sec=pkp_sec,
        pkikp_travel_sec=pkikp_sec,
        occurred_at=occurred_at,
        is_instant=is_instant,
        model_name=travel_result.model_name,
        lat=params.lat,
        lon=params.lon,
        magnitude=params.magnitude,
        depth_km=params.depth_km,
        point_lat=params.point_lat,
        point_lon=params.point_lon,
    )


__all__ = [
    "GroundMotionInput",
    "GroundMotionResult",
    "DEFAULT_VS30_MS",
    "csis_to_grade",
    "csis_to_pga_gal",
    "calculate_vs30_arv",
    "calculate_jma_shindo",
    "estimate_pkp_travel_sec",
    "estimate_pkikp_travel_sec",
    "distance_km_to_degree",
    "format_coordinates",
    "predict_ground_motion",
]
