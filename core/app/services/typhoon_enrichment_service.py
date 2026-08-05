"""
台风富化服务。

负责在收到 FAN Studio 台风推送后，向 EQSC API 拉取台风详细数据
（历史轨迹、预测路径、四象限风圈），并合并到事件中供展示使用。

EQSC 通道级能力（鉴权、保活、健康、熔断）由 EqscChannelService 统一提供，
本服务只保留台风业务相关逻辑：Fan 触发路径的富化合并与台风查询入口。

核心策略：
1. 同步阻塞等待 EQSC 查询完成才推送，只有一直匹配不上才回退到 FAN Studio 基础数据。
2. 指数退避重试，最多约5分钟。
3. 最大等待上限：300秒后强制放弃，以 FAN Studio 基础数据回退。
4. 熔断器：连续失败后短路（通道级，由 EqscChannelService 维护）。
5. 按台风 ID 缓存 EQSC 结果（5分钟 TTL），避免重复请求。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ....utils.plugin_logger import plugin_logger as logger
from ...domain.event_models import EventEnvelope, TyphoonEvent
from ...domain.typhoon import (
    clean_wind_circle,
    constrain_wind_circle_by_fan_radius,
    to_eqsc_id,
)
from ...network.http.eqsc_typhoon_client import EqscTyphoonClient
from .eqsc_channel_service import EqscChannelService


class TyphoonEnrichmentService:
    """台风 EQSC 富化服务（FAN 触发路径的遗留兼容 + 台风查询入口）。"""

    def __init__(
        self,
        config: dict[str, Any],
        eqsc_channel_service: EqscChannelService,
        message_logger: Any | None = None,
    ):
        """初始化富化服务。

        Args:
            config: 插件全局配置字典。
            eqsc_channel_service: EQSC 通道服务（提供共享令牌与熔断状态）。
            message_logger: 可选原始消息记录器，用于落盘 EQSC HTTP 响应。
        """
        eqsc_config = config.get("data_sources", {}).get("eqsc", {})
        if not isinstance(eqsc_config, dict):
            eqsc_config = {}
        self._channel_service = eqsc_channel_service
        # 台风富化子开关（通道总闸由 EqscChannelService 维护）
        self._typhoon_enrichment_enabled = (
            eqsc_channel_service.is_typhoon_enrichment_enabled
        )
        # 复用通道服务的 token_manager（不拥有；关闭由通道服务负责）
        self._token_manager = eqsc_channel_service.token_manager
        self._typhoon_client = EqscTyphoonClient(
            self._token_manager,
            eqsc_config,
            message_logger=message_logger,
            owns_token_manager=False,
        )

        # 重试参数（硬编码，降低使用门槛）
        self._initial_timeout = 15  # 首次同步查询超时（秒）
        self._max_retries = 8  # 后台最大重试次数
        self._base_delay = 15  # 首次重试延迟（秒），后续按指数增长
        self._max_delay = 180  # 指数退避延迟上限（秒）
        self._max_total_wait = 300  # 后台重试总等待上限（秒）

    @property
    def is_channel_enabled(self) -> bool:
        """EQSC 通道是否可用（委托通道服务）。"""
        return self._channel_service.is_channel_enabled

    @property
    def is_typhoon_enrichment_enabled(self) -> bool:
        """台风富化子能力是否开启（不含 token 判定）。"""
        return bool(self._typhoon_enrichment_enabled)

    @property
    def is_enabled(self) -> bool:
        """台风富化是否可实际工作：通道可用 + 子开关开启。"""
        return self.is_channel_enabled and self._typhoon_enrichment_enabled

    def _extract_eqsc_track_data(
        self,
        typhoon_data: dict[str, Any],
        *,
        reference_updated_at: Any = None,
        fan_radius7: Any = None,
        fan_radius10: Any = None,
    ) -> dict[str, Any]:
        """从 EQSC 台风数据中提取轨迹与风圈信息。

        风圈提取策略：
        1. 优先取历史轨迹末节点（通常为最新观测）。
        2. 若提供了 FAN Studio 观测时间，则优先匹配时间最接近且不晚于该时刻的节点。
        3. 以 FAN Studio 的 radius7/radius10 空值作为权威约束，避免把历史风圈误补到当前报文。
        """
        history_track = typhoon_data.get("historyTrack", []) or []
        future_track = typhoon_data.get("futureTrack", []) or []

        wind_circle: dict[str, Any] = {}
        if history_track and isinstance(history_track, list):
            selected_node = self._select_history_node_for_wind_circle(
                history_track,
                reference_updated_at=reference_updated_at,
            )
            if isinstance(selected_node, dict):
                wind_circle = clean_wind_circle(
                    selected_node.get("windCircle", {}) or {}
                )

        # FAN Studio 当前观测若明确没有对应等级风圈，则不允许 EQSC 历史节点补回
        wind_circle = constrain_wind_circle_by_fan_radius(
            wind_circle,
            fan_radius7=fan_radius7,
            fan_radius10=fan_radius10,
        )

        return {
            "history_track": history_track,
            "future_track": future_track,
            "wind_circle": wind_circle,
        }

    def _select_history_node_for_wind_circle(
        self,
        history_track: list[Any],
        *,
        reference_updated_at: Any = None,
    ) -> dict[str, Any] | None:
        """选择用于提取当前风圈的历史轨迹节点。"""
        valid_nodes = [node for node in history_track if isinstance(node, dict)]
        if not valid_nodes:
            return None

        # 无参考时间时，默认取末节点（EQSC 历史轨迹通常按时间升序）
        if reference_updated_at is None:
            return valid_nodes[-1]

        reference_dt = self._coerce_datetime(reference_updated_at)
        if reference_dt is None:
            return valid_nodes[-1]

        best_node: dict[str, Any] | None = None
        best_delta: float | None = None
        for node in valid_nodes:
            node_dt = self._coerce_datetime(node.get("time"))
            if node_dt is None:
                continue
            # 允许轻微时钟偏差，但优先不晚于 FAN Studio 观测时间的节点
            delta_seconds = (reference_dt - node_dt).total_seconds()
            if delta_seconds < -1800:
                continue
            abs_delta = abs(delta_seconds)
            if best_delta is None or abs_delta < best_delta:
                best_delta = abs_delta
                best_node = node

        return best_node or valid_nodes[-1]

    @staticmethod
    def _coerce_datetime(value: Any):
        """尽力把时间字段转换为 datetime，失败则返回 None。"""
        from datetime import datetime

        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text or text.upper() in {"NULL", "NONE", "无数据"}:
            return None

        # 兼容 EQSC/FAN 常见时间格式（含带毫秒的 ISO8601）
        for fmt in (
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ):
            try:
                return datetime.strptime(text.replace("Z", "+0000"), fmt)
            except ValueError:
                continue
        return None

    def _merge_eqsc_into_event(
        self,
        envelope: EventEnvelope,
        eqsc_typhoon: dict[str, Any],
    ) -> EventEnvelope:
        """将 EQSC 富化数据合并到事件中。"""
        typhoon_event = envelope.event
        if not isinstance(typhoon_event, TyphoonEvent):
            return envelope

        # 提取 EQSC 轨迹与风圈数据，并以 FAN 当前观测空值做权威约束
        track_data = self._extract_eqsc_track_data(
            eqsc_typhoon,
            reference_updated_at=typhoon_event.updated_at,
            fan_radius7=typhoon_event.radius7,
            fan_radius10=typhoon_event.radius10,
        )

        # 更新领域事件中的富化字段
        typhoon_event.history_track = track_data["history_track"]
        typhoon_event.future_track = track_data["future_track"]
        typhoon_event.wind_circle = track_data["wind_circle"]

        # 如果 EQSC 提供了更丰富的名称信息，补充到事件中
        eqsc_name_cn = str(eqsc_typhoon.get("nameCN", "") or "").strip()
        eqsc_name_en = str(eqsc_typhoon.get("nameEN", "") or "").strip()
        if eqsc_name_cn and not typhoon_event.name:
            typhoon_event.name = eqsc_name_cn
        if eqsc_name_en and not typhoon_event.name_en:
            typhoon_event.name_en = eqsc_name_en

        # EQSC 的 isActive 字段
        eqsc_is_active = eqsc_typhoon.get("isActive")
        if eqsc_is_active is not None:
            typhoon_event.is_active = bool(eqsc_is_active)

        # 轨迹与风圈已写入 TyphoonEvent；metadata 只保留流水线形态标记。
        metadata = dict(envelope.metadata or {})
        metadata["data_source"] = "enriched"
        metadata["info_type"] = "enriched"
        metadata["typhoon_data_mode"] = "enriched"
        envelope.metadata = metadata

        # 原始 payload 保持接入时的内容，不把富化结果复制到 attributes。

        return envelope

    async def _try_fetch_eqsc(
        self,
        typhoon_id: str,
        name: str = "",
        name_en: str = "",
        *,
        use_cache: bool = True,
    ) -> dict[str, Any] | None:
        """尝试从 EQSC 获取台风数据（单次尝试，含 ID 查询 + 名称匹配兜底）。

        先统一获取一次 AccessToken，再复用到 ID 查询和名称兜底查询中，
        避免单次重试内重复鉴权导致日志刷两遍。
        缓存命中时 fetch 方法内部会跳过 token 使用，无需额外处理。

        Args:
            use_cache: 查询指令应传 False，避免短缓存挡住最新编报。
        """
        # 统一获取一次 AccessToken，复用到后续所有查询
        access_token = await self._token_manager.get_access_token()
        if not access_token:
            return None

        # 优先按 ID 精确查询
        eqsc_id = to_eqsc_id(typhoon_id)
        if eqsc_id:
            result = await self._typhoon_client.fetch_typhoon_by_id(
                eqsc_id,
                access_token=access_token,
                use_cache=use_cache,
            )
            if result:
                return result

        # ID 查询无结果，回退到无参查询 + 名称匹配
        if name or name_en:
            typhoon_list = await self._typhoon_client.fetch_typhoon_list(
                access_token=access_token,
                use_cache=use_cache,
            )
            if typhoon_list:
                matched = self._typhoon_client.find_typhoon_by_name(
                    typhoon_list, name_cn=name, name_en=name_en
                )
                if matched:
                    return matched

        return None

    async def enrich(self, envelope: EventEnvelope) -> EventEnvelope:
        """对台风事件进行 EQSC 富化（同步阻塞模式）。

        同步等待 EQSC 查询完成才返回，走完整个指数退避重试链。
        只有全部重试失败后才返回原始事件（FAN Studio 回退）。
        富化成功则返回包含 EQSC 轨迹与风圈数据的更新事件。

        Args:
            envelope: 原始台风事件包裹。

        Returns:
            富化后的事件包裹（或原始事件作为回退）。
        """
        # 非台风事件直接返回
        if not isinstance(envelope.event, TyphoonEvent):
            return envelope

        # EQSC 未启用或未配置，直接回退
        if not self.is_enabled:
            return envelope

        # 熔断器开启时直接回退
        if self._channel_service.is_circuit_open():
            logger.debug("[灾害预警] EQSC 熔断器开启中，跳过富化直接回退")
            return envelope

        typhoon_event = envelope.event
        typhoon_id = typhoon_event.typhoon_id
        name = typhoon_event.name
        name_en = typhoon_event.name_en

        # 首次同步尝试使用独立超时；后续仍按既有退避策略重试。
        try:
            result = await asyncio.wait_for(
                self._try_fetch_eqsc(typhoon_id, name, name_en),
                timeout=float(self._initial_timeout),
            )
            if result:
                self._channel_service.record_success()
                logger.info(f"[灾害预警] 台风 {typhoon_id} EQSC 富化成功（首次查询）")
                return self._merge_eqsc_into_event(envelope, result)
        except Exception as e:
            logger.debug(f"[灾害预警] 台风 {typhoon_id} EQSC 首次查询异常: {e}")

        # 首次未命中，进入同步指数退避重试
        total_wait = 0.0
        delay = float(self._base_delay)

        for attempt in range(1, self._max_retries + 1):
            if total_wait >= self._max_total_wait:
                logger.debug(
                    f"[灾害预警] 台风 {typhoon_id} EQSC 重试达到最大等待时间 {self._max_total_wait}s，放弃"
                )
                break

            # 检查熔断器
            if self._channel_service.is_circuit_open():
                logger.info(f"[灾害预警] 台风 {typhoon_id} EQSC 重试因熔断器开启而中止")
                break

            # 指数退避等待
            logger.info(
                f"[灾害预警] 台风 {typhoon_id} EQSC 第 {attempt} 次重试将在 {delay:.0f}s 后执行"
            )
            await asyncio.sleep(delay)
            total_wait += delay

            try:
                result = await self._try_fetch_eqsc(typhoon_id, name, name_en)
                if result:
                    self._channel_service.record_success()
                    logger.info(
                        f"[灾害预警] 台风 {typhoon_id} EQSC 富化成功（第 {attempt} 次重试）"
                    )
                    return self._merge_eqsc_into_event(envelope, result)
            except Exception as e:
                logger.debug(
                    f"[灾害预警] 台风 {typhoon_id} EQSC 第 {attempt} 次重试异常: {e}"
                )

            # 指数退避：delay = min(delay * 2, max_delay)
            delay = min(delay * 2, float(self._max_delay))

        # 所有重试均失败，回退到 FAN Studio 基础数据
        self._channel_service.record_failure()
        logger.warning(
            f"[灾害预警] 台风 {typhoon_id} EQSC 富化失败，已用 FAN Studio 基础数据回退"
        )
        return envelope

    async def fetch_typhoon_detail(
        self,
        typhoon_id: str = "",
        *,
        name: str = "",
        name_en: str = "",
        use_cache: bool = False,
    ) -> dict[str, Any] | None:
        """按 ID/名称查询单条 EQSC 台风详情，供查询指令与管理端复用。

        优先按台风编号精确查询；编号缺失或未命中时，可按中英文名在列表中匹配。
        未启用、熔断或鉴权失败时返回 None，由上层决定是否回退本地数据库。

        默认 use_cache=False：用户主动查询应尽量拿到最新编报，
        避免与轮询侧短缓存互相污染。
        """
        if not self.is_enabled:
            return None
        if self._channel_service.is_circuit_open():
            logger.debug("[灾害预警] EQSC 熔断器开启中，跳过台风详情查询")
            return None

        try:
            result = await self._try_fetch_eqsc(
                str(typhoon_id or "").strip(),
                str(name or "").strip(),
                str(name_en or "").strip(),
                use_cache=use_cache,
            )
            if result:
                self._channel_service.record_success()
                return result
            self._channel_service.record_failure()
            return None
        except Exception as e:
            self._channel_service.record_failure()
            logger.error(f"[灾害预警] EQSC 台风详情查询异常: {e}")
            return None

    async def fetch_history_typhoons(
        self,
        *,
        use_cache: bool = False,
    ) -> list[dict[str, Any]]:
        """获取 EQSC 台风列表（至多 20 个最新台风，含历史）。

        无参查询 /typhoonNMC.json 时 EQSC 返回至多 20 个最新台风数据，
        包含已消亡的历史台风，可用于数据库冷启动重建。

        默认 use_cache=False：查询/重建场景优先新鲜数据。
        """
        if not self.is_enabled:
            return []

        if self._channel_service.is_circuit_open():
            logger.debug("[灾害预警] EQSC 熔断器开启中，跳过历史台风列表查询")
            return []

        try:
            access_token = await self._token_manager.get_access_token()
            if not access_token:
                logger.warning(
                    "[灾害预警] EQSC 获取历史台风列表失败：无有效 AccessToken"
                )
                return []

            typhoon_list = await self._typhoon_client.fetch_typhoon_list(
                access_token=access_token,
                use_cache=use_cache,
            )
            if typhoon_list:
                self._channel_service.record_success()
                logger.info(
                    f"[灾害预警] EQSC 历史台风列表查询成功，共 {len(typhoon_list)} 个台风"
                )
            return typhoon_list
        except Exception as e:
            self._channel_service.record_failure()
            logger.error(f"[灾害预警] EQSC 获取历史台风列表异常: {e}")
            return []

    async def close(self) -> None:
        """关闭富化服务，释放 HTTP 会话（token 由通道服务负责）。"""
        await self._typhoon_client.close()


__all__ = ["TyphoonEnrichmentService"]
