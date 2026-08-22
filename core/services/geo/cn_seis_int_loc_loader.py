"""
CnSeisIntLoc.js 加载器。

负责解析 resources/CnSeisIntLoc.js 文件，提取中国行政区采样点库，
供地震影响区县估算使用。

数据结构说明：
- key = 区县/市名字符串（可能重名，如多个「桥东区」）
- value = [[lng, lat], ...] 坐标数组（经度在前，纬度在后）
- 网格粒度约 0.05°（~5km），覆盖中国大陆 + 港澳台

该模块采用惰性加载 + 模块级缓存策略，仅在首次调用时解析文件，
后续直接返回缓存结果，避免重复 I/O 与正则开销。
"""

from __future__ import annotations

import re
from pathlib import Path

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------

_cached_district_points: dict[str, list[tuple[float, float]]] | None = None
_cached_flat_index: list[tuple[str, float, float]] | None = None
_cache_loading = False  # 标志位，标记加载是否正在进行


def _get_js_file_path() -> Path:
    """获取 CnSeisIntLoc.js 文件的绝对路径。"""
    # 本文件位于 core/services/geo/，资源文件位于 resources/
    # 向上回溯：geo -> services -> core -> 插件根目录
    return Path(__file__).resolve().parents[3] / "resources" / "CnSeisIntLoc.js"


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


def _parse_js_district_points(js_path: Path) -> dict[str, list[tuple[float, float]]]:
    """从 CnSeisIntLoc.js 提取 {区县名: [(lng, lat), ...]} 映射。

    JS 文件格式示例::

        export const cnSeisIntLoc = {
          东城区: [
            [116.4, 39.9],
            [116.4, 39.95],
          ],
          西城区: [[116.35, 39.9]],
          ...
        };

    由于 key 没有引号，无法直接 json.loads，因此使用正则逐条提取。
    本实现采用「定位 key: [ 后用方括号配对提取完整数组」的策略，
    确保跨多行与嵌套方括号均能正确解析。
    """
    text = js_path.read_text(encoding="utf-8")

    # 截取 cnSeisIntLoc = { 到对应闭合 } 之间的内容
    obj_start = re.search(r"cnSeisIntLoc\s*=\s*\{", text)
    if not obj_start:
        return {}

    # 用花括号配对找到对象闭合位置
    brace_depth = 1
    pos = obj_start.end()
    while pos < len(text) and brace_depth > 0:
        ch = text[pos]
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
        pos += 1
    obj_text = text[obj_start.end() : pos - 1]

    # 匹配模式：行首的区县名（中文字符开头），后跟 : [
    # 区县名由中文字符、数字、字母组成，不含特殊符号
    # 使用贪婪匹配确保完整捕获区县名
    entry_pattern = re.compile(
        r"(?m)^(\s*)([\u4e00-\u9fff][\u4e00-\u9fff\w]*?)\s*:\s*\["
    )

    district_points: dict[str, list[tuple[float, float]]] = {}
    # 提取所有 [lng, lat] 对（循环外编译，避免重复 compile）
    coord_pattern = re.compile(r"\[([\d.\-]+)\s*,\s*([\d.\-]+)\]")

    for match in entry_pattern.finditer(obj_text):
        name = match.group(2).strip()
        # match.end() 指向 '[' 之后第一个字符
        bracket_start = match.end() - 1  # 回退到 '[' 的位置
        bracket_end = _find_matching_bracket(obj_text, bracket_start)
        if bracket_end < 0:
            continue

        raw = obj_text[bracket_start + 1 : bracket_end]
        points: list[tuple[float, float]] = []
        for coord_match in coord_pattern.finditer(raw):
            lng = float(coord_match.group(1))
            lat = float(coord_match.group(2))
            points.append((lng, lat))

        if points:
            # 重名区县合并采样点
            if name in district_points:
                district_points[name].extend(points)
            else:
                district_points[name] = points

    return district_points


def _build_flat_index(
    district_points: dict[str, list[tuple[float, float]]],
) -> list[tuple[str, float, float]]:
    """构建扁平索引 (name, lng, lat) 供范围筛选使用。"""
    flat: list[tuple[str, float, float]] = []
    for name, points in district_points.items():
        for lng, lat in points:
            flat.append((name, lng, lat))
    return flat


def get_district_points() -> dict[str, list[tuple[float, float]]]:
    """获取区县采样点映射表（惰性加载 + 模块级缓存）。

    Returns:
        dict[str, list[tuple[float, float]]]: key 为区县名，
        value 为 [(lng, lat), ...] 采样点列表。

    若文件解析失败则返回空字典并记录错误日志，不影响主流程。
    """
    global _cached_district_points, _cached_flat_index, _cache_loading

    if _cached_district_points is not None:
        return _cached_district_points

    if _cache_loading:
        return {}

    _cache_loading = True
    try:
        js_path = _get_js_file_path()
        if not js_path.exists():
            logger.error(f"[灾害预警] CnSeisIntLoc.js 文件不存在: {js_path}")
            _cached_district_points = {}
            _cached_flat_index = []
            return _cached_district_points

        district_points = _parse_js_district_points(js_path)
        _cached_district_points = district_points
        _cached_flat_index = _build_flat_index(district_points)
        return _cached_district_points
    except Exception as exc:
        logger.error(f"[灾害预警] 加载 CnSeisIntLoc.js 失败: {exc}")
        _cached_district_points = {}
        _cached_flat_index = []
        return _cached_district_points
    finally:
        _cache_loading = False


def get_flat_index() -> list[tuple[str, float, float]]:
    """获取扁平索引 (name, lng, lat) 供范围筛选使用。

    Returns:
        list[tuple[str, float, float]]: 每个元素为 (区县名, 经度, 纬度)。
    """
    if _cached_flat_index is None:
        get_district_points()
    return _cached_flat_index or []


def clear_cache() -> None:
    """清除模块级缓存，强制下次调用时重新加载文件。"""
    global _cached_district_points, _cached_flat_index
    _cached_district_points = None
    _cached_flat_index = None


__all__ = [
    "get_district_points",
    "get_flat_index",
    "clear_cache",
]
