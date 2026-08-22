"""
烈度计算服务。
承接原 core.support.intensity_calculator 中的纯算法与描述职责，
作为本地监控与展示投影使用的主入口。
"""

from __future__ import annotations

import math

from ....utils.converters import ScaleConverter


class IntensityService:
    """地震烈度计算服务。"""

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        计算两点间的地表距离
        使用海夫赛文公式估算球面距离，返回值单位为公里
        """
        # 地球平均半径，单位：千米
        earth_radius_km = 6371.0
        # 经纬度差值转换为弧度
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        # 海夫赛文公式核心三角函数计算
        a = (
            math.sin(d_lat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(d_lon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return earth_radius_km * c

    @staticmethod
    def calculate_estimated_intensity(
        magnitude: float,
        distance_km: float,
        depth_km: float = 10.0,
        event_longitude: float = None,
    ) -> float:
        """
        估算本地烈度
        使用基于 GB/T 18306-2015 和相关地震烈度衰减研究的经验模型
        区分中国东部和西部地区，并尽量保持计算稳定性

        参数说明：
        - magnitude：震级
        - distance_km：震中距，单位为千米
        - depth_km：震源深度，默认取 10 千米
        - event_longitude：震中经度，用于判定东部或西部地区（以 105 度为界）

        返回值：
        - 预估烈度浮点值
        """
        # 1. 计算震源距
        # 同时考虑地表距离与震源深度，得到更适合衰减模型使用的空间距离。
        hypocentral_distance = math.sqrt(float(distance_km) ** 2 + float(depth_km) ** 2)

        # 限制最小有效距离，避免过近距离下公式出现异常放大。
        effective_distance = max(hypocentral_distance, 5.0)

        # 2. 选择区域参数
        # 默认按东部地区处理，经度小于 105 度时切换为西部地区参数。
        if event_longitude is not None and float(event_longitude) < 105.0:
            a_value, b_value, c_value, r0_value = 5.643, 1.538, 2.109, 25.0
        else:
            a_value, b_value, c_value, r0_value = 6.046, 1.480, 2.081, 25.0

        # 3. 执行烈度估算
        # 使用自然对数形式的衰减关系，保持与当前系数定义一致。
        magnitude_value = float(magnitude)
        intensity = (
            float(a_value)
            + float(b_value) * magnitude_value
            - float(c_value) * math.log(effective_distance + float(r0_value))
        )

        # 4. 做边界修正
        # 将结果限制在常用烈度区间 [0, 12] 内。
        return float(max(0.0, min(12.0, intensity)))

    @staticmethod
    def get_intensity_description(intensity: float) -> str:
        """
        获取烈度描述（带颜色 Emoji）
        参考中国地震烈度表，将浮点烈度映射为更易读的文本等级
        """
        if intensity < 1.0:
            return "⚪ 无感"
        if intensity < 2.0:
            return "⚪ 微有感"
        if intensity < 3.0:
            return "🔵 轻微有感"
        if intensity < 4.0:
            return "🔵 室内有感"
        if intensity < 5.0:
            return "🟢 震感明显"
        if intensity < 6.0:
            return "🟡 震感强烈"
        if intensity < 7.0:
            return "🟠 惊慌逃生"
        if intensity < 8.0:
            return "🟠 房屋损坏"
        if intensity < 9.0:
            return "🔴 严重破坏"
        if intensity < 10.0:
            return "🔴 毁灭性"
        return "🟣 极度毁灭"

    @staticmethod
    def get_shindo_description(value: float | int | None) -> str:
        """
        获取日本震度描述（带颜色 Emoji）。

        阈值与项目惯例对齐，保证描述与展示的震度阶级一致；
        emoji 色序与展示层档位保持一致。
        """
        classified = ScaleConverter.classify_measured_intensity(value)
        if classified is None:
            return "⚪ 无感"
        if classified >= 7.0:
            return "🟣 无法行动"
        if classified >= 6.0:  # 6强
            return "🔴 无法站立"
        if classified >= 5.5:  # 6弱
            return "🔴 站立困难"
        if classified >= 5.0:  # 5强
            return "🟠 行动困难"
        if classified >= 4.5:  # 5弱
            return "🟠 行动不便"
        if classified >= 3.5:  # 震度4
            return "🟡 惊惧难行"
        if classified >= 2.5:  # 震度3
            return "🟢 摇晃明显"
        if classified >= 1.5:  # 震度2
            return "🔵 室内有感"
        if classified >= 0.5:  # 震度1
            return "⚪ 轻微有感"
        return "⚪ 无感"  # 震度0


IntensityCalculator = IntensityService

__all__ = ["IntensityService", "IntensityCalculator"]
