"""
模拟事件流执行器。

核心职责：按顺序执行 SimulationFlow 中的每一步：
1. 调用 SimulationBuilder 构建合法 EventEnvelope
2. 走 message_manager.push_event 直达展示推送链路（旁路统计入库 / 跑马灯广播）
3. 记录步骤级结果并生成全量执行报告

设计要点：
- 支持两种执行模式：
  - preview：仅构建消息并返回预览文本（不发送）
  - send：完整走推送链路发送到目标会话
- 步骤级容错：某步失败不中断整流，继续执行并标记失败
- 整流可在后台任务运行，前端通过 run_id 轮询进度
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from .flow_models import SimulationFlow, SimulationStep
from .simulation_builder import SimulationBuilder

# 进程内执行任务序号：生成稳定不重复的 run_id
_run_sequence = 0
_run_sequence_lock = threading.Lock()


def _next_run_sequence() -> int:
    """获取下一个执行序号（线程安全，单调递增）。"""
    global _run_sequence
    with _run_sequence_lock:
        _run_sequence += 1
        return _run_sequence


@dataclass(slots=True)
class StepExecutionResult:
    """单步执行结果。"""

    step_index: int
    step_id: str
    disaster_type: str
    source_id: str
    event_id: str = ""
    status: str = "pending"  # pending / running / success / skipped / failed
    message: str = ""
    preview_text: str = ""
    error: str = ""
    # 计划启动偏移秒数（保留字段：整流执行不再有步骤间延迟，恒为 0）
    scheduled_offset_seconds: float = 0.0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "step_id": self.step_id,
            "disaster_type": self.disaster_type,
            "source_id": self.source_id,
            "event_id": self.event_id,
            "status": self.status,
            "message": self.message,
            "preview_text": self.preview_text,
            "error": self.error,
            "scheduled_offset_seconds": self.scheduled_offset_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


@dataclass(slots=True)
class SimulationRun:
    """一次完整执行的运行态。"""

    run_id: str
    flow: SimulationFlow
    mode: str  # preview / send
    status: str = "pending"  # pending / running / completed / cancelled / failed
    step_results: list[StepExecutionResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "flow_id": getattr(self.flow, "flow_id", ""),
            "flow_name": self.flow.name,
            "mode": self.mode,
            "status": self.status,
            "step_results": [r.to_dict() for r in self.step_results],
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class SimulationRunner:
    """模拟事件流执行器。"""

    def __init__(
        self, message_manager, session_config_manager=None, progress_callback=None
    ):
        """
        Args:
            message_manager: MessagePushManager 实例（用于推送与消息构建）
            session_config_manager: 会话配置管理器（用于解析目标会话与获取运行时配置）
            progress_callback: 可选步骤进度回调 async (run: SimulationRun) -> None，
                每步状态变更后触发（用于管理端 WebSocket 实时进度推送）
        """
        self.message_manager = message_manager
        self.session_config_manager = session_config_manager
        self._progress_callback = progress_callback
        # 运行态索引：run_id -> SimulationRun
        self._runs: dict[str, SimulationRun] = {}
        self._runs_lock = threading.Lock()

    @staticmethod
    def _new_builder() -> SimulationBuilder:
        """创建独立构建器实例。

        每次执行（整流或单步）都使用全新实例，天然隔离 event_key 前缀映射：
        - 避免同一 event_key 在多次执行间复用同一事件 ID（跨 run 串扰）
        - 避免并发执行时共享映射导致的相互覆盖
        """
        return SimulationBuilder()

    # ------------------------------------------------------------------
    # 运行态管理
    # ------------------------------------------------------------------
    def get_run(self, run_id: str) -> SimulationRun | None:
        """查询执行运行态。"""
        with self._runs_lock:
            return self._runs.get(run_id)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近执行记录（降序）。"""
        with self._runs_lock:
            runs = sorted(
                self._runs.values(),
                key=lambda r: r.created_at,
                reverse=True,
            )
            return [r.to_dict() for r in runs[:limit]]

    def _register_run(self, run: SimulationRun) -> None:
        """登记运行态。"""
        with self._runs_lock:
            self._runs[run.run_id] = run

    def cancel_run(self, run_id: str) -> bool:
        """取消执行（只标记取消，由执行协程在步骤间隙检查）。"""
        with self._runs_lock:
            run = self._runs.get(run_id)
            if run is None:
                return False
            run.cancelled = True
            return True

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------
    def create_run(self, flow: SimulationFlow, *, mode: str = "send") -> SimulationRun:
        """创建并登记一次执行运行态（预生成步骤骨架，供前端立即展示）。"""
        run = SimulationRun(
            run_id=f"run_{int(datetime.now(timezone.utc).timestamp())}_{_next_run_sequence()}",
            flow=flow,
            mode=mode,
        )
        # 预生成所有步骤结果（pending 状态），便于前端立即看到步骤骨架。
        results = []
        for idx, step in enumerate(flow.steps):
            results.append(
                StepExecutionResult(
                    step_index=idx,
                    step_id=step.step_id,
                    disaster_type=step.disaster_type,
                    source_id=step.source_id,
                    scheduled_offset_seconds=0.0,
                )
            )
        run.step_results = results
        self._register_run(run)
        return run

    async def run_flow(
        self,
        flow: SimulationFlow,
        *,
        mode: str = "send",
        runtime_config_getter=None,
        run: SimulationRun | None = None,
    ) -> SimulationRun:
        """同步执行完整事件流（调用方负责 await，异常由本方法收敛到运行态）。

        Args:
            flow: 模拟流草稿
            mode: preview（仅构建预览）或 send（完整推送）
            runtime_config_getter: 会话级运行时配置获取器，缺省时回退到
                session_config_manager.get_effective_config 或全局配置
            run: 可选预创建的运行态（由 create_run 生成并登记），缺省自动创建
        """
        run = run or self.create_run(flow, mode=mode)

        # 每次整流执行使用独立构建器，天然隔离 event_key 前缀映射：
        # - 避免同一 event_key 在多次执行间复用同一事件 ID（跨 run 串扰）
        # - 避免并发执行时共享映射导致的相互覆盖
        builder = self._new_builder()
        # 流级目标会话：整流执行应使用 flow.target_session（前端显式配置），
        # 不再回退到首个配置会话。
        flow_target_session = str(flow.target_session or "").strip()

        try:
            run.status = "running"
            for idx, step in enumerate(flow.steps):
                # 步骤间隙检查取消
                if run.cancelled:
                    run.status = "cancelled"
                    break

                result = run.step_results[idx]
                result.status = "running"
                result.started_at = datetime.now(timezone.utc)
                await self._notify_progress(run)

                try:
                    if mode == "preview":
                        self._execute_step_preview(step, result, builder=builder)
                    else:
                        await self._execute_step_send(
                            step,
                            result,
                            runtime_config_getter=runtime_config_getter,
                            target_session=flow_target_session,
                            builder=builder,
                        )
                    result.status = "success"
                    result.finished_at = datetime.now(timezone.utc)
                    await self._notify_progress(run)
                except Exception as exc:
                    # 步骤级容错：记录失败，继续执行后续步骤
                    result.status = "failed"
                    result.error = str(exc)
                    result.finished_at = datetime.now(timezone.utc)
                    logger.error(f"[灾害预警] 模拟步骤 {step.step_id} 执行失败: {exc}")
                    await self._notify_progress(run)

            if run.status != "cancelled":
                run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            await self._notify_progress(run)
            return run
        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            logger.error(f"[灾害预警] 模拟流执行异常: {exc}")
            return run

    # ------------------------------------------------------------------
    # 进度回调
    # ------------------------------------------------------------------
    async def _notify_progress(self, run: SimulationRun) -> None:
        """触发进度回调（如管理端 WebSocket 实时推送）。"""
        callback = self._progress_callback
        if not callable(callback):
            return
        try:
            result = callback(run)
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            logger.debug(f"[灾害预警] 模拟进度回调失败（已忽略）: {exc}")

    # ------------------------------------------------------------------
    # 步骤执行
    # ------------------------------------------------------------------
    def _execute_step_preview(
        self,
        step: SimulationStep,
        result: StepExecutionResult,
        *,
        builder: SimulationBuilder | None = None,
    ) -> None:
        """预览模式：构建事件与消息链，返回预览文本（不发送）。"""
        builder = builder or self._new_builder()
        envelope = builder.build_step_envelope(step)
        result.event_id = envelope.id
        chain = self.message_manager.message_build_service.build_message(envelope)
        texts = [
            getattr(comp, "text", "")
            for comp in getattr(chain, "chain", [])
            if hasattr(comp, "text")
        ]
        preview = "\n".join(text for text in texts if text).strip()
        result.preview_text = preview
        result.message = "已构建预览（未发送）"

    async def _execute_step_send(
        self,
        step: SimulationStep,
        result: StepExecutionResult,
        *,
        runtime_config_getter=None,
        target_session: str = "",
        builder: SimulationBuilder | None = None,
    ) -> None:
        """发送模式：构建事件并走完整推送链路发送到目标会话。

        Args:
            target_session: 显式目标会话（整流执行时由 flow.target_session 注入）。
                为空时回退到步骤 params.target_session，再回退到首个配置会话。
            builder: 执行级构建器（整流执行传入，保证同 run 内 event_key 共享前缀）。
        """
        builder = builder or self._new_builder()
        envelope = builder.build_step_envelope(step)
        result.event_id = envelope.id

        if not target_session:
            target_session = str(step.params.get("target_session") or "").strip()
        if not target_session and self.session_config_manager is not None:
            target_sessions = self.session_config_manager.list_target_sessions()
            target_session = target_sessions[0] if target_sessions else ""

        if not target_session:
            raise ValueError("未配置目标会话，无法发送模拟事件")

        # 获取会话级运行时配置（推送链路的会话筛选需要）
        runtime_config = None
        if callable(runtime_config_getter):
            runtime_config = runtime_config_getter(target_session)
        elif self.session_config_manager is not None:
            try:
                runtime_config = self.session_config_manager.get_effective_config(
                    target_session
                )
            except Exception:
                runtime_config = None

        # 模拟标记：让推送链路的规则筛选放行
        if isinstance(runtime_config, dict):
            runtime_config = dict(runtime_config)
            runtime_config["__simulation_bypass_regular_filters"] = True

        # 先走规则链评估，输出拦截原因（与命令侧 handle_simulate_disaster 对齐）
        decision_text = ""
        try:
            evaluate = getattr(self.message_manager, "evaluate_push_decision", None)
            if callable(evaluate):
                final_decision = evaluate(
                    envelope,
                    runtime_config=runtime_config,
                    session_id=target_session,
                    emit_filter_log=False,
                    commit_state=False,
                )
                if final_decision is not None:
                    reason = str(getattr(final_decision, "reason", "") or "")
                    detail = str(getattr(final_decision, "detail", "") or "")
                    if getattr(final_decision, "accepted", False):
                        decision_text = f"规则链: ✅ 通过（{reason}）"
                    else:
                        suffix = f"（{detail}）" if detail else ""
                        decision_text = f"规则链: ❌ 拦截（{reason}{suffix}）"
        except Exception as exc:
            logger.debug(f"[灾害预警] 模拟规则链评估失败（已忽略）: {exc}")

        # 直达推送编排器（跳过事件流水线 → 天然旁路统计入库与跑马灯广播）
        push_result = await self.message_manager.push_event(
            envelope,
            target_sessions=[target_session],
            session_config_getter=self.session_config_manager.get_effective_config
            if self.session_config_manager is not None
            else None,
            commit_state=False,
            skip_dedup=True,
            bypass_fusion=True,
            return_details=True,
        )
        if push_result:
            # push_result 可能是 dict（return_details=True）
            if isinstance(push_result, dict):
                pushed = bool(push_result.get("success"))
                fail_reason = str(
                    push_result.get("final_failure_reason")
                    or push_result.get("reason")
                    or ""
                ).strip()
            else:
                pushed = bool(push_result)
                fail_reason = ""
            if pushed:
                result.message = f"✅ 模拟事件已推送到会话 {target_session}"
            else:
                reason_part = f"｜{fail_reason}" if fail_reason else ""
                result.message = (
                    f"⚠️ 模拟事件未产生实际推送（被会话筛选拦截{reason_part}）"
                )
        else:
            result.message = "⚠️ 模拟事件未产生实际推送（被会话筛选拦截）"

        if decision_text:
            result.message = f"{result.message}\n{decision_text}"
