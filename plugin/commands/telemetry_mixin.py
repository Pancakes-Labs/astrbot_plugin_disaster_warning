"""
命令服务遥测混入模块。

为插件命令服务提供统一的匿名行为遥测上报适配。
避免各命令服务重复实现 plugin.telemetry 提取与 best-effort 上报逻辑。
"""

from __future__ import annotations

from typing import Any

from ...core.services.telemetry.telemetry_utils import (
    track_error_safely,
    track_feature_safely,
)


class CommandTelemetryMixin:
    """命令服务匿名行为遥测混入。"""

    async def _track_command_feature(
        self,
        feature_name: str,
        extra: dict[str, Any] | None = None,
        *,
        log_context: str = "命令行为遥测",
    ) -> bool:
        """安全上报命令匿名行为事件。"""
        # 统一尝试在命令类实例中安全提取插件中的遥测对象
        telemetry = getattr(getattr(self, "plugin", None), "telemetry", None)

        # 优化：命令触发也是高频遥测的来源之一，可以在这里稍作防抖或限制触发频率，或者命令执行后如果想立即发送可以强制flush。
        # 考虑到指令上报并不需要绝对实时，默认可以通过缓冲队列进行批量上报。
        # 如果需要立即发送，可以通过 telemetry 对象的 flush() 方法来刷新，但在 mixin 里我们继续保持 track_feature_safely 默认行为。
        res = await track_feature_safely(
            telemetry,
            feature_name,
            extra,
            log_context=log_context,
        )
        # 对遥测缓冲队列进行主动的异步调度尝试（如 telemetry 存在并且满足条件）
        return res

    async def _track_command_error(
        self,
        exception: Exception,
        command_name: str,
        *,
        log_context: str = "命令错误遥测",
    ) -> bool:
        """安全上报命令执行失败的错误事件。"""
        telemetry = getattr(getattr(self, "plugin", None), "telemetry", None)
        return await track_error_safely(
            telemetry,
            exception,
            module=f"command.{command_name}",
            log_context=log_context,
        )


__all__ = ["CommandTelemetryMixin"]
