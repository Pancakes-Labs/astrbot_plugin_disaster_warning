"""
运行态数据查询接口路由。
承接气象查询、台风信息查询与地理定位接口，
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
from ....services.telemetry.telemetry_utils import track_feature_safely
from ..payloads.api_response import ApiResponse


def register_runtime_routes(app, disaster_service, config: dict[str, Any]):
    """注册运行态查询与地理定位接口。"""

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

    @app.get("/api/geolocate")
    async def get_geolocation(request: Request):
        """获取客户端 IP 地理位置信息。"""
        try:
            client_ip = request.client.host if request.client else None
            location_data = await fetch_location_from_ip(ip=client_ip)
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
            logger.error(f"[灾害预警] IP地理定位失败: {e}")
            return ApiResponse.error(
                f"获取地理位置失败: {str(e)}", status_code=500, success=False
            )
