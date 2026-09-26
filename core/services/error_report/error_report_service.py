"""
错误报告自动上传服务。

插件捕获到错误时（经 track_error_safely 统一挂钩），把脱敏后的错误报告
上传至自建 LogPaste 服务生成链接，并以 info 级别打印到日志，方便用户
直接拿链接提交 issue。

启用策略跟随遥测开关（telemetry_config.enabled）：用户拒绝匿名遥测时
同样不上传错误报告，保持对外发送口径一致。本服务只做附加动作，
不改变现有异常的抛出与日志行为。
"""

from __future__ import annotations

import asyncio
import platform
import time
import traceback
from datetime import datetime, timezone

from astrbot.api import logger

from ..paste.paste_client import format_expires_at, get_paste_client
from ....utils.log_sanitizer import sanitize_log_text
from ....utils.version import get_astrbot_version

# 错误报告整体字符上限与堆栈截断长度。
_MAX_REPORT_CHARS = 20000
_MAX_STACK_CHARS = 6000

# 同一「模块 + 异常类型」两次**成功**上传的最小间隔（秒），防错误风暴刷爆 paste 服务；
# 上传失败不进入冷却，下一条同类错误可立即重试（并发由 in-flight 去重兜底）。
_ERROR_THROTTLE_SECONDS = 60

# 节流记录表容量上限，超过时清理已过冷却期的旧键，防止长期运行无限增长。
_MAX_THROTTLE_KEYS = 256


class ErrorReportService:
    """错误报告构建与上传服务。"""

    def __init__(
        self,
        *,
        telemetry_enabled: bool,
        plugin_version: str,
        astrbot_version: str = "",
    ):
        # 启用状态跟随遥测开关，停机置位后拒绝新上传。
        self._telemetry_enabled = telemetry_enabled
        self._plugin_version = plugin_version
        self._astrbot_version = astrbot_version
        # 成功上传时间表（键 -> monotonic 时间戳）：冷却期仅从成功后起算。
        self._last_upload_times: dict[str, float] = {}
        # 上传进行中的键集合：同键并发去重，避免同一错误点重复生成多份报告。
        self._in_flight_keys: set[str] = set()
        self._closed = False

    @property
    def enabled(self) -> bool:
        """是否允许上传错误报告。"""
        return self._telemetry_enabled and not self._closed

    async def report_error(
        self, exception: BaseException, module: str | None = None
    ) -> str | None:
        """构建并上传错误报告，成功返回链接（并输出 info 日志），否则返回 None。

        重试策略：冷却时间戳**仅在成功生成链接后**记录——临时性上传失败不消耗
        冷却窗口，下一条同类错误会立即重试；上传进行中（in-flight）的同类错误
        直接跳过，保证同键同时最多一份在途上传，失败后也不会堆积并发重试。
        """
        if not self.enabled or not self._should_report(exception):
            return None

        throttle_key = f"{(module or 'unknown').lower()}:{type(exception).__name__}"
        now = time.monotonic()
        last = self._last_upload_times.get(throttle_key, 0.0)
        if now - last < _ERROR_THROTTLE_SECONDS:
            return None
        if throttle_key in self._in_flight_keys:
            # 同键上传进行中：跳过本次，不叠加并发上传。
            return None
        self._trim_throttle_table(now)
        self._in_flight_keys.add(throttle_key)

        try:
            report_text = self._build_report(exception, module)
            try:
                payload = await get_paste_client().upload_text(report_text)
            except Exception as e:
                # 上传失败仅留调试日志，不影响主流程；
                # 不记录冷却时间戳，下一条同类错误可立即重试。
                logger.debug(f"[灾害预警] 错误报告上传失败（已忽略）: {e}")
                return None

            url = str(payload.get("url") or "")
            if not url:
                return None
            expires_at = format_expires_at(payload.get("expires_at"))
            logger.info(
                f"[灾害预警] 已生成错误报告链接: {url}"
                f"（模块: {module or 'unknown'}，异常: {type(exception).__name__}，"
                f"过期: {expires_at}）"
            )
            # 成功才开始 60 秒冷却（从完成时刻起算）。
            self._last_upload_times[throttle_key] = time.monotonic()
            return url
        finally:
            self._in_flight_keys.discard(throttle_key)

    @staticmethod
    def _should_report(exception: BaseException) -> bool:
        """协程撤销/生成器回收/解释器退出不属于需要人工跟进的运行期错误。"""
        return not isinstance(
            exception, (asyncio.CancelledError, GeneratorExit, SystemExit)
        )

    def _trim_throttle_table(self, now: float) -> None:
        """节流记录超限时清理已过冷却期的旧键。"""
        if len(self._last_upload_times) <= _MAX_THROTTLE_KEYS:
            return
        cutoff = now - _ERROR_THROTTLE_SECONDS
        self._last_upload_times = {
            key: ts for key, ts in self._last_upload_times.items() if ts > cutoff
        }

    def _build_report(self, exception: BaseException, module: str | None) -> str:
        """构建脱敏错误报告文本。"""
        generated_at = (
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        )
        exc_type = type(exception).__name__
        message = str(exception) or "（无消息内容）"

        stack = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
        if len(stack) > _MAX_STACK_CHARS:
            stack = stack[:_MAX_STACK_CHARS] + "\n…（堆栈过长已截断）"
        if not stack.strip():
            stack = "（无堆栈信息）"

        lines = [
            "=== 灾害预警插件错误报告 ===",
            f"生成时间: {generated_at}",
            f"插件版本: {self._plugin_version}",
            f"AstrBot 版本: {self._astrbot_version or '未知'}",
            "运行环境: "
            f"Python {platform.python_version()} / "
            f"{platform.system()} {platform.release()} ({platform.machine()})",
            f"错误模块: {module or 'unknown'}",
            f"异常类型: {exc_type}",
            "",
            "--- 异常消息 ---",
            message,
            "",
            "--- 异常堆栈 ---",
            stack,
            "",
            "说明: 本报告由插件自动生成，已对凭据与本机路径脱敏，可直接附在 issue 中。",
        ]
        text = sanitize_log_text("\n".join(lines))
        if len(text) > _MAX_REPORT_CHARS:
            text = text[:_MAX_REPORT_CHARS] + "\n…（报告过长已截断）"
        return text


# 模块级单例：initialize 阶段统一装配，停机阶段置空。
_error_report_service: ErrorReportService | None = None


def configure_error_report_service(
    config: dict, plugin_version: str
) -> ErrorReportService:
    """按插件配置装配全局错误报告服务（initialize 时调用）。

    启用状态跟随遥测开关（telemetry_config.enabled）。
    """
    global _error_report_service
    telemetry_config = (
        config.get("telemetry_config", {}) if isinstance(config, dict) else {}
    )
    if not isinstance(telemetry_config, dict):
        telemetry_config = {}
    telemetry_enabled = bool(telemetry_config.get("enabled", True))
    try:
        astrbot_version = get_astrbot_version()
    except Exception:
        astrbot_version = ""

    _error_report_service = ErrorReportService(
        telemetry_enabled=telemetry_enabled,
        plugin_version=plugin_version,
        astrbot_version=astrbot_version,
    )
    logger.debug(
        "[灾害预警] 错误报告自动上传已"
        f"{'启用' if telemetry_enabled else '停用'}（跟随遥测开关）"
    )
    return _error_report_service


def get_error_report_service() -> ErrorReportService | None:
    """获取全局错误报告服务单例（未装配时返回 None）。"""
    return _error_report_service


# 独立调度错误报告上传的后台任务集合：
# 持强引用防止任务被 GC，完成（含失败/取消）后由回调自行移除。
_pending_report_tasks: set[asyncio.Task[None]] = set()


def _on_report_done(task: asyncio.Task[None]) -> None:
    """上传任务结束回调：移除强引用并取回异常，避免未处理异常告警噪声。"""
    _pending_report_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        # report_error 内部已全量吞异常，此处仅为兜底取回，理论上不会走到。
        pass


async def report_error_safely(
    exception: BaseException, *, module: str | None = None
) -> None:
    """安全生成错误报告链接（供 track_error_safely 统一挂钩）。

    上传以**独立后台任务**尽力执行（fire-and-forget）：本函数立即返回，
    不等待网络 I/O，粘贴服务缓慢或不可达时不会阻塞调用方的错误处理路径。
    未装配或停用时静默跳过；任何失败都吞掉，绝不影响调用方的异常处理流程。
    """
    service = _error_report_service
    if service is None:
        return
    try:
        task = asyncio.create_task(service.report_error(exception, module=module))
    except RuntimeError:
        # 无运行事件循环时无法调度（调用方均为异步上下文，不应发生），静默放弃。
        return
    _pending_report_tasks.add(task)
    task.add_done_callback(_on_report_done)


async def close_error_report_service() -> None:
    """关闭全局错误报告服务（幂等）。

    在途上传任务不等待、不取消：它们持有服务实例的局部引用，
    停机后若 paste 会话已关闭会自然失败并被内部吞掉（best-effort 语义）。
    """
    global _error_report_service
    if _error_report_service is not None:
        _error_report_service._closed = True
        _error_report_service = None
        logger.debug("[灾害预警] 已关闭错误报告自动上传服务")


__all__ = [
    "ErrorReportService",
    "close_error_report_service",
    "configure_error_report_service",
    "get_error_report_service",
    "report_error_safely",
]
