"""
连接健康采样、日聚合与自动事故服务。

语义约定（管理端 Statuspage 风格）：
- 行 = 物理连接组（fan_studio_all / fan_studio_cenc_ir / p2p_main ...）
- degraded 不扣 uptime；partial_outage / major_outage 按 100% 计入中断
- 未启用 (not_monitored) 不进 uptime 分母、不进总横幅
- 闪断 < OPEN_INCIDENT_SECONDS 只记采样，不开 Past Incidents
- soft-degraded 需连续 DEGRADED_CONFIRM_SECONDS 才记入日聚合降级分钟
- 日格颜色严格按分钟阈值：不足阈值不染红/橙/黄（tooltip 仍展示实际分钟）
- 「通道建连中」仅在宽限期内且仍有未连通通道时展示；全员连通后立即退出
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ....utils.time_converter import TimeConverter
from ...network.admin.payloads.connections_payload_builder import (
    ConnectionsPayloadBuilder,
)
from ...storage.connection_health_repository import ConnectionHealthRepository
from ..query.source_runtime_query_service import SourceRuntimeQueryService

# 连接组展示顺序（与 ConnectionsGrid 列语义对齐）
COMPONENT_ORDER: tuple[str, ...] = (
    "fan_studio_all",
    "fan_studio_cenc_ir",
    "p2p_main",
    "wolfx_all",
    "openquake_api",
    "snet_msil",
    "eqsc",
)

COMPONENT_DISPLAY_NAMES: dict[str, str] = {
    "fan_studio_all": "FAN Studio",
    "fan_studio_cenc_ir": "FAN Studio（烈度速报）",
    "p2p_main": "P2P地震情報",
    "wolfx_all": "Wolfx",
    "openquake_api": "OpenQuakeAPI",
    "snet_msil": "NIED S-Net",
    "eqsc": "EQSC API",
}

# ConnectionsPayloadBuilder / SourceRuntimeQuery 可能使用的展示名别名
DISPLAY_NAME_ALIASES: dict[str, str] = {
    "FAN Studio": "fan_studio_all",
    "FAN Studio 烈度速报": "fan_studio_cenc_ir",
    "FAN Studio（烈度速报）": "fan_studio_cenc_ir",
    "Fan Studio（烈度速报）": "fan_studio_cenc_ir",
    "P2P地震情報": "p2p_main",
    "Wolfx": "wolfx_all",
    "OpenQuakeAPI": "openquake_api",
    "NIED S-Net": "snet_msil",
    "EQSC API": "eqsc",
}

STATE_LABELS_ZH: dict[str, str] = {
    "operational": "正常",
    "degraded": "降级",
    "partial_outage": "部分中断",
    "major_outage": "中断",
    "maintenance": "维护",
    "not_monitored": "未启用",
}

STATE_RANK: dict[str, int] = {
    "not_monitored": 0,
    "operational": 1,
    "degraded": 2,
    "maintenance": 2,
    "partial_outage": 3,
    "major_outage": 4,
}

# 日格着色阈值（分钟）：不足阈值保持绿色，tooltip 仍展示实际分钟。
# 1 分钟闪断/短时降级不应把整天染红/黄。
DAY_MAJOR_THRESHOLD_MIN = 5
DAY_PARTIAL_THRESHOLD_MIN = 5
DAY_DEGRADED_THRESHOLD_MIN = 15

# 事故开单：连续 major/partial 达到该秒数才开单
OPEN_INCIDENT_SECONDS = 180
# 恢复稳定秒数后关单
RESOLVE_INCIDENT_SECONDS = 120
# 闪断不记事故
FLAP_IGNORE_SECONDS = 60

# 高延迟视为 degraded 的阈值（ms）
HIGH_LATENCY_MS = 1500

# 服务启动/重载后的建连宽限期：此期间「已启用但未连通」记为降级(连接中)，
# 避免插件重载后几十秒内整页被误判为「核心通道中断」。
STARTUP_GRACE_SECONDS = 180

# 实时态 soft-degraded（retry/高延迟）需连续保持该秒数才记入日聚合。
# 瞬时重连、单次采样尖刺不会把日格染黄。
DEGRADED_CONFIRM_SECONDS = 180

# 日聚合 outage 防抖：major/partial 需连续保持该秒数才记入中断分钟。
# 与事故开单阈值对齐，避免单次采样把 uptime 和日格一起打歪。
OUTAGE_CONFIRM_SECONDS = 180


class ConnectionHealthService:
    """连接健康监控服务。"""

    def __init__(self, service, sample_interval_seconds: float = 60.0):
        """
        Args:
            service: DisasterWarningService 主服务实例。
            sample_interval_seconds: 周期采样间隔。
        """
        self.service = service
        self.sample_interval = max(15.0, float(sample_interval_seconds))
        self._task: asyncio.Task | None = None
        self._running = False
        self._repo: ConnectionHealthRepository | None = None
        # group_key -> 运行态边沿追踪
        self._trackers: dict[str, dict[str, Any]] = {}
        self._last_purge_at: float = 0.0
        self._display_tz = "UTC+8"
        # 完整 Statuspage 历史载荷短 TTL 缓存，降低管理端轮询对 DB 的压力
        self._history_cache: dict[str, Any] | None = None
        self._history_cache_key: str = ""
        self._history_cache_at: float = 0.0
        self._history_cache_ttl_seconds: float = 45.0

    # ──────────────────────────── 生命周期 ────────────────────────────

    def _ensure_repo(self) -> ConnectionHealthRepository | None:
        stats = getattr(self.service, "statistics_manager", None)
        if stats is None or not getattr(stats, "_db_initialized", False):
            return None
        db = getattr(stats, "db", None)
        if db is None:
            return None
        if self._repo is None or self._repo.db is not db:
            self._repo = ConnectionHealthRepository(db)
        return self._repo

    async def start(self) -> None:
        """启动后台采样循环。"""
        if self._running:
            return
        self._running = True
        # 启动时从 DB 回填未关闭事故，避免进程重启后 tracker 丢失导致重复开单。
        try:
            await self._hydrate_open_incidents()
        except Exception as exc:
            logger.warning(f"[灾害预警] 连接健康事故状态回填失败: {exc}")
        # 一次性修复历史 uptime_ratio 整除错误（中断分钟 > 0 却显示 100%）。
        try:
            repo = self._ensure_repo()
            if repo is not None:
                fixed = await repo.recompute_all_uptime_ratios()
                if fixed:
                    logger.debug(f"[灾害预警] 已重算连接健康日聚合可用性 {fixed} 条")
                    self._history_cache = None
                    self._history_cache_key = ""
                    self._history_cache_at = 0.0
        except Exception as exc:
            logger.warning(f"[灾害预警] 连接健康可用性重算失败: {exc}")
        self._task = asyncio.create_task(self._loop())
        logger.debug("[灾害预警] 连接健康采样服务已启动")

    async def stop(self) -> None:
        """停止后台采样循环。"""
        self._running = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"[灾害预警] 连接健康采样停止时异常: {exc}")
        logger.debug("[灾害预警] 连接健康采样服务已停止")

    async def _hydrate_open_incidents(self) -> None:
        """从数据库恢复各连接组未关闭事故到内存 tracker。"""
        repo = self._ensure_repo()
        if repo is None:
            return
        for group_key in COMPONENT_ORDER:
            open_inc = await repo.get_open_incident(group_key)
            if open_inc is None:
                continue
            tracker = self._trackers.setdefault(
                group_key,
                {
                    "bad_since": None,
                    "good_since": None,
                    "degraded_since": None,
                    "outage_since": None,
                    "outage_state": None,
                    "last_state": "not_monitored",
                    "open_incident_id": None,
                },
            )
            tracker["open_incident_id"] = int(open_inc.get("id") or 0) or None
            started = self._parse_iso(open_inc.get("started_at"))
            if started is not None:
                tracker["bad_since"] = started
            tracker["good_since"] = None

    async def _loop(self) -> None:
        # 启动后多等一会，让 WS/HTTP 通道先完成首轮建连与鉴权
        await asyncio.sleep(15.0)
        while self._running:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[灾害预警] 连接健康采样失败: {exc}")
            try:
                await asyncio.sleep(self.sample_interval)
            except asyncio.CancelledError:
                raise

    def _startup_grace_active(
        self, live_components: list[dict[str, Any]] | None = None
    ) -> bool:
        """服务启动后宽限期内返回 True（重载建连中）。

        提前结束条件：
        - 超过 STARTUP_GRACE_SECONDS
        - 或已启用通道全部连通（无需再把横幅钉在「通道建连中」）
        """
        start = getattr(self.service, "start_time", None)
        if start is None:
            # 无启动时间戳时不无限宽限，避免横幅永久「建连中」
            return False
        try:
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            age = (self._now_utc() - start.astimezone(timezone.utc)).total_seconds()
            if age >= STARTUP_GRACE_SECONDS:
                return False
        except Exception:
            return False

        if live_components is not None:
            enabled = [c for c in live_components if bool(c.get("enabled"))]
            # 尚无已启用通道时保持宽限；全部连通则提前结束
            if enabled and all(bool(c.get("connected")) for c in enabled):
                return False
        return True

    @staticmethod
    def _has_connecting_channels(live_components: list[dict[str, Any]]) -> bool:
        """是否仍有已启用但未连通的通道（建连中）。"""
        for comp in live_components:
            if bool(comp.get("enabled")) and not bool(comp.get("connected")):
                return True
        return False

    # ──────────────────────────── 时间工具 ────────────────────────────

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def _to_display(self, dt: datetime) -> datetime:
        return TimeConverter.convert_timezone(dt, self._display_tz)

    def _iso(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    def _format_display_datetime(self, dt: datetime) -> str:
        """格式化为管理端展示时区墙钟时间（UTC+8）。"""
        local = self._to_display(dt)
        return local.strftime("%Y-%m-%d %H:%M:%S")

    def _day_key(self, dt: datetime) -> str:
        local = self._to_display(dt)
        return local.strftime("%Y-%m-%d")

    # ──────────────────────────── 快照采集 ────────────────────────────

    def _latency_cache(self) -> dict[str, float | None]:
        web = getattr(self.service, "web_admin_server", None)
        if web is not None:
            cache = getattr(web, "_latency_cache", None)
            if isinstance(cache, dict):
                return cache
        return {}

    def _build_live_components(self) -> list[dict[str, Any]]:
        """从运行时连接状态构建各连接组当前健康快照。"""
        config = getattr(self.service, "config", {}) or {}
        latency_cache = self._latency_cache()
        builder = ConnectionsPayloadBuilder(
            disaster_service=self.service,
            config=config,
            latency_cache=latency_cache,
        )
        connections = builder.build()

        # 优先使用 payload 内稳定 group_key；展示名仅作兼容回退。
        by_display = connections if isinstance(connections, dict) else {}
        by_group: dict[str, dict[str, Any]] = {}
        for disp_name, info in by_display.items():
            if not isinstance(info, dict):
                continue
            explicit_key = str(info.get("group_key") or "").strip()
            if explicit_key:
                by_group[explicit_key] = info
                continue
            key = str(disp_name or "").strip()
            mapped = DISPLAY_NAME_ALIASES.get(key)
            if mapped:
                by_group[mapped] = info

        # 也读 raw WS status，补 retry / last_active
        raw_ws: dict[str, dict[str, Any]] = {}
        ws_manager = getattr(self.service, "ws_manager", None)
        if ws_manager is not None:
            try:
                raw_ws = ws_manager.get_all_connections_status() or {}
            except Exception:
                raw_ws = {}

        runtime_query = getattr(self.service, "source_runtime_query", None)
        if runtime_query is None:
            runtime_query = SourceRuntimeQueryService(config)

        group_status = {}
        try:
            group_status = runtime_query.build_connection_group_status() or {}
        except Exception:
            group_status = {}

        # 第一遍：只收集 enabled/connected，用于提前结束建连宽限
        prelim: list[dict[str, Any]] = []
        for group_key in COMPONENT_ORDER:
            display_name = COMPONENT_DISPLAY_NAMES.get(group_key, group_key)
            info = by_group.get(group_key) or by_display.get(display_name) or {}
            raw = raw_ws.get(group_key) or {}

            enabled = bool(info.get("enabled"))
            if not enabled:
                sub = group_status.get(group_key) or {}
                if isinstance(sub, dict) and any(bool(v) for v in sub.values()):
                    enabled = True

            connected = bool(info.get("connected"))
            if group_key in raw_ws and "connected" in raw:
                # WS 组以 raw 为准更贴近物理连接
                if group_key not in {"eqsc", "snet_msil"}:
                    connected = bool(raw.get("connected"))

            if not bool(getattr(self.service, "running", False)) and enabled:
                connected = False

            prelim.append(
                {
                    "group_key": group_key,
                    "display_name": display_name,
                    "info": info,
                    "raw": raw,
                    "enabled": enabled,
                    "connected": connected,
                }
            )

        # 全员连通时提前结束宽限，避免横幅卡在「通道建连中」
        in_grace = self._startup_grace_active(prelim)

        components: list[dict[str, Any]] = []
        for item in prelim:
            group_key = item["group_key"]
            display_name = item["display_name"]
            info = item["info"]
            raw = item["raw"]
            enabled = bool(item["enabled"])
            connected = bool(item["connected"])

            retry_count = int(info.get("retry_count") or raw.get("retry_count") or 0)
            circuit_open = bool(info.get("circuit_open"))
            latency = info.get("latency")
            if latency is None and group_key in latency_cache:
                latency = latency_cache.get(group_key)
            try:
                latency_ms = float(latency) if latency is not None else None
            except (TypeError, ValueError):
                latency_ms = None

            status_text = str(info.get("status") or "").strip()
            connection_type = str(
                info.get("connection_type")
                or raw.get("connection_type")
                or ("http" if group_key in {"eqsc", "snet_msil"} else "websocket")
            )

            state = self._classify_state(
                enabled=enabled,
                connected=connected,
                retry_count=retry_count,
                circuit_open=circuit_open,
                latency_ms=latency_ms,
                status_text=status_text,
                connection_type=connection_type,
                access_token_valid=info.get("access_token_valid"),
                startup_grace=in_grace,
            )

            # 插件整体未运行：已启用通道视为中断（避免停机期间“全绿”）
            if not bool(getattr(self.service, "running", False)) and enabled:
                state = "major_outage"
                connected = False

            # 宽限期内未连通：展示「连接中」而非「中断」
            if state == "degraded" and in_grace and enabled and not connected:
                status_label = "连接中"
            else:
                status_label = STATE_LABELS_ZH.get(state, state)

            components.append(
                {
                    "group_key": group_key,
                    "display_name": display_name,
                    "enabled": enabled,
                    "connected": connected,
                    "state": state,
                    "status_label": status_label,
                    "latency_ms": latency_ms,
                    "retry_count": retry_count,
                    "circuit_open": circuit_open,
                    "connection_type": connection_type,
                    "status_text": status_text,
                    "startup_grace": in_grace,
                }
            )
        return components

    @staticmethod
    def _classify_state(
        *,
        enabled: bool,
        connected: bool,
        retry_count: int,
        circuit_open: bool,
        latency_ms: float | None,
        status_text: str,
        connection_type: str,
        access_token_valid: Any,
        startup_grace: bool = False,
    ) -> str:
        if not enabled:
            return "not_monitored"

        text = (status_text or "").lower()
        if circuit_open or "熔断" in (status_text or ""):
            # 熔断是明确故障，宽限期也如实反映
            return "partial_outage"
        if connection_type == "http":
            if access_token_valid is False and "鉴权" in (status_text or ""):
                # 启动宽限期内 token 可能尚未预热完成 → 连接中
                if startup_grace:
                    return "degraded"
                return "partial_outage"
            if not connected:
                # 建连/鉴权预热中：降级而非中断
                if startup_grace:
                    return "degraded"
                return "major_outage"
            if latency_ms is not None and latency_ms >= HIGH_LATENCY_MS:
                return "degraded"
            return "operational"

        if not connected:
            # WS 建连宽限期：尚未握手成功不记 major
            if startup_grace:
                return "degraded"
            return "major_outage"
        if retry_count > 0:
            return "degraded"
        if latency_ms is not None and latency_ms >= HIGH_LATENCY_MS:
            return "degraded"
        if "备用" in (status_text or "") or "fallback" in text:
            return "degraded"
        return "operational"

    # ──────────────────────────── 采样主流程 ────────────────────────────

    def _aggregate_state_for_day(
        self,
        *,
        group_key: str,
        state: str,
        enabled: bool,
        now: datetime,
    ) -> str:
        """将实时态收敛为可写入日聚合的状态。

        - soft-degraded：连续 DEGRADED_CONFIRM_SECONDS 才记降级分钟
        - major/partial：连续 OUTAGE_CONFIRM_SECONDS 才记中断分钟
        未达确认窗口的样本仍计入 monitored，但不扣 uptime、不染日格。
        """
        tracker = self._trackers.setdefault(
            group_key,
            {
                "bad_since": None,
                "good_since": None,
                "degraded_since": None,
                "outage_since": None,
                "outage_state": None,
                "last_state": "not_monitored",
                "open_incident_id": None,
            },
        )
        if not enabled:
            tracker["degraded_since"] = None
            tracker["outage_since"] = None
            tracker["outage_state"] = None
            return "not_monitored"

        if state in {"major_outage", "partial_outage"}:
            tracker["degraded_since"] = None
            if (
                tracker.get("outage_since") is None
                or tracker.get("outage_state") != state
            ):
                # 严重度变化时重置确认窗口，避免 partial→major 误继承时长
                tracker["outage_since"] = now
                tracker["outage_state"] = state
            outage_for = (now - tracker["outage_since"]).total_seconds()
            if outage_for >= OUTAGE_CONFIRM_SECONDS:
                return state
            return "operational"

        tracker["outage_since"] = None
        tracker["outage_state"] = None

        if state == "degraded":
            if tracker.get("degraded_since") is None:
                tracker["degraded_since"] = now
            degraded_for = (now - tracker["degraded_since"]).total_seconds()
            if degraded_for >= DEGRADED_CONFIRM_SECONDS:
                return "degraded"
            return "operational"

        tracker["degraded_since"] = None
        return state

    async def sample_once(self) -> list[dict[str, Any]]:
        """执行一轮采样：写样本、累加日桶、推进事故状态机。"""
        repo = self._ensure_repo()
        if repo is None:
            return []

        now = self._now_utc()
        ts = self._iso(now)
        day = self._day_key(now)
        # 每样本代表的监控分钟（按采样间隔折算，上限 5 分钟防长暂停）。
        # 使用浮点分钟写入 REAL 列，避免 int(0.25)=0 导致日聚合永不累加。
        minutes = float(max(0.25, min(self.sample_interval / 60.0, 5.0)))

        components = self._build_live_components()
        samples: list[dict[str, Any]] = []

        for comp in components:
            group_key = comp["group_key"]
            state = comp["state"]
            enabled = bool(comp["enabled"])
            connected = bool(comp["connected"])
            # 日聚合用防抖后的状态；实时样本/事故机仍用原始 state
            aggregate_state = self._aggregate_state_for_day(
                group_key=group_key,
                state=state,
                enabled=enabled,
                now=now,
            )

            sample = {
                "group_key": group_key,
                "ts": ts,
                "state": state,
                "enabled": enabled,
                "connected": connected,
                "latency_ms": comp.get("latency_ms"),
                "retry_count": comp.get("retry_count") or 0,
                "circuit_open": bool(comp.get("circuit_open")),
                "detail": {
                    "status_text": comp.get("status_text"),
                    "connection_type": comp.get("connection_type"),
                    "display_name": comp.get("display_name"),
                    "aggregate_state": aggregate_state,
                },
            }
            samples.append(sample)

            # 日聚合：未启用不计入 monitored；降级需通过防抖确认
            day_row = {
                "group_key": group_key,
                "day": day,
                "minutes_monitored": minutes if enabled else 0.0,
                "minutes_major": (
                    minutes if enabled and aggregate_state == "major_outage" else 0.0
                ),
                "minutes_partial": (
                    minutes if enabled and aggregate_state == "partial_outage" else 0.0
                ),
                "minutes_degraded": (
                    minutes if enabled and aggregate_state == "degraded" else 0.0
                ),
                "worst_state": aggregate_state if enabled else "not_monitored",
                "sample_count": 1,
                "updated_at": ts,
            }
            try:
                await repo.upsert_day_aggregate(day_row)
            except Exception as exc:
                logger.debug(f"[灾害预警] 日聚合写入失败 {group_key}: {exc}")

            try:
                await self._advance_incident(repo, comp, now)
            except Exception as exc:
                logger.debug(f"[灾害预警] 事故状态推进失败 {group_key}: {exc}")

        try:
            await repo.insert_samples_batch(samples)
        except Exception as exc:
            logger.warning(f"[灾害预警] 健康采样批量写入失败: {exc}")

        # 每天最多清理一次旧数据
        try:
            mono = asyncio.get_running_loop().time()
            if mono - self._last_purge_at > 86400:
                await repo.purge_old_samples(keep_days=14)
                await repo.purge_old_days(keep_days=180)
                self._last_purge_at = mono
        except Exception:
            pass

        return components

    # ──────────────────────────── 事故状态机 ────────────────────────────

    async def _advance_incident(
        self,
        repo: ConnectionHealthRepository,
        comp: dict[str, Any],
        now: datetime,
    ) -> None:
        group_key = comp["group_key"]
        state = comp["state"]
        enabled = bool(comp["enabled"])
        display_name = comp.get("display_name") or COMPONENT_DISPLAY_NAMES.get(
            group_key, group_key
        )

        tracker = self._trackers.setdefault(
            group_key,
            {
                "bad_since": None,
                "good_since": None,
                "degraded_since": None,
                "outage_since": None,
                "outage_state": None,
                "last_state": "not_monitored",
                "open_incident_id": None,
            },
        )

        is_bad = enabled and state in {"major_outage", "partial_outage"}
        is_good = enabled and state in {"operational", "degraded"}

        # 始终按 group_key 查库恢复未关闭事故，避免仅依赖内存 id（进程重启后会丢）。
        open_inc = await repo.get_open_incident(group_key)
        if open_inc is not None:
            tracker["open_incident_id"] = int(open_inc.get("id") or 0) or None
            if tracker.get("bad_since") is None:
                started = self._parse_iso(open_inc.get("started_at"))
                if started is not None:
                    tracker["bad_since"] = started
        else:
            tracker["open_incident_id"] = None

        if is_bad:
            tracker["good_since"] = None
            if tracker["bad_since"] is None:
                tracker["bad_since"] = now
            bad_for = (now - tracker["bad_since"]).total_seconds()
            severity = "major_outage" if state == "major_outage" else "partial_outage"

            if open_inc is None and bad_for >= OPEN_INCIDENT_SECONDS:
                # 忽略极短闪断：若 bad_since 距今虽够，但中间曾恢复过由 good_since 重置
                title = (
                    f"{display_name} 中断"
                    if severity == "major_outage"
                    else f"{display_name} 部分中断"
                )
                timeline = [
                    {
                        "at": self._iso(tracker["bad_since"]),
                        "status": "investigating",
                        "message": f"检测到 {display_name} 进入{STATE_LABELS_ZH.get(state, state)}",
                    }
                ]
                incident_id = await repo.create_incident(
                    {
                        "group_key": group_key,
                        "severity": severity,
                        "status": "investigating",
                        "title": title,
                        "started_at": self._iso(tracker["bad_since"]),
                        "ended_at": None,
                        "timeline": timeline,
                    }
                )
                tracker["open_incident_id"] = incident_id
            elif open_inc is not None:
                # 可能升级 severity
                prev_sev = str(open_inc.get("severity") or "")
                if severity == "major_outage" and prev_sev != "major_outage":
                    timeline = list(open_inc.get("timeline") or [])
                    timeline.append(
                        {
                            "at": self._iso(now),
                            "status": "identified",
                            "message": f"{display_name} 升级为中断",
                        }
                    )
                    await repo.update_incident(
                        int(open_inc["id"]),
                        {
                            "severity": "major_outage",
                            "status": "identified",
                            "title": f"{display_name} 中断",
                            "timeline": timeline,
                        },
                    )
        elif is_good:
            tracker["bad_since"] = None
            if tracker["good_since"] is None:
                tracker["good_since"] = now
            good_for = (now - tracker["good_since"]).total_seconds()

            if open_inc is not None and good_for >= RESOLVE_INCIDENT_SECONDS:
                # 若事故总时长极短，仍关闭但 timeline 标注闪断恢复
                timeline = list(open_inc.get("timeline") or [])
                timeline.append(
                    {
                        "at": self._iso(now),
                        "status": "resolved",
                        "message": f"{display_name} 已恢复正常",
                    }
                )
                await repo.update_incident(
                    int(open_inc["id"]),
                    {
                        "status": "resolved",
                        "ended_at": self._iso(now),
                        "timeline": timeline,
                    },
                )
                tracker["open_incident_id"] = None
            elif open_inc is None and tracker.get("bad_since") is None:
                # 无事故：若曾短暂 bad 但未达开单阈值，自然丢弃
                pass
        else:
            # not_monitored：关闭进行中事故（用户关闭通道）
            tracker["bad_since"] = None
            tracker["good_since"] = None
            if open_inc is not None:
                timeline = list(open_inc.get("timeline") or [])
                timeline.append(
                    {
                        "at": self._iso(now),
                        "status": "resolved",
                        "message": f"{display_name} 已停用监控",
                    }
                )
                await repo.update_incident(
                    int(open_inc["id"]),
                    {
                        "status": "resolved",
                        "ended_at": self._iso(now),
                        "timeline": timeline,
                    },
                )
            tracker["open_incident_id"] = None

        tracker["last_state"] = state

    # ──────────────────────────── 查询 API 载荷 ────────────────────────────

    def _build_overall_block(
        self,
        *,
        monitored_states: list[str],
        now: datetime,
        live_components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        running = bool(getattr(self.service, "running", False))
        overall = self._overall_state(monitored_states, running=running)
        in_grace = self._startup_grace_active(live_components)
        still_connecting = bool(
            live_components and self._has_connecting_channels(live_components)
        )

        if not running:
            overall_label = "服务未运行"
        elif in_grace and still_connecting:
            # 仅当宽限期内仍有未连通通道时显示「通道建连中」；
            # 全员连通后立即退出，不再被 soft-degraded 卡住。
            overall_label = "通道建连中"
            # 横幅状态也按建连中展示，避免文案与颜色脱节
            if overall in {"operational", "degraded", "not_monitored"}:
                overall = "degraded"
        else:
            overall_label = self._overall_label(overall)
        return {
            "state": overall if running else "major_outage",
            "label": overall_label,
            "updated_at": self._iso(now),
            "updated_at_display": self._format_display_datetime(now),
            "running": running,
        }

    def _legend(self) -> list[dict[str, str]]:
        return [
            {"state": "operational", "label": "正常", "color": "green"},
            {"state": "degraded", "label": "降级", "color": "yellow"},
            {"state": "partial_outage", "label": "部分中断", "color": "orange"},
            {"state": "major_outage", "label": "中断", "color": "red"},
            {"state": "not_monitored", "label": "未启用", "color": "gray"},
        ]

    @staticmethod
    def _uptime_percent(uptime_ratio: float | None) -> float | None:
        if uptime_ratio is None:
            return None
        # 保留两位小数，与前端 toFixed(2) 对齐，避免假精度。
        return round(float(uptime_ratio) * 10000) / 100.0

    def build_live_status_payload(self) -> dict[str, Any]:
        """仅构建实时态（不读历史日聚合/事故），供高频轮询。"""
        now = self._now_utc()
        live_components = self._build_live_components()
        monitored_states: list[str] = []
        components_out: list[dict[str, Any]] = []
        for comp in live_components:
            state = str(comp.get("state") or "not_monitored")
            if state != "not_monitored":
                monitored_states.append(state)
            group_key = str(comp.get("group_key") or "")
            components_out.append(
                {
                    "group_key": group_key,
                    "name": comp.get("display_name")
                    or COMPONENT_DISPLAY_NAMES.get(group_key, group_key),
                    "current_state": state,
                    "current_label": comp.get("status_label")
                    or STATE_LABELS_ZH.get(state, state),
                    "enabled": bool(comp.get("enabled")),
                    "connected": bool(comp.get("connected")),
                    "latency_ms": comp.get("latency_ms"),
                    "retry_count": int(comp.get("retry_count") or 0),
                    "circuit_open": bool(comp.get("circuit_open")),
                }
            )
        return {
            "overall": self._build_overall_block(
                monitored_states=monitored_states,
                now=now,
                live_components=live_components,
            ),
            "legend": self._legend(),
            "meta": {
                "mode": "live",
                "timezone": self._display_tz,
                "sample_interval_seconds": self.sample_interval,
            },
            "components": components_out,
        }

    async def build_statuspage_payload(
        self, *, days: int = 90, mode: str = "full"
    ) -> dict[str, Any]:
        """构建管理端 Statuspage 风格载荷。

        mode:
        - full: 实时态 + 90 天条带 + 事故（历史部分短 TTL 缓存）
        - live: 仅实时态，不读库
        """
        mode_norm = str(mode or "full").strip().lower()
        if mode_norm == "live":
            return self.build_live_status_payload()

        days = max(1, min(int(days or 90), 180))
        now = self._now_utc()
        local_today = self._to_display(now).date()
        start_day = (local_today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        cache_key = f"full:{days}:{start_day}"

        live_components = self._build_live_components()
        live_by_key = {c["group_key"]: c for c in live_components}

        day_rows: list[dict[str, Any]] = []
        incidents: list[dict[str, Any]] = []
        use_history_cache = False
        try:
            mono = asyncio.get_running_loop().time()
        except RuntimeError:
            mono = 0.0
        if (
            self._history_cache is not None
            and self._history_cache_key == cache_key
            and mono
            and (mono - self._history_cache_at) < self._history_cache_ttl_seconds
        ):
            use_history_cache = True
            day_rows = list(self._history_cache.get("day_rows") or [])
            incidents = list(self._history_cache.get("incidents") or [])

        if not use_history_cache:
            repo = self._ensure_repo()
            if repo is not None:
                try:
                    day_rows = await repo.list_day_aggregates(
                        days=days,
                        group_keys=list(COMPONENT_ORDER),
                        since_day=start_day,
                    )
                except Exception as exc:
                    logger.warning(f"[灾害预警] 读取健康日聚合失败: {exc}")
                try:
                    incidents = await repo.list_incidents(days=max(days, 14), limit=200)
                except Exception as exc:
                    logger.warning(f"[灾害预警] 读取通道事故失败: {exc}")
            self._history_cache = {
                "day_rows": day_rows,
                "incidents": incidents,
            }
            self._history_cache_key = cache_key
            self._history_cache_at = mono

        # group -> day -> row
        day_map: dict[str, dict[str, dict[str, Any]]] = {}
        for row in day_rows:
            gk = str(row.get("group_key") or "")
            d = str(row.get("day") or "")
            if not gk or not d:
                continue
            day_map.setdefault(gk, {})[d] = row

        # 过滤 incidents 到窗口内（按 display tz 日）
        window_start_dt = datetime.combine(
            local_today - timedelta(days=days - 1),
            datetime.min.time(),
            tzinfo=TimeConverter._get_timezone(self._display_tz),
        )
        filtered_incidents: list[dict[str, Any]] = []
        for inc in incidents:
            started = self._parse_iso(inc.get("started_at"))
            if started is None:
                continue
            if started.astimezone(timezone.utc) < window_start_dt.astimezone(
                timezone.utc
            ) - timedelta(days=1):
                # 略放宽，避免边界丢失
                if (local_today - self._to_display(started).date()).days > days:
                    continue
            filtered_incidents.append(self._serialize_incident(inc))

        components_out: list[dict[str, Any]] = []
        monitored_states: list[str] = []

        for group_key in COMPONENT_ORDER:
            live = live_by_key.get(group_key) or {
                "group_key": group_key,
                "display_name": COMPONENT_DISPLAY_NAMES.get(group_key, group_key),
                "enabled": False,
                "connected": False,
                "state": "not_monitored",
                "status_label": "未启用",
                "latency_ms": None,
                "retry_count": 0,
                "circuit_open": False,
            }
            bars: list[dict[str, Any]] = []
            uptime_num = 0.0
            uptime_den = 0.0

            for offset in range(days):
                day_date = local_today - timedelta(days=days - 1 - offset)
                day_str = day_date.strftime("%Y-%m-%d")
                row = (day_map.get(group_key) or {}).get(day_str)
                bar = self._day_row_to_bar(
                    day_str, row, live if offset == days - 1 else None
                )
                bars.append(bar)
                mon = float((row or {}).get("minutes_monitored") or 0)
                if row is not None and mon > 0:
                    major = float(row.get("minutes_major") or 0)
                    partial = float(row.get("minutes_partial") or 0)
                    uptime_den += mon
                    uptime_num += max(0.0, mon - min(mon, major + partial))

            if uptime_den > 0:
                uptime_ratio = uptime_num / uptime_den
            else:
                uptime_ratio = None

            current_state = live.get("state") or "not_monitored"
            if current_state != "not_monitored":
                monitored_states.append(current_state)

            components_out.append(
                {
                    "group_key": group_key,
                    "name": live.get("display_name")
                    or COMPONENT_DISPLAY_NAMES.get(group_key, group_key),
                    "current_state": current_state,
                    "current_label": live.get("status_label")
                    or STATE_LABELS_ZH.get(current_state, current_state),
                    "enabled": bool(live.get("enabled")),
                    "connected": bool(live.get("connected")),
                    "latency_ms": live.get("latency_ms"),
                    "retry_count": int(live.get("retry_count") or 0),
                    "circuit_open": bool(live.get("circuit_open")),
                    "uptime_ratio": uptime_ratio,
                    "uptime_percent": self._uptime_percent(uptime_ratio),
                    "days": bars,
                }
            )

        overall = self._build_overall_block(
            monitored_states=monitored_states,
            now=now,
            live_components=live_components,
        )

        # Past incidents 按日分组
        incidents_by_day = self._group_incidents_by_day(
            filtered_incidents, local_today, history_days=14
        )

        return {
            "overall": overall,
            "legend": self._legend(),
            "meta": {
                "mode": "full",
                "days": days,
                "timezone": self._display_tz,
                "uptime_note": f"近 {days} 天可用性",
                "sample_interval_seconds": self.sample_interval,
                "history_cache_ttl_seconds": self._history_cache_ttl_seconds,
            },
            "components": components_out,
            "incidents": filtered_incidents[:100],
            "incidents_by_day": incidents_by_day,
        }

    def _day_row_to_bar(
        self,
        day: str,
        row: dict[str, Any] | None,
        live_today: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if row is None:
            # 今日尚无样本时，用 live 填一格弱提示
            if live_today and bool(live_today.get("enabled")):
                state = live_today.get("state") or "operational"
            else:
                state = "not_monitored"
            return {
                "day": day,
                "state": state,
                "minutes_monitored": 0,
                "minutes_major": 0,
                "minutes_partial": 0,
                "minutes_degraded": 0,
                "uptime_ratio": None,
            }

        # 分钟字段为 REAL，读取时用 float，避免截断亚分钟样本。
        monitored = float(row.get("minutes_monitored") or 0)
        major = float(row.get("minutes_major") or 0)
        partial = float(row.get("minutes_partial") or 0)
        degraded = float(row.get("minutes_degraded") or 0)

        # 日格颜色严格按阈值：不足阈值一律绿色。
        # tooltip 仍展示实际中断/降级分钟，避免「1 分钟就红」。
        if monitored <= 0:
            state = "not_monitored"
        elif major >= DAY_MAJOR_THRESHOLD_MIN:
            state = "major_outage"
        elif partial >= DAY_PARTIAL_THRESHOLD_MIN:
            state = "partial_outage"
        elif degraded >= DAY_DEGRADED_THRESHOLD_MIN:
            state = "degraded"
        else:
            state = "operational"

        # 始终按分钟字段重算可用性，避免历史整除错误或脏 uptime_ratio 误导 tooltip。
        uptime_ratio = ConnectionHealthRepository._compute_uptime_ratio(
            monitored, major, partial
        )

        return {
            "day": day,
            "state": state,
            "minutes_monitored": monitored,
            "minutes_major": major,
            "minutes_partial": partial,
            "minutes_degraded": degraded,
            "uptime_ratio": uptime_ratio,
        }

    @staticmethod
    def _overall_state(monitored_states: list[str], *, running: bool) -> str:
        if not running:
            return "major_outage"
        if not monitored_states:
            return "not_monitored"
        worst = "operational"
        for state in monitored_states:
            if STATE_RANK.get(state, 0) > STATE_RANK.get(worst, 0):
                worst = state
        return worst

    @staticmethod
    def _overall_label(state: str) -> str:
        mapping = {
            "operational": "全部通道正常",
            "degraded": "部分通道降级",
            "partial_outage": "部分通道异常",
            "major_outage": "核心通道中断",
            "not_monitored": "无已启用通道",
            "maintenance": "维护中",
        }
        return mapping.get(state, STATE_LABELS_ZH.get(state, state))

    def _serialize_incident(self, inc: dict[str, Any]) -> dict[str, Any]:
        group_key = str(inc.get("group_key") or "")
        started = self._parse_iso(inc.get("started_at"))
        ended = self._parse_iso(inc.get("ended_at"))
        duration_seconds = None
        if started is not None:
            end_ref = ended or self._now_utc()
            duration_seconds = max(
                0,
                int(
                    (
                        end_ref.astimezone(timezone.utc)
                        - started.astimezone(timezone.utc)
                    ).total_seconds()
                ),
            )
        return {
            "id": inc.get("id"),
            "group_key": group_key,
            "component_name": COMPONENT_DISPLAY_NAMES.get(group_key, group_key),
            "severity": inc.get("severity"),
            "severity_label": STATE_LABELS_ZH.get(
                str(inc.get("severity") or ""), str(inc.get("severity") or "")
            ),
            "status": inc.get("status"),
            "title": inc.get("title"),
            "started_at": inc.get("started_at"),
            "ended_at": inc.get("ended_at"),
            "duration_seconds": duration_seconds,
            "timeline": inc.get("timeline") or [],
        }

    def _group_incidents_by_day(
        self,
        incidents: list[dict[str, Any]],
        local_today,
        *,
        history_days: int = 14,
    ) -> list[dict[str, Any]]:
        """生成近 N 日的事故分组（无事故也占位）。"""
        by_day: dict[str, list[dict[str, Any]]] = {}
        for inc in incidents:
            started = self._parse_iso(inc.get("started_at"))
            if started is None:
                continue
            day = self._to_display(started).strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append(inc)

        result: list[dict[str, Any]] = []
        for offset in range(history_days):
            day_date = local_today - timedelta(days=offset)
            day_str = day_date.strftime("%Y-%m-%d")
            items = by_day.get(day_str) or []
            result.append(
                {
                    "day": day_str,
                    "label": day_date.strftime("%Y年%m月%d日"),
                    "incidents": items,
                    "empty_text": ("今日无通道事故" if offset == 0 else "无通道事故"),
                }
            )
        return result

    @staticmethod
    def _parse_iso(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
