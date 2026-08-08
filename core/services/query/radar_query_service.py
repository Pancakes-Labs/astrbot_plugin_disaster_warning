"""
气象雷达查询服务。

统一承接命令侧（/雷达 /雷达动图 /雷达列表）的查询编排：
- 站点关键词匹配（城市/省份/区域拼图/拼音）
- 抓取页面 data-img 序列，下载最新一帧或最近 N 帧
- Pillow 合成循环 GIF（动图）
- 格式化站点列表文本

数据源：中央气象台雷达页面 https://www.nmc.cn/publish/radar/chinaall.html
站点表：resources/radar_stations.json（8 区域拼图 + 213 单站雷达）
省份匹配统一按站点表的 province 字段判定，避免依赖路径前缀导致
山西/陕西（NMC 拼音同为 shan-xi）互相串号。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
from datetime import datetime
from typing import Any

from PIL import Image

from astrbot.api import logger

from ...network.http.nmc_radar_client import NmcRadarClient

# 本文件位于 core/services/query/ 下，向上 4 级到插件根目录
_PLUGIN_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_RESOURCE_PATH = os.path.join(_PLUGIN_ROOT, "resources", "radar_stations.json")

# 动图默认帧数与并发
DEFAULT_GIF_FRAMES = 20
MAX_GIF_FRAMES = 512
MIN_GIF_FRAMES = 3

# 省份名（仅用于省份拼音目录匹配提示；省份精确匹配走站点表 province 字段）
_PROVINCE_DIRS = {
    "北京": "bei-jing",
    "天津": "tian-jin",
    "河北": "he-bei",
    "山西": "shan-xi",
    "内蒙古": "nei-meng",
    "辽宁": "liao-ning",
    "吉林": "ji-lin",
    "黑龙江": "hei-long-jiang",
    "上海": "shang-hai",
    "江苏": "jiang-su",
    "浙江": "zhe-jiang",
    "安徽": "an-hui",
    "福建": "fu-jian",
    "江西": "jiang-xi",
    "山东": "shan-dong",
    "河南": "he-nan",
    "湖北": "hu-bei",
    "湖南": "hu-nan",
    "广东": "guang-dong",
    "广西": "guang-xi",
    "海南": "hai-nan",
    "重庆": "chong-qing",
    "四川": "si-chuan",
    "贵州": "gui-zhou",
    "云南": "yun-nan",
    "西藏": "xi-cang",
    "陕西": "shan-xi",
    "甘肃": "gan-su",
    "青海": "qing-hai",
    "宁夏": "ning-xia",
    "新疆": "xin-jiang",
}


def _load_stations() -> dict[str, Any]:
    """加载雷达站点资源表；失败时返回空结构。"""
    try:
        with open(_RESOURCE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[灾害预警] 雷达站点表加载失败: {e}")
        return {"version": 1, "puzzles": {}, "stations": {}}


_STATIONS_CACHE: dict[str, Any] | None = None


def _get_stations() -> dict[str, Any]:
    """站点表缓存。"""
    global _STATIONS_CACHE
    if _STATIONS_CACHE is None:
        _STATIONS_CACHE = _load_stations()
    return _STATIONS_CACHE


def _norm_key(text: str) -> str:
    """规范化关键词：去空格、去连字符、转小写。"""
    return re.sub(r"[\s\-]+", "", str(text or "")).lower()


def _match_puzzle_exact(keyword: str, puzzles: dict[str, Any]) -> dict[str, Any] | None:
    """区域拼图精确匹配（中文名或拼音文件名）。"""
    for name, info in puzzles.items():
        info_dict = info if isinstance(info, dict) else {}
        puzzle_path = str(info_dict.get("path", "") or "")
        puzzle_key = _norm_key(name)
        path_key = _norm_key(puzzle_path.rsplit("/", 1)[-1].replace(".html", ""))
        if keyword == puzzle_key or keyword == path_key:
            return {
                "matched": True,
                "kind": "puzzle",
                "name": name,
                "path": puzzle_path,
                "code": str(info_dict.get("code", "") or ""),
            }
    return None


def _match_city_exact(keyword: str, stations: dict[str, Any]) -> dict[str, Any] | None:
    """城市名精确匹配（含省份信息）。"""
    exact = []
    for city, info in stations.items():
        info_list = info if isinstance(info, list) else [info]
        if _norm_key(city) != keyword:
            continue
        for it in info_list:
            exact.append(
                {
                    "city": city,
                    "province": it.get("province", ""),
                    "path": it.get("path", ""),
                    "code": it.get("code", ""),
                }
            )
    if len(exact) == 1:
        it = exact[0]
        return {
            "matched": True,
            "kind": "station",
            "name": it["city"],
            "province": it["province"],
            "path": it["path"],
            "code": it["code"],
        }
    if len(exact) > 1:
        return {"matched": False, "reason": "ambiguous", "candidates": exact}
    return None


def _first_station_of_province(
    province: str,
    stations: dict[str, Any],
    *,
    note: str,
) -> dict[str, Any] | None:
    """按站点表 province 字段匹配，返回该省首个站点。

    不依赖路径前缀，规避山西/陕西（NMC 拼音均为 shan-xi）以及
    hunan/ 目录等路径命名不一致导致的串号/漏匹配。
    """
    for city, info in stations.items():
        info_list = info if isinstance(info, list) else [info]
        for it in info_list:
            if str(it.get("province", "")) != province:
                continue
            return {
                "matched": True,
                "kind": "station",
                "name": city,
                "province": province,
                "path": it.get("path", ""),
                "code": it.get("code", ""),
                "note": note.format(province=province, city=city),
            }
    return None


def _match_province_exact(
    keyword: str,
    stations: dict[str, Any],
) -> dict[str, Any] | None:
    """省份名精确匹配（按 province 字段）。"""
    if keyword not in _PROVINCE_DIRS:
        return None
    found = _first_station_of_province(
        keyword,
        stations,
        note="已匹配「{province}」省首个雷达站：{city}",
    )
    if found:
        return found
    return {"matched": False, "reason": "no_province_station", "keyword": keyword}


def _match_province_pinyin(
    keyword: str,
    stations: dict[str, Any],
) -> dict[str, Any] | None:
    """省份拼音匹配（如 beijing → 北京，命中该省首个站点）。"""
    for prov_name, prov_dir in _PROVINCE_DIRS.items():
        prov_dir_key = _norm_key(prov_dir)
        # 仅允许：完全相等，或关键词是拼音前缀（避免长关键词误命中短拼音）
        if keyword != prov_dir_key and not (
            len(keyword) >= 2 and prov_dir_key.startswith(keyword)
        ):
            continue
        found = _first_station_of_province(
            prov_name,
            stations,
            note="已匹配「{province}」省首个雷达站：{city}",
        )
        if found:
            return found
    return None


def _match_puzzle_pinyin(
    keyword: str, puzzles: dict[str, Any]
) -> dict[str, Any] | None:
    """区域拼图拼音匹配（如 huabei → 华北）。"""
    for name, info in puzzles.items():
        info_dict = info if isinstance(info, dict) else {}
        puzzle_path = str(info_dict.get("path", "") or "")
        path_key = _norm_key(puzzle_path.rsplit("/", 1)[-1].replace(".html", ""))
        if keyword == path_key:
            return {
                "matched": True,
                "kind": "puzzle",
                "name": name,
                "path": puzzle_path,
                "code": str(info_dict.get("code", "") or ""),
            }
    return None


def _match_fuzzy(
    keyword: str,
    stations: dict[str, Any],
) -> dict[str, Any] | None:
    """模糊匹配（拼音子串 / 中文子串），返回首个命中或候选列表。"""
    # 收紧规则：仅允许「关键词是站名/拼音的子串」（单向、关键词更短），
    # 禁止「长关键词包含短站名」的反向匹配，避免「佛罗里达州」误命中「达州」。
    candidates: list[dict[str, Any]] = []

    # 4a. 拼音匹配：关键词是页面路径拼音的子串（关键词长度 >= 2）
    if len(keyword) >= 2:
        for city, info in stations.items():
            info_list = info if isinstance(info, list) else [info]
            for it in info_list:
                path = str(it.get("path", ""))
                path_pinyin = _norm_key(path.rsplit("/", 1)[-1].split(".")[0])
                if len(path_pinyin) > len(keyword) and keyword in path_pinyin:
                    candidates.append(
                        {
                            "city": city,
                            "province": it.get("province", ""),
                            "path": path,
                            "code": it.get("code", ""),
                        }
                    )
    # 4b. 中文包含匹配：关键词是站名的子串（关键词长度 >= 2）
    if not candidates and len(keyword) >= 2:
        for city, info in stations.items():
            info_list = info if isinstance(info, list) else [info]
            if keyword not in _norm_key(city):
                continue
            for it in info_list:
                candidates.append(
                    {
                        "city": city,
                        "province": it.get("province", ""),
                        "path": it.get("path", ""),
                        "code": it.get("code", ""),
                    }
                )

    if len(candidates) == 1:
        it = candidates[0]
        return {
            "matched": True,
            "kind": "station",
            "name": it["city"],
            "province": it["province"],
            "path": it["path"],
            "code": it["code"],
        }
    if len(candidates) > 1:
        return {"matched": False, "reason": "ambiguous", "candidates": candidates}
    return None


def resolve_radar_target(
    keyword: str,
) -> dict[str, Any]:
    """解析雷达查询关键词，返回目标信息。

    支持匹配（按优先级）：
    - 区域拼图：全国 / 华北 / 东北 / 华东 / 华中 / 华南 / 西南 / 西北（含拼音）
    - 城市名：北京 / 大兴 / 石家庄 ...
    - 省份名：河北 / 山西 / 陕西 ...（按站点表 province 字段精确匹配）
    - 拼音：beijing / daxing / huabei ...

    Returns:
        dict 包含：matched(bool)、kind("puzzle"|"station")、name、province、path、code、
        candidates(模糊匹配候选列表)。
    """
    data = _get_stations()
    puzzles = data.get("puzzles") or {}
    stations = data.get("stations") or {}
    keyword = _norm_key(keyword)

    if not keyword:
        return {"matched": False, "reason": "empty", "candidates": []}

    # 1. 区域拼图精确匹配（中文名或拼音文件名）
    hit = _match_puzzle_exact(keyword, puzzles)
    if hit is not None:
        return hit

    # 2. 城市名精确匹配
    hit = _match_city_exact(keyword, stations)
    if hit is not None:
        return hit

    # 3. 省份名精确匹配（按 province 字段）
    hit = _match_province_exact(keyword, stations)
    if hit is not None:
        return hit

    # 3a. 省份拼音目录匹配
    hit = _match_province_pinyin(keyword, stations)
    if hit is not None:
        return hit

    # 3b. 区域拼图拼音匹配
    hit = _match_puzzle_pinyin(keyword, puzzles)
    if hit is not None:
        return hit

    # 4. 模糊匹配（拼音子串 / 中文子串）
    hit = _match_fuzzy(keyword, stations)
    if hit is not None:
        return hit

    return {"matched": False, "reason": "no_match", "keyword": keyword}


def _format_time_label(raw_ts: str | None) -> str:
    """把 URL 时间戳（如 20260808102400000）格式化为 YYYY-MM-DD HH:MM。"""
    if not raw_ts:
        return ""
    try:
        dt = datetime.strptime(str(raw_ts)[:14], "%Y%m%d%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def build_radar_image_result(
    *,
    target: dict[str, Any],
    image_bytes: bytes,
    url: str,
) -> dict[str, Any]:
    """构建单图查询结果。"""
    label = f"{target.get('name', '')}" + (
        f"（{target.get('province', '')}）" if target.get("province") else ""
    )
    return {
        "success": True,
        "kind": target.get("kind", "station"),
        "name": label,
        "time": _format_time_label(NmcRadarClient.parse_time_from_url(url)),
        "image_base64": base64.b64encode(image_bytes).decode(),
        "source_url": url,
    }


def _degraded_single_result(
    *,
    target: dict[str, Any],
    image_bytes: bytes,
    label: str,
    times: list[str],
    frames_count: int,
) -> dict[str, Any]:
    """帧数不足时降级为单图结果（取最新一帧，即时间序列最后一个）。"""
    return {
        "success": True,
        "kind": target.get("kind", "station"),
        "name": label,
        "time": _format_time_label(times[-1]) if times else "",
        "image_base64": base64.b64encode(image_bytes).decode(),
        "degraded": True,
        "frames": frames_count,
    }


def _render_gif_sync(
    frames: list[bytes],
) -> bytes | None:
    """在独立线程中同步合成循环 GIF（Pillow CPU 密集操作）。"""
    pil_frames = []
    for data in frames:
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
            pil_frames.append(img)
        except Exception as e:
            logger.warning(f"[灾害预警] 雷达帧解析失败: {e}")
    if not pil_frames:
        return None

    buf = io.BytesIO()
    # 统一各帧尺寸，保证 GIF 可正常播放
    base_w, base_h = pil_frames[0].size
    normalized = []
    for img in pil_frames:
        if img.size != (base_w, base_h):
            img = img.resize((base_w, base_h), Image.Resampling.LANCZOS)
        normalized.append(img)
    normalized[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=normalized[1:],
        duration=500,
        loop=0,
        optimize=False,
    )
    return buf.getvalue()


async def _render_gif(frames: list[bytes]) -> bytes | None:
    """异步包装 GIF 合成，避免阻塞事件循环线程。"""
    return await asyncio.to_thread(_render_gif_sync, frames)


async def build_radar_gif_result(
    *,
    target: dict[str, Any],
    frames: list[bytes],
    times: list[str],
) -> dict[str, Any]:
    """用多帧合成循环 GIF 并返回结果。

    times 与 frames 必须按同一帧序对应（由调用方保证）。
    降级单图时取最新一帧（时间序列最后一个）。
    """
    label = f"{target.get('name', '')}" + (
        f"（{target.get('province', '')}）" if target.get("province") else ""
    )
    if not frames:
        return {"success": False, "error": "无可用雷达图像帧"}
    if len(frames) < MIN_GIF_FRAMES:
        return _degraded_single_result(
            target=target,
            image_bytes=frames[-1],
            label=label,
            times=times,
            frames_count=len(frames),
        )

    gif_bytes = await _render_gif(frames)
    if gif_bytes is None:
        # 有效帧不足，降级为单图（取最新帧）
        return _degraded_single_result(
            target=target,
            image_bytes=frames[-1],
            label=label,
            times=times,
            frames_count=len(frames),
        )

    # GIF 由 frames 合成，时间标签取序列最后一个（最新时次）
    return {
        "success": True,
        "kind": target.get("kind", "station"),
        "name": label,
        "time": _format_time_label(times[-1]) if times else "",
        "frames": len(frames),
        "image_base64": base64.b64encode(gif_bytes).decode(),
    }


def build_radar_list_text() -> str:
    """构建雷达列表文本。"""
    data = _get_stations()
    puzzles = data.get("puzzles") or {}
    stations = data.get("stations") or {}

    lines = ["📡 气象雷达站点列表", ""]
    lines.append("【区域拼图】")
    for name, info in puzzles.items():
        info_dict = info if isinstance(info, dict) else {}
        if info_dict.get("path"):
            lines.append(f"  • {name}")
    lines.append("")

    # 按省份分组
    by_province: dict[str, list[str]] = {}
    for city, info in stations.items():
        info_list = info if isinstance(info, list) else [info]
        for it in info_list:
            prov = it.get("province", "其他")
            by_province.setdefault(prov, []).append(city)
    lines.append("【单站雷达】")
    for prov in sorted(by_province.keys()):
        cities = sorted(by_province[prov])
        lines.append(f"  {prov}：{' '.join(cities)}")
    return "\n".join(lines)


def format_candidates_text(candidates: list[dict[str, Any]]) -> str:
    """把候选站点列表格式化为提示文本。"""
    if not candidates:
        return ""
    lines = ["🔍 找到多个候选雷达站，请指定具体名称："]
    for i, c in enumerate(candidates[:10], 1):
        prov = c.get("province", "")
        city = c.get("city", "")
        lines.append(f"  {i}. {city}（{prov}）")
    if len(candidates) > 10:
        lines.append(f"  ... 共 {len(candidates)} 个候选")
    lines.append("例如：/雷达 北京 或 /雷达 大兴")
    return "\n".join(lines)


async def query_radar_image(
    *,
    client: NmcRadarClient,
    target: dict[str, Any],
) -> dict[str, Any]:
    """查询最新一帧雷达图。"""
    path = target.get("path", "")
    if not path:
        return {"success": False, "error": "站点页面路径缺失"}
    urls = await client.fetch_page_radar_urls(path)
    if not urls:
        return {"success": False, "error": "未能获取雷达图片地址"}
    # 取最新一帧（去掉 medium 取原图）
    latest_url = client.strip_medium(urls[0])
    data = await client.download_image(latest_url)
    if not data:
        return {"success": False, "error": "雷达图片下载失败"}
    return build_radar_image_result(target=target, image_bytes=data, url=latest_url)


async def query_radar_gif(
    *,
    client: NmcRadarClient,
    target: dict[str, Any],
    frames: int = DEFAULT_GIF_FRAMES,
) -> dict[str, Any]:
    """查询最近 N 帧并合成循环 GIF。"""
    path = target.get("path", "")
    if not path:
        return {"success": False, "error": "站点页面路径缺失"}
    urls = await client.fetch_page_radar_urls(path)
    if not urls:
        return {"success": False, "error": "未能获取雷达图片地址"}

    frames = max(1, min(int(frames or DEFAULT_GIF_FRAMES), MAX_GIF_FRAMES))
    # data-img 序列为时间倒序（最新帧在前），取最近 N 帧后反转，
    # 使 GIF 按时间正序播放（从早到晚展示云系演变）。
    selected = list(reversed(urls[:frames]))
    # 统一取原图（去掉 medium）
    selected = [client.strip_medium(u) for u in selected]
    # 下载帧并保留 URL 配对，避免失败帧导致时间标签错位
    pairs = await client.download_frames(selected)
    if not pairs:
        return {"success": False, "error": "雷达动图帧下载失败"}

    data_list = [d for _, d in pairs]
    times = [NmcRadarClient.parse_time_from_url(u) for u, _ in pairs]
    return await build_radar_gif_result(target=target, frames=data_list, times=times)


async def query_radar_list() -> dict[str, Any]:
    """查询雷达站点列表文本。"""
    return {"success": True, "text": build_radar_list_text()}


__all__ = [
    "resolve_radar_target",
    "query_radar_image",
    "query_radar_gif",
    "query_radar_list",
    "format_candidates_text",
    "build_radar_list_text",
]
