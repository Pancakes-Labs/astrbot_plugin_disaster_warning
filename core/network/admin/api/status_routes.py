"""
Web 管理端状态与统计路由。
负责注册状态查询、统计查询与统计重置接口，减少 WebAdminServer 的路由定义体积。
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from astrbot.api import logger

from ..payloads.api_response import ApiResponse


def register_status_routes(
    app,
    *,
    disaster_service,
    realtime_payload_builder,
    password_getter=None,
):
    """注册状态与统计相关路由。"""

    @app.get("/api/status")
    async def get_status():
        """获取服务状态。"""
        try:
            guard_result = ApiResponse.guard_service_ready(disaster_service)
            if guard_result is not None:
                return guard_result
            # 直接复用实时载荷构建器中的状态快照逻辑，避免多个接口字段逐渐漂移。
            return ApiResponse.success(
                realtime_payload_builder.build_status_api_payload()
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取状态失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/connection-health")
    async def get_connection_health(days: int = 90, mode: str = "full"):
        """获取连接健康 Statuspage 载荷。

        mode=full: 实时态 + 90 天条带 + 事故
        mode=live: 仅实时态（供前端高频轮询）
        """
        try:
            guard_result = ApiResponse.guard_service_ready(disaster_service)
            if guard_result is not None:
                return guard_result

            health_service = getattr(
                disaster_service, "connection_health_service", None
            )
            if health_service is None:
                return ApiResponse.error("连接健康服务未就绪", status_code=503)

            # 允许 1–180；默认 90
            try:
                day_count = int(days or 90)
            except (TypeError, ValueError):
                day_count = 90
            day_count = max(1, min(day_count, 180))
            mode_norm = str(mode or "full").strip().lower()
            if mode_norm not in {"full", "live"}:
                mode_norm = "full"

            payload = await health_service.build_statuspage_payload(
                days=day_count,
                mode=mode_norm,
            )
            return ApiResponse.success(payload)
        except Exception as e:
            logger.error(f"[灾害预警] 获取连接健康状态失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/statistics")
    async def get_statistics():
        """获取统计数据。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return guard_result
            # 统计汇总数据通常由入库和启动加载机制保证内存态实时，无需在每次管理端页面访问时都执行耗时 500ms+ 的全量数据库扫描与重建
            return ApiResponse.success(
                realtime_payload_builder.build_statistics_api_payload()
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取统计失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/statistics/reset")
    async def reset_statistics(payload: dict[str, Any] | None = None):
        """清除统计数据（等价于 /灾害预警统计清除）。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return guard_result

            admin_password = str(password_getter() if password_getter else "")
            provided_password = str((payload or {}).get("password", ""))
            if admin_password and not secrets.compare_digest(
                provided_password, admin_password
            ):
                return ApiResponse.error(
                    "管理端密码错误，二次验证失败", status_code=401
                )

            await disaster_service.statistics_manager.reset_stats()

            return ApiResponse.success(
                {
                    "success": True,
                    "message": "统计数据已清除",
                    "timestamp": datetime.now().isoformat(),
                }
            )
        except Exception as e:
            logger.error(f"[灾害预警] 通过Web端清除统计失败: {e}")
            return ApiResponse.error(str(e), status_code=500)
