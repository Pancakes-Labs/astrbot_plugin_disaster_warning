"""
降水量预报查询服务。

统一承接命令侧（/降水量预报 /降水量预报动图）的查询编排：
- 产品解析（24 小时 / 6 小时累计降水）
- 时次解析（数字如 72，或自然语言如 明天/后天；默认最新一帧）
- 抓取页面 data-img 序列，下载指定时次单图或全部时次帧
- Pillow 合成循环 GIF（动图，每秒一帧）

数据源：中央气象台降水量预报页面
    24 小时：https://www.nmc.cn/publish/precipitation/1-day.html（7 时次：24/48/72/96/120/144/168）
    6 小时：https://www.nmc.cn/publish/precipitation/6hours-6.html（4 时次：6/12/18/24）
页面 data-img 序列为时效递增，天然按时间正序，动图直接按页面顺序从早到晚播放。
"""

from __future__ import annotations

import asyncio
import base64
import io
from datetime import datetime, timedelta
from typing import Any

from PIL import Image

from astrbot.api import logger

from ...network.http.nmc_precipitation_client import (
    NmcPrecipitationClient,
    PrecipitationFrame,
)

# 动图帧间隔（毫秒）：1000，即每秒一帧
GIF_DURATION_MS = 1000
# 动图最少帧数，不足则降级为单图
MIN_GIF_FRAMES = 3

# 产品表：关键词 → 页面路径 / 显示名 / 可选时次说明
_PRODUCT_24H = {
    "key": "24h",
    "name": "24小时降水量预报",
    "path": "/publish/precipitation/1-day.html",
    "hours_desc": "24/48/72/96/120/144/168",
    "hours": [24, 48, 72, 96, 120, 144, 168],
}
_PRODUCT_6H = {
    "key": "6h",
    "name": "6小时降水量预报",
    "path": "/publish/precipitation/6hours-6.html",
    "hours_desc": "6/12/18/24",
    "hours": [6, 12, 18, 24],
}
_PRODUCT_ALIASES = {
    "24h": _PRODUCT_24H,
    "24": _PRODUCT_24H,
    "24小时": _PRODUCT_24H,
    "一天": _PRODUCT_24H,
    "1天": _PRODUCT_24H,
    "6h": _PRODUCT_6H,
    "6": _PRODUCT_6H,
    "6小时": _PRODUCT_6H,
}

# 自然语言时次（仅对 24h 产品语义明确）
_NATURAL_HOURS = {
    "今天": 0,  # 今天 → 最新（实际取最近一帧）
    "明天": 24,
    "后天": 48,
    "大后天": 72,
}


def resolve_product(keyword: str | None) -> dict[str, Any] | None:
    """解析产品关键词，返回产品定义；无法识别返回 None。"""
    if not keyword:
        return None
    key = str(keyword).strip().lower()
    return _PRODUCT_ALIASES.get(key)


def resolve_frame_hour(
    keyword: str | None,
    product: dict[str, Any],
) -> int | None:
    """解析时次关键词，返回预报时效（小时）；无法识别返回 None（表示取最新）。

    支持：
    - 纯数字：如 72 / 12
    - 自然语言：今天/明天/后天/大后天（仅 24h 产品语义明确）

    返回的时效不在产品可选时次内时，由调用方做最近匹配。
    """
    if not keyword:
        return None
    text = str(keyword).strip().lower()
    if text in ("最新", "latest"):
        return None
    if text.isdigit():
        return int(text)
    if product.get("key") == "24h" and text in _NATURAL_HOURS:
        return _NATURAL_HOURS[text]
    return None


def _nearest_frame(
    frames: list[PrecipitationFrame],
    hour: int | None,
) -> PrecipitationFrame | None:
    """按请求时效取帧。

    - hour 为空：取最新一帧（页面第一个）。
    - hour 非空：取时效与请求最接近的帧（完全匹配优先）。
    """
    if not frames:
        return None
    if hour is None:
        return frames[0]
    best = frames[0]
    best_gap = abs(best.fffmm - hour)
    for f in frames[1:]:
        gap = abs(f.fffmm - hour)
        if gap < best_gap:
            best = f
            best_gap = gap
    return best


def _format_bj_time(dt: datetime) -> str:
    """格式化为北京时字符串。"""
    return dt.strftime("%Y-%m-%d %H:%M")


def _frame_valid_time(frame: PrecipitationFrame) -> datetime | None:
    """计算帧的有效时间（北京时间 = UTC 起报 + 8 小时 + 预报时效）。"""
    if not frame.init_time:
        return None
    try:
        utc = datetime.strptime(frame.init_time, "%Y%m%d%H%M")
        return utc + timedelta(hours=8) + timedelta(hours=frame.fffmm)
    except (ValueError, TypeError):
        return None


def _frame_init_bj_time(frame: PrecipitationFrame) -> datetime | None:
    """计算帧的起报时间（北京时间）。"""
    if not frame.init_time:
        return None
    try:
        utc = datetime.strptime(frame.init_time, "%Y%m%d%H%M")
        return utc + timedelta(hours=8)
    except (ValueError, TypeError):
        return None


def build_precip_image_result(
    *,
    product: dict[str, Any],
    frame: PrecipitationFrame,
    image_bytes: bytes,
) -> dict[str, Any]:
    """构建单图查询结果。"""
    valid = _frame_valid_time(frame)
    init = _frame_init_bj_time(frame)
    return {
        "success": True,
        "kind": "precipitation",
        "product": product.get("key"),
        "name": product.get("name"),
        "time": _format_bj_time(valid) if valid else "",
        "init_time": _format_bj_time(init) if init else "",
        "fffmm": frame.fffmm,
        "image_base64": base64.b64encode(image_bytes).decode(),
        "source_url": NmcPrecipitationClient.strip_version(frame.url),
    }


async def build_precip_gif_result(
    *,
    product: dict[str, Any],
    frames: list[PrecipitationFrame],
    data_list: list[bytes],
) -> dict[str, Any]:
    """用多帧合成循环 GIF 并返回结果。

    frames 与 data_list 必须按同一帧序对应（由调用方保证）。
    frames 按时间正序（时效递增，最新时次在前，即 frames[0] 为最近一帧）。
    降级单图时取最近一帧（时间序列第一个）。
    """
    if not frames:
        return {"success": False, "error": "无可用降水图像帧"}
    if len(frames) < MIN_GIF_FRAMES:
        return _degraded_single_result(
            product=product, frame=frames[0], image_bytes=data_list[0]
        )

    gif_bytes = await _render_gif(data_list)
    if gif_bytes is None:
        return _degraded_single_result(
            product=product, frame=frames[0], image_bytes=data_list[0]
        )

    latest = frames[0]
    valid = _frame_valid_time(latest)
    init = _frame_init_bj_time(latest)
    return {
        "success": True,
        "kind": "precipitation",
        "product": product.get("key"),
        "name": product.get("name"),
        "time": _format_bj_time(valid) if valid else "",
        "init_time": _format_bj_time(init) if init else "",
        "frames": len(frames),
        "duration_ms": GIF_DURATION_MS,
        "image_base64": base64.b64encode(gif_bytes).decode(),
    }


def _degraded_single_result(
    *,
    product: dict[str, Any],
    frame: PrecipitationFrame,
    image_bytes: bytes,
) -> dict[str, Any]:
    """帧数不足时降级为单图结果（取最新一帧）。"""
    result = build_precip_image_result(
        product=product,
        frame=frame,
        image_bytes=image_bytes,
    )
    result["degraded"] = True
    return result


def _render_gif_sync(frames: list[bytes]) -> bytes | None:
    """同步合成循环 GIF（Pillow CPU 密集操作，每秒一帧）。"""
    pil_frames = []
    for data in frames:
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
            pil_frames.append(img)
        except Exception as e:
            logger.warning(f"[灾害预警] 降水帧解析失败: {e}")
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
        duration=GIF_DURATION_MS,
        loop=0,
        optimize=False,
    )
    return buf.getvalue()


async def _render_gif(frames: list[bytes]) -> bytes | None:
    """异步包装 GIF 合成，避免阻塞事件循环线程。"""
    return await asyncio.to_thread(_render_gif_sync, frames)


async def query_precip_image(
    *,
    client: NmcPrecipitationClient,
    product: dict[str, Any],
    hour: int | None = None,
) -> dict[str, Any]:
    """查询单张降水预报图（支持指定时次，默认最新一帧）。

    Args:
        client: 降水客户端。
        product: 产品定义（resolve_product 返回值）。
        hour: 请求的预报时效（小时）；None 表示取最新一帧。
    """
    path = product.get("path", "")
    if not path:
        return {"success": False, "error": "产品页面路径缺失"}
    frames = await client.fetch_page_frames(path)
    if not frames:
        return {"success": False, "error": "未能获取降水图片地址"}

    frame = _nearest_frame(frames, hour)
    if frame is None:
        return {"success": False, "error": "未找到可用降水图片帧"}

    data = await client.download_image(frame.url)
    if not data:
        return {"success": False, "error": "降水图片下载失败"}

    result = build_precip_image_result(
        product=product,
        frame=frame,
        image_bytes=data,
    )
    # 请求了具体时次但未精确命中时，附加提示
    if hour is not None and frame.fffmm != hour:
        result["nearest"] = True
    return result


async def query_precip_gif(
    *,
    client: NmcPrecipitationClient,
    product: dict[str, Any],
) -> dict[str, Any]:
    """查询全部时次并合成循环 GIF（每秒一帧）。"""
    path = product.get("path", "")
    if not path:
        return {"success": False, "error": "产品页面路径缺失"}
    frames = await client.fetch_page_frames(path)
    if not frames:
        return {"success": False, "error": "未能获取降水图片地址"}

    # 降水页面 data-img 序列为时效递增（24h：024→168，6h：006→024），
    # 天然按时间正序（从早到晚展示降水演变），直接按页面顺序播放即可。
    pairs = await client.download_frames(frames)
    if not pairs:
        return {"success": False, "error": "降水动图帧下载失败"}

    frame_list = [f for f, _ in pairs]
    data_list = [d for _, d in pairs]
    return await build_precip_gif_result(
        product=product,
        frames=frame_list,
        data_list=data_list,
    )


__all__ = [
    "resolve_product",
    "resolve_frame_hour",
    "query_precip_image",
    "query_precip_gif",
    "GIF_DURATION_MS",
]
