"""
中国影响区县估算服务。

基于 CnSeisIntLoc.js 采样点库，结合震中位置、震级与深度，
估算各行政区县的预估烈度，并按烈度阈值聚合分组输出。

性能与聚合策略：
- 先用 bbox（经纬度矩形框）粗筛采样点，避免全量 10 万点扫描
- 每个区县仍取 max 烈度
- 对「大区县/大市 key」仅在其采样点质心附近的核心点集上取 max，
  避免远郊贴边点把整市抬爆（如成都市西缘靠近汶川的网格）
- 始终为震中选择一个宿主区县注入虚拟点，提升近场峰值与中心命名稳定性
  （通用规则，不绑定具体历史地震）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from astrbot.api import logger

from .cn_seis_int_loc_loader import get_flat_index
from .intensity_service import IntensityService


@dataclass(slots=True)
class DistrictIntensityEstimate:
    """单个区县的影响估算结果。

    Attributes:
        name: 区县名。
        intensity: 该区县代表烈度（取受限点集内最大值）。
        distance_km: 该区县距震中最近有效采样点的距离（km）。
    """

    name: str
    intensity: float
    distance_km: float


class CnDistrictIntensityService:
    """中国影响区县估算服务。"""

    # 默认最小展示烈度阈值：低于此值的区县不输出
    DEFAULT_MIN_INTENSITY = 1.0

    # bbox 粗筛的经纬度扩展量（度），1 度约 111km
    # 基于烈度衰减公式反推烈度1.0有感距离，加 50% 余量后确定搜索半径
    # 100° 半径约 11100km，足以覆盖 M9.5 超级地震的完整有感范围
    BBOX_MARGIN_MAX = 100.0

    # 震级 -> bbox 扩展量映射表（基于烈度公式反推 + 50% 余量）
    # M3.0→1°, M4.0→3°, M5.0→5°, M5.5→8°, M6.0→11°, M6.5→16°,
    # M7.0→22°, M7.5→31°, M8.0→45°, M8.5→64°, M9.0→92°, M9.5+→100°(截断)
    _BBOX_MARGIN_TABLE: list[tuple[float, float]] = [
        (3.0, 1.0),
        (4.0, 3.0),
        (5.0, 5.0),
        (5.5, 8.0),
        (6.0, 11.0),
        (6.5, 16.0),
        (7.0, 22.0),
        (7.5, 31.0),
        (8.0, 45.0),
        (8.5, 64.0),
        (9.0, 92.0),
        (9.5, 100.0),
    ]

    # 大区县判定（相对本数据集分位收紧，避免绝大多数 key 都被当大区县）：
    # - 点数 >= 100：地级市/大县域
    # - 或跨度 >= 130km：明显大于中位跨度
    # - 或名称以「市」结尾且点数 >= 50
    # 目标：命中真正大块 key，减少对普通县/极震县的误伤
    LARGE_DISTRICT_POINT_THRESHOLD = 100
    LARGE_DISTRICT_SPAN_KM = 130.0
    CITY_NAME_POINT_THRESHOLD = 50

    # 大区县只在「采样点质心」附近的核心点集上取 max
    # 避免远郊贴边点把整市抬到极震级
    LARGE_DISTRICT_CORE_RADIUS_KM = 30.0

    # 震中宿主区县选择：
    # - 在最近采样点距离 + 容差范围内的候选中挑宿主
    # - 同等接近时优先更“细”的行政单元（县/区优于大地级市）
    # 这是通用规则，用于改善近场峰值与中心命名，不绑定具体地震
    HOST_DISTANCE_TOLERANCE_KM = 12.0
    HOST_MAX_SEARCH_KM = 60.0

    @classmethod
    def _calc_bbox_margin(cls, magnitude: float) -> float:
        """根据震级查表插值计算 bbox 扩展量。"""
        if magnitude <= cls._BBOX_MARGIN_TABLE[0][0]:
            return cls._BBOX_MARGIN_TABLE[0][1]
        if magnitude >= cls._BBOX_MARGIN_TABLE[-1][0]:
            return cls._BBOX_MARGIN_TABLE[-1][1]
        # 在表中线性插值
        for i in range(len(cls._BBOX_MARGIN_TABLE) - 1):
            m0, d0 = cls._BBOX_MARGIN_TABLE[i]
            m1, d1 = cls._BBOX_MARGIN_TABLE[i + 1]
            if m0 <= magnitude <= m1:
                ratio = (magnitude - m0) / (m1 - m0)
                return d0 + ratio * (d1 - d0)
        return cls.BBOX_MARGIN_MAX

    @staticmethod
    def _bbox_filter(
        points: list[tuple[str, float, float]],
        center_lng: float,
        center_lat: float,
        margin: float,
    ) -> list[tuple[str, float, float]]:
        """用经纬度矩形框粗筛采样点。"""
        min_lng = center_lng - margin
        max_lng = center_lng + margin
        min_lat = center_lat - margin
        max_lat = center_lat + margin
        return [
            (name, lng, lat)
            for name, lng, lat in points
            if min_lng <= lng <= max_lng and min_lat <= lat <= max_lat
        ]

    @staticmethod
    def _approx_span_km(points: list[tuple[float, float, float]]) -> float:
        """用经纬度包围盒粗估区县采样点跨度（km）。

        points 元素为 (dist_km, lng, lat)。跨度取 lon/lat 方向较大者。
        """
        if len(points) < 2:
            return 0.0
        lngs = [p[1] for p in points]
        lats = [p[2] for p in points]
        min_lng, max_lng = min(lngs), max(lngs)
        min_lat, max_lat = min(lats), max(lats)
        mean_lat = 0.5 * (min_lat + max_lat)
        lat_span = (max_lat - min_lat) * 111.0
        lon_span = (
            (max_lng - min_lng) * 111.0 * max(0.2, math.cos(math.radians(mean_lat)))
        )
        return max(lat_span, lon_span)

    @classmethod
    def _is_large_district(
        cls,
        name: str,
        points: list[tuple[float, float, float]],
    ) -> bool:
        """判断是否为大跨度/多样本区县 key。

        规则（满足任一）：
        1. 采样点数 >= LARGE_DISTRICT_POINT_THRESHOLD
        2. 包围盒跨度 >= LARGE_DISTRICT_SPAN_KM
        3. 名称以「市」结尾且点数 >= CITY_NAME_POINT_THRESHOLD
        """
        n = len(points)
        if n >= cls.LARGE_DISTRICT_POINT_THRESHOLD:
            return True
        if cls._approx_span_km(points) >= cls.LARGE_DISTRICT_SPAN_KM:
            return True
        if name.endswith("市") and n >= cls.CITY_NAME_POINT_THRESHOLD:
            return True
        return False

    @staticmethod
    def _admin_fineness_score(name: str) -> int:
        """行政粒度分，越高越细（越适合作为震中宿主名）。

        仅用于近场宿主选择的通用偏好，不针对具体地名。
        """
        if name.endswith("县") or name.endswith("旗"):
            return 3
        if name.endswith("区"):
            return 2
        if name.endswith("市"):
            return 1
        return 0

    @classmethod
    def _select_representative_points(
        cls,
        name: str,
        points: list[tuple[float, float, float]],
    ) -> list[tuple[float, float, float]]:
        """为区县选择用于 max 统计的代表点集。

        - 普通区县：使用全部采样点
        - 大区县：仅使用「采样点质心」半径内的核心点；
          若核心区无点，回退为距质心最近的若干点
        """
        if not points:
            return []
        if not cls._is_large_district(name, points):
            return points

        # 质心（算术平均，足够做核心区裁剪）
        mean_lng = sum(p[1] for p in points) / len(points)
        mean_lat = sum(p[2] for p in points) / len(points)
        cos_lat_factor = max(0.2, math.cos(math.radians(mean_lat)))

        core: list[tuple[float, float, float]] = []
        for dist, lng, lat in points:
            # 点到质心的近似平面距离
            dlat = (lat - mean_lat) * 111.0
            dlng = (lng - mean_lng) * 111.0 * cos_lat_factor
            if math.hypot(dlng, dlat) <= cls.LARGE_DISTRICT_CORE_RADIUS_KM:
                core.append((dist, lng, lat))

        if core:
            return core

        # 核心区无点时，取距质心最近的点（至少 1 个）
        ranked = sorted(
            points,
            key=lambda p: math.hypot(
                (p[1] - mean_lng) * 111.0 * cos_lat_factor,
                (p[2] - mean_lat) * 111.0,
            ),
        )
        return ranked[: max(1, min(5, len(ranked)))]

    @classmethod
    def _choose_epicenter_host(
        cls,
        district_points: dict[str, list[tuple[float, float, float]]],
    ) -> tuple[str, float] | None:
        """为震中选择宿主区县。

        通用策略：
        1. 先找最近采样距离 d_min
        2. 在 [d_min, d_min + 容差] 且不超过 HOST_MAX_SEARCH_KM 的候选中
        3. 优先：更细行政单元 > 非大区县 > 更近 > 更少采样点
        """
        candidates: list[tuple[str, float, bool, int, int]] = []
        for name, pts in district_points.items():
            if not pts:
                continue
            d0 = pts[0][0]
            if d0 > cls.HOST_MAX_SEARCH_KM:
                continue
            is_large = cls._is_large_district(name, pts)
            fineness = cls._admin_fineness_score(name)
            candidates.append((name, d0, is_large, fineness, len(pts)))

        if not candidates:
            return None

        d_min = min(c[1] for c in candidates)
        near = [c for c in candidates if c[1] <= d_min + cls.HOST_DISTANCE_TOLERANCE_KM]
        if not near:
            near = candidates

        # 排序：细粒度优先、非大区县优先、更近优先、点数更少优先
        near.sort(key=lambda c: (-c[3], c[2], c[1], c[4], c[0]))
        best = near[0]
        return best[0], best[1]

    @classmethod
    def _inject_epicenter_virtual_point(
        cls,
        *,
        latitude: float,
        longitude: float,
        magnitude: float,
        depth: float,
        district_points: dict[str, list[tuple[float, float, float]]],
        district_max_intensity: dict[str, float],
        district_min_distance: dict[str, float],
    ) -> str | None:
        """为震中注入虚拟点到宿主区县，提升近场峰值与中心命名。

        始终尝试注入（只要找得到合理宿主），不依赖“附近完全无采样点”。
        这样中强震在网格较稀时也不会只剩钝化的远点 max。

        Returns:
            被注入虚拟点的区县名；无法选择宿主时返回 None。
        """
        host = cls._choose_epicenter_host(district_points)
        if host is None:
            return None
        best_name, nearest_sample_dist = host

        # 虚拟点：震中本身，距离 0
        virtual_intensity = IntensityService.calculate_estimated_intensity(
            magnitude, 0.0, depth, event_longitude=longitude
        )
        prev_i = district_max_intensity.get(best_name, -1.0)
        prev_d = district_min_distance.get(best_name, float("inf"))
        if virtual_intensity > prev_i:
            district_max_intensity[best_name] = virtual_intensity
        # 宿主区县的代表距离记为 0（震中落在该单元代理范围内）
        if 0.0 < prev_d:
            district_min_distance[best_name] = 0.0

        logger.debug(
            f"[灾害预警] 近场虚拟点注入: 宿主为{best_name}, "
            f"最近采样距离 {nearest_sample_dist:.1f}km, 烈度 {virtual_intensity:.2f}"
        )
        return best_name

    @classmethod
    def estimate_affected_districts(
        cls,
        latitude: float,
        longitude: float,
        magnitude: float,
        depth: float,
        min_intensity: float = DEFAULT_MIN_INTENSITY,
    ) -> list[DistrictIntensityEstimate]:
        """估算受影响区县列表。

        Args:
            latitude: 震中纬度。
            longitude: 震中经度。
            magnitude: 震级。
            depth: 震源深度（km）。
            min_intensity: 最小展示烈度阈值，低于此值的区县不返回。

        Returns:
            按烈度从高到低排序的区县估算结果列表。
            资源加载失败时返回空列表，不影响主流程。
        """
        # 直接使用缓存的扁平索引，避免每次调用重建 10 万点列表
        flat = get_flat_index()
        if not flat:
            return []

        # 根据震级动态计算 bbox 范围，大震覆盖更广
        margin = cls._calc_bbox_margin(magnitude)
        candidates = cls._bbox_filter(flat, longitude, latitude, margin)
        if not candidates:
            return []

        # 先按区县收集采样点，并预计算距离，便于大区县半径限制
        # value: list[(dist_km, lng, lat)]，按距离升序
        district_points: dict[str, list[tuple[float, float, float]]] = {}
        for name, lng, lat in candidates:
            dist = IntensityService.calculate_distance(latitude, longitude, lat, lng)
            district_points.setdefault(name, []).append((dist, lng, lat))
        for name in district_points:
            district_points[name].sort(key=lambda item: item[0])

        district_max_intensity: dict[str, float] = {}
        district_min_distance: dict[str, float] = {}

        for name, pts in district_points.items():
            rep_pts = cls._select_representative_points(name, pts)
            if not rep_pts:
                continue
            max_intensity = -1.0
            min_distance = float("inf")
            for dist, _lng, _lat in rep_pts:
                intensity = IntensityService.calculate_estimated_intensity(
                    magnitude, dist, depth, event_longitude=longitude
                )
                if intensity > max_intensity:
                    max_intensity = intensity
                if dist < min_distance:
                    min_distance = dist
            district_max_intensity[name] = max_intensity
            district_min_distance[name] = min_distance

        # 近场宿主虚拟点：抬升峰值，并稳定震中附近命名
        cls._inject_epicenter_virtual_point(
            latitude=latitude,
            longitude=longitude,
            magnitude=magnitude,
            depth=depth,
            district_points=district_points,
            district_max_intensity=district_max_intensity,
            district_min_distance=district_min_distance,
        )

        # 按最小烈度阈值过滤并构建结果
        results: list[DistrictIntensityEstimate] = []
        for name, intensity in district_max_intensity.items():
            if intensity >= min_intensity:
                results.append(
                    DistrictIntensityEstimate(
                        name=name,
                        intensity=intensity,
                        distance_km=district_min_distance.get(name, 0.0),
                    )
                )

        # 按烈度从高到低排序
        results.sort(key=lambda x: x.intensity, reverse=True)

        logger.debug(
            f"[灾害预警] 影响区县估算: 震中 ({latitude},{longitude}) M {magnitude} "
            f"深度 {depth}km, 粗筛 {len(candidates)} 点, 命中 {len(results)} 个区县"
        )
        return results

    @classmethod
    def group_by_intensity(
        cls,
        estimates: list[DistrictIntensityEstimate],
    ) -> dict[int, list[str]]:
        """将区县估算结果按烈度整数分组。

        Args:
            estimates: 区县估算结果列表。

        Returns:
            dict[int, list[str]]: key 为烈度整数（向下取整），
            value 为该烈度等级的区县名列表，按烈度从高到低排序。
        """
        groups: dict[int, list[str]] = {}
        for est in estimates:
            level = int(math.floor(est.intensity))
            groups.setdefault(level, []).append(est.name)
        # 按烈度从高到低排序
        return dict(sorted(groups.items(), reverse=True))


__all__ = [
    "DistrictIntensityEstimate",
    "CnDistrictIntensityService",
]
