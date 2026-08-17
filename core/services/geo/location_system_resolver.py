"""
本地监控强度体系解析器。

根据本地监控坐标自动判定应使用哪种烈度/震度计算公式体系：
- jma：日本 JMA 计测震度（距离衰减式）
- cenc：中国 CENC 烈度（GB/T 18306-2015 衰减式，分东西部）

判定采用三级策略：
1. 快速判断：F-E 区划矩阵（RegionService.get_fe_name，1° 分辨率）
   地名以「日本」开头 → jma；以「中国」开头 → cenc；
2. 边界精修：F-E 未命中或归属模糊（如琉球群岛/未定义）时，
   用 JmaSeisIntLoc / CnSeisIntLoc 采样点库做最近归属判定；
3. 手动覆盖：配置 intensity_system 显式指定 auto/cenc/jma 时优先。

设计目标：
- 冲绳/琉球用户能被正确识别为 jma（F-E 把琉球标为「琉球群岛」而非「日本」）
- 远海坐标（如日本海中央）不会被误判为 jma
- 中国东西部用户都能归为 cenc（东西部由经度在烈度公式内部切换）
"""

from __future__ import annotations

from astrbot.api import logger

from .cn_seis_int_loc_loader import get_flat_index as get_cn_flat_index
from .intensity_service import IntensityService
from .jma_seis_int_loc_loader import get_flat_index as get_jp_flat_index
from .region_service import region_service

# 强度体系常量（值同时兼容中英文，中文用于配置页下拉展示）
SYSTEM_AUTO = "auto"
SYSTEM_CENC = "cenc"
SYSTEM_JMA = "jma"
SYSTEM_AUTO_CN = "自动判定"
SYSTEM_CENC_CN = "中国烈度"
SYSTEM_JMA_CN = "日本震度"

# 规范化映射：任意别名 -> 规范英文值
_NORMALIZE_MAP = {
    SYSTEM_AUTO: SYSTEM_AUTO,
    SYSTEM_CENC: SYSTEM_CENC,
    SYSTEM_JMA: SYSTEM_JMA,
    SYSTEM_AUTO_CN: SYSTEM_AUTO,
    SYSTEM_CENC_CN: SYSTEM_CENC,
    SYSTEM_JMA_CN: SYSTEM_JMA,
}
_VALID_SYSTEMS = tuple(_NORMALIZE_MAP.keys())

# 采样点最近归属阈值（km）：
# - 日本町丁目采样点密集（4372 个），50km 内必有采样点 → 可判定在日本
# - 中国区县采样点密度较稀，放宽到 150km
JP_SAMPLE_THRESHOLD_KM = 50.0
CN_SAMPLE_THRESHOLD_KM = 150.0

# F-E 地名中的海域特征词：命中时跳过 F-E 快速判断，避免把
# 「日本海」「中国东部东岸远海」等海域地名误判为陆地归属，
# 统一交给采样点兜底判定（远海坐标无采样点 → 正确回退）。
_SEA_KEYWORDS = ("海", "远海", "海域")

# 二级精修的 bbox 搜索半径（度）：以坐标为中心做矩形粗筛，
# 在半径内全量精确计算最近采样点距离，避免网格采样漏判。
_JP_SEARCH_BBOX_DEG = 3.0  # 日本采样点密集，3° 足够覆盖 50km 阈值
_CN_SEARCH_BBOX_DEG = 6.0  # 中国采样点较稀，放宽到 6°

# 模块级缓存：最近采样点距离（km）
_cached_jp_min_dist: float | None = None
_cached_cn_min_dist: float | None = None
_cached_probe_lat: float | None = None
_cached_probe_lng: float | None = None


def _bbox_min_distance(
    lat: float,
    lng: float,
    flat: list,
    bbox_deg: float,
    threshold_km: float,
) -> float:
    """在 bbox 范围内精确求坐标到采样点库的最近距离（km）。

    先用矩形框粗筛候选采样点，再逐点精确计算球面距离取最小。
    相比全量扫描 10 万点，bbox 粗筛后候选点数量级大幅下降。

    Args:
        lat: 坐标纬度。
        lng: 坐标经度。
        flat: 采样点扁平索引列表（元素含 lat/lon，见各 loader）。
        bbox_deg: bbox 半边长（度）。
        threshold_km: 提前返回阈值；达到即认为「足够近」。

    Returns:
        最近距离（km）。bbox 内无采样点时返回极大值。
    """
    if not flat:
        return float("inf")

    min_lng, max_lng = lng - bbox_deg, lng + bbox_deg
    min_lat, max_lat = lat - bbox_deg, lat + bbox_deg

    best = float("inf")
    for item in flat:
        # flat 元素结构：日本 (name, lat, lon, sect, arv) / 中国 (name, lng, lat)
        if len(item) == 5:
            plat, plon = item[1], item[2]
        elif len(item) == 3:
            plon, plat = item[1], item[2]
        else:
            continue
        if not (min_lng <= plon <= max_lng and min_lat <= plat <= max_lat):
            continue
        d = IntensityService.calculate_distance(lat, lng, plat, plon)
        if d < best:
            best = d
            if best <= threshold_km:
                return best
    return best


def _clear_probe_cache() -> None:
    """清除模块级粗采样缓存（测试或坐标变更时调用）。"""
    global _cached_jp_min_dist, _cached_cn_min_dist
    global _cached_probe_lat, _cached_probe_lng
    _cached_jp_min_dist = None
    _cached_cn_min_dist = None
    _cached_probe_lat = None
    _cached_probe_lng = None


def _resolve_by_samples(lat: float, lng: float) -> str | None:
    """二级精修：采样点最近归属判定。

    Returns:
        "jma" / "cenc" / None（均不归属）。
    """
    global _cached_jp_min_dist, _cached_cn_min_dist
    global _cached_probe_lat, _cached_probe_lng

    # 坐标变化时清除缓存。
    # 注意：不能用「±0.01° 视为同一坐标」的容差缓存——约 1km 的坐标变化
    # 可能跨越 50km/150km 的分类阈值，导致 SYSTEM_JMA/SYSTEM_CENC/None 误判，
    # 因此使用精确坐标作为缓存键。
    if _cached_probe_lat is not None and (
        lat != _cached_probe_lat or lng != _cached_probe_lng
    ):
        _clear_probe_cache()

    if _cached_jp_min_dist is None:
        jp_flat = get_jp_flat_index()
        _cached_jp_min_dist = _bbox_min_distance(
            lat, lng, jp_flat, _JP_SEARCH_BBOX_DEG, JP_SAMPLE_THRESHOLD_KM
        )
        _cached_probe_lat, _cached_probe_lng = lat, lng

    if (
        _cached_jp_min_dist is not None
        and _cached_jp_min_dist <= JP_SAMPLE_THRESHOLD_KM
    ):
        return SYSTEM_JMA

    if _cached_cn_min_dist is None:
        cn_flat = get_cn_flat_index()
        _cached_cn_min_dist = _bbox_min_distance(
            lat, lng, cn_flat, _CN_SEARCH_BBOX_DEG, CN_SAMPLE_THRESHOLD_KM
        )

    if (
        _cached_cn_min_dist is not None
        and _cached_cn_min_dist <= CN_SAMPLE_THRESHOLD_KM
    ):
        return SYSTEM_CENC

    logger.debug(
        f"[灾害预警] 本地坐标 ({lat},{lng}) 未命中任何采样点库，"
        f"最近日本采样点 {_cached_jp_min_dist:.0f}km，"
        f"最近中国采样点 {_cached_cn_min_dist:.0f}km"
    )
    return None


def resolve_intensity_system(
    latitude: float,
    longitude: float,
    intensity_system: str = SYSTEM_AUTO,
) -> str | None:
    """解析本地监控坐标应使用的强度体系。

    Args:
        latitude: 本地纬度。
        longitude: 本地经度。
        intensity_system: 手动指定体系（auto/cenc/jma），缺省 auto。

    Returns:
        "cenc" / "jma" / None（无法判定或未启用本地监控）。
    """
    raw = (intensity_system or SYSTEM_AUTO).strip()
    # 兼容中英文：中文别名与英文小写别名
    system = _NORMALIZE_MAP.get(raw) or _NORMALIZE_MAP.get(raw.lower()) or SYSTEM_AUTO
    if system not in _VALID_SYSTEMS:
        logger.warning(
            f"[灾害预警] 非法强度体系配置 {intensity_system!r}，已按 auto 处理"
        )
        system = SYSTEM_AUTO

    # 1. 手动覆盖优先
    if system == SYSTEM_CENC:
        return SYSTEM_CENC
    if system == SYSTEM_JMA:
        return SYSTEM_JMA

    # 2. F-E 区划快速判断
    try:
        fe_name = region_service.get_fe_name(latitude, longitude, add_suffix=False)
    except Exception as exc:
        logger.debug(f"[灾害预警] F-E 区划查询失败: {exc}")
        fe_name = None

    if fe_name:
        # 海域地名跳过快速判断，交给采样点兜底判定，避免把海域误判为陆地归属。
        if not any(keyword in fe_name for keyword in _SEA_KEYWORDS):
            if fe_name.startswith("日本"):
                return SYSTEM_JMA
            if fe_name.startswith("中国"):
                return SYSTEM_CENC

    # 3. 采样点库兜底（F-E 未命中或归属模糊时）
    return _resolve_by_samples(latitude, longitude)


__all__ = [
    "SYSTEM_AUTO",
    "SYSTEM_CENC",
    "SYSTEM_JMA",
    "resolve_intensity_system",
]
