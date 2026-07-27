"""
JMA 震央分布 HTTP 客户端。

数据源：
  https://www.jma.go.jp/bosai/hypo/data/{YYYY}/{MM}/hypo{YYYYMMDD}.geojson

说明：
- 官方按日提供 GeoJSON，响应可能是 gzip 或明文。
- 历史可回溯窗口会随官方数据源变化，客户端不假设固定起始日。
- fetch_day 语义：
  - list：请求成功（含合法零事件日）
  - None：数据缺失/请求失败（404、非 200、解析异常等）
"""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

import aiohttp

from astrbot.api import logger

JMA_HYPO_BASE = "https://www.jma.go.jp/bosai/hypo/data"
DEFAULT_CONCURRENCY = 8
DEFAULT_TIMEOUT_SEC = 30

# 日本附近视窗（略放宽，避免边缘点被误丢）
MAP_LON_MIN = 119.0
MAP_LON_MAX = 154.0
MAP_LAT_MIN = 20.0
MAP_LAT_MAX = 50.0


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_event_time(raw: str) -> datetime | None:
    """解析 JMA hypo date 字段，如 2026/07/20.00:10。"""
    text = str(raw or "").strip()
    if not text:
        return None
    # 兼容带秒/小数秒：2026/07/18.07:52:19.25
    text = text.replace("/", "-")
    if "." in text and text.count(":") >= 1:
        date_part, time_part = text.split(".", 1)
        time_part = time_part.split(".")[0]
        text = f"{date_part} {time_part}"
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d.%H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            # JMA hypo 时间为日本标准时（UTC+9）
            return dt.replace(tzinfo=timezone(timedelta(hours=9)))
        except ValueError:
            continue
    return None


def _normalize_feature(feature: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(feature, dict):
        return None
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates") or []
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return None
    if not (MAP_LON_MIN <= lon <= MAP_LON_MAX and MAP_LAT_MIN <= lat <= MAP_LAT_MAX):
        return None

    props = feature.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    mag = _safe_float(props.get("mag"))
    dep = _safe_float(props.get("dep"))
    place = str(props.get("place") or "").strip()
    date_str = str(props.get("date") or "").strip()
    occurred_at = _parse_event_time(date_str)
    return {
        "lon": lon,
        "lat": lat,
        "mag": mag if mag is not None else 0.0,
        "dep": dep if dep is not None else 0.0,
        "place": place,
        "date_str": date_str,
        "occurred_at": occurred_at,
    }


class JmaHypoClient:
    """JMA 每日震央 GeoJSON 客户端。"""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._external_session = session
        self._concurrency = max(1, int(concurrency or DEFAULT_CONCURRENCY))
        self._timeout = aiohttp.ClientTimeout(
            total=float(timeout_sec or DEFAULT_TIMEOUT_SEC)
        )

    @staticmethod
    def build_url(target_date: date) -> str:
        yyyy = target_date.strftime("%Y")
        mm = target_date.strftime("%m")
        ymd = target_date.strftime("%Y%m%d")
        return f"{JMA_HYPO_BASE}/{yyyy}/{mm}/hypo{ymd}.geojson"

    async def fetch_day(
        self,
        target_date: date,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> list[dict[str, Any]] | None:
        """拉取单日震央列表。

        Returns:
            list: 请求成功（可为空列表，表示合法零事件日）
            None: 数据缺失或请求失败（404 / 非 200 / 异常）
        """
        url = self.build_url(target_date)
        owns_session = False
        client = session or self._external_session
        if client is None:
            # 保持默认 TLS 证书校验，避免中间人篡改地震数据
            client = aiohttp.ClientSession(timeout=self._timeout)
            owns_session = True
        try:
            async with client.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    logger.warning(
                        f"[灾害预警] JMA 震央 {target_date.isoformat()} 返回 HTTP 状态码 {resp.status}"
                    )
                    return None
                raw = await resp.read()
            try:
                decoded = gzip.decompress(raw)
            except (gzip.BadGzipFile, OSError):
                decoded = raw
            geojson = json.loads(decoded)
            features = geojson.get("features") or []
            events: list[dict[str, Any]] = []
            for feat in features:
                item = _normalize_feature(feat if isinstance(feat, dict) else {})
                if item:
                    events.append(item)
            return events
        except Exception as exc:
            logger.warning(
                f"[灾害预警] JMA 震央 {target_date.isoformat()} 拉取失败: {exc}"
            )
            return None
        finally:
            if owns_session and client is not None:
                await client.close()

    async def fetch_range(
        self,
        dates: list[date],
        *,
        session: aiohttp.ClientSession | None = None,
        timeout_sec: float | None = 60.0,
    ) -> dict[str, Any]:
        """并发拉取多日数据。

        Returns:
            {
              "events": [...],
              "day_counts": {"YYYYMMDD": n, ...},  # 仅成功覆盖日（含 0 事件）
              "missing_days": ["YYYY-MM-DD", ...],  # 仅真正缺失/失败/超时日
              "zero_event_days": ["YYYY-MM-DD", ...],  # 成功但零事件
              "requested_days": int,
              "covered_days": int,
            }
        """
        if not dates:
            return {
                "events": [],
                "day_counts": {},
                "missing_days": [],
                "zero_event_days": [],
                "requested_days": 0,
                "covered_days": 0,
            }

        owns_session = False
        client = session or self._external_session
        if client is None:
            # 保持默认 TLS 证书校验
            client = aiohttp.ClientSession(timeout=self._timeout)
            owns_session = True

        sem = asyncio.Semaphore(self._concurrency)
        day_counts: dict[str, int] = {}
        missing_days: list[str] = []
        zero_event_days: list[str] = []
        all_events: list[dict[str, Any]] = []

        async def _one(d: date) -> tuple[date, list[dict[str, Any]] | None]:
            async with sem:
                events = await self.fetch_day(d, session=client)
                return d, events

        try:
            tasks = [asyncio.create_task(_one(d)) for d in dates]
            if timeout_sec and timeout_sec > 0:
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=timeout_sec,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[灾害预警] JMA 震央范围抓取达到整体超时上限 ({timeout_sec}s)，将保留已完成日期的结果"
                    )
                    results = []
                    for t in tasks:
                        if t.done() and not t.cancelled() and t.exception() is None:
                            results.append(t.result())
                        else:
                            t.cancel()
            else:
                results = await asyncio.gather(*tasks, return_exceptions=True)

            processed_dates: set[date] = set()
            for res in results:
                if not isinstance(res, tuple) or len(res) != 2:
                    continue
                d, events = res
                processed_dates.add(d)
                key = d.strftime("%Y%m%d")
                if events is None:
                    # 404 / 非 200 / 异常：真正缺失
                    missing_days.append(d.isoformat())
                    continue
                # 成功覆盖（含合法零事件日）
                day_counts[key] = len(events)
                if events:
                    all_events.extend(events)
                else:
                    zero_event_days.append(d.isoformat())

            # 因整体超时未完成的日期补充计入 missing_days
            for d in dates:
                if d not in processed_dates:
                    missing_days.append(d.isoformat())

        finally:
            if owns_session and client is not None:
                await client.close()

        return {
            "events": all_events,
            "day_counts": day_counts,
            "missing_days": missing_days,
            "zero_event_days": zero_event_days,
            "requested_days": len(dates),
            "covered_days": len(day_counts),
        }


__all__ = [
    "DEFAULT_CONCURRENCY",
    "JMA_HYPO_BASE",
    "JmaHypoClient",
]
