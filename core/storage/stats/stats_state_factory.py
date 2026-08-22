"""
统计状态工厂。
统一构建 StatisticsManager 使用的默认统计数据结构，
避免初始化与重置逻辑重复维护。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class StatsStateFactory:
    """统计状态工厂。

    负责定义统计模块运行时状态的完整基线结构，
    让初始化、重置、历史恢复都围绕同一份默认骨架展开。
    """

    @staticmethod
    def build_initial_stats(now: datetime | None = None) -> dict[str, Any]:
        """构建默认统计状态。

        返回值中不仅包含计数器，还包含近期事件缓存、时间序列桶和会话统计结构，
        供统计模块整个生命周期直接复用。
        """
        current_time = (now or datetime.now(timezone.utc)).isoformat()
        # 这里定义的是统计管理器的完整内存基线结构，重置与首次启动都会复用这一份默认骨架。
        return {
            # 全局计数与基础时间字段。
            "total_received": 0,
            "total_events": 0,
            "start_time": current_time,
            "last_updated": current_time,
            "by_type": defaultdict(int),
            "by_source": defaultdict(int),
            # 地震统计块：维护震级分布、地区分布与最大地震摘要。
            "earthquake_stats": {
                "by_magnitude": defaultdict(int),
                "by_region": defaultdict(int),
                "max_magnitude": None,
            },
            # 气象统计块：按预警颜色、类型和地区维护聚合结果。
            "weather_stats": {
                "by_level": defaultdict(int),
                "by_type": defaultdict(int),
                "by_region": defaultdict(int),
            },
            # 台风统计块：按强度等级和台风 ID 维护聚合结果。
            # by_level 统计每次推送时各强度等级出现的频次（含同一台风多次推送）。
            # by_max_level 统计每个台风个体历史最高强度等级的去重分布。
            # max_wind_typhoons 记录每个台风名称出现过的最大风速（及对应气压），用于风王榜。
            # min_pressure_typhoons 记录每个台风名称出现过的最低中心气压，用于气压榜。
            "typhoon_stats": {
                "by_level": defaultdict(int),
                "by_max_level": defaultdict(int),
                "max_wind_typhoons": {},
                "min_pressure_typhoons": {},
            },
            # S-Net 观测峰值统计：不计入 total_events / by_type 事件流。
            "snet_stats": {
                "station_count": 0,
                "stations_with_peak": 0,
                "global_max": None,
                "top_peaks": [],
                "recent_peak_updates": [],
                "last_observation_at": None,
            },
            # 近期事件摘要与去重辅助字段。
            "recent_pushes": [],
            "major_events": [],
            "recent_event_ids": [],
            "recent_source_event_ids": [],
            # 时间序列统计分别服务于趋势图与热力图。
            "hourly_counts": defaultdict(int),
            "daily_counts": defaultdict(int),
            # 会话统计块：记录各会话接收、推送与最近推送时间。
            "session_stats": {
                "by_session": defaultdict(
                    lambda: {
                        "received": 0,
                        "pushed": 0,
                        "last_push_time": None,
                    }
                ),
                "top_sessions": [],
            },
        }
