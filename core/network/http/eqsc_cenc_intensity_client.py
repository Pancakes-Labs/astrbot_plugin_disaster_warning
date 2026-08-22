"""
EQSC CENC 烈度速报 HTTP 客户端。

负责：
- 拉取 /listIntensityReportCENC.json 列表索引
- 按 eventID 拉取 /intensityReportCENC.json 详情
- 列表 No* 字典规范化
- 列表 / 详情 TTL 缓存

公共鉴权 / 会话 / 日志能力由 EqscHttpClient 提供。
"""

from __future__ import annotations

import re
import time
from typing import Any

from astrbot.api import logger

from .eqsc_http_client import EqscHttpClient
from .eqsc_token_manager import EqscTokenManager

_NO_KEY_PATTERN = re.compile(r"^No(\d+)$", re.IGNORECASE)


class EqscCencIntensityClient(EqscHttpClient):
    """EQSC CENC 烈度速报 HTTP 客户端。"""

    # 详情缓存容量上限，防止长跑按 event_id 无限堆积大包。
    MAX_DETAIL_CACHE_ENTRIES = 128

    def __init__(
        self,
        token_manager: EqscTokenManager,
        config: dict[str, Any],
        message_logger: Any | None = None,
        *,
        owns_token_manager: bool = False,
    ):
        """初始化烈度速报客户端。

        Args:
            token_manager: EQSC 令牌管理器（可与台风/海啸共享）。
            config: EQSC 配置字典。
            message_logger: 可选原始消息记录器。
            owns_token_manager: 为 True 时 close() 会一并关闭 token_manager。
                默认 False，便于与其它 EQSC 客户端共享同一 token_manager。
        """
        super().__init__(
            token_manager,
            config,
            message_logger=message_logger,
            owns_token_manager=owns_token_manager,
            # 详情相对稳定；默认 10 分钟缓存，减少重复大包下载。
            # cenc_ir_cache_ttl 配置项已从配置契约移除：传入 None 完全禁用配置读取，避免任何残留键覆盖默认值。
            default_cache_ttl=600,
            cache_ttl_config_key=None,
        )
        # 列表缓存：(items, expires_at)
        self._list_cache: tuple[list[dict[str, Any]], float] | None = None
        # 详情缓存: {event_id: (data, expires_at)}；按插入顺序近似 FIFO 淘汰
        self._detail_cache: dict[str, tuple[dict[str, Any], float]] = {}

    def clear_cache(self) -> None:
        """清除列表与详情缓存。"""
        self._list_cache = None
        self._detail_cache.clear()

    def _store_detail_cache(self, event_id: str, data: dict[str, Any]) -> None:
        """写入详情缓存，并做过期清理 + 容量上限淘汰。"""
        now = time.time()
        # 先清过期项，再写入，避免无界增长
        expired_keys = [
            key
            for key, (_payload, expires_at) in self._detail_cache.items()
            if not self._is_cache_valid(expires_at)
        ]
        for key in expired_keys:
            self._detail_cache.pop(key, None)

        # 更新已存在 key 时先 pop 再 put，保持“最近写入”位于末尾
        self._detail_cache.pop(event_id, None)
        self._detail_cache[event_id] = (data, now + self._cache_ttl)

        overflow = len(self._detail_cache) - self.MAX_DETAIL_CACHE_ENTRIES
        if overflow <= 0:
            return
        stale_ids = list(self._detail_cache.keys())[:overflow]
        for key in stale_ids:
            self._detail_cache.pop(key, None)

    @staticmethod
    def normalize_event_id(value: Any) -> str:
        """统一 eventID 为非空字符串。"""
        if value is None:
            return ""
        text = str(value).strip()
        if not text or text.lower() in {"null", "none"}:
            return ""
        # 详情文档示例可能是 number；避免 "20251129065328.0"
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if text.endswith(".0") and text[:-2].isdigit():
            return text[:-2]
        return text

    @classmethod
    def normalize_list_payload(cls, raw: Any) -> list[dict[str, Any]]:
        """把 EQSC No1/No2 字典或数组规范为有序列表。

        返回项字段：
        - event_id
        - place_name
        - magnitude
        - url
        - raw
        """
        items: list[dict[str, Any]] = []
        if isinstance(raw, list):
            source_items = [(idx, item) for idx, item in enumerate(raw, start=1)]
        elif isinstance(raw, dict):
            numbered: list[tuple[int, Any]] = []
            for key, value in raw.items():
                match = _NO_KEY_PATTERN.match(str(key or "").strip())
                if not match:
                    continue
                try:
                    order = int(match.group(1))
                except (TypeError, ValueError):
                    continue
                numbered.append((order, value))
            numbered.sort(key=lambda pair: pair[0])
            source_items = numbered
        else:
            return []

        for _order, value in source_items:
            if not isinstance(value, dict):
                continue
            event_id = cls.normalize_event_id(
                value.get("eventID") or value.get("eventId") or value.get("id")
            )
            if not event_id:
                continue
            place_name = str(
                value.get("placeName") or value.get("locName") or ""
            ).strip()
            magnitude = value.get("magnitude")
            url = str(value.get("url") or "").strip()
            items.append(
                {
                    "event_id": event_id,
                    "place_name": place_name,
                    "magnitude": magnitude,
                    "url": url,
                    "raw": dict(value),
                }
            )
        return items

    async def fetch_list(
        self,
        *,
        limit: int | None = None,
        access_token: str | None = None,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """获取 CENC 烈度速报列表（规范化后的有序条目）。"""
        if use_cache and self._list_cache and self._is_cache_valid(self._list_cache[1]):
            return list(self._list_cache[0])

        if not self._base_url:
            logger.warning("[灾害预警] EQSC base_url 为空，无法拉取烈度速报列表")
            return []

        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return []

        params: dict[str, Any] | None = None
        if limit is not None:
            try:
                limit_val = int(limit)
            except (TypeError, ValueError):
                limit_val = 0
            if limit_val > 0:
                params = {"limit": limit_val}

        try:
            url = f"{self._base_url}/listIntensityReportCENC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                params=params,
                log_label="EQSC 查询 CENC 烈度速报列表",
            )
            if status != 200 or data is None:
                return []

            items = self.normalize_list_payload(data)
            self._list_cache = (items, time.time() + self._cache_ttl)
            return list(items)
        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询 CENC 烈度速报列表异常: "
                f"{type(e).__name__}: {str(e) or repr(e)}"
            )
            return []

    async def fetch_detail(
        self,
        event_id: str,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any] | None:
        """按 eventID 获取 CENC 烈度速报详情。"""
        normalized_id = self.normalize_event_id(event_id)
        if not normalized_id:
            return None

        if use_cache:
            cached = self._detail_cache.get(normalized_id)
            if cached and self._is_cache_valid(cached[1]):
                return cached[0]

        if not self._base_url:
            logger.warning("[灾害预警] EQSC base_url 为空，无法拉取烈度速报详情")
            return None

        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return None

        try:
            url = f"{self._base_url}/intensityReportCENC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                params={"id": normalized_id},
                log_label=f"EQSC 查询 CENC 烈度速报详情 {normalized_id}",
            )
            if status != 200 or not isinstance(data, dict) or not data:
                return None

            self._store_detail_cache(normalized_id, data)
            return data
        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询 CENC 烈度速报详情 {normalized_id} 异常: "
                f"{type(e).__name__}: {str(e) or repr(e)}"
            )
            return None


__all__ = ["EqscCencIntensityClient"]
