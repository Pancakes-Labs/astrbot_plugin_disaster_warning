"""
P/S 波走时查询服务。

基于 TravelTimes.js 中的 JMA2001 与 JB 走时模型，
根据震源深度与震中距进行双线性插值，估算 P 波与 S 波的预计走时秒数。

模型选择规则：
- 震中距 <= 2000 km：使用 jma2001 模型（近中距/区域地震）
- 震中距 > 2000 km：使用 jb 模型（远震）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from astrbot.api import logger

from .travel_time_loader import TravelTimeModel, get_model


@dataclass(slots=True)
class TravelTimeResult:
    """走时查询结果。

    Attributes:
        p_travel_sec: P 波走时（秒），查询失败时为 None。
        s_travel_sec: S 波走时（秒），查询失败时为 None。
        model_name: 实际使用的模型名。
    """

    p_travel_sec: float | None
    s_travel_sec: float | None
    model_name: str = ""


def _normalize_utc(value: datetime | None) -> datetime | None:
    """把可能缺失时区的发震时间统一归一为 UTC 时间。

    部分上游来源产出的时间可能没有时区信息，
    这里统一按 UTC 处理，避免与墙钟求差时出现时区错位。

    Args:
        value: 发震时间，可为 naive 或 aware。

    Returns:
        UTC 时间；输入为 None 时返回 None。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_s_wave_countdown(
    occurred_at: datetime | None,
    s_travel_sec: float | None,
    now: datetime | None = None,
) -> float | None:
    """计算 S 波实时剩余到达秒数（近似实时预估）。

    核心公式：剩余秒数 = 绝对走时(发震→到达) - 已流逝时间(发震→当前墙钟)。

    返回值语义：
    - 正数：S 波尚未到达，数值为剩余秒数。
    - 0 或负数：S 波已到达（或正好到达）。
    - None：缺少发震时间或 S 波走时，无法计算（调用方应静默跳过该行）。

    Args:
        occurred_at: 发震时间；缺失时无法计算倒计时，返回 None。
        s_travel_sec: S 波绝对走时秒数（发震瞬间算出，与当前时刻无关）。
        now: 当前墙钟时间；不传时内部取 datetime.now(timezone.utc)，
            便于单元测试注入固定时间。

    Returns:
        S 波剩余到达秒数；数据不足返回 None。
    """
    if s_travel_sec is None or occurred_at is None:
        return None

    occurred_utc = _normalize_utc(occurred_at)
    if occurred_utc is None:
        return None

    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    # elapsed 钳制为不小于 0，避免时钟不同步/未来时间导致的负流逝
    elapsed = max(0.0, (current - occurred_utc).total_seconds())
    # 极小的浮点误差视为已到达
    remaining = s_travel_sec - elapsed
    if math.isclose(remaining, 0.0, abs_tol=1e-6):
        return 0.0
    return remaining


class TravelTimeService:
    """P/S 波走时查询服务。"""

    # jma2001 模型的最大适用震中距
    JMA2001_MAX_DISTANCE_KM = 2000.0

    @staticmethod
    def _bilinear_interpolate(
        table: list[list[float]],
        depths: list[float],
        distances: list[float],
        depth_km: float,
        distance_km: float,
    ) -> float | None:
        """在 depths × distances 网格上对 table 做双线性插值。

        Args:
            table: 二维走时表，table[depth_i][dist_j]。
            depths: 深度轴序列。
            distances: 距离轴序列。
            depth_km: 目标震源深度。
            distance_km: 目标震中距。

        Returns:
            插值后的走时秒数，数据不足时返回 None。
        """
        if not table or not depths or not distances:
            return None

        n_depths = len(depths)
        n_dists = len(distances)
        # 校验表维度与轴长度一致
        if len(table) < n_depths:
            return None
        for row in table[:n_depths]:
            if len(row) < n_dists:
                return None

        # 将目标值钳制到网格范围内，避免越界
        d = max(depths[0], min(depths[-1], float(depth_km)))
        r = max(distances[0], min(distances[-1], float(distance_km)))

        # 定位深度方向的下界索引
        i0 = 0
        for idx in range(n_depths - 1):
            if depths[idx] <= d <= depths[idx + 1]:
                i0 = idx
                break
        else:
            # d 超出上界时取最后一段
            i0 = max(0, n_depths - 2)
        i1 = min(i0 + 1, n_depths - 1)

        # 定位距离方向的下界索引
        j0 = 0
        for idx in range(n_dists - 1):
            if distances[idx] <= r <= distances[idx + 1]:
                j0 = idx
                break
        else:
            j0 = max(0, n_dists - 2)
        j1 = min(j0 + 1, n_dists - 1)

        # 四个角点的走时值
        d0, d1 = depths[i0], depths[i1]
        r0, r1 = distances[j0], distances[j1]
        v00 = table[i0][j0]
        v01 = table[i0][j1]
        v10 = table[i1][j0]
        v11 = table[i1][j1]

        # 深度方向与距离方向的插值权重
        td = (d - d0) / (d1 - d0) if d1 != d0 else 0.0
        tr = (r - r0) / (r1 - r0) if r1 != r0 else 0.0

        # 双线性插值公式
        v0 = v00 + (v01 - v00) * tr
        v1 = v10 + (v11 - v10) * tr
        return v0 + (v1 - v0) * td

    @staticmethod
    def _select_model(distance_km: float) -> tuple[str, TravelTimeModel | None]:
        """根据震中距选择走时模型。"""
        if distance_km <= TravelTimeService.JMA2001_MAX_DISTANCE_KM:
            return "jma2001", get_model("jma2001")
        return "jb", get_model("jb")

    @classmethod
    def lookup(cls, depth_km: float, distance_km: float) -> TravelTimeResult:
        """查询 P/S 波走时。

        Args:
            depth_km: 震源深度（km）。
            distance_km: 震中距（km）。

        Returns:
            走时查询结果，数据不足时对应字段为 None。
        """
        if distance_km < 0:
            return TravelTimeResult(None, None, "")

        model_name, model = cls._select_model(float(distance_km))
        if model is None or not model.depths or not model.distances:
            return TravelTimeResult(None, None, model_name)

        p_sec = cls._bilinear_interpolate(
            model.p_times,
            model.depths,
            model.distances,
            float(depth_km),
            float(distance_km),
        )
        s_sec = cls._bilinear_interpolate(
            model.s_times,
            model.depths,
            model.distances,
            float(depth_km),
            float(distance_km),
        )

        if p_sec is None and s_sec is None:
            logger.debug(
                f"[灾害预警] 走时查询失败: depth={depth_km}km, "
                f"dist={distance_km}km, model={model_name}"
            )

        return TravelTimeResult(
            p_travel_sec=p_sec,
            s_travel_sec=s_sec,
            model_name=model_name,
        )


__all__ = [
    "TravelTimeService",
    "TravelTimeResult",
    "compute_s_wave_countdown",
]
