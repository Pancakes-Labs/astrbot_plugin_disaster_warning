"""
台风信息查询服务。

统一承接命令侧（/台风信息查询）与 Web 管理端（/api/typhoon/query）的查询编排，
避免两处各自实现导致参数语义与展示结果分叉。

分层职责：
- typhoon_query_parser：参数解析
- typhoon_data_adapter：EQSC / 本地数据标准化
- typhoon_query_service：编排与回退
- typhoon_query_presenter：命令文本

数据策略（由上到下）：
1. 优先复用 EQSC 富化链路（配置启用且鉴权可用时）。
2. EQSC 未配置、熔断、接口失败或无匹配时，回退本地数据库。
3. 本地库可能包含 Fan 实时、Fan+EQSC 富化、EQSC 历史重建三类形态。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...domain.typhoon import clean_text, to_fan_id
from .typhoon_data_adapter import (
    filter_items,
    normalize_eqsc_typhoon,
    normalize_local_typhoon,
    sort_items_stable,
)
from .typhoon_query_models import TyphoonQueryResult
from .typhoon_query_parser import (
    DEFAULT_COUNT,
    is_typhoon_id_token,
    normalize_typhoon_count,
    normalize_typhoon_detail,
    parse_typhoon_query_args,
)
from .typhoon_query_presenter import attach_summary_text, build_typhoon_query_text


async def _query_eqsc(
    enrichment_service: Any | None,
    *,
    typhoon_id: str | None,
    keyword: str | None,
    count: int,
    detail: str,
    active_only: bool,
) -> TyphoonQueryResult | None:
    """尝试通过 EQSC 查询。

    返回值约定：
    - None：EQSC 不可用/异常，上层应继续本地回退
    - dict(success=True/False)：EQSC 已给出明确结果（含无匹配）
    """
    if enrichment_service is None:
        return None
    is_enabled = getattr(enrichment_service, "is_enabled", False)
    if callable(is_enabled):
        try:
            enabled = bool(is_enabled())
        except TypeError:
            enabled = bool(is_enabled)
    else:
        enabled = bool(is_enabled)
    if not enabled:
        return None

    try:
        if typhoon_id:
            fetch_detail = getattr(enrichment_service, "fetch_typhoon_detail", None)
            raw = None
            if callable(fetch_detail):
                raw = await fetch_detail(typhoon_id=typhoon_id)
            else:
                try_fetch = getattr(enrichment_service, "_try_fetch_eqsc", None)
                if callable(try_fetch):
                    raw = await try_fetch(typhoon_id, keyword or "", "")
            if not raw:
                return None
            item = normalize_eqsc_typhoon(raw, detail=detail, data_source="eqsc")
            if not item:
                return None
            attach_summary_text(item, detail=detail)
            if active_only and not item.get("is_active"):
                return {
                    "success": False,
                    "query_mode": "id",
                    "source": "eqsc",
                    "detail": detail,
                    "error": f"台风 {item.get('display_name') or typhoon_id} 当前不在活跃编报状态",
                }
            return {
                "success": True,
                "query_mode": "id",
                "source": "eqsc",
                "detail": detail,
                "data": item,
                "items": [item],
                "total": 1,
            }

        fetch_list = getattr(enrichment_service, "fetch_history_typhoons", None)
        raw_list: list[dict[str, Any]] = []
        if callable(fetch_list):
            raw_list = await fetch_list() or []
        if not raw_list:
            return None

        items: list[dict[str, Any]] = []
        for raw in raw_list:
            item = normalize_eqsc_typhoon(raw, detail=detail, data_source="eqsc")
            if item:
                attach_summary_text(item, detail=detail)
                items.append(item)

        items = filter_items(items, keyword=keyword, active_only=active_only)
        items = sort_items_stable(items)[:count]
        if not items:
            return {
                "success": False,
                "query_mode": "search" if keyword else "list",
                "source": "eqsc",
                "detail": detail,
                "error": "EQSC 未返回符合条件的台风数据",
                "filters": {
                    "keyword": keyword or "",
                    "active_only": active_only,
                    "count": count,
                },
            }

        query_mode = "search" if keyword else "list"
        if keyword and len(items) == 1:
            return {
                "success": True,
                "query_mode": "id",
                "source": "eqsc",
                "detail": detail,
                "data": items[0],
                "items": items,
                "total": 1,
            }

        return {
            "success": True,
            "query_mode": query_mode,
            "source": "eqsc",
            "detail": detail,
            "items": items,
            "total": len(items),
            "filters": {
                "keyword": keyword or "",
                "active_only": active_only,
                "count": count,
            },
        }
    except Exception as exc:
        logger.warning(f"[灾害预警] EQSC 台风查询失败，将尝试本地回退: {exc}")
        return None


async def _query_local(
    db: Any,
    *,
    typhoon_id: str | None,
    keyword: str | None,
    count: int,
    detail: str,
    active_only: bool,
) -> TyphoonQueryResult:
    """从本地数据库查询台风信息（EQSC 回退路径）。"""
    if db is None:
        return {
            "success": False,
            "query_mode": "local",
            "source": "local",
            "detail": detail,
            "error": "本地数据库不可用",
        }

    if typhoon_id:
        finder = getattr(db, "find_typhoon_event_by_id", None)
        raw = None
        if callable(finder):
            raw = await finder(typhoon_id)
        if not raw:
            fan_id = to_fan_id(typhoon_id)
            find_by_real = getattr(db, "find_event_by_real_id", None)
            if callable(find_by_real):
                raw = await find_by_real(fan_id, "typhoon_fanstudio")
        if not raw:
            return {
                "success": False,
                "query_mode": "id",
                "source": "local",
                "detail": detail,
                "error": (
                    f"未在本地数据库中找到台风编号 {typhoon_id} 的记录。"
                    "可尝试配置 EQSC 后重试，或通过其他官方渠道查询"
                ),
            }
        item = normalize_local_typhoon(raw, detail=detail)
        if not item:
            return {
                "success": False,
                "query_mode": "id",
                "source": "local",
                "detail": detail,
                "error": f"本地台风记录 {typhoon_id} 数据无效",
            }
        attach_summary_text(item, detail=detail)
        if active_only and not item.get("is_active"):
            return {
                "success": False,
                "query_mode": "id",
                "source": "local",
                "detail": detail,
                "error": f"台风 {item.get('display_name') or typhoon_id} 当前不在活跃编报状态",
            }
        return {
            "success": True,
            "query_mode": "id",
            "source": "local",
            "detail": detail,
            "data": item,
            "items": [item],
            "total": 1,
        }

    loader = getattr(db, "get_recent_typhoon_events", None)
    raw_events: list[dict[str, Any]] = []
    if callable(loader):
        raw_events = await loader(limit=200) or []
    else:
        paginated = getattr(db, "get_events_paginated", None)
        if callable(paginated):
            raw_events = (
                await paginated(
                    page=1,
                    limit=200,
                    event_type="typhoon",
                    keyword=keyword,
                )
                or []
            )

    items: list[dict[str, Any]] = []
    for raw in raw_events:
        item = normalize_local_typhoon(raw, detail=detail)
        if item:
            attach_summary_text(item, detail=detail)
            items.append(item)

    items = filter_items(items, keyword=keyword, active_only=active_only)
    items = sort_items_stable(items)[:count]

    if not items:
        return {
            "success": False,
            "query_mode": "search" if keyword else "list",
            "source": "local",
            "detail": detail,
            "error": (
                "本地数据库中暂无符合条件的台风记录。"
                "可尝试配置 EQSC 后重试，或通过其他官方渠道查询"
            ),
            "filters": {
                "keyword": keyword or "",
                "active_only": active_only,
                "count": count,
            },
        }

    query_mode = "search" if keyword else "list"
    if keyword and len(items) == 1:
        return {
            "success": True,
            "query_mode": "id",
            "source": "local",
            "detail": detail,
            "data": items[0],
            "items": items,
            "total": 1,
        }

    return {
        "success": True,
        "query_mode": query_mode,
        "source": "local",
        "detail": detail,
        "items": items,
        "total": len(items),
        "filters": {
            "keyword": keyword or "",
            "active_only": active_only,
            "count": count,
        },
    }


async def query_typhoon_data(
    db: Any,
    enrichment_service: Any | None = None,
    *,
    typhoon_id: str | None = None,
    keyword: str | None = None,
    count: int | str | None = None,
    detail: str | None = None,
    active_only: bool = False,
    prefer_eqsc: bool = True,
) -> TyphoonQueryResult:
    """查询台风信息（统一入口）。

    优先 EQSC；失败、未配置或无匹配时回退本地数据库。
    返回结构对命令侧与 Web 端保持一致：
    - success / query_mode / source / detail
    - data（单条）或 items/total（列表）
    - 失败时附带 error / usage / filters
    """
    normalized_id = clean_text(typhoon_id) or None
    if normalized_id and not is_typhoon_id_token(normalized_id):
        if not keyword:
            keyword = normalized_id
        normalized_id = None

    normalized_keyword = clean_text(keyword) or None
    normalized_count = normalize_typhoon_count(count, default=DEFAULT_COUNT)
    normalized_detail = normalize_typhoon_detail(detail)

    usage = [
        "/台风信息查询",
        "/台风信息查询 <数量>",
        "/台风信息查询 <台风ID>",
        "/台风信息查询 <台风名称>",
        "/台风信息查询 <台风ID|名称> [完整|简要]",
        "/台风信息查询 [活跃] [数量] [完整|简要]",
    ]

    if prefer_eqsc:
        eqsc_result = await _query_eqsc(
            enrichment_service,
            typhoon_id=normalized_id,
            keyword=normalized_keyword,
            count=normalized_count,
            detail=normalized_detail,
            active_only=bool(active_only),
        )
        if eqsc_result is not None:
            if eqsc_result.get("success"):
                return eqsc_result
            local_result = await _query_local(
                db,
                typhoon_id=normalized_id,
                keyword=normalized_keyword,
                count=normalized_count,
                detail=normalized_detail,
                active_only=bool(active_only),
            )
            if local_result.get("success"):
                local_result["fallback_from"] = "eqsc"
                local_result["eqsc_error"] = eqsc_result.get("error")
                return local_result
            eqsc_result.setdefault("usage", usage)
            return eqsc_result

    local_result = await _query_local(
        db,
        typhoon_id=normalized_id,
        keyword=normalized_keyword,
        count=normalized_count,
        detail=normalized_detail,
        active_only=bool(active_only),
    )
    if not local_result.get("success"):
        local_result.setdefault("usage", usage)
    return local_result


__all__ = [
    "build_typhoon_query_text",
    "is_typhoon_id_token",
    "normalize_typhoon_count",
    "normalize_typhoon_detail",
    "parse_typhoon_query_args",
    "query_typhoon_data",
]
