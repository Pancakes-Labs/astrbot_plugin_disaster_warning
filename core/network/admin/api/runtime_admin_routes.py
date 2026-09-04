"""
Web 管理端运行态路由。
负责注册连接状态、配置摘要与重连等运行期接口，收敛 WebServerRuntimeService 中残留的路由实现。
"""

from __future__ import annotations

from astrbot.api import logger

from ....services.telemetry.telemetry_utils import track_feature_safely
from ..payloads.api_response import ApiResponse


def register_runtime_admin_routes(
    app,
    *,
    disaster_service,
    connections_payload_builder,
    config_payload_builder,
    expected_sources_getter,
    plugin=None,
):
    """注册运行态管理路由。

    plugin: 插件实例（可选）。注入后 Web 按钮可复用指令侧的重载/重启
        链路，保证与插件指令行为等价。
    """

    async def _track_admin_feature(feature_name: str, extra: dict | None = None):
        telemetry = getattr(disaster_service, "_telemetry", None)
        await track_feature_safely(
            telemetry,
            feature_name,
            extra,
            log_context="Web管理行为遥测",
        )

    @app.post("/api/reconnect")
    async def force_reconnect():
        """触发所有数据源立即重连。

        回执策略说明：本接口为纯同步触发路径——调用 reconnect_all_sources()
        时不传 request_id（默认空串）且不注册异步回执回调，因此：
        - 底层手动重连结果回调因空 request_id 会被下游直接忽略，不会产生异步回执；
        - 本接口只返回同步的触发结果（哪些连接已触发/跳过/失败），
          前端需真实重连结果可由下方连接矩阵状态实时展示。
        """
        try:
            guard_result = ApiResponse.guard_service_ready(disaster_service)
            if guard_result is not None:
                return guard_result

            results = await disaster_service.reconnect_all_sources()
            # 同时返回汇总结果与逐连接明细，便于前端展示总览提示和排障详情。
            triggered = sum(1 for s in results.values() if "已触发" in s)
            failed = sum(1 for s in results.values() if "失败" in s)
            await _track_admin_feature(
                "web_force_reconnect",
                {
                    "triggered_count": triggered,
                    "failed_count": failed,
                    "total_count": len(results),
                },
            )
            return ApiResponse.success(
                {
                    "success": True,
                    "message": f"操作完成: 触发 {triggered} 个重连, {failed} 个失败",
                    "details": results,
                }
            )
        except Exception as e:
            await _track_admin_feature("web_force_reconnect", {"failed": True})
            logger.error(f"[灾害预警] 通过Web端进行手动重连失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/connections")
    async def get_connections():
        """获取连接状态接口响应。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "ws_manager",
            )
            if guard_result is not None:
                return guard_result

            expected_sources = expected_sources_getter()
            return ApiResponse.success(
                connections_payload_builder.build_api_payload(expected_sources)
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取连接状态失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/config")
    async def get_config():
        """获取管理端使用的配置摘要。"""
        try:
            return ApiResponse.success(config_payload_builder.build_summary())
        except Exception as e:
            logger.error(f"[灾害预警] 获取配置失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/plugin/reload")
    async def reload_plugin():
        """重载灾害预警插件（等价于 /灾害预警重启 指令）。"""
        try:
            admin_service = getattr(plugin, "_admin_command_service", None)
            if admin_service is None:
                return ApiResponse.error("插件管理服务未就绪", status_code=503)

            ok, message = await admin_service.web_reload_plugin()
            if not ok:
                await _track_admin_feature("web_reload_plugin", {"success": False})
                return ApiResponse.error(message, status_code=500)

            await _track_admin_feature("web_reload_plugin", {"success": True})
            return ApiResponse.success({"success": True, "message": message})
        except Exception as e:
            await _track_admin_feature("web_reload_plugin", {"failed": True})
            logger.error(f"[灾害预警] Web端重载插件失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/astrbot/restart")
    async def restart_astrbot():
        """重启 AstrBot 进程（等价于 /重启AstrBot 指令）。"""
        try:
            admin_service = getattr(plugin, "_admin_command_service", None)
            if admin_service is None:
                return ApiResponse.error("插件管理服务未就绪", status_code=503)

            ok, message = await admin_service.web_restart_astrbot()
            if not ok:
                await _track_admin_feature("web_restart_astrbot", {"success": False})
                return ApiResponse.error(message, status_code=500)

            await _track_admin_feature("web_restart_astrbot", {"success": True})
            return ApiResponse.success({"success": True, "message": message})
        except Exception as e:
            await _track_admin_feature("web_restart_astrbot", {"failed": True})
            logger.error(f"[灾害预警] Web端重启 AstrBot 失败: {e}")
            return ApiResponse.error(str(e), status_code=500)
