"""
JmaSeisIntLoc.js 加载器。

负责解析 resources/JmaSeisIntLoc.js 文件，提取：
- 「町丁目 -> 地域(sect)」映射表，供地震展示器将町丁目级震度聚合为地域级震度使用；
- 町丁目采样点扁平索引（名称/纬度/经度/地域/速度放大比 arv），
  供日本影响地域震度估算服务做 bbox 粗筛与逐点计算。

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

_cached_sect_map: dict[str, str] | None = None
# 扁平索引：每项为 (name, lat, lon, sect, arv)
_cached_flat_index: list[tuple[str, float, float, str, float]] | None = None
_cache_loading = False  # 标志位，标记加载是否正在进行


def _get_js_file_path() -> Path:
    """获取 JmaSeisIntLoc.js 文件的绝对路径。"""
    # 本文件位于 core/services/geo/，资源文件位于 resources/
    # 向上回溯：geo -> services -> core -> 插件根目录
    return Path(__file__).resolve().parents[3] / "resources" / "JmaSeisIntLoc.js"


def _parse_js_entries(js_path: Path) -> dict[str, dict]:
    """从 JmaSeisIntLoc.js 提取 {町丁目名: {location, sect, arv}} 映射。

    JS 文件格式示例::

        export const jmaSeisIntLoc = {
          石狩市花川: {
            location: [43.17, 141.32],
            sect: "石狩地方北部",
            arv: 1.44,
          },
          ...
        };

    由于 key 没有引号，无法直接 json.loads，因此使用正则逐条提取。
    location 顺序为 [纬度, 经度]（与资源文件一致）。
    """
    text = js_path.read_text(encoding="utf-8")

    # 匹配模式：
    #   町丁目名: {
    #       location: [lat, lon],
    #       sect: "地域名",
    #       arv: 1.44,
    pattern = re.compile(
        r"  (.+?):\s*\{\s*\n"
        r"    location:\s*\[([\d.\-]+),\s*([\d.\-]+)\],\s*\n"
        r'    sect:\s*"(.+?)",\s*\n'
        r"    arv:\s*([\d.\-]+),",
        re.MULTILINE,
    )

    entries: dict[str, dict] = {}
    for match in pattern.finditer(text):
        addr = match.group(1).strip()
        try:
            lat = float(match.group(2))
            lon = float(match.group(3))
        except (TypeError, ValueError):
            continue
        sect = match.group(4).strip()
        try:
            arv = float(match.group(5))
        except (TypeError, ValueError):
            arv = 1.0
        entries[addr] = {"location": (lat, lon), "sect": sect, "arv": arv}

    return entries


def _load_entries_once() -> dict[str, dict]:
    """执行一次真实文件加载（带模块级缓存与加载中保护）。

    Returns:
        {町丁目名: {location, sect, arv}}；失败时返回空字典。
    """
    global _cached_sect_map, _cached_flat_index, _cache_loading

    if _cached_flat_index is not None and _cached_sect_map is not None:
        # 已加载，直接由缓存重建（防御性分支）
        return {}

    if _cache_loading:
        # 加载过程中被再次调用时返回空兜底，
        # 加载完成后后续调用会命中缓存
        return {}

    _cache_loading = True
    try:
        js_path = _get_js_file_path()
        if not js_path.exists():
            logger.error(f"[灾害预警] JmaSeisIntLoc.js 文件不存在: {js_path}")
            _cached_sect_map = {}
            _cached_flat_index = []
            return {}

        entries = _parse_js_entries(js_path)

        sect_map: dict[str, str] = {}
        flat_index: list[tuple[str, float, float, str, float]] = []
        for addr, entry in entries.items():
            sect_map[addr] = entry["sect"]
            lat, lon = entry["location"]
            flat_index.append((addr, lat, lon, entry["sect"], entry["arv"]))

        _cached_sect_map = sect_map
        _cached_flat_index = flat_index
        return entries
    except Exception as exc:
        logger.error(f"[灾害预警] 加载 JmaSeisIntLoc.js 失败: {exc}")
        _cached_sect_map = {}
        _cached_flat_index = []
        return {}
    finally:
        _cache_loading = False


def get_sect_map() -> dict[str, str]:
    """获取「町丁目 -> 地域」映射表（惰性加载 + 模块级缓存）。

    Returns:
        dict[str, str]: key 为町丁目名（如 "御坊市薗"），
        value 为地域名（如"和歌山県北部"）。

    若文件解析失败则返回空字典并记录错误日志，不影响主流程。
    """
    if _cached_sect_map is not None:
        return _cached_sect_map
    _load_entries_once()
    return _cached_sect_map or {}


def get_flat_index() -> list[tuple[str, float, float, str, float]]:
    """获取日本町丁目采样点扁平索引（惰性加载 + 模块级缓存）。

    Returns:
        list[tuple[str, float, float, str, float]]: 每项为
        (町丁目名, 纬度, 经度, 地域名, 速度放大比 arv)。
        资源加载失败时返回空列表，不影响主流程。
    """
    if _cached_flat_index is not None:
        return _cached_flat_index
    _load_entries_once()
    return _cached_flat_index or []


def lookup_sect(addr: str) -> str | None:
    """查询单个町丁目对应的地域名。

    Args:
        addr: 町丁目名（如 "御坊市薗"）。

    Returns:
        地域名字符串，未找到时返回 None。
    """
    if not addr:
        return None
    return get_sect_map().get(addr)


def clear_cache() -> None:
    """清除模块级缓存，强制下次调用时重新加载文件。

    主要用于测试场景或热更新资源文件后手动刷新。
    """
    global _cached_sect_map, _cached_flat_index
    _cached_sect_map = None
    _cached_flat_index = None
