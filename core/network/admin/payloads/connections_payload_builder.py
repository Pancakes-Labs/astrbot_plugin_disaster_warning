"""
Web 管理端连接状态载荷构建器。
统一组装 /api/connections 与实时数据中的连接状态视图，避免重复拼装逻辑。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ....app.services.eqsc_channel_service import EqscChannelService
from ....services.query.source_runtime_query_service import SourceRuntimeQueryService
from ....sources.display_registry import CONNECTION_DISPLAY_NAMES


class ConnectionsPayloadBuilder:
    """连接状态载荷构建器。"""

    # EQSC / S-Net 展示名统一从 display_registry 连接组展示名表派生，
    # 避免与管理端 / 健康监控模块各自维护导致口径漂移。
    EQSC_DISPLAY_NAME = CONNECTION_DISPLAY_NAMES["eqsc"]
    SNET_DISPLAY_NAME = CONNECTION_DISPLAY_NAMES["snet_msil"]
    SNET_GROUP_KEY = "snet_msil"

    def __init__(
        self,
        disaster_service,
        config: dict[str, Any],
        latency_cache: dict[str, float | None] | None = None,
    ):
        # 构建器既可依赖真实灾害服务，也可在服务未完全就绪时退化为纯配置查询模式。
        self.disaster_service = disaster_service
        self.config = config
        self.source_runtime_query = (
            disaster_service.source_runtime_query
            if disaster_service
            else SourceRuntimeQueryService(config)
        )
        self.latency_cache = latency_cache if latency_cache is not None else {}

    @staticmethod
    def resolve_eqsc_host(config: dict[str, Any] | None) -> str:
        """从配置解析 EQSC 探测主机名，失败时回退官方域名。"""
        eqsc_cfg = {}
        if isinstance(config, dict):
            data_sources = config.get("data_sources", {})
            if isinstance(data_sources, dict):
                raw = data_sources.get("eqsc", {})
                if isinstance(raw, dict):
                    eqsc_cfg = raw
        base_url = str(eqsc_cfg.get("base_url", "") or "").strip()
        if base_url:
            try:
                parsed = urlparse(
                    base_url if "://" in base_url else f"https://{base_url}"
                )
                if parsed.hostname:
                    return parsed.hostname
            except Exception:
                pass
        return "equake.top"

    def _build_eqsc_connection_info(self) -> dict[str, Any]:
        """构建 EQSC HTTP 辅助通道的连接状态条目。"""
        eqsc_cfg = {}
        data_sources = (
            self.config.get("data_sources", {}) if isinstance(self.config, dict) else {}
        )
        if isinstance(data_sources, dict):
            raw = data_sources.get("eqsc", {})
            if isinstance(raw, dict):
                eqsc_cfg = raw

        channel_enabled, typhoon_enrichment = EqscChannelService.resolve_eqsc_flags(
            eqsc_cfg
        )
        config_enabled = channel_enabled
        token_configured = bool(str(eqsc_cfg.get("refresh_token", "") or "").strip())
        latency = self.latency_cache.get("eqsc")

        health: dict[str, Any] = {}
        eqsc_channel = None
        if self.disaster_service is not None:
            eqsc_channel = getattr(self.disaster_service, "eqsc_channel_service", None)
        if eqsc_channel is not None:
            getter = getattr(eqsc_channel, "get_health_status", None)
            if callable(getter):
                try:
                    maybe_health = getter()
                    if isinstance(maybe_health, dict):
                        health = maybe_health
                except Exception:
                    health = {}

        # enabled：组总闸开启且 refresh_token 已配置（通道可工作）
        enabled = (
            bool(health.get("enabled"))
            if health
            else (config_enabled and token_configured)
        )
        effective_config_enabled = bool(
            health.get("config_enabled", config_enabled) if health else config_enabled
        )
        # 子数据源展示只看子开关本身，不与总闸/服务状态做 AND
        effective_typhoon_enrichment = bool(
            health.get("typhoon", typhoon_enrichment) if health else typhoon_enrichment
        )
        if "jma_tsunami" in eqsc_cfg:
            jma_tsunami_cfg = bool(eqsc_cfg.get("jma_tsunami"))
        else:
            jma_tsunami_cfg = config_enabled
        effective_jma_tsunami = bool(
            health.get("jma_tsunami", jma_tsunami_cfg) if health else jma_tsunami_cfg
        )
        if "china_cenc_intensity_report" in eqsc_cfg:
            cenc_ir_cfg = bool(eqsc_cfg.get("china_cenc_intensity_report"))
        else:
            cenc_ir_cfg = config_enabled
        effective_cenc_ir = bool(
            health.get("china_cenc_intensity_report", cenc_ir_cfg)
            if health
            else cenc_ir_cfg
        )
        circuit_open = bool(health.get("circuit_open", False))
        access_token_valid = bool(health.get("access_token_valid", False))
        # 子源展示固定顺序：台风 → 海啸 → CENC 烈度速报
        sub_sources = {
            "china_typhoon": effective_typhoon_enrichment,
            "jma_tsunami": effective_jma_tsunami,
            "china_cenc_intensity_report": effective_cenc_ir,
        }

        # HTTP 通道无 WS 重试语义。
        # 活跃连接判定：AccessToken 当前有效即视为 connected。
        # latency 缓存区分：
        # - 键不存在：尚未完成首次探测（测量中）
        # - 值为 None：连续探测失败（不可达）
        # - 值为数字：TCP 可达
        latency_probed = "eqsc" in self.latency_cache
        unreachable = latency_probed and latency is None

        if not enabled:
            status_text = "未启用"
            connected = False
        elif circuit_open:
            status_text = "熔断中"
            connected = False
        elif access_token_valid:
            # AccessToken 有效即视为活跃连接（可用）
            status_text = "可用"
            connected = True
        elif unreachable:
            status_text = "离线"
            connected = False
        else:
            # 已启用但 AccessToken 尚未获取/已失效
            status_text = "鉴权失效"
            connected = False

        return {
            "group_key": "eqsc",
            "display_name": self.EQSC_DISPLAY_NAME,
            "enabled": enabled,
            "connected": connected,
            "retry_count": 0,
            "has_handler": False,
            "status": status_text,
            "latency": latency,
            "sub_sources": dict(sub_sources),
            "source_ids": ["jma_tsunami_eqsc", "cenc_ir_eqsc"],
            "connection_type": "http",
            "provider": "eqsc",
            "circuit_open": circuit_open,
            "token_configured": bool(health.get("token_configured", token_configured)),
            "config_enabled": effective_config_enabled,
            "typhoon": effective_typhoon_enrichment,
            "access_token_valid": access_token_valid,
        }

    def _build_snet_connection_info(self) -> dict[str, Any]:
        """构建 NIED S-Net（MSIL 瓦片 HTTP 轮询）连接状态条目。"""
        snet_cfg: dict[str, Any] = {}
        data_sources = (
            self.config.get("data_sources", {}) if isinstance(self.config, dict) else {}
        )
        if isinstance(data_sources, dict):
            raw = data_sources.get("snet", {})
            if isinstance(raw, dict):
                snet_cfg = raw

        # 配置开关：data_sources.snet.enabled（schema 文案「启用 S-Net 数据源」）
        # catalog 中 snet_msil 的 config_key 也是 enabled，与组级开关同一字段
        config_enabled = bool(snet_cfg.get("enabled", False))
        latency = self.latency_cache.get(self.SNET_GROUP_KEY)
        latency_probed = self.SNET_GROUP_KEY in self.latency_cache
        unreachable = latency_probed and latency is None

        poll = None
        if self.disaster_service is not None:
            poll = getattr(self.disaster_service, "snet_poll_service", None)

        poll_running = bool(poll and getattr(poll, "running", False))
        # 启用判定与 SnetPollService.is_enabled / catalog is_source_enabled 一致
        try:
            enabled = bool(self.source_runtime_query.is_source_enabled("snet_msil"))
        except Exception:
            enabled = config_enabled
        # 快照新鲜度：有最近成功抓取则视为通道可用
        snapshot_fresh = False
        last_ts = ""
        if poll is not None:
            snap = getattr(poll, "_latest_snapshot", None)
            if isinstance(snap, dict) and snap.get("timestamp"):
                last_ts = str(snap.get("timestamp") or "")
                try:
                    age = time.time() - float(snap.get("fetched_at") or 0.0)
                    ttl = 120.0
                    if hasattr(poll, "_resolve_tile_cache_ttl"):
                        try:
                            ttl = float(poll._resolve_tile_cache_ttl())
                        except Exception:
                            ttl = 120.0
                    snapshot_fresh = age <= max(ttl * 2.0, 90.0)
                except (TypeError, ValueError):
                    snapshot_fresh = bool(last_ts)

        if not enabled:
            status_text = "未启用"
            connected = False
        elif poll_running and (snapshot_fresh or not latency_probed):
            # 轮询在跑：有新鲜快照或尚未完成延迟探测 → 可用
            status_text = "轮询中"
            connected = True
        elif poll_running and unreachable:
            status_text = "离线"
            connected = False
        elif poll_running:
            # 轮询在跑但快照偏旧，仍视为在线（可能处于安静间隔）
            status_text = "轮询中"
            connected = True
        elif unreachable:
            status_text = "离线"
            connected = False
        else:
            status_text = "未启动"
            connected = False

        sub_sources = {
            "snet_msil": enabled,
        }

        return {
            "group_key": self.SNET_GROUP_KEY,
            "display_name": self.SNET_DISPLAY_NAME,
            "enabled": enabled,
            "connected": connected,
            "retry_count": 0,
            "has_handler": False,
            "status": status_text,
            "latency": latency,
            "sub_sources": sub_sources,
            "source_ids": ["snet_msil"],
            "connection_type": "http",
            "provider": "snet",
            "circuit_open": False,
            "config_enabled": config_enabled,
            "poll_running": poll_running,
            "last_timestamp": last_ts,
        }

    def build(
        self, expected_sources: dict[str, str] | None = None
    ) -> dict[str, dict[str, Any]]:
        """构建连接状态视图。"""
        # 若服务或连接管理器尚未就绪，则返回空视图，避免管理端接口抛错。
        if not self.disaster_service or not self.disaster_service.ws_manager:
            # 即便 WS 管理器未就绪，也尽量返回 HTTP 通道占位，便于配置页预览
            return {
                self.EQSC_DISPLAY_NAME: self._build_eqsc_connection_info(),
                self.SNET_DISPLAY_NAME: self._build_snet_connection_info(),
            }

        # 先读取真实运行时连接状态，再交给统一查询服务补齐展示层所需结构。
        actual_connections = (
            self.disaster_service.ws_manager.get_all_connections_status()
        )
        snapshot = self.source_runtime_query.build_runtime_snapshot(
            actual_connections=actual_connections,
            latency_cache=self.latency_cache,
        )
        connections = dict(snapshot.get("connections", {}))
        # HTTP 通道不是 WebSocket 连接组，单独合并进连接状态面板。
        # catalog 里 jma_tsunami_eqsc 的 connection_group="eqsc" 会生成占位条目：
        # - 旧逻辑展示键为原始 "eqsc"，status 默认 “未连接”
        # - 新逻辑展示键为 "EQSC API"
        # 两种都要先剔除，再写入正式 HTTP 通道状态，避免前端读到占位“未连接”。
        for stale_key, info in list(connections.items()):
            key_lower = str(stale_key or "").strip().lower()
            provider = ""
            if isinstance(info, dict):
                provider = str(info.get("provider") or "").strip().lower()
            if (
                key_lower in {"eqsc", "eqsc api"}
                or key_lower.startswith("eqsc")
                or provider == "eqsc"
            ):
                connections.pop(stale_key, None)
        connections[self.EQSC_DISPLAY_NAME] = self._build_eqsc_connection_info()
        # 覆盖 catalog 占位条目，附带轮询运行态与 HTTP 语义
        connections[self.SNET_DISPLAY_NAME] = self._build_snet_connection_info()
        return connections

    def build_api_payload(
        self, expected_sources: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """构建 /api/connections 响应载荷。"""
        return {
            "connections": self.build(expected_sources),
            "timestamp": datetime.now().isoformat(),
        }
