"""
运行态数据与测试接口路由。
承接气象查询、模拟参数、模拟推送与地理定位接口，
进一步缩减 WebAdminServer 的内联路由规模。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from astrbot.api import logger

from .....utils.geolocation import fetch_location_from_ip
from ....services.query.typhoon_query_service import (
    normalize_typhoon_count,
    normalize_typhoon_detail,
    query_typhoon_data,
)
from ....services.query.weather_query_service import query_weather_alarm_data
from ....services.simulation.simulation_service import (
    build_earthquake_simulation,
    get_simulation_params,
    resolve_target_session,
)
from ....services.telemetry.telemetry_utils import track_feature_safely
from ..payloads.api_response import ApiResponse


def register_runtime_routes(app, disaster_service, config: dict[str, Any]):
    """注册运行态查询与模拟接口。"""

    async def _track_runtime_feature(
        feature_name: str, extra: dict[str, Any] | None = None
    ):
        telemetry = getattr(disaster_service, "_telemetry", None)
        await track_feature_safely(
            telemetry,
            feature_name,
            extra,
            log_context="Web运行态行为遥测",
        )

    @app.get("/api/weather/query")
    async def query_weather_alarm(
        keyword: str = "",
        optional_a: str = "",
        optional_b: str = "",
        filter_by_time: bool = True,
        optional_c: str = "",
    ):
        """查询气象预警，逻辑与命令侧查询保持一致。

        支持 filter_by_time 关闭时间过滤，以及 optional_c 指定“全部/全日期”检索。
        """
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return guard_result

            db = disaster_service.statistics_manager.db
            query_result = await query_weather_alarm_data(
                db,
                keyword,
                optional_a or None,
                optional_b or None,
                filter_by_time=filter_by_time,
                optional_c=optional_c or None,
            )
            await _track_runtime_feature(
                "web_weather_query",
                {
                    "success": bool(query_result.get("success")),
                    "query_mode": str(query_result.get("query_mode") or "unknown"),
                    "has_optional_type": bool(optional_a),
                    "has_optional_level": bool(optional_b),
                    "result_count": len(query_result.get("items") or []),
                },
            )
            return ApiResponse.success(query_result)
        except Exception as e:
            logger.error(f"[灾害预警] Web端查询气象预警失败: {e}")
            return ApiResponse.error(str(e), status_code=500, success=False)

    @app.get("/api/typhoon/query")
    async def query_typhoon_info(
        typhoon_id: str = "",
        keyword: str = "",
        count: int = 1,
        detail: str = "current",
        active_only: bool = False,
    ):
        """查询台风信息，逻辑与命令侧 `/台风信息查询` 保持一致。

        优先 EQSC；配置无效或查询失败时回退本地数据库。
        """
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return guard_result

            db = disaster_service.statistics_manager.db
            enrichment = getattr(disaster_service, "typhoon_enrichment_service", None)
            query_result = await query_typhoon_data(
                db,
                enrichment,
                typhoon_id=typhoon_id or None,
                keyword=keyword or None,
                count=normalize_typhoon_count(count),
                detail=normalize_typhoon_detail(detail),
                active_only=bool(active_only),
            )
            await _track_runtime_feature(
                "web_typhoon_query",
                {
                    "success": bool(query_result.get("success")),
                    "query_mode": str(query_result.get("query_mode") or "unknown"),
                    "source": str(query_result.get("source") or "unknown"),
                    "detail": str(query_result.get("detail") or "current"),
                    "has_id": bool(typhoon_id),
                    "has_keyword": bool(keyword),
                    "active_only": bool(active_only),
                    "result_count": int(query_result.get("total") or 0),
                },
            )
            return ApiResponse.success(query_result)
        except Exception as e:
            logger.error(f"[灾害预警] Web端查询台风信息失败: {e}")
            return ApiResponse.error(str(e), status_code=500, success=False)

    @app.get("/api/simulation-params")
    async def get_simulation_params_api():
        """获取模拟预警可用参数选项。"""
        try:
            # 传入会话配置管理器，使 target_sessions 携带备注名信息
            mgr = getattr(disaster_service, "session_config_manager", None)
            return ApiResponse.success(get_simulation_params(config, mgr))
        except Exception as e:
            logger.error(f"[灾害预警] 获取模拟参数失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/simulate")
    async def simulate_disaster(simulation_data: dict[str, Any]):
        """模拟灾害预警，当前仅支持地震。"""
        try:
            if not disaster_service:
                return ApiResponse.error("服务未初始化", status_code=503)

            target_session = simulation_data.get("target_session", "")
            disaster_type = simulation_data.get("disaster_type", "earthquake")
            test_type = simulation_data.get("test_type", "cea_fanstudio")
            custom_params = simulation_data.get("custom_params", {})

            if disaster_type != "earthquake":
                return ApiResponse.error(
                    f"暂不支持 {disaster_type} 类型的模拟，仅支持 earthquake",
                    status_code=400,
                )

            # 目标会话允许显式指定，也允许回退到配置中的默认会话。
            final_target_session = resolve_target_session(config, target_session)
            if not final_target_session:
                return ApiResponse.error("未配置目标会话", status_code=400)

            lat = float(custom_params.get("latitude", 39.9))
            lon = float(custom_params.get("longitude", 116.4))
            magnitude = float(custom_params.get("magnitude", 5.5))
            depth = float(custom_params.get("depth", 10.0))
            source = custom_params.get("source", test_type)

            manager = disaster_service.message_manager
            session_config_manager = disaster_service.session_config_manager
            runtime_config = session_config_manager.get_effective_config(
                final_target_session
            )
            try:
                # 先构造模拟事件并执行规则判定，再决定是否真正发送消息。
                simulation_result = build_earthquake_simulation(
                    manager,
                    lat=lat,
                    lon=lon,
                    magnitude=magnitude,
                    depth=depth,
                    source=source,
                    runtime_config=runtime_config,
                )
            except ValueError as ve:
                await _track_runtime_feature(
                    "web_simulation_result",
                    {
                        "success": False,
                        "reason": "invalid_params",
                        "source": str(source or "unknown"),
                    },
                )
                return ApiResponse.error(str(ve), status_code=400)

            if simulation_result.global_pass and simulation_result.local_pass:
                # 只有全局与本地判定都通过，才继续走完整模拟推送链路。
                logger.info("[灾害预警] 开始执行模拟预警推送链路...")
                push_success = await manager.push_event(
                    simulation_result.disaster_event,
                    target_sessions=[final_target_session],
                    session_config_getter=session_config_manager.get_effective_config,
                    commit_state=False,
                    skip_dedup=True,
                    bypass_fusion=True,
                )
                if push_success:
                    target_log_str = session_config_manager.get_session_log_str(
                        final_target_session
                    )
                    logger.info(f"[灾害预警] ✅ 模拟事件已成功推送到 {target_log_str}")
                    simulation_result.report_lines.append(
                        f"\n✅ 消息已发送到: {target_log_str}"
                    )
                else:
                    simulation_result.report_lines.append(
                        "\n⛔ 结论: 该事件未通过目标会话的正式推送链路筛选。"
                    )
                await _track_runtime_feature(
                    "web_simulation_result",
                    {
                        "success": True,
                        "triggered": bool(push_success),
                        "source": str(source or "unknown"),
                        "magnitude_bucket": round(magnitude),
                        "depth_bucket": int(depth // 10 * 10),
                    },
                )
                return ApiResponse.success(
                    {
                        "success": bool(push_success),
                        "message": "\n".join(simulation_result.report_lines),
                    }
                )

            simulation_result.report_lines.append("\n⛔ 结论: 该事件不会触发预警推送。")
            await _track_runtime_feature(
                "web_simulation_result",
                {
                    "success": True,
                    "triggered": False,
                    "source": str(source or "unknown"),
                    "magnitude_bucket": round(magnitude),
                    "depth_bucket": int(depth // 10 * 10),
                },
            )
            return ApiResponse.success(
                {
                    "success": False,
                    "message": "\n".join(simulation_result.report_lines),
                }
            )

        except Exception as e:
            logger.error(f"[灾害预警] 模拟推送失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/geolocate")
    async def get_geolocation(request: Request):
        """获取客户端 IP 地理位置信息。"""
        try:
            client_ip = request.client.host if request.client else None
            location_data = await fetch_location_from_ip(ip=client_ip)
            await _track_runtime_feature("web_geolocate", {"success": True})
            return ApiResponse.success(
                {
                    "success": True,
                    "data": {
                        "latitude": location_data.get("latitude"),
                        "longitude": location_data.get("longitude"),
                        "city": location_data.get("city_zh", ""),
                        "province": location_data.get("province_name_zh", ""),
                        "country": location_data.get("country_name_zh", ""),
                        "ip": location_data.get("ip", ""),
                    },
                }
            )
        except Exception as e:
            await _track_runtime_feature("web_geolocate", {"success": False})
            logger.error(f"[灾害预警] IP地理定位失败: {e}")
            return ApiResponse.error(
                f"获取地理位置失败: {str(e)}", status_code=500, success=False
            )
