"""
日本影响地域震度估算服务。

基于 JmaSeisIntLoc.js 采样点库（町丁目 -> 地域 sect + 速度放大比 arv），
结合震中位置、震级与深度，用紧急地震速报距离衰减式估算各地域的预估
计测震度，并按震度阶级分组输出。

性能与聚合策略：
- 先用 bbox（经纬度矩形框）粗筛采样点，避免全量扫描
- 每个地域（sect）取 max 计测震度
- 场地放大直接使用采样点自带 arv（速度放大比），物理上更精确
- 资源加载失败或无命中时返回空列表，不影响主链路
"""

from __future__ import annotations

from dataclasses import dataclass

from astrbot.api import logger

from ....utils.converters import ScaleConverter
from .intensity_service import IntensityService
from .jma_seis_int_loc_loader import get_flat_index
from .jma_shindo_service import calculate_jma_shindo


@dataclass(slots=True)
class SectShindoEstimate:
    """单个地域的震度估算结果。

    Attributes:
        sect: 地域名（如 "石狩地方北部"）。
        shindo: 该地域代表计测震度（取受限点集内最大值）。
        distance_km: 该地域距震中最近有效采样点的距离（km）。
    """

    sect: str
    shindo: float
    distance_km: float


class JpSectIntensityService:
    """日本影响地域震度估算服务。"""

    # 默认最小展示震度阈值：低于此值（计测震度 < 0.5，即震度 0/无感）不输出
    DEFAULT_MIN_SHINDO = 0.5

    # bbox 粗筛的经纬度扩展量（度），1 度约 111km。
    # 用二分法在 JMA 距离衰减式（depth=10km, arv=1.0）上连续求解
    # 计测震度 = 0.5（震度1 有感起点）的震中距，取「震中距/111 * 1.5」，
    # 即有感距离加 50% 余量后确定搜索半径。
    # M9.5 余量后约 20.7°，已超过日本列岛南北跨度（约 27° 的一半），
    # 故上限截断至 30°，足以覆盖本土与近海采样点，无需中国方案的大范围。
    BBOX_MARGIN_MAX = 30.0

    # 震级 -> bbox 扩展量映射表（二分法反推 + 50% 余量）
    # M3.0→0.6°, M3.5→1.1°, M4.0→1.7°, M4.5→2.5°, M5.0→3.5°, M5.5→4.7°,
    # M6.0→6.0°, M6.5→7.4°, M7.0→8.9°, M7.5→10.6°, M8.0→12.4°, M8.5→14.6°,
    # M9.0→17.3°, M9.5→20.7°(截断至 30°)
    _BBOX_MARGIN_TABLE: list[tuple[float, float]] = [
        (3.0, 0.6),
        (3.5, 1.1),
        (4.0, 1.7),
        (4.5, 2.5),
        (5.0, 3.5),
        (5.5, 4.7),
        (6.0, 6.0),
        (6.5, 7.4),
        (7.0, 8.9),
        (7.5, 10.6),
        (8.0, 12.4),
        (8.5, 14.6),
        (9.0, 17.3),
        (9.5, 20.7),
    ]

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
        points: list[tuple[str, float, float, str, float]],
        center_lng: float,
        center_lat: float,
        margin: float,
    ) -> list[tuple[str, float, float, str, float]]:
        """用经纬度矩形框粗筛采样点。

        points 元素为 (name, lat, lon, sect, arv)。
        """
        min_lng = center_lng - margin
        max_lng = center_lng + margin
        min_lat = center_lat - margin
        max_lat = center_lat + margin
        return [
            (name, lat, lon, sect, arv)
            for name, lat, lon, sect, arv in points
            if min_lng <= lon <= max_lng and min_lat <= lat <= max_lat
        ]

    @classmethod
    def estimate_affected_sects(
        cls,
        latitude: float,
        longitude: float,
        magnitude: float,
        depth: float,
        min_shindo: float = DEFAULT_MIN_SHINDO,
    ) -> list[SectShindoEstimate]:
        """估算受影响地域列表。

        Args:
            latitude: 震中纬度。
            longitude: 震中经度。
            magnitude: 震级（MJMA 近似）。
            depth: 震源深度（km）。
            min_shindo: 最小展示计测震度阈值，低于此值的地域不返回。

        Returns:
            按计测震度从高到低排序的地域估算结果列表。
            资源加载失败或震级过小时返回空列表，不影响主流程。
        """
        # 震级过小（< 3）时距离衰减式无意义，直接返回空
        try:
            mag = float(magnitude)
        except (TypeError, ValueError):
            return []
        if mag < 3.0:
            return []

        flat = get_flat_index()
        if not flat:
            return []

        # 根据震级动态计算 bbox 范围，大震覆盖更广
        margin = cls._calc_bbox_margin(mag)
        candidates = cls._bbox_filter(flat, longitude, latitude, margin)
        if not candidates:
            return []

        # 先按地域收集采样点，并预计算距离，便于后续取 max
        # value: list[(dist_km, shindo)]，按距离升序
        sect_points: dict[str, list[tuple[float, float]]] = {}
        for _name, lat, lon, sect, arv in candidates:
            dist = IntensityService.calculate_distance(latitude, longitude, lat, lon)
            shindo = calculate_jma_shindo(
                magnitude=mag,
                depth_km=depth,
                distance_km=dist,
                arv=arv,
            )
            if shindo is None:
                continue
            sect_points.setdefault(sect, []).append((dist, shindo))
        for sect in sect_points:
            sect_points[sect].sort(key=lambda item: item[0])

        results: list[SectShindoEstimate] = []
        for sect, pts in sect_points.items():
            max_shindo = max(s for _d, s in pts)
            min_distance = min(d for d, _s in pts)
            if max_shindo >= min_shindo:
                results.append(
                    SectShindoEstimate(
                        sect=sect,
                        shindo=max_shindo,
                        distance_km=min_distance,
                    )
                )

        # 按计测震度从高到低排序
        results.sort(key=lambda x: x.shindo, reverse=True)

        logger.debug(
            f"[灾害预警] 日本影响地域估算: 震中 ({latitude},{longitude}) M {mag} "
            f"深度 {depth}km, 粗筛 {len(candidates)} 点, 命中 {len(results)} 个地域"
        )
        return results

    @classmethod
    def group_by_shindo(
        cls,
        estimates: list[SectShindoEstimate],
    ) -> dict[float, list[str]]:
        """将地域估算结果按震度阶级分组。

        Args:
            estimates: 地域估算结果列表。

        Returns:
            dict[float, list[str]]: key 为规范震度阶级值
            （4.5=5弱, 5.0=5强, 5.5=6弱, 6.0=6强, 7.0=7），
            value 为该阶级的地域名列表，按阶级从高到低排序。
        """
        groups: dict[float, list[str]] = {}
        for est in estimates:
            level = ScaleConverter.classify_measured_intensity(est.shindo)
            if level is None:
                continue
            groups.setdefault(level, []).append(est.sect)
        # 按阶级从高到低排序
        return dict(sorted(groups.items(), reverse=True))


__all__ = [
    "SectShindoEstimate",
    "JpSectIntensityService",
]
