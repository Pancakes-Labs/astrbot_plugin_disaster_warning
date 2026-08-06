"""
事件去重与指纹服务。
统一承接运行时事件去重、报次更新判定与事件指纹生成逻辑，
用于替代旧 support 层中的去重业务实现。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
)
from ...domain.tsunami.jma_tsunami_normalize import (
    build_jma_tsunami_content_fingerprint,
    coerce_bool,
)
from ...domain.typhoon.typhoon_ids import normalize_typhoon_id
from ...sources.source_catalog import get_source_entry
from .event_identity import EventIdentityService


class EventDeduplicationService:
    """运行时事件去重服务。

    负责在短时间窗口内识别重复事件，并允许合法的报次更新或状态升级继续放行。
    """

    def __init__(
        self,
        time_window_minutes: int = 1,
        location_tolerance_km: float = 20.0,
        magnitude_tolerance: float = 0.5,
    ):
        # 时间窗口、位置容差和震级容差共同决定“同一事件”的聚类范围。
        self.time_window = timedelta(minutes=time_window_minutes)
        self.location_tolerance = location_tolerance_km
        self.magnitude_tolerance = magnitude_tolerance
        # 内存中维护的最近事件指纹去重字典
        self.recent_events: dict[str, dict[str, dict[str, Any]]] = {}
        # 台风去重缓存：key 为台风 ID，value 为核心参数指纹。
        # 当同一台风的核心参数（等级、位置、风速、气压、移向移速、风圈半径）
        # 与上次推送完全一致时直接过滤，避免数据源刷屏。
        self._typhoon_cache: dict[str, str] = {}
        # JMA 海啸同源去重缓存：
        # key = source_id|content_fingerprint
        # value = {"source_id", "priority", "event_id", "ts"}
        # 各源独立推送，不再跨源互斥；priority 仅作语义记录保留。
        # 带容量上限，防止长跑进程内存无限增长。
        self._tsunami_cache: dict[str, dict[str, Any]] = {}
        self._tsunami_cache_max_size = 512
        # CENC 烈度速报跨源去重：
        # key = soft fingerprint（发震时间+震级+位置网格）
        # value = {"source_id", "priority", "event_id", "ts"}
        # EQSC 优先级更高：同内容后到的 FAN 会被吞掉。
        self._cenc_ir_cache: dict[str, dict[str, Any]] = {}
        self._cenc_ir_cache_max_size = 256

    @staticmethod
    def _extract_issue_type_from_earthquake(
        earthquake: EarthquakeEvent,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """统一提取日本地震情报类型，优先读取元数据字段。"""
        active_metadata = metadata if isinstance(metadata, dict) else {}
        info_type = str(
            active_metadata.get("info_type")
            or active_metadata.get("issue_type")
            or getattr(getattr(earthquake, "metadata", {}), "get", lambda *_: "")(
                "info_type"
            )
            or ""
        ).strip()
        if info_type:
            return info_type
        return ""

    @staticmethod
    def _get_domain_earthquake(event: EventEnvelope) -> EarthquakeEvent | None:
        """从统一事件中安全提取地震领域对象。"""
        if isinstance(event.event, EarthquakeEvent):
            return event.event
        return None

    @staticmethod
    def _get_source_id(event: EventEnvelope) -> str:
        """解析事件对应的数据源标识。"""
        resolved_source_id = EventIdentityService.resolve_source_id(event)
        if resolved_source_id:
            return resolved_source_id
        source = getattr(event, "source", None)
        source_value = getattr(source, "value", source)
        return str(source_value or "unknown")

    @staticmethod
    def _resolve_report_num(
        event: EventEnvelope,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """解析当前事件报次，缺失时回退为首报。"""
        del metadata
        resolved = EventIdentityService.resolve_report_num(event)
        if isinstance(resolved, int) and resolved > 0:
            return resolved
        return 1

    @staticmethod
    def _resolve_event_stream_by_source_id(source_id: str) -> str:
        """根据数据源标识解析事件流标签，用于细粒度日志级别控制。

        确保去重器中的报数日志与对应解析器的日志流标签一致，
        避免事件的报数日志以 earthquake 流输出导致级别不匹配。
        """
        if not source_id:
            return "earthquake"
        if source_id == "global_quake":
            return "global_quake"
        if "weather" in source_id:
            return "weather_alarm"
        if "typhoon" in source_id:
            return "typhoon"
        if "tsunami" in source_id:
            return "tsunami"
        return "earthquake"

    @staticmethod
    def _normalize_fingerprint_value(value: Any) -> str:
        """规范化指纹字段，避免 int/float/None 的字符串差异导致误放行。

        典型问题：
        - 62 vs 62.0 会被 str() 判为不同指纹
        - 0 / 0.0 在 `value or ""` 写法下会被错误折叠为空串
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value != value:  # NaN
                return ""
            if value == int(value):
                return str(int(value))
            # 固定小数精度后再去尾零，吸收浮点噪声
            return f"{value:.6f}".rstrip("0").rstrip(".")
        text = str(value).strip()
        if not text or text.lower() in {"none", "null", "无数据", "-"}:
            return ""
        try:
            number = float(text)
        except ValueError:
            return text
        if number != number:
            return ""
        if number == int(number):
            return str(int(number))
        return f"{number:.6f}".rstrip("0").rstrip(".")

    @classmethod
    def _generate_typhoon_fingerprint(cls, typhoon: TyphoonEvent) -> str:
        """生成台风核心参数指纹。

        对比维度包括：等级（typhoon_type）、中心位置（latitude/longitude）、
        风速（wind_speed）、气压（pressure）、移动方向（move_direction）、
        移动速度（move_speed）以及风圈半径（radius7/radius10）。
        当这些参数完全一致时，指纹相同，视为重复数据。

        说明：多台风共舞时，FAN Studio 会在“任意一个台风变化”时整包重推数组。
        因此指纹必须按单台风 ID 粒度稳定比较，不能因为数组内其他台风变化而失效。
        """
        return "|".join(
            [
                str(typhoon.typhoon_type or "").strip(),
                cls._normalize_fingerprint_value(typhoon.latitude),
                cls._normalize_fingerprint_value(typhoon.longitude),
                cls._normalize_fingerprint_value(typhoon.wind_speed),
                cls._normalize_fingerprint_value(typhoon.pressure),
                str(typhoon.move_direction or "").strip(),
                cls._normalize_fingerprint_value(typhoon.move_speed),
                cls._normalize_fingerprint_value(typhoon.radius7),
                cls._normalize_fingerprint_value(typhoon.radius10),
                # 活跃状态参与指纹：停编（isActive 翻转）即使核心参数未变化
                # 也视为变化放行，用于推送停编通知。FAN 数据不含活跃状态，
                # 事件 is_active 恒为 True，不受影响。
                "1" if bool(typhoon.is_active) else "0",
            ]
        )

    def peek_typhoon_should_push(self, event: EventEnvelope) -> bool:
        """只读检查台风是否应推送，不写入缓存。

        供富化前的早期过滤使用：未变化的台风可跳过昂贵的 EQSC 查询，
        真正放行后再由 should_push_event / commit 写入缓存。
        """
        typhoon = event.event
        if not isinstance(typhoon, TyphoonEvent):
            return True

        typhoon_id = normalize_typhoon_id(typhoon.typhoon_id)
        if not typhoon_id:
            return True

        fingerprint = self._generate_typhoon_fingerprint(typhoon)
        cached_fingerprint = self._typhoon_cache.get(typhoon_id)
        if cached_fingerprint is not None and cached_fingerprint == fingerprint:
            plugin_logger.info(
                f"[灾害预警] 台风 {typhoon_id} 核心参数未变化，过滤重复推送",
                is_event_linked=True,
                event_stream="typhoon",
            )
            return False
        return True

    def _should_push_typhoon(self, event: EventEnvelope) -> bool:
        """台风事件去重判定。

        若同一台风 ID 的核心参数指纹与缓存中上次推送的指纹完全一致，
        则判定为重复数据直接过滤；否则更新缓存并放行。
        """
        typhoon = event.event
        if not isinstance(typhoon, TyphoonEvent):
            return True

        typhoon_id = normalize_typhoon_id(typhoon.typhoon_id)
        if not typhoon_id:
            # 缺少 ID 无法建立缓存，直接放行避免误杀
            return True

        fingerprint = self._generate_typhoon_fingerprint(typhoon)
        cached_fingerprint = self._typhoon_cache.get(typhoon_id)

        if cached_fingerprint is not None and cached_fingerprint == fingerprint:
            plugin_logger.info(
                f"[灾害预警] 台风 {typhoon_id} 核心参数未变化，过滤重复推送",
                is_event_linked=True,
                event_stream="typhoon",
            )
            return False

        # 参数有变化（或首次出现），更新缓存并放行
        self._typhoon_cache[typhoon_id] = fingerprint
        plugin_logger.debug(
            f"[灾害预警] 台风 {typhoon_id} 核心参数已更新，允许推送 (指纹: {fingerprint})"
        )
        return True

    @staticmethod
    def _resolve_source_priority(source_id: str) -> int:
        """读取 catalog 优先级；缺失时回退 0。"""
        entry = get_source_entry(source_id)
        if entry is None:
            return 0
        try:
            return int(entry.priority)
        except (TypeError, ValueError):
            return 0

    def _extract_tsunami_fingerprint(self, event: EventEnvelope) -> str:
        """提取海啸内容指纹；优先用解析器写入的 content_fingerprint。"""
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        cached = str(metadata.get("content_fingerprint") or "").strip()
        if cached:
            return cached

        domain = event.event
        if not isinstance(domain, TsunamiEvent):
            return ""
        domain_meta = domain.metadata if isinstance(domain.metadata, dict) else {}
        forecasts = metadata.get("forecasts") or domain_meta.get("forecasts") or []
        if not isinstance(forecasts, list):
            forecasts = []
        cancelled = coerce_bool(
            metadata.get("cancelled"),
            default=str(domain.level or "") == "解除",
        )
        return build_jma_tsunami_content_fingerprint(
            event_id=str(
                event.id or metadata.get("event_id") or metadata.get("code") or ""
            ),
            cancelled=cancelled,
            max_grade=str(domain.level or metadata.get("level") or ""),
            areas=forecasts,
            is_training=coerce_bool(metadata.get("is_training"), default=False),
        )

    def _remember_priority_fingerprint(
        self,
        cache: dict[str, dict[str, Any]],
        max_size: int,
        fingerprint: str,
        *,
        source_id: str,
        priority: int,
        event_id: str,
    ) -> None:
        """写入跨源优先级指纹缓存，并在超限时按时间戳淘汰最旧条目。"""
        cache[fingerprint] = {
            "source_id": source_id,
            "priority": priority,
            "event_id": event_id,
            "ts": datetime.now(timezone.utc).timestamp(),
        }
        overflow = len(cache) - int(max_size)
        if overflow <= 0:
            return
        ordered = sorted(
            cache.items(),
            key=lambda item: float((item[1] or {}).get("ts") or 0.0),
        )
        for key, _ in ordered[:overflow]:
            cache.pop(key, None)

    def _remember_tsunami_fingerprint(
        self,
        fingerprint: str,
        *,
        source_id: str,
        priority: int,
        event_id: str,
    ) -> None:
        """写入海啸指纹缓存，并在超限时淘汰最旧条目。"""
        self._remember_priority_fingerprint(
            self._tsunami_cache,
            self._tsunami_cache_max_size,
            fingerprint,
            source_id=source_id,
            priority=priority,
            event_id=event_id,
        )

    def _peek_priority_fingerprint(
        self,
        *,
        cache: dict[str, dict[str, Any]],
        fingerprint: str,
        source_id: str,
        event_id: str,
        kind: str,
        log_level: str = "info",
        filter_same_source: bool = True,
    ) -> bool:
        """跨源优先级去重只读预判（不写缓存）。

        判定规则与 _should_push_by_priority_fingerprint 完全一致，
        但不会写入 cache，供轮询侧在投递前提前过滤并准确统计，
        避免预判写缓存污染下游二次判定（如报次/指纹被提前消耗）。

        规则：
        1. 指纹未见过 → 放行
        2. 指纹相同且来源优先级更低 → 过滤
        3. 指纹相同且同优先级不同源 → 过滤（先到先得）
        4. 指纹相同且同优先级同源 → 由 filter_same_source 决定
        5. 指纹相同但来源优先级更高 → 放行（升级由下游真实判定完成）
        """
        priority = self._resolve_source_priority(source_id)
        existing = cache.get(fingerprint)
        if existing is None:
            return True

        existing_priority = int(existing.get("priority") or 0)
        existing_source = str(existing.get("source_id") or "")
        stream_tag = self._resolve_event_stream_by_source_id(source_id)

        def _log_filter(msg: str) -> None:
            if log_level == "info":
                plugin_logger.info(msg, is_event_linked=True, event_stream=stream_tag)
            else:
                plugin_logger.debug(msg)

        if priority < existing_priority:
            _log_filter(
                f"[灾害预警] {kind}内容与 {existing_source} 重复且优先级更低，"
                f"过滤 {source_id} 推送 (event={event_id})"
            )
            return False
        if priority == existing_priority and existing_source == source_id:
            if not filter_same_source:
                # 粗软指纹：同源更新交给下游 _should_allow_update 判定
                plugin_logger.debug(
                    f"[灾害预警] {kind}同源软指纹命中，放行供下游更新判定: "
                    f"{source_id} (事件 ID {event_id})"
                )
                return True
            _log_filter(
                f"[灾害预警] {kind}内容未变化，过滤重复推送: {source_id} "
                f"(事件 ID {event_id})"
            )
            return False
        if priority == existing_priority and existing_source != source_id:
            # 同优先级不同源：先到先得
            _log_filter(
                f"[灾害预警] {kind}内容与 {existing_source} 重复，过滤 {source_id} 推送"
            )
            return False

        # 更高优先级源后到：放行（缓存升级由下游真实判定完成）
        return True

    def _should_push_by_priority_fingerprint(
        self,
        *,
        cache: dict[str, dict[str, Any]],
        max_size: int,
        fingerprint: str,
        source_id: str,
        event_id: str,
        kind: str,
        log_level: str = "info",
        filter_same_source: bool = True,
    ) -> bool:
        """跨源优先级去重公共实现。

        Args:
            kind: 业务类别文案，如「烈度速报」，用于日志前缀。
            filter_same_source: 为 True 时，同优先级同源也过滤。
                为 False 时同源放行给下游更新链路（适合粗软指纹，
                如 CENC 烈度速报仅做跨源去重）。

        规则：
        1. 指纹未见过 → 放行并记录
        2. 指纹相同且来源优先级更低 → 过滤
        3. 指纹相同且同优先级不同源 → 过滤（先到先得）
        4. 指纹相同且同优先级同源 → 由 filter_same_source 决定
        5. 指纹相同但来源优先级更高 → 放行并升级缓存
        """
        priority = self._resolve_source_priority(source_id)
        existing = cache.get(fingerprint)
        if existing is None:
            self._remember_priority_fingerprint(
                cache,
                max_size,
                fingerprint,
                source_id=source_id,
                priority=priority,
                event_id=event_id,
            )
            return True

        existing_priority = int(existing.get("priority") or 0)
        existing_source = str(existing.get("source_id") or "")
        stream_tag = self._resolve_event_stream_by_source_id(source_id)

        # plugin_logger.debug 不接受 is_event_linked / event_stream 关键字，
        # 透传到底层 logger 会触发 TypeError 中断去重链路；仅 info 分支携带事件流参数。
        def _log_filter(msg: str) -> None:
            if log_level == "info":
                plugin_logger.info(msg, is_event_linked=True, event_stream=stream_tag)
            else:
                plugin_logger.debug(msg)

        if priority < existing_priority:
            _log_filter(
                f"[灾害预警] {kind}内容与 {existing_source} 重复且优先级更低，"
                f"过滤 {source_id} 推送 (event={event_id})"
            )
            return False
        if priority == existing_priority and existing_source == source_id:
            if not filter_same_source:
                # 粗软指纹：同源更新交给 recent_events / _should_allow_update
                plugin_logger.debug(
                    f"[灾害预警] {kind}同源软指纹命中，放行供下游更新判定: "
                    f"{source_id} (事件 ID {event_id})"
                )
                return True
            _log_filter(
                f"[灾害预警] {kind}内容未变化，过滤重复推送: {source_id} "
                f"(事件 ID {event_id})"
            )
            return False
        if priority == existing_priority and existing_source != source_id:
            # 同优先级不同源：先到先得
            _log_filter(
                f"[灾害预警] {kind}内容与 {existing_source} 重复，过滤 {source_id} 推送"
            )
            return False

        # 更高优先级源后到：放行并升级缓存
        self._remember_priority_fingerprint(
            cache,
            max_size,
            fingerprint,
            source_id=source_id,
            priority=priority,
            event_id=event_id,
        )
        plugin_logger.debug(
            f"[灾害预警] {kind}高优先级源 {source_id} 覆盖 {existing_source}，允许推送"
        )
        return True

    def _should_push_tsunami(self, event: EventEnvelope) -> bool:
        """JMA 海啸同源内容去重：各源独立推送，同内容只推一次。

        设计取舍：
        - 不再做 P2P / EQSC 跨源互斥，便于双源互补与测试数据积累。
        - catalog 中的 priority 语义仍保留并写入缓存，但不参与过滤决策。
        - 同源同内容（含 event_id）未变化时过滤，避免轮询/重放刷屏。
        - 解除报 areas 为空时依赖 event_id 区分不同事件，避免误杀后续解除。
        """
        if not isinstance(event.event, TsunamiEvent):
            return True

        source_id = self._get_source_id(event)
        # 仅对日本海啸双源做同源内容去重；中国海啸等保持放行
        if source_id not in {"jma_tsunami_p2p", "jma_tsunami_eqsc"}:
            return True

        fingerprint = self._extract_tsunami_fingerprint(event)
        if not fingerprint:
            return True

        event_id = str(event.id or "")
        # 同源隔离：不同源即使内容相同也各自放行
        cache_key = f"{source_id}|{fingerprint}"
        existing = self._tsunami_cache.get(cache_key)
        if existing is not None:
            plugin_logger.info(
                f"[灾害预警] 海啸内容未变化，过滤重复推送: {source_id} "
                f"(event={event_id})",
                is_event_linked=True,
                event_stream="tsunami",
                is_silent_window=True,
            )
            return False

        # priority 仅作语义记录，不参与跨源过滤
        priority = self._resolve_source_priority(source_id)
        self._remember_tsunami_fingerprint(
            cache_key,
            source_id=source_id,
            priority=priority,
            event_id=event_id,
        )
        return True

    @classmethod
    def _is_cenc_intensity_report_source(cls, source_id: str) -> bool:
        """是否为 CENC 烈度速报双源之一。"""
        return source_id in {"cenc_ir_eqsc", "cenc_ir_fanstudio"}

    def _build_cenc_ir_soft_fingerprint(
        self,
        event: EventEnvelope,
        domain_eq: EarthquakeEvent,
        source_id: str,
    ) -> str:
        """构建 CENC 烈度速报跨源软指纹。"""
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        info_type = str(
            metadata.get("info_type")
            or self._extract_issue_type_from_earthquake(domain_eq, metadata)
            or ""
        ).strip()
        if "烈度速报" not in info_type and not self._is_cenc_intensity_report_source(
            source_id
        ):
            return ""

        if domain_eq.occurred_at is None:
            time_key = "unknown_time"
        else:
            utc_time = self._to_utc(domain_eq.occurred_at, source_id)
            time_key = utc_time.strftime("%Y%m%d%H%M")

        mag = domain_eq.magnitude
        try:
            mag_key = f"{float(mag):.1f}" if mag is not None else "na"
        except (TypeError, ValueError):
            mag_key = "na"

        lat = domain_eq.latitude
        lon = domain_eq.longitude
        if lat is None or lon is None:
            place = str(getattr(domain_eq, "place_name", "") or "").strip() or "na"
            return f"cenc_ir|{time_key}|{mag_key}|{place}"

        lat_grid = round(float(lat) * 5.0) / 5.0
        lon_grid = round(float(lon) * 5.0) / 5.0
        return f"cenc_ir|{time_key}|{mag_key}|{lat_grid:.1f},{lon_grid:.1f}"

    def _remember_cenc_ir_fingerprint(
        self,
        fingerprint: str,
        *,
        source_id: str,
        priority: int,
        event_id: str,
    ) -> None:
        """写入烈度速报跨源指纹缓存。"""
        self._remember_priority_fingerprint(
            self._cenc_ir_cache,
            self._cenc_ir_cache_max_size,
            fingerprint,
            source_id=source_id,
            priority=priority,
            event_id=event_id,
        )

    def _should_push_cenc_intensity_report(
        self,
        event: EventEnvelope,
        domain_eq: EarthquakeEvent,
        source_id: str,
    ) -> bool:
        """CENC 烈度速报跨源去重（FAN / EQSC，EQSC 优先）。

        软指纹仅含发震时间/震级/粗网格，不含报次与内容版本；
        因此只过滤跨源重复，同源更新放行给下游。
        """
        if not self._is_cenc_intensity_report_source(source_id):
            return True

        fingerprint = self._build_cenc_ir_soft_fingerprint(event, domain_eq, source_id)
        if not fingerprint:
            return True

        return self._should_push_by_priority_fingerprint(
            cache=self._cenc_ir_cache,
            max_size=self._cenc_ir_cache_max_size,
            fingerprint=fingerprint,
            source_id=source_id,
            event_id=str(event.id or ""),
            kind="烈度速报",
            log_level="debug",
            filter_same_source=False,
        )

    def peek_cenc_intensity_should_push(self, event: EventEnvelope) -> bool:
        """CENC 烈度速报跨源去重只读预判（不写缓存）。

        与 _should_push_cenc_intensity_report 判定规则一致，但不会写入
        _cenc_ir_cache，供轮询侧在投递前提前过滤并准确统计，
        避免预判写缓存污染下游二次判定。
        """
        domain_eq = self._get_domain_earthquake(event)
        if domain_eq is None:
            return True
        source_id = self._get_source_id(event)
        if not self._is_cenc_intensity_report_source(source_id):
            return True

        fingerprint = self._build_cenc_ir_soft_fingerprint(event, domain_eq, source_id)
        if not fingerprint:
            return True

        return self._peek_priority_fingerprint(
            cache=self._cenc_ir_cache,
            fingerprint=fingerprint,
            source_id=source_id,
            event_id=str(event.id or ""),
            kind="烈度速报",
            log_level="debug",
            filter_same_source=False,
        )

    def seed_event(self, event: EventEnvelope) -> bool:
        """静默启动播种：写入去重指纹，不表示允许推送。

        供建连/首轮快照阶段学习当前世界状态，避免静默结束后整批误报。
        """
        envelope = event
        if isinstance(envelope.event, TyphoonEvent):
            return self._seed_typhoon(envelope)
        if isinstance(envelope.event, TsunamiEvent):
            return self._seed_tsunami(envelope)
        domain_eq = self._get_domain_earthquake(event)
        if domain_eq is None:
            return True
        return self._seed_earthquake(envelope, domain_eq)

    def _seed_typhoon(self, event: EventEnvelope) -> bool:
        """播种台风核心参数指纹。"""
        typhoon = event.event
        if not isinstance(typhoon, TyphoonEvent):
            return False
        typhoon_id = normalize_typhoon_id(typhoon.typhoon_id)
        if not typhoon_id:
            return False
        fingerprint = self._generate_typhoon_fingerprint(typhoon)
        self._typhoon_cache[typhoon_id] = fingerprint
        plugin_logger.debug(
            f"[灾害预警] 静默播种台风指纹: {typhoon_id} ({fingerprint})"
        )
        return True

    def _seed_tsunami(self, event: EventEnvelope) -> bool:
        """播种海啸同源内容指纹。"""
        if not isinstance(event.event, TsunamiEvent):
            return False
        source_id = self._get_source_id(event)
        fingerprint = self._extract_tsunami_fingerprint(event)
        if not fingerprint:
            return False
        # 与推送路径一致：按源隔离，避免静默播种时跨源互相覆盖
        cache_key = f"{source_id}|{fingerprint}"
        priority = self._resolve_source_priority(source_id)
        self._remember_tsunami_fingerprint(
            cache_key,
            source_id=source_id,
            priority=priority,
            event_id=str(event.id or ""),
        )
        plugin_logger.debug(
            f"[灾害预警] 静默播种海啸指纹: {source_id} 事件 ID {event.id}"
        )
        return True

    def _seed_earthquake(
        self, event: EventEnvelope, domain_eq: EarthquakeEvent
    ) -> bool:
        """播种地震滑动窗口指纹。"""
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        source_id = self._get_source_id(event)
        event_fingerprint = self.generate_event_fingerprint(event, domain_eq, source_id)
        current_time = self._to_utc(domain_eq.occurred_at, source_id)
        current_report = self._resolve_report_num(event, metadata)
        issue_type = self._extract_issue_type_from_earthquake(domain_eq, metadata)
        record = {
            "timestamp": current_time,
            "source": source_id,
            "latitude": domain_eq.latitude or 0,
            "longitude": domain_eq.longitude or 0,
            "magnitude": domain_eq.magnitude or 0,
            "info_type": metadata.get("info_type")
            or self._extract_issue_type_from_earthquake(domain_eq, metadata)
            or "",
            "issue_type": issue_type,
            "processed_reports": {current_report},
            "is_final": bool(metadata.get("is_final", False)),
        }
        if event_fingerprint in self.recent_events:
            source_events = self.recent_events[event_fingerprint]
            if source_id in source_events:
                existing = source_events[source_id]
                processed = existing.get("processed_reports", set())
                if not isinstance(processed, set):
                    processed = {existing.get("updates", 1)}
                processed.add(current_report)
                existing["processed_reports"] = processed
                existing["timestamp"] = current_time
                existing["is_final"] = existing.get("is_final", False) or bool(
                    metadata.get("is_final", False)
                )
                if issue_type:
                    existing["issue_type"] = issue_type
            else:
                source_events[source_id] = record
        else:
            self.recent_events[event_fingerprint] = {source_id: record}
        plugin_logger.debug(
            f"[灾害预警] 静默播种地震指纹: {source_id} 事件指纹：{event_fingerprint}"
        )
        return True

    def should_push_event(self, event: EventEnvelope) -> bool:
        """判断是否应该推送事件。

        台风事件走专用核心参数指纹去重链路，
        海啸事件走 JMA 同源内容指纹去重（各源独立推送），
        地震事件进入指纹与报次更新判定链路，
        其余非地震事件直接放行。
        """
        envelope = event
        domain_eq = self._get_domain_earthquake(event)

        # 台风事件：基于核心参数指纹去重，过滤数据源完全一致的重复推送
        if isinstance(envelope.event, TyphoonEvent):
            return self._should_push_typhoon(envelope)

        # 海啸事件：P2P / EQSC 同源内容去重（不再跨源互斥）
        if isinstance(envelope.event, TsunamiEvent):
            return self._should_push_tsunami(envelope)

        # 非地震事件（气象预警等）直接放行，无需在此进行滑动时间窗口位置碰撞去重
        if domain_eq is None:
            return True

        metadata = envelope.metadata if isinstance(envelope.metadata, dict) else {}
        source_id = self._get_source_id(event)

        # CENC 烈度速报：FAN / EQSC 跨源软去重（EQSC 优先）
        if self._is_cenc_intensity_report_source(source_id):
            if not self._should_push_cenc_intensity_report(
                envelope, domain_eq, source_id
            ):
                return False

        event_fingerprint = self.generate_event_fingerprint(event, domain_eq, source_id)
        current_time = self._to_utc(domain_eq.occurred_at, source_id)

        plugin_logger.debug(
            f"[灾害预警] 检查事件: {source_id}, 指纹: {event_fingerprint}"
        )

        # 指纹命中说明近期已有相近事件，需要进一步区分是重复还是合法更新。
        if event_fingerprint in self.recent_events:
            source_events = self.recent_events[event_fingerprint]

            if source_id in source_events:
                existing_event = source_events[source_id]
                existing_timestamp = existing_event["timestamp"]
                if existing_timestamp.tzinfo is None:
                    existing_timestamp = existing_timestamp.astimezone(timezone.utc)

                time_diff = abs(
                    (current_time - existing_timestamp).total_seconds() / 60
                )
                # 仍在允许的去重时间滑动窗口之内
                if time_diff <= self.time_window.total_seconds() / 60:
                    if self._should_allow_update(
                        event,
                        domain_eq,
                        existing_event,
                        source_id,
                        metadata=metadata,
                    ):
                        plugin_logger.debug(
                            f"[灾害预警] 允许同一数据源更新: {source_id}"
                        )
                        current_report = self._resolve_report_num(event, metadata)
                        # 将当前的报数加入历史已处理的报数集合中，规避重复推送
                        existing_event["processed_reports"].add(current_report)
                        existing_event["timestamp"] = current_time
                        existing_event["is_final"] = existing_event["is_final"] or bool(
                            metadata.get("is_final", False)
                        )
                        return True
                    plugin_logger.info(
                        f"[灾害预警] 同一数据源重复事件，过滤: {source_id}",
                        is_event_linked=True,
                        event_stream=self._resolve_event_stream_by_source_id(source_id),
                    )
                    return False

            # 同一指纹但来自不同数据源的事件允许继续入链，便于后续融合策略处理。
            plugin_logger.info(
                f"[灾害预警] 不同数据源，允许推送: {source_id}",
                is_event_linked=True,
                event_stream=self._resolve_event_stream_by_source_id(source_id),
            )
            current_report = self._resolve_report_num(event, metadata)
            issue_type = self._extract_issue_type_from_earthquake(domain_eq, metadata)
            self.recent_events[event_fingerprint][source_id] = {
                "timestamp": current_time,
                "source": source_id,
                "latitude": domain_eq.latitude or 0,
                "longitude": domain_eq.longitude or 0,
                "magnitude": domain_eq.magnitude or 0,
                "info_type": metadata.get("info_type")
                or self._extract_issue_type_from_earthquake(domain_eq, metadata)
                or "",
                "issue_type": issue_type,
                "processed_reports": {current_report},
                "is_final": bool(metadata.get("is_final", False)),
            }
            return True

        current_report = self._resolve_report_num(event, metadata)
        issue_type = self._extract_issue_type_from_earthquake(domain_eq, metadata)
        self.recent_events[event_fingerprint] = {
            source_id: {
                "timestamp": current_time,
                "source": source_id,
                "latitude": domain_eq.latitude or 0,
                "longitude": domain_eq.longitude or 0,
                "magnitude": domain_eq.magnitude or 0,
                "info_type": metadata.get("info_type")
                or self._extract_issue_type_from_earthquake(domain_eq, metadata)
                or "",
                "issue_type": issue_type,
                "processed_reports": {current_report},
                "is_final": bool(metadata.get("is_final", False)),
            }
        }
        plugin_logger.debug(f"[灾害预警] 事件通过基础去重检查: {source_id}")
        return True

    def generate_event_fingerprint(
        self,
        event: EventEnvelope,
        domain_eq: EarthquakeEvent,
        source_id: str,
    ) -> str:
        """生成事件指纹。

        优先使用稳定事件标识，缺失时再退回到时间、位置和震级聚类键。
        """
        identity = getattr(event, "identity", None)
        stable_event_id = str(getattr(identity, "event_id", "") or "").strip()
        source_entry = get_source_entry(source_id)
        # 优先读取数据源配置指定的硬指纹前缀进行组装，避免地理位置碰撞模糊指纹误伤
        if source_entry is not None and stable_event_id:
            fingerprint_prefix = source_entry.identity_fingerprint_prefix
            if fingerprint_prefix:
                return f"{fingerprint_prefix}_{stable_event_id}"
        if domain_eq.latitude is None or domain_eq.longitude is None:
            # 禁止退化为全局常量 "unknown_location"：
            # 否则多场「震源参数调查中」会撞成同一 unique_id 并错误合并。
            place = (
                str(getattr(domain_eq, "place_name", "") or "").strip()
                or "unknown_place"
            )
            # _to_utc 在 dt 为空时会回退到 now()；指纹路径必须用稳定占位，
            # 避免缺发震时间时因墙钟分钟变化/轮询同分钟而错误撞键。
            if domain_eq.occurred_at is None:
                time_key = "unknown_time"
            else:
                utc_time = self._to_utc(domain_eq.occurred_at, source_id)
                time_key = utc_time.strftime("%Y%m%d%H%M")
            mag = getattr(domain_eq, "magnitude", None)
            mag_key = f"{float(mag):.1f}" if isinstance(mag, (int, float)) else "na"
            source_key = str(source_id or "unknown_source").strip() or "unknown_source"
            return f"{source_key}|unknown_loc|{time_key}|{mag_key}|{place}"

        # 降级方案：计算基于网格空间容差及时间微调的模糊聚类物理指纹
        lat_grid = round(domain_eq.latitude * (111.0 / self.location_tolerance)) / (
            111.0 / self.location_tolerance
        )
        lon_grid = round(domain_eq.longitude * (111.0 / self.location_tolerance)) / (
            111.0 / self.location_tolerance
        )
        mag_grid = (
            round((domain_eq.magnitude or 0) / self.magnitude_tolerance)
            * self.magnitude_tolerance
        )
        utc_time = self._to_utc(domain_eq.occurred_at, source_id)
        time_minute = utc_time.replace(second=0, microsecond=0)
        return f"{lat_grid:.3f},{lon_grid:.3f},{mag_grid:.1f},{time_minute.strftime('%Y%m%d%H%M')}"

    def _should_allow_update(
        self,
        event: EventEnvelope,
        domain_eq: EarthquakeEvent,
        existing_event: dict[str, Any],
        source_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """判断是否允许同源事件更新。

        允许的新情况包括报次增加、最终报到达，以及部分来源的状态升级。
        """
        active_metadata = metadata if isinstance(metadata, dict) else {}
        current_report = self._resolve_report_num(event, active_metadata)
        processed_reports = existing_event.get("processed_reports", set())
        if not isinstance(processed_reports, set):
            old_updates = existing_event.get("updates", 1)
            processed_reports = {old_updates}

        # 新报数到达允许放行更新，例如第 1 报 -> 第 2 报
        if current_report not in processed_reports:
            current_is_final = bool(active_metadata.get("is_final", False))
            plugin_logger.info(
                f"[灾害预警] 新报数: 第 {current_report} 报 {'(最终报)' if current_is_final else ''}"
                f" (已处理: {sorted(processed_reports)})",
                is_event_linked=True,
                event_stream=self._resolve_event_stream_by_source_id(source_id),
            )
            return True

        # 若是最终报，且历史已推送记录中未判定为最终报，则放行更新
        current_is_final = bool(active_metadata.get("is_final", False))
        if current_is_final and not existing_event.get("is_final", False):
            plugin_logger.info(
                "[灾害预警] 最终报更新: 非最终报 -> 最终报",
                is_event_linked=True,
                event_stream=self._resolve_event_stream_by_source_id(source_id),
            )
            return True

        current_info_type = str(
            active_metadata.get("info_type")
            or self._extract_issue_type_from_earthquake(domain_eq, active_metadata)
            or ""
        ).lower()
        if source_id == "usgs_fanstudio":
            existing_info_type = (existing_event.get("info_type", "") or "").lower()
            if existing_info_type == "automatic" and current_info_type == "reviewed":
                plugin_logger.debug(
                    "[灾害预警] 允许USGS状态升级: automatic -> reviewed"
                )
                return True

        jma_types = ["ScalePrompt", "Destination", "ScaleAndDestination", "DetailScale"]
        current_issue_type = self._extract_issue_type_from_earthquake(
            domain_eq, active_metadata
        )
        existing_issue_type = existing_event.get("issue_type", "")
        if current_issue_type in jma_types and existing_issue_type in jma_types:
            try:
                curr_idx = jma_types.index(current_issue_type)
                prev_idx = jma_types.index(existing_issue_type)
                # 情报优先级提升时（例如震度速报 -> 地震报告），允许放行更新
                if curr_idx > prev_idx:
                    plugin_logger.debug(
                        f"[灾害预警] 允许JMA情报升级: {existing_issue_type} -> {current_issue_type}"
                    )
                    return True
            except ValueError:
                pass

        existing_info_type = (existing_event.get("info_type", "") or "").lower()
        # 允许由“自动测定”状态跃升为“正式测定”状态的更新推送
        if "自动" in existing_info_type and "正式" in current_info_type:
            plugin_logger.debug(
                f"[灾害预警] 允许状态升级: {existing_info_type} -> {current_info_type}"
            )
            return True

        plugin_logger.debug(f"[灾害预警] 报数 {current_report} 已处理过，跳过")
        return False

    def cleanup_old_events(self):
        """清理过期事件。"""
        # 过期阈值放宽到两倍时间窗口，兼顾短时补报场景与内存占用控制。
        cutoff_aware = datetime.now(timezone.utc) - self.time_window * 2
        old_fingerprints = []
        for fingerprint, source_events in self.recent_events.items():
            all_expired = True
            for event_info in source_events.values():
                timestamp = event_info["timestamp"]
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp >= cutoff_aware:
                    all_expired = False
                    break
            if all_expired:
                old_fingerprints.append(fingerprint)

        # 从内存缓存中剔除超时的去重条目
        for fingerprint in old_fingerprints:
            del self.recent_events[fingerprint]

        # 台风去重缓存不按时间过期：只要核心参数发生变化就会放行并更新缓存，
        # 台风消散后数据源不再推送该 ID，缓存条目自然不再被访问。
        # 此处仅在缓存条目过多时输出调试日志，便于排查内存占用问题。
        if len(self._typhoon_cache) > 64:
            plugin_logger.debug(
                f"[灾害预警] 台风去重缓存当前持有 {len(self._typhoon_cache)} 个条目"
            )

    @staticmethod
    def _to_utc(dt: datetime | None, source_id: str | None = None) -> datetime:
        """将时间转换为 UTC 时区时间对象。"""
        if dt is None:
            return datetime.now(timezone.utc)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)

        normalized_source_id = (source_id or "").strip()
        resolved = EventIdentityService.ensure_utc_datetime(dt, normalized_source_id)
        return resolved or datetime.now(timezone.utc)


__all__ = ["EventDeduplicationService"]
