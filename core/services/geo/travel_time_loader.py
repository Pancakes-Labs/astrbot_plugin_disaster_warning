"""
TravelTimes.js 加载器。

负责解析 resources/TravelTimes.js 文件，提取 JMA2001 与 JB 两种走时模型的
depths / distances / p_times / s_times 网格数据，供 P/S 波预计到达时间估算使用。

该模块采用惰性加载 + 模块级缓存策略，仅在首次调用时解析文件，
后续直接返回缓存结果，避免重复 I/O 与正则开销。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------

_cached_travel_times: dict[str, TravelTimeModel] | None = None
_cache_loading = False  # 标志位，标记加载是否正在进行


@dataclass(slots=True)
class TravelTimeModel:
    """单个走时模型数据。

    Attributes:
        depths: 震源深度序列，单位 km。
        distances: 震中距序列，单位 km。
        p_times: P 波走时二维表，p_times[depth_i][dist_j]，单位秒。
        s_times: S 波走时二维表，s_times[depth_i][dist_j]，单位秒。
    """

    depths: list[float] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)
    p_times: list[list[float]] = field(default_factory=list)
    s_times: list[list[float]] = field(default_factory=list)


def _get_js_file_path() -> Path:
    """获取 TravelTimes.js 文件的绝对路径。"""
    # 本文件位于 core/services/geo/，资源文件位于 resources/
    # 向上回溯：geo -> services -> core -> 插件根目录
    return Path(__file__).resolve().parents[3] / "resources" / "TravelTimes.js"


def _find_matching_bracket(text: str, start: int) -> int:
    """从 text[start] == '[' 开始，找到对应的闭合 ']' 位置。

    正确处理嵌套方括号，返回闭合 ']' 的索引，找不到时返回 -1。
    """
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_number_list(text: str, key: str) -> list[float]:
    """从文本中提取 ``key: [...]`` 中的数字数组。

    使用方括号配对确保跨多行正确提取完整数组内容。
    """
    # 精确匹配 key: [ 的位置（key 前面是行首或空白）
    match = re.search(r"\b" + re.escape(key) + r"\s*:\s*\[", text)
    if not match:
        return []

    # match.end() 指向 '[' 之后第一个字符
    bracket_start = match.end() - 1  # 回退到 '[' 的位置
    bracket_end = _find_matching_bracket(text, bracket_start)
    if bracket_end < 0:
        return []

    raw = text[bracket_start + 1 : bracket_end]
    numbers = re.findall(r"-?\d+(?:\.\d+)?", raw)
    return [float(n) for n in numbers]


def _extract_2d_table(text: str, key: str) -> list[list[float]]:
    """从文本中提取 ``key: [[...], [...], ...]`` 中的二维数字表。

    先用方括号配对定位外层数组范围，再在内层逐个提取方括号块。
    """
    match = re.search(r"\b" + re.escape(key) + r"\s*:\s*\[", text)
    if not match:
        return []

    bracket_start = match.end() - 1
    bracket_end = _find_matching_bracket(text, bracket_start)
    if bracket_end < 0:
        return []

    inner_text = text[bracket_start + 1 : bracket_end]
    table: list[list[float]] = []
    # 逐个匹配内层方括号块（不含嵌套）
    for inner_match in re.finditer(r"\[([^\[\]]*)\]", inner_text):
        raw = inner_match.group(1)
        numbers = re.findall(r"-?\d+(?:\.\d+)?", raw)
        if numbers:
            table.append([float(n) for n in numbers])
    return table


def _parse_model_block(text: str, model_name: str) -> TravelTimeModel:
    """解析单个走时模型块。

    通过定位 ``model_name: {`` 到匹配的 ``}`` 的范围，
    在该范围内提取 depths / distances / p_times / s_times。
    """
    # 定位模型块起始
    block_start = re.search(re.escape(model_name) + r"\s*:\s*\{", text)
    if not block_start:
        logger.warning(f"[灾害预警] TravelTimes.js 未找到模型 {model_name}")
        return TravelTimeModel()

    # 从模型块起始位置向后扫描花括号深度，深度归零时即为模型块结束
    depth = 1
    pos = block_start.end()
    while pos < len(text) and depth > 0:
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        pos += 1

    block_text = text[block_start.end() : pos - 1]

    model = TravelTimeModel()
    model.depths = _extract_number_list(block_text, "depths")
    model.distances = _extract_number_list(block_text, "distances")
    model.p_times = _extract_2d_table(block_text, "p_times")
    model.s_times = _extract_2d_table(block_text, "s_times")
    return model


def _parse_travel_times(js_path: Path) -> dict[str, TravelTimeModel]:
    """从 TravelTimes.js 提取全部走时模型。"""
    text = js_path.read_text(encoding="utf-8")

    models: dict[str, TravelTimeModel] = {}
    for name in ("jma2001", "jb"):
        model = _parse_model_block(text, name)
        models[name] = model
    return models


def get_travel_times() -> dict[str, TravelTimeModel]:
    """获取走时模型表（惰性加载 + 模块级缓存）。

    Returns:
        dict[str, TravelTimeModel]: key 为模型名（"jma2001" / "jb"），
        value 为对应走时模型数据。

    若文件解析失败则返回空字典并记录错误日志，不影响主流程。
    """
    global _cached_travel_times, _cache_loading

    if _cached_travel_times is not None:
        return _cached_travel_times

    if _cache_loading:
        return {}

    _cache_loading = True
    try:
        js_path = _get_js_file_path()
        if not js_path.exists():
            logger.error(f"[灾害预警] TravelTimes.js 文件不存在: {js_path}")
            _cached_travel_times = {}
            return _cached_travel_times

        models = _parse_travel_times(js_path)
        _cached_travel_times = models
        return _cached_travel_times
    except Exception as exc:
        logger.error(f"[灾害预警] 加载 TravelTimes.js 失败: {exc}")
        _cached_travel_times = {}
        return _cached_travel_times
    finally:
        _cache_loading = False


def get_model(name: str) -> TravelTimeModel | None:
    """查询单个走时模型。

    Args:
        name: 模型名（"jma2001" 或 "jb"）。

    Returns:
        走时模型对象，未找到时返回 None。
    """
    return get_travel_times().get(name)


def clear_cache() -> None:
    """清除模块级缓存，强制下次调用时重新加载文件。"""
    global _cached_travel_times
    _cached_travel_times = None


__all__ = [
    "TravelTimeModel",
    "get_travel_times",
    "get_model",
    "clear_cache",
]
