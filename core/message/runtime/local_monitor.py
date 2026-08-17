"""
运行时本地监控器。

该模块负责根据本地位置与事件参数估算本地可能感受到的烈度/震度，
为本地阈值过滤与本地预估展示提供统一能力。

强度体系自动判定：
- 按本地坐标自动解析应使用 CENC 烈度（中国）还是 JMA 震度（日本）公式；
- 中国境内再按本地坐标经度自动切换东部/西部衰减参数；
- 可手动覆盖（auto/cenc/jma）。
"""

from __future__ import annotations

from typing import TypedDict

from astrbot.api import logger

from ...services.geo.intensity_service import IntensityCalculator
from ...services.geo.jma_shindo_service import calculate_jma_shindo
from ...services.geo.location_system_resolver import (
    SYSTEM_AUTO,
    SYSTEM_CENC,
    SYSTEM_JMA,
    resolve_intensity_system,
)
from ...services.geo.travel_time_service import TravelTimeService

# 本地无采样点 arv 时的默认场地放大（vs30=600 工程基岩等效）
_DEFAULT_LOCAL_VS30_MS = 600.0


class LocalEstimationResult(TypedDict):
    """本地预估结果类型。"""

    is_allowed: bool
    distance: float
    intensity: float
    place_name: str
    # 使用的强度体系：cenc（中国烈度）/ jma（日本震度），供展示层适配文案
    system: str
    # 当前生效的阈值（按体系选择：cenc 用烈度阈值，jma 用震度阈值）
    threshold: float
    # 阈值对应的强度单位：cenc 为 "烈度"，jma 为 "震度"，供日志/详情区分措辞
    threshold_unit: str
    # P/S 波预计到达时间（秒），走时模型不可用时为 None
    p_travel_sec: float | None
    s_travel_sec: float | None


class LocalMonitor:
    """本地烈度/震度监控器。"""

    def __init__(self, config: dict):
        # 这些配置共同决定本地监控是否启用、监控地点坐标和允许通过的强度阈值。
        self.enabled = config.get("enabled", False)
        self.latitude = config.get("latitude", 0.0)
        self.longitude = config.get("longitude", 0.0)
        # 烈度阈值（cenc 中国烈度体系使用，0~12）
        self.intensity_threshold = config.get("intensity_threshold", 2.0)
        # 震度阈值（jma 日本震度体系使用，計測震度 0~7）
        self.shindo_threshold = config.get("shindo_threshold", 2.0)
        self.strict_mode = config.get("strict_mode", False)
        self.place_name = config.get("place_name", "本地")
        # 强度体系手动指定：auto（自动判定）/ cenc（中国烈度）/ jma（日本震度）
        self.intensity_system = config.get("intensity_system", SYSTEM_AUTO)
        # 解析出的强度体系（惰性缓存；坐标或手动指定变化时重算）
        self._resolved_system: str | None = None
        # 上次参与体系解析的坐标/手动指定，用于判断是否需要失效缓存
        self._resolved_system_key: tuple[float, float, str] | None = None

    # 配置档位「震度 7」对应的计测震度下界：
    # ScaleConverter.classify_measured_intensity 将 ≥6.5 归类为震度 7，
    # 若直接以 7.0 作为阈值，会过滤掉 6.5~7.0 区间实际属震度 7 的事件。
    _SHINDO_7_MEASURED_MIN = 6.5

    def _resolve_threshold(self) -> tuple[float, str]:
        """按解析出的强度体系返回 (当前生效阈值, 阈值单位)。

        cenc（中国烈度）使用烈度阈值，jma（日本震度）使用震度阈值，
        避免两种量纲完全不同的体系混用同一个数值。

        震度阈值做档位映射，避免严格模式过滤掉 6.5~7.0 区间实际属震度 7 的事件；其余数值原样使用。
        """
        if self._get_system() == SYSTEM_JMA:
            threshold = float(self.shindo_threshold)
            if threshold >= 7.0:
                threshold = self._SHINDO_7_MEASURED_MIN
            return threshold, "震度"
        return float(self.intensity_threshold), "烈度"

    def _get_system(self) -> str | None:
        """解析本地坐标应使用的强度体系（带坐标变更缓存）。

        首次调用或坐标/手动指定变化时重算并写入 _resolved_system，
        其余情况直接复用缓存，避免每条事件重复执行 F-E 区划与采样点查询。
        """
        try:
            key = (
                float(self.latitude),
                float(self.longitude),
                str(self.intensity_system or SYSTEM_AUTO),
            )
        except (TypeError, ValueError):
            key = (0.0, 0.0, SYSTEM_AUTO)
        if self._resolved_system_key != key:
            self._resolved_system = resolve_intensity_system(*key)
            self._resolved_system_key = key
        if self._resolved_system is None:
            # 体系解析失败时按中国烈度兜底（与旧行为一致），避免本地监控失效
            return SYSTEM_CENC
        return self._resolved_system

    def _estimate_intensity(
        self, magnitude: float, distance: float, depth: float, epicenter_lng: float
    ) -> float:
        """按解析出的强度体系估算本地强度值。

        中国体系（cenc）：使用烈度衰减式，按震中经度自动切换东西部参数。
        日本体系（jma）：使用 JMA 距离衰减式，本地坐标场地按 vs30=600 兜底。

        Args:
            epicenter_lng: 震中经度，用于 CENC 烈度 105°E 东西部分界。
        """
        system = self._get_system()
        if system == SYSTEM_JMA:
            shindo = calculate_jma_shindo(
                magnitude=magnitude,
                depth_km=depth,
                distance_km=distance,
                vs30=_DEFAULT_LOCAL_VS30_MS,
            )
            if shindo is None:
                return 0.0
            return shindo

        # CENC 烈度：event_longitude 传震中经度，自动切换东西部衰减参数
        return IntensityCalculator.calculate_estimated_intensity(
            magnitude,
            distance,
            depth,
            event_longitude=epicenter_lng,
        )

    def check_event(self, earthquake) -> tuple[bool, float, float]:
        """检查地震事件是否满足本地监控条件，并返回距离与预估强度。"""
        if not self.enabled:
            return True, 0.0, 0.0

        latitude = getattr(earthquake, "latitude", None)
        longitude = getattr(earthquake, "longitude", None)
        magnitude = getattr(earthquake, "magnitude", None)
        depth = getattr(earthquake, "depth", None)

        if latitude is None or longitude is None:
            return not self.strict_mode, 0.0, 0.0

        distance = IntensityCalculator.calculate_distance(
            latitude, longitude, self.latitude, self.longitude
        )
        intensity = self._estimate_intensity(
            magnitude or 0.0,
            distance,
            depth if depth is not None else 10.0,
            float(longitude),
        )

        threshold, unit = self._resolve_threshold()
        if self.strict_mode and intensity < threshold:
            logger.info(
                f"[灾害预警] 本地{unit} {intensity:.1f} < 阈值 {threshold}，严格模式已过滤"
            )
            return False, distance, intensity

        return True, distance, intensity

    def evaluate(self, earthquake) -> LocalEstimationResult | None:
        """纯判定接口，不对事件对象写入副作用。"""
        if not self.enabled:
            return None

        is_allowed, distance, intensity = self.check_event(earthquake)

        # 基于震源深度与震中距估算 P/S 波预计到达时间。
        # 缺 depth时与本地强度一致，按 10 km 兜底估算。
        depth = getattr(earthquake, "depth", None)
        depth_km = float(depth) if depth is not None else 10.0
        p_travel_sec: float | None = None
        s_travel_sec: float | None = None
        if distance > 0:
            try:
                travel_result = TravelTimeService.lookup(depth_km, float(distance))
                p_travel_sec = travel_result.p_travel_sec
                s_travel_sec = travel_result.s_travel_sec
            except Exception as exc:
                logger.debug(f"[灾害预警] P/S 波走时查询失败: {exc}")

        threshold, unit = self._resolve_threshold()
        return {
            "is_allowed": is_allowed,
            "distance": distance,
            "intensity": intensity,
            "place_name": self.place_name,
            "system": self._get_system() or SYSTEM_CENC,
            "threshold": threshold,
            "threshold_unit": unit,
            "p_travel_sec": p_travel_sec,
            "s_travel_sec": s_travel_sec,
        }
