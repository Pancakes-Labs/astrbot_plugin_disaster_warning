"""
遥测安全辅助工具。

提供跨模块复用的 best-effort 遥测调用封装
避免各业务模块重复实现启用检查、异常吞噬与调试日志输出逻辑。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..error_report.error_report_service import report_error_safely


async def track_feature_safely(
    telemetry: Any,
    feature_name: str,
    extra: dict[str, Any] | None = None,
    *,
    log_context: str = "遥测行为事件",
) -> bool:
    """安全上报匿名行为事件。"""
    # 优先检查遥测模块实体是否存在及其内部开启状态
    if not telemetry or not getattr(telemetry, "enabled", False):
        return False
    try:
        return bool(await telemetry.track_feature(feature_name, extra or {}))
    except Exception as exc:
        logger.debug(f"[灾害预警] {log_context}上报失败（已忽略）: {exc}")
        return False


async def track_error_safely(
    telemetry: Any,
    exception: Exception,
    *,
    module: str | None = None,
    log_context: str = "遥测错误事件",
) -> bool:
    """安全上报错误事件。"""
    # 附加动作：上传脱敏错误报告并生成链接（info 日志）。
    # 内部以独立后台任务尽力执行（立即返回，不等待网络 I/O），
    # 自带开关（跟随遥测）/节流/噪声过滤且全量吞异常，
    # 不阻塞下方原有遥测逻辑与调用方的错误处理路径。
    await report_error_safely(exception, module=module)
    if not telemetry or not getattr(telemetry, "enabled", False):
        return False
    # 统一转换模块前缀为小写，确保上报规范
    normalized_module = str(module).lower() if module is not None else None
    try:
        return bool(await telemetry.track_error(exception, module=normalized_module))
    except Exception as exc:
        logger.debug(f"[灾害预警] {log_context}上报失败（已忽略）: {exc}")
        return False


__all__ = ["track_feature_safely", "track_error_safely"]
