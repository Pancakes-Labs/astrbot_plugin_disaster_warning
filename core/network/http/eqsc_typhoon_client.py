"""
EQSC 台风 HTTP 客户端。

负责向 EQSC API 发送台风数据查询请求，并维护简单的内存缓存以减少重复请求。
公共鉴权 / 会话 / 日志能力由 EqscHttpClient 提供。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from .eqsc_http_client import EqscHttpClient
from .eqsc_token_manager import EqscTokenManager


class EqscTyphoonClient(EqscHttpClient):
    """EQSC 台风数据 HTTP 客户端。"""

    def __init__(
        self,
        token_manager: EqscTokenManager,
        config: dict[str, Any],
        message_logger: Any | None = None,
        *,
        owns_token_manager: bool = True,
    ):
        """初始化台风客户端。

        Args:
            token_manager: EQSC 令牌管理器实例。
            config: EQSC 配置字典，包含 cache_ttl 等字段。
            message_logger: 可选原始消息记录器；启用后会落盘 EQSC HTTP 响应。
            owns_token_manager: close() 时是否关闭 token_manager。
        """
        super().__init__(
            token_manager,
            config,
            message_logger=message_logger,
            owns_token_manager=owns_token_manager,
            default_cache_ttl=300,
            cache_ttl_config_key="cache_ttl",
        )
        # 缓存结构: {typhoon_id: (data, expires_at)}
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        # 无参查询缓存（全部最新台风列表）
        self._list_cache: tuple[list[dict[str, Any]], float] | None = None

    def clear_cache(self) -> None:
        """清除所有缓存。"""
        self._cache.clear()
        self._list_cache = None

    async def fetch_typhoon_by_id(
        self,
        typhoon_id: str,
        access_token: str | None = None,
    ) -> dict[str, Any] | None:
        """按台风 ID 查询台风详细数据。

        EQSC 的台风 ID 格式为 4 位（年份后2位+编号2位），
        FAN Studio 的台风 ID 格式为 6 位（年份4位+编号2位），
        调用方需在传入前完成 ID 转换。

        Args:
            typhoon_id: EQSC 格式的台风 ID（4位）。
            access_token: 可复用的 AccessToken；若未提供则内部自行获取。

        Returns:
            台风数据字典，或 None 表示查询失败/未找到。
        """
        # 检查缓存
        cached = self._cache.get(typhoon_id)
        if cached and self._is_cache_valid(cached[1]):
            logger.debug(f"[灾害预警] EQSC 台风 {typhoon_id} 命中缓存")
            return cached[0]

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return None

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                params={"id": typhoon_id},
                log_label=f"EQSC 查询台风 {typhoon_id}",
            )
            if status != 200 or not isinstance(data, dict):
                return None

            # 解析响应：{"typhoon": [{...}]}
            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            if not typhoon_list:
                logger.debug(f"[灾害预警] EQSC 台风 {typhoon_id} 未找到匹配数据")
                return None

            # 取第一个匹配的台风
            typhoon_data = typhoon_list[0]
            # 写入缓存
            self._cache[typhoon_id] = (typhoon_data, time.time() + self._cache_ttl)
            logger.debug(f"[灾害预警] EQSC 台风 {typhoon_id} 查询成功并已缓存")
            return typhoon_data

        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询台风 {typhoon_id} 异常: "
                f"{type(e).__name__}: {e or repr(e)}"
            )
            return None

    async def fetch_typhoon_list(
        self,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """查询 EQSC 台风列表（无参，至多约 20 个最新台风，含历史）。

        注意：该接口并非严格“仅活跃台风”，实际常返回最新历史编报集合。
        兼容别名：`fetch_active_typhoons`。

        Args:
            use_cache: 为 False 时强制绕过列表缓存（轮询侧使用）。
        """
        # 检查缓存
        if use_cache and self._list_cache and self._is_cache_valid(self._list_cache[1]):
            logger.debug("[灾害预警] EQSC 台风列表命中缓存")
            return self._list_cache[0]

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return []

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                log_label="EQSC 查询台风列表",
            )
            if status != 200 or not isinstance(data, dict):
                return []

            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            # 写入缓存
            self._list_cache = (typhoon_list, time.time() + self._cache_ttl)
            logger.debug(
                f"[灾害预警] EQSC 台风列表查询成功，共 {len(typhoon_list)} 个台风"
            )
            return typhoon_list

        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询台风列表异常: {type(e).__name__}: {e or repr(e)}"
            )
            return []

    async def fetch_active_typhoons(
        self,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """兼容旧名：实际返回 EQSC 无参台风列表（含历史）。"""
        return await self.fetch_typhoon_list(
            access_token=access_token, use_cache=use_cache
        )

    def find_typhoon_by_name(
        self,
        typhoon_list: list[dict[str, Any]],
        name_cn: str = "",
        name_en: str = "",
    ) -> dict[str, Any] | None:
        """在台风列表中按名称匹配台风。

        Args:
            typhoon_list: EQSC 返回的台风列表。
            name_cn: 台风中文名。
            name_en: 台风英文名。

        Returns:
            匹配到的台风数据字典，或 None。
        """
        for typhoon in typhoon_list:
            if not isinstance(typhoon, dict):
                continue
            eqsc_name_cn = str(typhoon.get("nameCN", "") or "").strip()
            eqsc_name_en = str(typhoon.get("nameEN", "") or "").strip()
            if name_cn and eqsc_name_cn and name_cn == eqsc_name_cn:
                return typhoon
            if name_en and eqsc_name_en and name_en.upper() == eqsc_name_en.upper():
                return typhoon
        return None


__all__ = ["EqscTyphoonClient"]
