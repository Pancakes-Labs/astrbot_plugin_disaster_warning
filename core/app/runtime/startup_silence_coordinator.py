"""
启动静默协调器。

用状态机替代固定秒数静默：在建连/首包/首轮轮询完成前吸收 bootstrap 噪音，
同时播种去重指纹，避免静默结束后整批误报。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from astrbot.api import logger

from ....utils.banner import print_startup_summary


class SilenceState(str, Enum):
    """启动静默状态。"""

    DISABLED = "disabled"
    # 待武装：AstrBot 加载完成前吸收事件并播种，但不开始计时/门闩
    PENDING = "pending"
    ARMING = "arming"
    PRIMING = "priming"
    READY = "ready"


@dataclass
class GateState:
    """单个就绪门闩状态。"""

    gate_id: str
    kind: str  # websocket | poll
    required: bool = True
    connected: bool = False
    primed: bool = False
    skipped: bool = False
    skip_reason: str = ""
    connected_at: float | None = None
    primed_at: float | None = None
    last_bootstrap_kind: str = ""
    # 轮询门闩：首轮抓取开始时间与是否正在抓取中。
    # 超时判定从实际开始抓取时间起算，避免 arm 到轮询启动之间的初始化耗时（数据库/浏览器/缓存加载）被误计入轮询超时。
    # fetching=True 时不触发超时放行，避免首轮抓取进行中被误放行。
    fetch_started_at: float | None = None
    fetching: bool = False

    @property
    def satisfied(self) -> bool:
        if not self.required or self.skipped:
            return True
        if self.kind == "websocket":
            # 已连接且收到首包/bootstrap，或已显式跳过
            return self.connected and self.primed
        # poll：完成至少一次成功抓取即视为 primed
        return self.primed


@dataclass
class StartupSilenceCoordinator:
    """启动静默状态机。

    状态流转：
    - DISABLED：配置关闭
    - ARMING：start() 后进入，等待注册门闩
    - PRIMING：建连/首轮轮询中，吸收事件并播种
    - READY：门闩满足 + settle，或硬超时

    时长判定统一使用单调时钟（event loop time），避免系统墙钟回拨
    导致硬超时失效；started_at / ready_at 仅用于展示。
    """

    # 时序参数（秒）
    # 目标：多数场景约 5 秒内结束静默（建连/首包 + 短 settle），
    # 同时保留硬超时兜底，避免慢源无限阻塞推送。
    min_silence_seconds: float = 0.5
    settle_seconds: float = 1.0
    # 硬超时兜底：留足余量（冷启动通常更久）；已就绪场景由 _evaluate_ready 提前结束，
    # 不会被本超时拖慢。门闩级 first_payload/first_poll 超时仍负责单个源提前放行。
    hard_timeout_seconds: float = 60.0
    first_payload_timeout_seconds: float = 2.0
    # 轮询门闩：武装后若长时间无成功首轮，按超时视为可跳过，避免拖到硬超时
    first_poll_timeout_seconds: float = 8.0
    # 待武装（PENDING）超时兜底：防止 on_astrbot_loaded 钩子丢失或时序竞态
    # 导致静默永久停在 PENDING 无限吸收事件，超时后尝试强制武装，失败则直接放行。
    pending_timeout_seconds: float = 180.0

    state: SilenceState = SilenceState.DISABLED
    enabled: bool = False
    started_at: datetime | None = None
    ready_at: datetime | None = None
    # 单调时钟起点，专用于超时/最小静默等时长计算
    started_mono: float | None = None
    ready_reason: str = ""
    absorbed_events: int = 0
    last_bootstrap_at: float | None = None

    gates: dict[str, GateState] = field(default_factory=dict)
    # 待武装（PENDING）期间记录的连接/就绪进度，arm() 时迁移到正式门闩，
    # 避免连接在 AstrBot 加载窗口内就绪后，arm 后门闩永远等不到回调。
    # - _pending_connected: 已建连（WebSocket），用于恢复 connected 状态；
    # - _pending_primed: 已收到首包/首轮成功（ws + poll 通用），用于恢复 primed；
    # - _pending_skipped: 已显式跳过（缺鉴权/熔断/轮询未启用），arm() 后恢复 skipped，
    #   避免 PENDING 期间的跳过进度在 gates.clear() 后丢失而干等硬超时。
    _pending_connected: set[str] = field(default_factory=set)
    _pending_primed: set[str] = field(default_factory=set)
    _pending_skipped: set[str] = field(default_factory=set)
    # 待武装（PENDING）开始时刻（单调钟），用于超时兜底判定
    _pending_started_mono: float | None = field(default=None, repr=False)
    _watchdog_task: asyncio.Task | None = field(default=None, repr=False)
    _service: Any = field(default=None, repr=False)

    def bind_service(self, service: Any) -> None:
        """绑定主服务，便于读取 running 标志。"""
        self._service = service

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _mono() -> float:
        return asyncio.get_running_loop().time()

    def is_silencing(self) -> bool:
        """是否仍应抑制推送/统计/原始日志。"""
        if not self.enabled:
            return False
        if self.state in {SilenceState.DISABLED, SilenceState.READY}:
            return False
        # 硬超时兜底：即使 watchdog 未跑，查询时也强制退出（单调钟）。
        # PENDING 状态 started_mono 为 None，不会触发超时。
        if self.started_mono is not None:
            age = self._try_mono() - self.started_mono
            if age >= self.hard_timeout_seconds:
                self._force_ready("hard_timeout_on_query")
                return False
        # 待武装（PENDING）超时兜底：即便 watchdog 未跑，查询时也强制退出，
        # 避免 on_astrbot_loaded 钩子丢失后静默永久停在 PENDING 无限吸收事件。
        if (
            self.state == SilenceState.PENDING
            and self._pending_started_mono is not None
        ):
            age = self._try_mono() - self._pending_started_mono
            if age >= self.pending_timeout_seconds:
                self._force_pending_escape()
                # 逃生可能成功重新武装（进入 ARMING/PRIMING），
                # 此时当前事件仍应被静默吸收，不应绕过刚武装的门闩；
                # 仅当逃生后状态为 READY/DISABLED 时才放行。
                return self.state in {
                    SilenceState.PENDING,
                    SilenceState.ARMING,
                    SilenceState.PRIMING,
                }
        return self.state in {
            SilenceState.PENDING,
            SilenceState.ARMING,
            SilenceState.PRIMING,
        }

    # 兼容旧名
    def is_in_silence_period(self) -> bool:
        return self.is_silencing()

    def resolve_enabled(self, debug_config: dict[str, Any] | None) -> bool:
        """从 debug_config 解析是否启用静默启动。

        优先 silent_startup；若缺失则兼容旧 startup_silence_duration：
        - duration > 0 → True
        - duration == 0 → False
        - 都缺失 → 默认 True
        """
        cfg = debug_config if isinstance(debug_config, dict) else {}
        if "silent_startup" in cfg:
            return bool(cfg.get("silent_startup"))
        legacy = cfg.get("startup_silence_duration")
        if isinstance(legacy, bool):
            return legacy
        if isinstance(legacy, (int, float)):
            return float(legacy) > 0
        return True

    def begin_deferred(self, enabled: bool = True) -> None:
        """进入待武装静默：吸收事件但不开始计时/门闩。

        用于首次启动/进程重启场景：服务先以吸收模式运行，
        等 AstrBot 加载完成钩子调用 arm() 后转入正式门闩流程，
        避免 AstrBot 加载窗口内的启动快照事件直接推送。
        """
        self.enabled = bool(enabled)
        if not self.enabled:
            self.state = SilenceState.DISABLED
            logger.debug("[灾害预警] 静默启动已关闭，事件将立即进入推送链路")
            return
        self.state = SilenceState.PENDING
        self.started_at = self._now()
        self.started_mono = None  # 待武装期间不计时，不触发硬超时
        self._pending_started_mono = self._try_mono()  # 记录 PENDING 起点用于超时兜底
        self.ready_at = None
        self.ready_reason = ""
        self.absorbed_events = 0
        self.last_bootstrap_at = None
        self.gates.clear()
        self._pending_connected.clear()
        self._pending_primed.clear()
        self._pending_skipped.clear()
        self._cancel_watchdog()
        self._start_watchdog()  # PENDING 阶段也启动 watchdog，提供超时逃生通道
        logger.debug("[灾害预警] 静默启动进入待武装状态（等待 AstrBot 加载完成钩子）")

    def arm(
        self,
        *,
        enabled: bool,
        expected_ws: list[str],
        expected_polls: list[str],
        hard_timeout_seconds: float | None = None,
    ) -> None:
        """服务 start() 时武装静默期并注册门闩。"""
        self.enabled = bool(enabled)
        # 允许调用方按场景覆盖硬超时（如插件重载时缩短，避免等满默认 30 秒）
        if hard_timeout_seconds is not None and hard_timeout_seconds > 0:
            self.hard_timeout_seconds = float(hard_timeout_seconds)
        self.started_at = self._now()
        self.started_mono = self._try_mono()
        self._pending_started_mono = None  # 已离开 PENDING，清理超时起点
        self.ready_at = None
        self.ready_reason = ""
        self.absorbed_events = 0
        self.last_bootstrap_at = None
        self.gates.clear()
        # 暂存待武装期间已建连/已就绪/已跳过的进度，注册正式门闩后迁移，避免回调错过。
        pending_connected = set(self._pending_connected)
        pending_primed = set(self._pending_primed)
        pending_skipped = set(self._pending_skipped)
        self._pending_connected.clear()
        self._pending_primed.clear()
        self._pending_skipped.clear()
        self._cancel_watchdog()

        if not self.enabled:
            self.state = SilenceState.DISABLED
            logger.debug("[灾害预警] 静默启动已关闭，事件将立即进入推送链路")
            return

        for name in expected_ws:
            gate_id = str(name or "").strip()
            if not gate_id:
                continue
            self.gates[gate_id] = GateState(gate_id=gate_id, kind="websocket")

        for name in expected_polls:
            gate_id = str(name or "").strip()
            if not gate_id:
                continue
            self.gates[gate_id] = GateState(gate_id=gate_id, kind="poll")

        self.state = SilenceState.ARMING
        # 迁移待武装期间已建连/已就绪的门闩进度：
        # - 已建连的 WebSocket → 恢复 connected（首包若也已到，则 primed 一并恢复）；
        # - 已 primed（收到首包 / 首轮成功）→ 无论 ws/poll 均恢复 primed，
        #   避免 PENDING 期间完成的首轮同步在 arm 后被丢弃而干等硬超时。
        now = self._try_mono()
        for name in pending_connected:
            gate = self.gates.get(name)
            if gate is None:
                gate = self._ensure_ws_gate(name)
            if gate is not None:
                gate.connected = True
                gate.connected_at = now
        for name in pending_primed:
            gate = self.gates.get(name)
            if gate is None:
                gate = self._ensure_ws_gate(name)
            if gate is not None:
                gate.connected = True
                gate.primed = True
                gate.primed_at = now
        for name in pending_skipped:
            gate = self.gates.get(name)
            if gate is None:
                gate = self._ensure_ws_gate(name)
            if gate is not None:
                gate.skipped = True
                gate.skip_reason = "skipped_in_pending"
                gate.primed = True
                gate.connected = True
        if pending_connected or pending_primed or pending_skipped:
            logger.debug(
                f"[灾害预警] 待武装期间数据源进度已迁移到正式门闩："
                f"已建连 {len(pending_connected)} 个、已就绪 {len(pending_primed)} 个、"
                f"已跳过 {len(pending_skipped)} 个"
            )
        if not self.gates:
            # 无任何上游门闩：最短静默后直接就绪
            self.state = SilenceState.PRIMING
            self.last_bootstrap_at = self._try_mono()
            logger.debug("[灾害预警] 静默启动已开启（无待同步数据源）")
        else:
            ws_n = sum(1 for g in self.gates.values() if g.kind == "websocket")
            poll_n = sum(1 for g in self.gates.values() if g.kind == "poll")
            parts: list[str] = []
            if ws_n:
                parts.append(f"{ws_n} 路连接")
            if poll_n:
                parts.append(f"{poll_n} 路轮询")
            scope = "、".join(parts) if parts else f"{len(self.gates)} 路数据源"
            logger.debug(
                f"[灾害预警] 静默启动已开启，等待 {scope}完成首轮同步"
                f"（超时时间 {self.hard_timeout_seconds:.0f} 秒）"
            )

        self._start_watchdog()
        self._evaluate_ready(reason_hint="arm")

    def disarm(self) -> None:
        """服务 stop() 时解除静默并清理。"""
        self._cancel_watchdog()
        self.state = SilenceState.DISABLED
        self.enabled = False
        self.gates.clear()
        self._pending_connected.clear()
        self._pending_primed.clear()
        self._pending_skipped.clear()
        self._pending_started_mono = None
        self.ready_reason = "disarmed"
        self.started_mono = None

    def note_connection_established(self, connection_name: str) -> None:
        """WebSocket 建连成功。"""
        if not self.is_silencing():
            return
        gate = self._ensure_ws_gate(connection_name)
        if gate is None:
            return
        if gate.skipped:
            return
        now = self._try_mono()
        gate.connected = True
        gate.connected_at = now
        if self.state == SilenceState.ARMING:
            self.state = SilenceState.PRIMING
        # 待武装期间仅记录已建连连接，等 arm() 后迁移到正式门闩
        if self.state == SilenceState.PENDING:
            self._pending_connected.add(gate.gate_id)
        logger.debug(f"[灾害预警] 静默门闩已建连: {gate.gate_id}")
        self._evaluate_ready(reason_hint=f"ws_connected:{gate.gate_id}")

    def note_connection_skipped(self, connection_name: str, reason: str = "") -> None:
        """某连接无法完成（缺鉴权/熔断等），不阻塞全局就绪。"""
        if not self.enabled or self.state == SilenceState.READY:
            return
        gate = self._ensure_ws_gate(connection_name)
        if gate is None:
            return
        gate.skipped = True
        gate.skip_reason = reason or "skipped"
        gate.primed = True
        gate.connected = True
        # 待武装期间登记跳过进度，等 arm() 后迁移到正式门闩，
        # 避免 gates.clear() 后 skipped 丢失而干等硬超时。
        if self.state == SilenceState.PENDING:
            self._pending_skipped.add(gate.gate_id)
        logger.debug(
            f"[灾害预警] 静默门闩已跳过: {gate.gate_id}"
            + (f" ({gate.skip_reason})" if gate.skip_reason else "")
        )
        self._evaluate_ready(reason_hint=f"ws_skipped:{gate.gate_id}")

    def note_bootstrap_payload(
        self,
        *,
        connection_name: str | None = None,
        gate_id: str | None = None,
        kind: str = "bootstrap",
    ) -> None:
        """标记某连接/门闩收到 bootstrap 或首包。"""
        if not self.is_silencing():
            return
        target = str(gate_id or connection_name or "").strip()
        if not target:
            return
        gate = self.gates.get(target)
        if gate is None and connection_name:
            gate = self._ensure_ws_gate(connection_name)
        if gate is None:
            return
        self._mark_primed(gate, bootstrap_kind=kind)

    def note_poll_fetch_started(self, poll_id: str) -> None:
        """HTTP 轮询开始首轮抓取。

        记录实际开始抓取的时间，作为 first_poll_timeout 的起算点。
        arm 到轮询启动之间有数据库初始化、浏览器启动等耗时操作，
        若从 arm 时间起算会导致轮询尚未开始抓取即被超时放行。
        """
        if not self.enabled or self.state == SilenceState.READY:
            return
        gate_id = str(poll_id or "").strip()
        if not gate_id:
            return
        gate = self.gates.get(gate_id)
        if gate is None:
            # 待武装（PENDING）阶段不与正式门闩混用：仅记录抓取进度，
            # 门闩标记为非强制，避免 PENDING 内创建的 required 门闩
            # 在后续被误当作正式就绪门闩参与判定。
            gate = GateState(
                gate_id=gate_id,
                kind="poll",
                required=self.state != SilenceState.PENDING,
            )
            self.gates[gate_id] = gate
        now = self._try_mono()
        gate.fetch_started_at = now
        gate.fetching = True
        if self.state == SilenceState.ARMING:
            self.state = SilenceState.PRIMING
        # 待武装期间无需登记：首轮成功由 note_poll_fetch_completed → _mark_primed 登记
        self._evaluate_ready(reason_hint=f"poll_fetch_started:{gate_id}")

    def note_poll_fetch_completed(self, poll_id: str, *, success: bool = True) -> None:
        """HTTP 轮询完成首轮（成功或确认可跳过）。"""
        if not self.enabled or self.state == SilenceState.READY:
            return
        gate_id = str(poll_id or "").strip()
        if not gate_id:
            return
        gate = self.gates.get(gate_id)
        if gate is None:
            # 与 note_poll_fetch_started 对齐：PENDING 阶段创建的门闩不强制 required，
            # 仅承载抓取进度记录，不参与正式就绪判定。
            gate = GateState(
                gate_id=gate_id,
                kind="poll",
                required=self.state != SilenceState.PENDING,
            )
            self.gates[gate_id] = gate
        # 无论成功失败，都清除抓取中标志，允许 watchdog 超时判定。
        gate.fetching = False
        if not success:
            # 失败不直接 primed；watchdog 的 first_poll_timeout / 硬超时会兜底。
            return
        self._mark_primed(gate, bootstrap_kind="poll_first_fetch")

    def note_poll_skipped(self, poll_id: str, reason: str = "") -> None:
        """轮询源未启用或无法运行。"""
        if not self.enabled or self.state == SilenceState.READY:
            return
        gate_id = str(poll_id or "").strip()
        if not gate_id:
            return
        gate = self.gates.get(gate_id)
        if gate is None:
            gate = GateState(gate_id=gate_id, kind="poll")
            self.gates[gate_id] = gate
        gate.skipped = True
        gate.skip_reason = reason or "disabled"
        gate.primed = True
        # 待武装期间登记跳过进度，等 arm() 后迁移到正式门闩，
        # 避免 gates.clear() 后 skipped 丢失而干等硬超时。
        if self.state == SilenceState.PENDING:
            self._pending_skipped.add(gate.gate_id)
        self._evaluate_ready(reason_hint=f"poll_skipped:{gate_id}")

    def note_event_absorbed(
        self,
        event: Any = None,
        *,
        connection_name: str | None = None,
        bootstrap_kind: str = "",
    ) -> None:
        """静默期吸收一条事件（已播种后调用）。"""
        if not self.is_silencing():
            return
        self.absorbed_events += 1
        self.last_bootstrap_at = self._try_mono()
        if self.state == SilenceState.ARMING:
            self.state = SilenceState.PRIMING
        # 待武装状态只吸收与播种，不做就绪判定；正式 arm() 后才进入门闩流程
        if self.state == SilenceState.PENDING:
            return

        meta = {}
        if event is not None and isinstance(getattr(event, "metadata", None), dict):
            meta = event.metadata
        conn = (
            connection_name
            or str(
                (meta.get("connection_info") or {}).get("connection_name") or ""
            ).strip()
            or str(meta.get("connection_name") or "").strip()
        )
        kind = (
            bootstrap_kind
            or str(meta.get("bootstrap_kind") or "").strip()
            or ("bootstrap" if meta.get("bootstrap") else "event")
        )
        if conn and conn in self.gates:
            self._mark_primed(self.gates[conn], bootstrap_kind=kind)
        elif conn:
            gate = self._ensure_ws_gate(conn)
            if gate is not None:
                gate.connected = True
                self._mark_primed(gate, bootstrap_kind=kind)

        self._evaluate_ready(reason_hint="event_absorbed")

    def get_status(self) -> dict[str, Any]:
        """供管理端/日志使用的状态快照。"""
        pending = [
            g.gate_id for g in self.gates.values() if g.required and not g.satisfied
        ]
        age = None
        if self.started_mono is not None:
            age = self._try_mono() - self.started_mono
        return {
            "enabled": self.enabled,
            "state": self.state.value,
            "silencing": self.is_silencing(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ready_at": self.ready_at.isoformat() if self.ready_at else None,
            "ready_reason": self.ready_reason,
            "absorbed_events": self.absorbed_events,
            "elapsed_seconds": age,
            "pending_gates": pending,
            "gates": {
                gid: {
                    "kind": g.kind,
                    "required": g.required,
                    "connected": g.connected,
                    "primed": g.primed,
                    "skipped": g.skipped,
                    "skip_reason": g.skip_reason,
                    "satisfied": g.satisfied,
                    "last_bootstrap_kind": g.last_bootstrap_kind,
                    "fetch_started_at": g.fetch_started_at,
                    "fetching": g.fetching,
                }
                for gid, g in self.gates.items()
            },
            "min_silence_seconds": self.min_silence_seconds,
            "settle_seconds": self.settle_seconds,
            "hard_timeout_seconds": self.hard_timeout_seconds,
            "first_payload_timeout_seconds": self.first_payload_timeout_seconds,
            "first_poll_timeout_seconds": self.first_poll_timeout_seconds,
        }

    # ── 内部 ──────────────────────────────────────────────

    def _try_mono(self) -> float:
        try:
            return self._mono()
        except RuntimeError:
            # 无运行中的事件循环时退回 wall clock 相对值（仅兜底）
            return self._now().timestamp()

    def _ensure_ws_gate(self, connection_name: str) -> GateState | None:
        gate_id = str(connection_name or "").strip()
        if not gate_id:
            return None
        gate = self.gates.get(gate_id)
        if gate is None:
            # 计划外连接（例如运行中热启用）也纳入观察，但不强制 required
            gate = GateState(gate_id=gate_id, kind="websocket", required=False)
            self.gates[gate_id] = gate
        return gate

    def _mark_primed(self, gate: GateState, *, bootstrap_kind: str = "") -> None:
        now = self._try_mono()
        if not gate.connected and gate.kind == "websocket":
            gate.connected = True
            gate.connected_at = now
        gate.primed = True
        gate.primed_at = now
        if bootstrap_kind:
            gate.last_bootstrap_kind = bootstrap_kind
        self.last_bootstrap_at = now
        if self.state == SilenceState.ARMING:
            self.state = SilenceState.PRIMING
        # 待武装期间登记已就绪门闩，等 arm() 后迁移到正式门闩
        if self.state == SilenceState.PENDING:
            self._pending_primed.add(gate.gate_id)
        self._evaluate_ready(reason_hint=f"primed:{gate.gate_id}")

    def _all_gates_satisfied(self) -> bool:
        if not self.gates:
            return True
        return all(g.satisfied for g in self.gates.values() if g.required)

    def _min_silence_elapsed(self) -> bool:
        if self.started_mono is None:
            return True
        return (self._try_mono() - self.started_mono) >= self.min_silence_seconds

    def _settle_elapsed(self) -> bool:
        if self.last_bootstrap_at is None:
            # 尚无任何 bootstrap：若门闩已全满足（例如全 skip），仍要求 min silence
            return self._all_gates_satisfied()
        return (self._try_mono() - self.last_bootstrap_at) >= self.settle_seconds

    @staticmethod
    def _format_ready_reason(reason: str) -> str:
        """把内部 reason 转成用户可读说明（日志用）。"""
        text = str(reason or "").strip()
        if not text:
            return "数据源已就绪"
        if text.startswith("gates_ok"):
            return "数据源已就绪"
        if text.startswith("hard_timeout"):
            return "等待超时"
        if text == "disarmed":
            return "服务已停止"
        return text

    def _evaluate_ready(self, *, reason_hint: str = "") -> None:
        if not self.enabled or self.state == SilenceState.READY:
            return
        # 待武装状态不参与就绪判定，需等正式 arm() 后转入门闩流程
        if self.state == SilenceState.PENDING:
            return
        if not self._min_silence_elapsed():
            return
        if not self._all_gates_satisfied():
            return
        if not self._settle_elapsed():
            return
        # 内部仍保留 hint，便于状态快照排查；日志侧会翻译成可读文案
        reason = "gates_ok"
        if reason_hint:
            reason = f"gates_ok:{reason_hint}"
        self._force_ready(reason)

    def _force_pending_escape(self) -> None:
        """PENDING 超时逃生：尝试正式武装，失败则直接放行。"""
        self._cancel_watchdog()
        # 若主服务已运行，尝试按当前连接计划正式武装；
        # 否则直接放行，避免静默永久停在 PENDING 无限吸收事件。
        service = self._service
        if service is not None and getattr(service, "running", False):
            lifecycle = getattr(service, "lifecycle_service", None)
            if lifecycle is not None:
                arm = getattr(lifecycle, "arm_startup_silence", None)
                if callable(arm):
                    try:
                        arm()
                    except Exception as exc:
                        logger.debug(
                            f"[灾害预警] PENDING 超时强制武装失败（已忽略）: {exc}"
                        )
        # 无论武装是否成功，都确保状态离开 PENDING，避免无限吸收。
        if self.state == SilenceState.PENDING:
            self._force_ready("pending_timeout_escape")
        logger.warning(
            "[灾害预警] 待武装静默超时（PENDING 超时兜底），已强制结束静默，"
            "防止灾害事件被无限期吸收"
        )

    def _force_ready(self, reason: str) -> None:
        if self.state == SilenceState.READY and self.enabled:
            return
        if not self.enabled and self.state == SilenceState.DISABLED:
            return
        pending = [
            g.gate_id for g in self.gates.values() if g.required and not g.satisfied
        ]
        self.state = SilenceState.READY
        self.ready_at = self._now()
        self.ready_reason = reason
        self._cancel_watchdog()
        why = self._format_ready_reason(reason)
        absorbed = self.absorbed_events
        if pending:
            pending_text = "、".join(pending)
            logger.warning(
                f"[灾害预警] 静默启动结束（{why}），"
                f"仍有未就绪：{pending_text}；"
                f"已忽略启动快照 {absorbed} 条，开始正常推送"
            )
        elif absorbed > 0:
            logger.debug(
                f"[灾害预警] 静默启动结束（{why}），"
                f"已忽略启动快照 {absorbed} 条，开始正常推送"
            )
        else:
            logger.debug(f"[灾害预警] 静默启动结束（{why}），开始正常推送")

        # 静默真正结束时（WS 连接、ready_at 均已落定）打印启动汇总大屏。
        # 此前在 start() 末尾打印会拿到未建立的连接与未落定的耗时，导致统计失真。
        service = self._service
        if service is not None:
            try:
                print_startup_summary(service)
            except Exception as banner_err:
                logger.debug(f"[灾害预警] 启动汇总大屏打印失败（已忽略）: {banner_err}")

    def _start_watchdog(self) -> None:
        self._cancel_watchdog()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _watch() -> None:
            try:
                while self.enabled and self.state not in {
                    SilenceState.READY,
                    SilenceState.DISABLED,
                }:
                    if self._service is not None and not getattr(
                        self._service, "running", True
                    ):
                        break
                    now = self._try_mono()
                    # 正式门闩超时放行仅限 ARMING/PRIMING 阶段：
                    # PENDING 只吸收与播种、不做就绪判定，若在此阶段把「未成功首轮」
                    # 的轮询门闩经超时标记为 primed，会被 _pending_primed 迁移到
                    # arm() 后的正式门闩，导致没有成功首轮抓取就提前结束启动静默。
                    # 只有真正成功首轮（note_poll_fetch_completed success=True）才应
                    # 在 PENDING 中登记到 _pending_primed。
                    if self.state != SilenceState.PENDING:
                        # 建连后长时间无首包 → 视为空闲就绪
                        for gate in list(self.gates.values()):
                            if (
                                gate.kind == "websocket"
                                and gate.required
                                and not gate.skipped
                                and gate.connected
                                and not gate.primed
                                and gate.connected_at is not None
                                and (now - gate.connected_at)
                                >= self.first_payload_timeout_seconds
                            ):
                                self._mark_primed(
                                    gate, bootstrap_kind="first_payload_timeout"
                                )
                            # 轮询门闩：开始抓取后长时间无成功首轮 → 超时放行，避免拖满硬超时。
                            # 超时从实际开始抓取时间起算（fetch_started_at），
                            # 而非 arm 时间，避免初始化耗时被误计入。
                            # fetching=True（正在抓取中）时不触发超时，避免首轮抓取
                            # 耗时较长（如台风 HTTP + 渲染）时被误放行。
                            if (
                                gate.kind == "poll"
                                and gate.required
                                and not gate.skipped
                                and not gate.primed
                                and gate.fetch_started_at is not None
                                and not gate.fetching
                                and (now - gate.fetch_started_at)
                                >= self.first_poll_timeout_seconds
                            ):
                                self._mark_primed(
                                    gate, bootstrap_kind="poll_first_fetch_timeout"
                                )
                    # 待武装（PENDING）超时兜底：watchdog 循环内主动检查，
                    # 即使 is_silencing 查询路径未被调用也能及时逃生。
                    if (
                        self.state == SilenceState.PENDING
                        and self._pending_started_mono is not None
                    ):
                        age = now - self._pending_started_mono
                        if age >= self.pending_timeout_seconds:
                            self._force_pending_escape()
                            break
                    # 硬超时（单调钟）
                    if self.started_mono is not None:
                        age = now - self.started_mono
                        if age >= self.hard_timeout_seconds:
                            self._force_ready("hard_timeout")
                            break
                    self._evaluate_ready(reason_hint="watchdog")
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"[灾害预警] 静默启动 watchdog 异常: {exc}")

        self._watchdog_task = loop.create_task(
            _watch(), name="dw_startup_silence_watchdog"
        )
        if self._service is not None and hasattr(
            self._service, "register_background_task"
        ):
            self._service.register_background_task(self._watchdog_task)

    def _cancel_watchdog(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()


__all__ = ["SilenceState", "GateState", "StartupSilenceCoordinator"]
