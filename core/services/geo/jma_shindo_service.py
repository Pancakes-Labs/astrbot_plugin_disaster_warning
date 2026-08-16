"""
JMA 计测震度（距离衰减式）公共计算服务。

作为地震动预测与日本影响地域震度估算共用的主入口。

参考《緊急地震速報で使われる距離減衰式による震度計算》：
    Mw  = MJMA - 0.171（宇津 1982）
    L   = 10^(0.5*Mw - 1.85)（宇津 1977，断层长 km）
    X   = max(√(D²+d²) - L/2, 3)（最短距离，km）
    PGV600 = 10^(0.58*Mw + 0.0038*D - 1.29
                - log10(X + 0.0028*10^(0.5*Mw)) - 0.002*X)（司・翠川 1999）
    PGV400 = PGV600 * 1.31（松岡・翠川 1994）
    PGVs   = PGV400 * ARV（ARV 由 Vs30 求得，或采样点直接提供）
    I      = 2.68 + 1.72 * log10(PGVs)（计测震度）
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# Vs30 默认值（m/s）：工程基岩平均 S 波速度，日本 J-SHIS 表层面板常用 400/600
DEFAULT_VS30_MS = 600.0

# JMA 距离衰减式常量（参考《緊急地震速報で使われる距離減衰式による震度計算》）
# Mw = MJMA - 0.171（宇津 1982）
_MW_DELTA = 0.171
# log10(L) = 0.5 * Mw - 1.85（宇津 1977，断层长 km；半径取 L/2）
_FAULT_LEN_A = 0.5
_FAULT_LEN_B = -1.85
# 最短距离下限（km）
_MIN_DISTANCE_KM = 3.0
# PGV600 距离衰减式（司・翠川 1999）
_PGV600_C0 = 0.58
_PGV600_C1 = 0.0038
_PGV600_C2 = -1.29
_PGV600_C3 = 0.0028
_PGV600_C4 = -0.002
# Vs600 → Vs400 工程基岩换算増幅率（松岡・翠川 1994）
_VS600_TO_VS400_AMPLIF = 1.31
# 计测震度换算：I = 2.68 + 1.72 * log10(PGVs)
_JMA_I_A = 2.68
_JMA_I_B = 1.72

# ARV 速度放大比（Vs30 经验式）系数：ARV = 10^(a - b * log10(Vs30))
# ARV = 2.367 - 0.852 * log10(VS30)（对数域），
# 这里转为线性放大率：ARV = 10^(2.367 - 0.852 * log10(Vs30))，
# 使 Vs30=600m/s 时 ARV≈1.0（工程基岩等效），Vs30 越小放大越大。
_ARV_A = 2.367
_ARV_B = 0.852


def calculate_vs30_arv(vs30: float) -> float:
    """Vs30（m/s）-> ARV（速度放大比，无量纲）。

    采用经验式（对数域）并转为线性放大率：
        ARV = 10^(2.367 - 0.852 * log10(Vs30))
    - Vs30=600 m/s（工程基岩等效）→ ARV≈1.0
    - Vs30=400 m/s → ≈1.41
    - Vs30=200 m/s → ≈2.55

    Args:
        vs30: 地表 30 米平均剪切波速（m/s），应 > 0。

    Returns:
        速度放大比（无量纲，> 0）。
    """
    vs30 = float(vs30)
    if vs30 <= 0:
        vs30 = DEFAULT_VS30_MS
    return 10.0 ** (_ARV_A - _ARV_B * math.log10(vs30))


def calculate_jma_shindo(
    *,
    magnitude: float,
    depth_km: float,
    distance_km: float,
    vs30: float = DEFAULT_VS30_MS,
    arv: float | None = None,
) -> float | None:
    """基于紧急地震速报距离衰减式计算预测点 JMA 计测震度。

    支持两种场地放大输入：
    - 显式传入 arv（速度放大比，无量纲，如 JmaSeisIntLoc.js 采样点自带）；
    - 传入 vs30（m/s），由经验式反推 ARV（默认 600）。

    Args:
        magnitude: 震级（MJMA 近似）。
        depth_km: 震源深度（km）。
        distance_km: 预测点与震中地表距离（km）。
        vs30: 预测点 Vs30（m/s），默认 600；仅在 arv 未给出时使用。
        arv: 速度放大比（无量纲）；给出时优先于 vs30 使用。

    Returns:
        计测震度（连续值）；输入非法（震级过小、距离非有限等）时返回 None。
    """
    try:
        mag = float(magnitude)
        depth = float(depth_km)
        dist = float(distance_km)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(mag) and math.isfinite(depth) and math.isfinite(dist)):
        return None
    # 震级过小（< 3）时距离衰减式无意义（断层长度/能量模型不适用）
    if mag < 3.0:
        return None
    if depth < 0.0 or dist < 0.0:
        return None

    mw = mag - _MW_DELTA

    # 断层长（km）与半径；宇津 1977 相似律
    fault_len = 10.0 ** (_FAULT_LEN_A * mw + _FAULT_LEN_B)
    fault_radius = fault_len * 0.5

    # 震源距（√(D²+d²)），再扣除断层半径得到最短距离，下限 3km
    hypocentral = math.sqrt(depth * depth + dist * dist)
    shortest = max(hypocentral - fault_radius, _MIN_DISTANCE_KM)

    # 工程基岩（Vs=600m/s）最大速度 PGV600（司・翠川 1999）
    log10_pgv600 = (
        _PGV600_C0 * mw
        + _PGV600_C1 * depth
        + _PGV600_C2
        - math.log10(shortest + _PGV600_C3 * 10.0 ** (0.5 * mw))
        + _PGV600_C4 * shortest
    )
    pgv600 = 10.0**log10_pgv600

    # Vs600 → Vs400 工程基岩换算，再乘 ARV 得到地表最大速度
    if arv is not None:
        try:
            arv_value = float(arv)
        except (TypeError, ValueError):
            arv_value = 0.0
        if arv_value <= 0:
            arv_value = calculate_vs30_arv(vs30)
    else:
        arv_value = calculate_vs30_arv(vs30)
    pgv_surface = pgv600 * _VS600_TO_VS400_AMPLIF * arv_value

    # 计测震度：I = 2.68 + 1.72 * log10(PGVs)
    if pgv_surface <= 0:
        return None
    shindo = _JMA_I_A + _JMA_I_B * math.log10(pgv_surface)
    # 物理下限：计测震度不应无限低，但可低于 0；这里仅做数值保护
    return float(shindo)


__all__ = [
    "DEFAULT_VS30_MS",
    "calculate_vs30_arv",
    "calculate_jma_shindo",
]
