"""
运行时本地监控器。

该模块负责根据本地位置与事件参数估算本地可能感受到的烈度，
为本地阈值过滤与本地预估展示提供统一能力。
"""

from __future__ import annotations

from typing import TypedDict

from astrbot.api import logger

from ...services.geo.intensity_service import IntensityCalculator
from ...services.geo.travel_time_service import TravelTimeService


class LocalEstimationResult(TypedDict):
    """本地预估结果类型。"""

    is_allowed: bool
    distance: float
    intensity: float
    place_name: str
    # P/S 波预计到达时间（秒），走时模型不可用时为 None
    p_travel_sec: float | None
    s_travel_sec: float | None


class LocalMonitor:
    """本地烈度监控器。"""

    def __init__(self, config: dict):
        # 这些配置共同决定本地监控是否启用、监控地点坐标和允许通过的烈度阈值。
        self.enabled = config.get("enabled", False)
        self.latitude = config.get("latitude", 0.0)
        self.longitude = config.get("longitude", 0.0)
        self.threshold = config.get("intensity_threshold", 2.0)
        self.strict_mode = config.get("strict_mode", False)
        self.place_name = config.get("place_name", "本地")

    def check_event(self, earthquake) -> tuple[bool, float, float]:
        """检查地震事件是否满足本地监控条件，并返回距离与预估烈度。"""
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
        intensity = IntensityCalculator.calculate_estimated_intensity(
            magnitude or 0.0,
            distance,
            depth if depth is not None else 10.0,
            event_longitude=longitude,
        )

        if self.strict_mode and intensity < self.threshold:
            logger.info(
                f"[灾害预警] 本地烈度 {intensity:.1f} < 阈值 {self.threshold}，严格模式已过滤"
            )
            return False, distance, intensity

        return True, distance, intensity

    def evaluate(self, earthquake) -> LocalEstimationResult | None:
        """纯判定接口，不对事件对象写入副作用。"""
        if not self.enabled:
            return None

        is_allowed, distance, intensity = self.check_event(earthquake)

        # 基于震源深度与震中距估算 P/S 波预计到达时间。
        # 缺 depth时与本地烈度一致，按 10 km 兜底估算。
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

        return {
            "is_allowed": is_allowed,
            "distance": distance,
            "intensity": intensity,
            "place_name": self.place_name,
            "p_travel_sec": p_travel_sec,
            "s_travel_sec": s_travel_sec,
        }
