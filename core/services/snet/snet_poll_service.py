"""
NIED S-Net / MSIL 瓦片轮询服务。

独立于通用 WebSocket / Wolfx HTTP 列表轮询：
需要下载 PNG 瓦片、解码测站颜色并进入统一事件流水线。

内置短时快照缓存：同一分钟瓦片与解码后的测站列表可在轮询与 /snet 之间复用，
降低对 MSIL 上游的重复请求。
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from PIL import Image

from astrbot.api import logger

from ...parsers.snet_parser import MSIL_TILE_BASE, SNET_REAL_COORDS, _build_stations
from ..query.source_runtime_query_service import SourceRuntimeQueryService
from .snet_filter_constants import (
    DEFAULT_MIN_SHINDO,
    DEFAULT_STATION_MIN_SHINDO,
    normalize_min_shindo,
    normalize_station_min_shindo,
)


class SnetPollService:
    """S-Net MSIL 瓦片轮询服务。"""

    SOURCE_ID = "snet_msil"
    DEFAULT_INTERVAL_SECONDS = 60
    TILE_NAMES = (("y11", "11"), ("y12", "12"))
    # 推送去重：仅比较震度最高的前 N 个触发测站（名称 + 震度）
    PUSH_DEDUP_TOP_N = 5
    # 瓦片快照最短/最长 TTL（秒）；实际 TTL 会结合 poll_interval 收敛
    MIN_TILE_CACHE_TTL = 30.0
    MAX_TILE_CACHE_TTL = 600.0

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(service.config)
        self._task: asyncio.Task | None = None
        self._last_event_id: str | None = None
        self._last_payload_fingerprint: str | None = None
        # 最近一次成功抓取的快照：{timestamp, tiles, stations|None, fetched_at}
        self._latest_snapshot: dict[str, Any] | None = None
        self._fetch_lock = asyncio.Lock()
        # 禁用态日志：仅首次跳过本轮时打一次
        self._disabled_logged = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        return self._source_runtime_query.is_source_enabled(self.SOURCE_ID)

    def _resolve_interval(self) -> int:
        data_sources = self.service.config.get("data_sources", {})
        group = data_sources.get("snet", {}) if isinstance(data_sources, dict) else {}
        if not isinstance(group, dict):
            return self.DEFAULT_INTERVAL_SECONDS
        try:
            interval = int(
                group.get("poll_interval_seconds", self.DEFAULT_INTERVAL_SECONDS)
            )
        except (TypeError, ValueError):
            interval = self.DEFAULT_INTERVAL_SECONDS
        return max(30, min(interval, 600))

    def _resolve_tile_cache_ttl(self) -> float:
        """瓦片快照 TTL：略短于轮询间隔，避免过期数据拖太久。"""
        interval = float(self._resolve_interval())
        return max(
            self.MIN_TILE_CACHE_TTL,
            min(interval * 0.9, self.MAX_TILE_CACHE_TTL),
        )

    def _get_snet_filter_config(self) -> dict[str, Any]:
        """读取全局 earthquake_filters.snet_filter。"""
        filters = self.service.config.get("earthquake_filters", {})
        snet_filter = (
            filters.get("snet_filter", {}) if isinstance(filters, dict) else {}
        )
        return snet_filter if isinstance(snet_filter, dict) else {}

    def _get_snet_filter_value(
        self,
        key: str,
        default: float,
        *,
        normalizer,
    ) -> float:
        """统一解析 S-Net 过滤器中的震度类阈值。"""
        snet_filter = self._get_snet_filter_config()
        if not snet_filter or not snet_filter.get("enabled", True):
            return float(default)
        return normalizer(snet_filter.get(key, default))

    def _resolve_min_shindo(self) -> float:
        """解析最大震度门槛（默认 1.5）。"""
        return self._get_snet_filter_value(
            "min_shindo",
            DEFAULT_MIN_SHINDO,
            normalizer=normalize_min_shindo,
        )

    def _resolve_station_min_shindo(self) -> float:
        """解析测站计数用震度门槛（默认 0.5）。"""
        return self._get_snet_filter_value(
            "station_min_shindo",
            DEFAULT_STATION_MIN_SHINDO,
            normalizer=normalize_station_min_shindo,
        )

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self.running:
            return
        if not self.is_enabled():
            logger.info("[灾害预警] S-Net 数据源未启用，跳过轮询启动")
            return
        self._task = asyncio.create_task(self._poll_loop(), name="dw_snet_poll")
        self.service.scheduled_tasks.append(self._task)
        logger.debug("[灾害预警] S-Net 轮询任务已启动")

    async def stop(self) -> None:
        """停止后台轮询任务。"""
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_started("snet_msil")
            except Exception as exc:
                logger.debug(f"[灾害预警] S-Net 轮询通知静默协调器抓取开始失败: {exc}")
        # 启动后先立即抓一次（若仍处于静默期，流水线会自行吞掉推送）
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] S-Net 首次抓取失败: {exc}")
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed("snet_msil", success=False)
                except Exception:
                    pass

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    # 仅首次禁用时打一次，避免配置禁用后每轮刷屏
                    if not self._disabled_logged:
                        logger.debug("[灾害预警] S-Net 已禁用，跳过本轮轮询")
                        self._disabled_logged = True
                    continue
                self._disabled_logged = False
                await self.fetch_once(emit_event=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[灾害预警] S-Net 轮询异常: {exc}")

    async def fetch_once(
        self,
        *,
        emit_event: bool = True,
        min_shindo: float | None = None,
        parse_stations: bool = False,
    ) -> dict[str, Any] | None:
        """抓取并（可选）推送一轮 S-Net 数据。

        Returns:
            成功时返回 {
              "timestamp", "tiles", "min_shindo",
              "stations"(可选), "triggered"(可选)
            }；失败返回 None。
        """
        tiles_payload = await self._download_latest_tiles()
        if not tiles_payload:
            coordinator = getattr(self.service, "startup_silence", None)
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed("snet_msil", success=False)
                except Exception as exc:
                    logger.debug(f"[灾害预警] S-Net 轮询通知静默协调器失败: {exc}")
            return None

        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_completed("snet_msil", success=True)
            except Exception as exc:
                logger.debug(f"[灾害预警] S-Net 轮询通知静默协调器失败: {exc}")

        threshold = (
            self._resolve_min_shindo()
            if min_shindo is None
            else normalize_min_shindo(min_shindo)
        )

        # 获取针对触发测站数判定的最小测站震度阈值
        station_threshold = self._resolve_station_min_shindo()

        # 前置过滤阈值：取 min_shindo 和 station_min_shindo 中较小者，以便震度低但测站多的情况也能解析出事件
        fetch_min_shindo = min(threshold, station_threshold)

        raw_dict: dict[str, Any] = {
            "tiles": tiles_payload["tiles"],
            "timestamp": tiles_payload["timestamp"],
            "min_shindo": threshold,
            "station_min_shindo": station_threshold,
        }

        # 峰值归档与推送解耦：只要拿到瓦片就解码并写入峰值档案，
        # 不依赖 emit_event / parse_stations，保证无推送时历史最大震度仍可更新。
        stations = self._get_or_decode_stations(tiles_payload)
        if stations is not None:
            raw_dict["stations"] = stations
            # 这里的 triggered 用于通过 parser_data，它需要支持两边的触发条件（低震度多站或高震度单站）
            # 所以使用较小的 fetch_min_shindo 作为基础过滤线，具体的过滤由 intensity_rule 负责。
            raw_dict["triggered"] = [
                s
                for s in stations
                if float(s.get("shindo", -999.0)) >= fetch_min_shindo
            ]
            await self._observe_station_peaks(
                stations,
                timestamp=str(tiles_payload.get("timestamp") or ""),
                hit_threshold=fetch_min_shindo,
            )

        if emit_event:
            await self._emit_event(raw_dict)

        return raw_dict

    async def _observe_station_peaks(
        self,
        stations: list[dict[str, Any]],
        *,
        timestamp: str,
        hit_threshold: float | None = None,
    ) -> None:
        """将本轮测站观测写入峰值档案（不依赖是否触发推送）。"""
        stats_manager = getattr(self.service, "statistics_manager", None)
        peak_service = (
            getattr(stats_manager, "snet_peak_service", None) if stats_manager else None
        )
        if peak_service is None:
            return
        try:
            if stats_manager is not None and not getattr(
                stats_manager, "_db_initialized", False
            ):
                await stats_manager.initialize()
            await peak_service.observe_stations(
                stations,
                observed_at=timestamp,
                hit_threshold=hit_threshold,
            )
            if stats_manager is not None and hasattr(stats_manager, "save_stats"):
                stats_manager.save_stats()
        except Exception as exc:
            logger.debug(f"[灾害预警] S-Net 峰值观测旁路写入失败: {exc}")

    async def fetch_for_query(
        self,
        *,
        min_shindo: float = 0.0,
        debug_mode: str | None = None,
    ) -> dict[str, Any] | None:
        """供 /snet 命令使用的即时抓取。

        debug_mode:
          - None: 正常下载（优先复用轮询快照缓存）
          - "random": 随机震度
          - "7"/"6+"/"6-"/...: 全站统一震度
        """
        if debug_mode:
            stations = self._build_debug_stations(debug_mode)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M00")
            triggered = [
                s for s in stations if float(s.get("shindo", -999.0)) >= min_shindo
            ]
            return {
                "timestamp": timestamp,
                "tiles": {},
                "min_shindo": min_shindo,
                "stations": stations,
                "triggered": triggered,
                "debug_mode": debug_mode,
            }

        return await self.fetch_once(
            emit_event=False,
            min_shindo=min_shindo,
            parse_stations=True,
        )

    def _candidate_timestamps(self) -> list[str]:
        """最近 3 个整分钟时间戳（UTC），与下载回退策略一致。"""
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        return [
            (now - timedelta(minutes=offset)).strftime("%Y%m%d%H%M00")
            for offset in range(3)
        ]

    def _snapshot_if_fresh(self) -> dict[str, Any] | None:
        """若内存快照仍在 TTL 内且属于最近 3 分钟之一，返回只读副本。"""
        snap = self._latest_snapshot
        if not snap:
            return None
        age = time.time() - float(snap.get("fetched_at") or 0.0)
        if age > self._resolve_tile_cache_ttl():
            return None
        ts = str(snap.get("timestamp") or "")
        if ts not in self._candidate_timestamps():
            return None
        tiles = snap.get("tiles")
        if not isinstance(tiles, dict) or len(tiles) < 2:
            return None
        return {
            "timestamp": ts,
            "tiles": tiles,
            "stations": snap.get("stations"),
            "from_cache": True,
        }

    def _store_snapshot(
        self,
        *,
        timestamp: str,
        tiles: dict[str, str],
        stations: list[dict[str, Any]] | None = None,
    ) -> None:
        """写入/刷新最近快照。"""
        prev = self._latest_snapshot
        # 同 timestamp 保留已解码测站，避免重复 PNG 解码
        if (
            stations is None
            and prev
            and prev.get("timestamp") == timestamp
            and isinstance(prev.get("stations"), list)
        ):
            stations = prev.get("stations")
        self._latest_snapshot = {
            "timestamp": timestamp,
            "tiles": tiles,
            "stations": stations,
            "fetched_at": time.time(),
        }

    async def _download_latest_tiles(self) -> dict[str, Any] | None:
        """下载最近可用的 MSIL 瓦片（带短时快照缓存）。"""
        async with self._fetch_lock:
            cached = self._snapshot_if_fresh()
            if cached is not None:
                return {
                    "timestamp": cached["timestamp"],
                    "tiles": cached["tiles"],
                }

            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            timeout = aiohttp.ClientTimeout(total=12)

            # MSIL 为公网 HTTPS，保持默认证书校验，避免中间人风险。
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for offset_min in range(3):
                    try_ts = now - timedelta(minutes=offset_min)
                    ts = try_ts.strftime("%Y%m%d%H%M00")
                    tiles: dict[str, str] = {}
                    failed_tiles = 0
                    for tile_name, tile_y in self.TILE_NAMES:
                        url = f"{MSIL_TILE_BASE}/{ts}/{ts}/5/28/{tile_y}.png"
                        try:
                            async with session.get(url) as resp:
                                if resp.status != 200:
                                    failed_tiles += 1
                                    continue
                                content = await resp.read()
                                if not content:
                                    failed_tiles += 1
                                    continue
                                tiles[tile_name] = base64.b64encode(content).decode(
                                    "ascii"
                                )
                        except Exception:
                            failed_tiles += 1
                    if failed_tiles and len(tiles) < 2:
                        logger.debug(
                            f"[灾害预警] S-Net 瓦片获取失败，共 {failed_tiles} 张"
                        )
                    if len(tiles) >= 2:
                        self._store_snapshot(timestamp=ts, tiles=tiles)
                        return {"timestamp": ts, "tiles": tiles}

            logger.warning("[灾害预警] S-Net 最近 3 分钟均未拿到完整瓦片")
            return None

    def _get_or_decode_stations(
        self, tiles_payload: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """解码测站列表；同 timestamp 复用快照中的解码结果。"""
        ts = str(tiles_payload.get("timestamp") or "")
        tiles = tiles_payload.get("tiles")
        if not isinstance(tiles, dict):
            return None

        snap = self._latest_snapshot
        if (
            snap
            and snap.get("timestamp") == ts
            and isinstance(snap.get("stations"), list)
        ):
            return copy.deepcopy(snap["stations"])

        stations = self._decode_stations(tiles)
        if stations is not None:
            self._store_snapshot(timestamp=ts, tiles=tiles, stations=stations)
            return copy.deepcopy(stations)
        return None

    @staticmethod
    def _decode_stations(tiles_b64: dict[str, str]) -> list[dict[str, Any]] | None:
        """把 base64 PNG 解码为测站列表。"""
        decoded = {}
        for tn in ("y11", "y12"):
            b64 = tiles_b64.get(tn)
            if not b64:
                continue
            try:
                png = base64.b64decode(b64)
                decoded[tn] = Image.open(io.BytesIO(png)).convert("RGB")
            except Exception as exc:
                logger.warning(f"[灾害预警] S-Net 瓦片解码失败 {tn}: {exc}")
        if not decoded:
            return None

        stations = _build_stations(decoded)
        normalized: list[dict[str, Any]] = []
        for item in stations:
            rgb = item.get("rgb")
            if isinstance(rgb, tuple):
                rgb = list(rgb)
            normalized.append(
                {
                    "name": str(item.get("name") or ""),
                    "lat": float(item.get("lat") or 0.0),
                    "lon": float(item.get("lon") or 0.0),
                    "shindo": float(item.get("shindo") or 0.0),
                    "rgb": rgb if isinstance(rgb, list) else None,
                    "tile": str(item.get("tile") or ""),
                    "px": int(item.get("px") or 0),
                    "py": int(item.get("py") or 0),
                }
            )
        return normalized

    @classmethod
    def _build_push_fingerprint(cls, triggered: list[dict[str, Any]]) -> str:
        """构建推送去重指纹：仅看震度最高的前 N 个测站（名称 + 震度）。

        不含瓦片时间戳，避免“每分钟时间变了但 Top-N 未变”仍刷屏。
        """
        ranked: list[tuple[str, float]] = []
        for item in triggered or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                shindo = round(float(item.get("shindo", -999.0)), 3)
            except (TypeError, ValueError):
                continue
            ranked.append((name, shindo))

        # 先按震度降序，同震度按名称稳定排序，再截取 Top-N
        ranked.sort(key=lambda row: (-row[1], row[0]))
        top_n = ranked[: cls.PUSH_DEDUP_TOP_N]
        return json.dumps(
            {
                "top": [{"name": name, "shindo": shindo} for name, shindo in top_n],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _emit_event(self, raw_dict: dict[str, Any]) -> None:
        """解析并送入统一事件流水线。"""
        # 指纹：仅比较触发测站中震度最高的前 5 个（名称+震度），无变化则不推送
        triggered = (
            raw_dict.get("triggered")
            if isinstance(raw_dict.get("triggered"), list)
            else []
        )
        if not triggered:
            return

        fingerprint = self._build_push_fingerprint(triggered)
        if fingerprint == self._last_payload_fingerprint:
            return

        message = json.dumps(raw_dict, ensure_ascii=False)
        event = self.service.parse_event(self.SOURCE_ID, message)
        if event is None:
            return

        event_id = getattr(event, "id", None)
        if event_id and event_id == self._last_event_id:
            # 同 event_id 且指纹已在上方判等；此处兜底防止重复投递
            if fingerprint == self._last_payload_fingerprint:
                return

        self._last_payload_fingerprint = fingerprint
        self._last_event_id = event_id
        await self.service._handle_disaster_event(event)

    @staticmethod
    def _build_debug_stations(mode: str) -> list[dict[str, Any]]:
        """构建调试用伪测站数据。"""
        mode = (mode or "").strip().lower()
        label_map = {
            "7": 7.0,
            "6+": 6.2,
            "6-": 5.7,
            "5+": 5.2,
            "5-": 4.7,
            "4": 4.0,
            "3": 3.0,
            "2": 2.0,
            "1": 1.0,
            "0": 0.0,
        }

        stations: list[dict[str, Any]] = []
        for name, (lat, lon) in SNET_REAL_COORDS.items():
            if mode == "random":
                shindo = round(random.uniform(0.0, 7.0), 3)
            else:
                shindo = float(label_map.get(mode, 0.0))
            stations.append(
                {
                    "name": name,
                    "lat": float(lat),
                    "lon": float(lon),
                    "shindo": shindo,
                    "rgb": None,
                    "tile": "debug",
                    "px": 0,
                    "py": 0,
                }
            )
        return stations
