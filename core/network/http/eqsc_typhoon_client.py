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

# 台风查询结果状态（随查询调用返回，不落实例字段）：
# - hit：成功拿到数据；
# - empty：接口正常返回但未命中（编号不存在 / 列表为空），属确定结果，不应重试；
# - error：鉴权失败、服务器错误或网络异常等可恢复故障，应继续重试。
LOOKUP_HIT = "hit"
LOOKUP_EMPTY = "empty"
LOOKUP_ERROR = "error"


class EqscTyphoonClient(EqscHttpClient):
    """EQSC 台风数据 HTTP 客户端。

    查询方法统一返回 (数据, 状态) 二元组，状态随单次调用返回字段。
    """

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
            config: EQSC 配置字典（缓存 TTL 已硬编码，不再读取 cache_ttl）。
            message_logger: 可选原始消息记录器；启用后会落盘 EQSC HTTP 响应。
            owns_token_manager: close() 时是否关闭 token_manager。
        """
        super().__init__(
            token_manager,
            config,
            message_logger=message_logger,
            owns_token_manager=owns_token_manager,
            # cache_ttl 配置项已从配置契约移除：硬编码 300 秒缓存，
            # 传入 None 完全禁用配置读取，避免任何残留键覆盖默认值。
            default_cache_ttl=300,
            cache_ttl_config_key=None,
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
        *,
        use_cache: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        """按台风 ID 查询台风详细数据。

        EQSC 的台风 ID 格式为 4 位（年份后2位+编号2位），
        FAN Studio 的台风 ID 格式为 6 位（年份4位+编号2位），
        调用方需在传入前完成 ID 转换。

        Args:
            typhoon_id: EQSC 格式的台风 ID（4位）。
            access_token: 可复用的 AccessToken；若未提供则内部自行获取。
            use_cache: 为 False 时强制绕过详情缓存（查询指令侧使用）。

        Returns:
            (台风数据, 状态) 二元组：

            - (data, "hit")：查询成功；
            - (None, "empty")：EQSC 正常返回但无该编号
              （EQSC 以 HTTP 500 表示「编号不存在」，属确定未命中）；
            - (None, "error")：鉴权失败、服务器错误或网络异常，可重试。
        """
        # 检查缓存
        if use_cache:
            cached = self._cache.get(typhoon_id)
            if cached and self._is_cache_valid(cached[1]):
                return cached[0], LOOKUP_HIT

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            # 无可用令牌属通道级异常，交由上层走重试 / 熔断逻辑
            return None, LOOKUP_ERROR

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                params={"id": typhoon_id},
                log_label=f"EQSC 查询台风 {typhoon_id}",
                # EQSC 以 HTTP 500 表示「编号不存在」，仅该状态码降级为 INFO；
                # 其余失败（401/403、429、502/503 等）仍保持 WARNING，
                # 避免把真实服务故障静默成低级别日志。
                info_status_codes={500},
            )
            if status != 200 or not isinstance(data, dict):
                # 500 语义为「未命中」；其余非 200（鉴权 / 服务器错误）
                # 保留 error 语义，交由上层继续退避重试。
                if status == 500:
                    return None, LOOKUP_EMPTY
                return None, LOOKUP_ERROR

            # 解析响应：{"typhoon": [{...}]}
            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            # 非列表视为无效响应（上游异常 / 结构变更），保留可重试语义，
            # 避免把错误结构当作命中缓存后污染下游解析。
            if not isinstance(typhoon_list, list):
                logger.warning(
                    f"[灾害预警] EQSC 台风 {typhoon_id} 响应格式异常："
                    f"typhoon 字段非列表（{type(typhoon_list).__name__}）"
                )
                return None, LOOKUP_ERROR
            if not typhoon_list:
                logger.debug(f"[灾害预警] EQSC 台风 {typhoon_id} 未找到匹配数据")
                return None, LOOKUP_EMPTY

            # 取第一个字典元素作为台风数据；列表元素类型不符同样按无效响应处理。
            typhoon_data = next(
                (item for item in typhoon_list if isinstance(item, dict)), None
            )
            if typhoon_data is None:
                logger.warning(
                    f"[灾害预警] EQSC 台风 {typhoon_id} 响应格式异常：未含有效台风对象"
                )
                return None, LOOKUP_ERROR
            # 写入缓存
            self._cache[typhoon_id] = (typhoon_data, time.time() + self._cache_ttl)
            return typhoon_data, LOOKUP_HIT

        except Exception as e:
            logger.error(
                f"[灾害预警] EQSC 查询台风 {typhoon_id} 异常: "
                f"{type(e).__name__}: {str(e) or repr(e)}"
            )
            return None, LOOKUP_ERROR

    async def fetch_typhoon_list(
        self,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> tuple[list[dict[str, Any]], str]:
        """查询 EQSC 台风列表（无参，至多约 20 个最新台风，含历史）。

        注意：该接口并非严格“仅活跃台风”，实际常返回最新历史编报集合。

        Args:
            access_token: 可复用的 AccessToken；若未提供则内部自行获取。
            use_cache: 为 False 时强制绕过列表缓存（轮询侧使用）。

        Returns:
            (台风列表, 状态) 二元组：列表非空为 hit，空列表为 empty
            （接口正常但无数据），鉴权 / 网络 / 服务器错误为 error。
        """
        # 检查缓存
        if use_cache and self._list_cache and self._is_cache_valid(self._list_cache[1]):
            cached_list = self._list_cache[0]
            # 列表接口成功返回（含空列表）均视为通道正常：空列表为 empty。
            return cached_list, (LOOKUP_HIT if cached_list else LOOKUP_EMPTY)

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            return [], LOOKUP_ERROR

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                log_label="EQSC 查询台风列表",
            )
            if status != 200 or not isinstance(data, dict):
                # 列表接口异常：标记为 error，供上层区分网络故障与编号未命中。
                return [], LOOKUP_ERROR

            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            # 非列表视为无效响应：返回 error 语义而非把它当成空列表，
            # 以免上层将服务异常误判为「未命中」而提前放弃重试。
            if not isinstance(typhoon_list, list):
                logger.warning(
                    "[灾害预警] EQSC 台风列表响应格式异常：typhoon 字段非列表"
                    f"（{type(typhoon_list).__name__}）"
                )
                return [], LOOKUP_ERROR
            # 写入缓存
            self._list_cache = (typhoon_list, time.time() + self._cache_ttl)
            # 接口调用成功：列表非空为 hit，空列表为 empty（通道正常但无数据）。
            return typhoon_list, (LOOKUP_HIT if typhoon_list else LOOKUP_EMPTY)

        except Exception as e:
            error_name = type(e).__name__
            # DNS 解析失败（getaddrinfo failed）等连接层错误对普通用户是黑话，
            # 先给出人性化提示，再附带原始技术细节便于排障。
            # 注意：DNS 失败常以 socket.gaierror 作为 __cause__ 被 ClientConnectorError
            # 包装，或作为 __context__ 关联，因此需遍历整条异常链判断，而非只看顶层类型名。
            if self._is_dns_error(e):
                logger.error(
                    f"[灾害预警] EQSC 查询台风列表失败：无法解析域名 {self._base_url}，"
                    f"请检查网络或 DNS 配置（原始错误: {error_name}: {str(e) or repr(e)}）"
                )
            else:
                logger.error(
                    f"[灾害预警] EQSC 查询台风列表异常: {error_name}: {str(e) or repr(e)}"
                )
            return [], LOOKUP_ERROR

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


__all__ = [
    "LOOKUP_EMPTY",
    "LOOKUP_ERROR",
    "LOOKUP_HIT",
    "EqscTyphoonClient",
]
