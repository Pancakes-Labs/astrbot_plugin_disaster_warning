"""
P2P 区域代码映射加载器。
负责读取 resources 中的 [`epsp-area.csv`](resources/epsp-area.csv) 并生成区域代码到地域名称的映射。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import logger


class P2PAreaMappingLoader:
    """P2P 区域代码映射加载器。"""

    @staticmethod
    def load(csv_path: Path) -> dict[int, str]:
        """从 CSV 文件加载 P2P 区域代码映射。"""
        # 返回区域代码到地区名称的映射，供日志格式化阶段把纯数字区域码翻译为可读地名。
        area_mapping: dict[int, str] = {}

        try:
            if csv_path.exists():
                with open(csv_path, encoding="utf-8") as f:
                    # 首行通常为表头，直接跳过。
                    next(f)
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) < 5:
                            continue
                        try:
                            area_code = int(parts[1])
                            region_name = parts[4]
                            if area_code and region_name:
                                area_mapping[area_code] = region_name
                        except (ValueError, IndexError):
                            continue

            else:
                logger.warning("[灾害预警] 未找到epsp-area.csv文件，请检查资源完整性")
        except Exception as e:
            logger.error(f"[灾害预警] 加载P2P区域代码映射失败: {e}")
            logger.error("[灾害预警] 请检查epsp-area.csv文件是否存在且格式正确")

        return area_mapping
