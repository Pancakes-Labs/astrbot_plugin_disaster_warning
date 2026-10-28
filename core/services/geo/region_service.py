"""
区域资源服务。
承接原区域资源读取逻辑，集中提供带缓存的区划数据加载、区域翻译与统计能力。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from astrbot.api import logger


class RegionService:
    """F-E 地震区划资源服务。

    负责加载全球区划网格资源，并提供经纬度到中文区域名称的映射能力。
    """

    def __init__(self):
        # 缓存区划网格矩阵数组
        self._fe_numbers = None
        # 缓存区划编码对应的中文地名名称列表
        self._fe_names = None
        # 预设的区划 JSON 数据文件位置
        self._data_file = (
            Path(__file__).resolve().parents[3] / "resources" / "fe_regions_data.json"
        )

    async def load_data_async(self) -> None:
        """异步预加载 F-E 区划数据。"""
        # 使用线程池执行同步文件加载，避免阻塞 asyncio 异步主事件循环
        await asyncio.to_thread(self._load_data)

    def _load_data(self) -> None:
        # 若缓存已就绪，则直接复用，避免重复读取资源文件。
        if self._fe_numbers is not None and self._fe_names is not None:
            return

        try:
            with self._data_file.open(encoding="utf-8") as file:
                data = json.load(file)
                self._fe_numbers = data["fe_numbers"]
                self._fe_names = data["fe_names"]
        except FileNotFoundError:
            logger.warning(
                f"[灾害预警] FE 区划数据文件不存在，回退默认空映射: {self._data_file}"
            )
            # 文件不存在时，初始化大小为 180x360 且值全部为 729（未定义编码）的矩阵，防止运行时异常
            self._fe_numbers = [[729] * 360 for _ in range(180)]
            self._fe_names = ["未定义"] * 729
        except Exception as exc:
            logger.warning(f"[灾害预警] FE 区划数据加载失败，回退默认空映射: {exc}")
            self._fe_numbers = [[729] * 360 for _ in range(180)]
            self._fe_names = ["未定义"] * 729

    def get_fe_name(
        self, lat: float, lng: float, add_suffix: bool = True
    ) -> str | None:
        """根据经纬度获取 F-E 区域中文名称。

        可按需补上“附近”后缀，以便直接用于展示文案。
        """
        # 确保数据已同步加载
        self._load_data()

        if self._fe_numbers is None or self._fe_names is None:
            return None

        try:
            # 经纬度映射到 180x360 矩阵网格索引
            lat_i = min(max(int(lat + 90), 0), 179)
            lng_i = min(max(int(lng + 180), 0), 359)
            region_number = self._fe_numbers[lat_i][lng_i]

            # 区划编号 1-based，获取列表对应的中文地名
            if 1 <= region_number <= len(self._fe_names):
                region_name = self._fe_names[region_number - 1]
                if region_name == "未定义":
                    return None
                # 根据配置决定是否向地名后附加“附近”以修饰模糊定位描述
                if add_suffix and not region_name.endswith("附近"):
                    region_name += "附近"
                return region_name
            return None
        except (IndexError, ValueError, TypeError, OverflowError):
            return None

    def translate_place_name(
        self,
        original_name: str,
        lat: float,
        lng: float,
        fallback_to_original: bool = True,
    ) -> str:
        """优先使用 F-E 区域翻译，失败时回退到原始地名。"""
        chinese_name = self.get_fe_name(lat, lng)
        if chinese_name:
            return chinese_name
        return original_name if fallback_to_original else ""

    def is_data_loaded(self) -> bool:
        """检查数据是否已加载。"""
        return self._fe_numbers is not None and self._fe_names is not None

    def get_region_stats(self) -> dict:
        """获取区域数据统计信息。

        可用于调试或管理端查看当前资源是否正确加载。
        """
        self._load_data()

        if self._fe_numbers is None or self._fe_names is None:
            return {
                "loaded": False,
                "total_names": 0,
                "grid_rows": 0,
                "grid_cols": 0,
                "unique_regions": 0,
            }

        flat_numbers = [num for row in self._fe_numbers for num in row]
        unique_regions = len(set(flat_numbers))
        return {
            "loaded": True,
            "total_names": len(self._fe_names),
            "grid_rows": len(self._fe_numbers),
            "grid_cols": len(self._fe_numbers[0]) if self._fe_numbers else 0,
            "unique_regions": unique_regions,
            "grid_precision": "1° × 1°",
            "coverage": "全球 (-90°~90°, -180°~180°)",
        }


region_service = RegionService()
