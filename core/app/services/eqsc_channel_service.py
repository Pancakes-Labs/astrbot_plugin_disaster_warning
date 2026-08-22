"""
EQSC 通道服务。

负责 EQSC HTTP 通道的公共基础设施：
- 组总闸与子开关解析（resolve_eqsc_flags）
- AccessToken 管理（EqscTokenManager）与启动预热 / 后台保活
- 通道健康状态与连接计数（get_health_status / get_connection_counts）
- 通道级熔断器（连续失败短路，供富化 / 查询复用）

台风富化、海啸轮询、CENC 烈度速报轮询等 EQSC 子能力共享本通道，
避免各自维护一份鉴权状态与熔断状态。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ....utils.plugin_logger import plugin_logger as logger
from ...network.http.eqsc_token_manager import EqscTokenManager


class EqscChannelService:
    """EQSC HTTP 通道总闸服务。"""

    def __init__(
        self,
        config: dict[str, Any],
    ):
        """初始化 EQSC 通道服务。

        Args:
            config: 插件全局配置字典。
        """
        eqsc_config = config.get("data_sources", {}).get("eqsc", {})
        if not isinstance(eqsc_config, dict):
            eqsc_config = {}
        self._eqsc_config = eqsc_config
        channel_enabled, typhoon_enrichment = self.resolve_eqsc_flags(eqsc_config)
        # 组总闸：控制 EQSC 通道（鉴权/连通/后续子能力入口）
        self._channel_enabled = channel_enabled
        # 子能力：台风轮询/富化（可独立关闭）
        self._typhoon_enrichment_enabled = typhoon_enrichment
        self._token_manager = EqscTokenManager(eqsc_config)

        # 熔断器参数（通道级：连续失败短路）
        self._circuit_failure_threshold = 5
        self._circuit_cooldown = 300
        self._circuit_failures = 0
        self._circuit_open_until: float = 0.0

        # AccessToken 后台保活：状态面板只读 has_valid_access_token，
        # 若仅启动预热、无业务请求触发 get_access_token，约 1 小时后会误显示“鉴权失效”。
        self._token_keepalive_task: asyncio.Task | None = None
        self._token_keepalive_stop = asyncio.Event()
        # 最短检查间隔，避免异常情况下 tight loop
        self._token_keepalive_min_interval = 30.0
        # 无有效 token 时的重试间隔
        self._token_keepalive_retry_interval = 120.0

    @staticmethod
    def resolve_eqsc_flags(eqsc_config: dict[str, Any] | None) -> tuple[bool, bool]:
        """解析 EQSC 组总闸与台风子开关。

        Returns:
            (channel_enabled, typhoon_enrichment_enabled)

        兼容旧配置：若缺少 typhoon 字段，则回退使用 enabled 的值
        （旧语义下 enabled 同时表示通道与台风轮询）。
        """
        if not isinstance(eqsc_config, dict):
            return False, False
        channel_enabled = bool(eqsc_config.get("enabled", False))
        if "typhoon" in eqsc_config:
            typhoon_enrichment = bool(eqsc_config.get("typhoon"))
        else:
            typhoon_enrichment = channel_enabled
        return channel_enabled, typhoon_enrichment

    @property
    def is_channel_enabled(self) -> bool:
        """EQSC 通道是否可用：组总闸开启且 refresh_token 已配置。"""
        return self._channel_enabled and self._token_manager.is_configured

    @property
    def is_typhoon_enrichment_enabled(self) -> bool:
        """台风子能力是否开启（不含 token 判定）。"""
        return bool(self._typhoon_enrichment_enabled)

    @property
    def token_manager(self) -> EqscTokenManager:
        """共享的 EQSC 令牌管理器（供子客户端复用）。"""
        return self._token_manager

    @staticmethod
    def resolve_shared_token_manager(service: Any) -> EqscTokenManager | None:
        """从灾害主服务安全解析共享的 EQSC 令牌管理器。

        供台风富化、海啸轮询、CENC 烈度速报轮询等 EQSC 子能力复用，
        避免各自维护一份鉴权状态。

        Args:
            service: 灾害主服务（DisasterWarningService）实例。

        Returns:
            共享的 EqscTokenManager；服务缺失或类型不符时返回 None。
        """
        if service is None:
            return None
        channel = getattr(service, "eqsc_channel_service", None)
        if channel is None:
            return None
        token_manager = getattr(channel, "token_manager", None)
        if isinstance(token_manager, EqscTokenManager):
            return token_manager
        return None

    # ---------- 熔断器（通道级） ----------

    def is_circuit_open(self) -> bool:
        """检查通道熔断器是否处于开启状态。"""
        if self._circuit_failures >= self._circuit_failure_threshold:
            if time.time() < self._circuit_open_until:
                return True
            # 冷却期已过，重置熔断器
            self._circuit_failures = 0
        return False

    def record_success(self) -> None:
        """记录一次成功，重置熔断器。"""
        self._circuit_failures = 0

    def record_failure(self) -> None:
        """记录一次失败，可能触发熔断器。"""
        self._circuit_failures += 1
        if self._circuit_failures >= self._circuit_failure_threshold:
            self._circuit_open_until = time.time() + self._circuit_cooldown
            logger.warning(
                f"[灾害预警] EQSC 熔断器已开启，{self._circuit_cooldown}秒内跳过 EQSC 查询"
            )

    # ---------- 健康与连接状态 ----------

    def get_health_status(self) -> dict[str, Any]:
        """返回 EQSC 通道健康快照，供管理端连接状态面板使用。"""
        circuit_open = self.is_circuit_open()
        # config_enabled：组总闸（不混入 token）
        config_enabled = bool(self._channel_enabled)
        typhoon_enrichment = bool(self._typhoon_enrichment_enabled)
        access_token_valid = bool(self._token_manager.has_valid_access_token)
        # 海啸子开关：缺省跟随通道总闸（与配置校验语义一致）
        raw_eqsc = self._eqsc_config if isinstance(self._eqsc_config, dict) else {}
        if "jma_tsunami" in raw_eqsc:
            jma_tsunami = bool(raw_eqsc.get("jma_tsunami"))
        else:
            jma_tsunami = config_enabled
        if "china_cenc_intensity_report" in raw_eqsc:
            cenc_ir = bool(raw_eqsc.get("china_cenc_intensity_report"))
        else:
            cenc_ir = config_enabled
        return {
            # enabled：通道可工作（组总闸 + token 已配置）
            "enabled": self.is_channel_enabled,
            "config_enabled": config_enabled,
            "typhoon": typhoon_enrichment,
            # 台风富化实际可工作
            "typhoon_active": self.is_channel_enabled and typhoon_enrichment,
            "jma_tsunami": jma_tsunami,
            "china_cenc_intensity_report": cenc_ir,
            "token_configured": bool(self._token_manager.is_configured),
            "access_token_valid": access_token_valid,
            "circuit_open": circuit_open,
            "circuit_failures": int(self._circuit_failures),
            "connection_type": "http",
            "provider": "eqsc",
            # EQSC 子数据源：台风 → 海啸 → CENC 烈度速报
            "sub_sources": {
                "china_typhoon": typhoon_enrichment,
                "jma_tsunami": jma_tsunami,
                "china_cenc_intensity_report": cenc_ir,
            },
        }

    def get_connection_counts(self) -> tuple[int, int]:
        """返回 EQSC 对活跃/总连接数的贡献 (active, total)。

        - total：组总闸开启且 refresh_token 已配置时计 1
        - active：当前内存 AccessToken 仍有效时计 1
        """
        health = self.get_health_status()
        total = 1 if bool(health.get("enabled")) else 0
        active = 1 if bool(health.get("access_token_valid")) else 0
        return active, total

    @staticmethod
    def resolve_connection_counts(service: Any) -> tuple[int, int]:
        """从灾害主服务安全解析 EQSC 连接计数，供状态/实时载荷复用。"""
        if service is None:
            return 0, 0
        channel = getattr(service, "eqsc_channel_service", None)
        if channel is None:
            return 0, 0
        getter = getattr(channel, "get_connection_counts", None)
        if not callable(getter):
            return 0, 0
        try:
            result = getter()
        except Exception:
            return 0, 0
        if not isinstance(result, tuple) or len(result) != 2:
            return 0, 0
        try:
            return int(result[0] or 0), int(result[1] or 0)
        except (TypeError, ValueError):
            return 0, 0

    # ---------- AccessToken 预热与保活 ----------

    async def warm_up_access_token(self) -> bool:
        """启动后主动预热 AccessToken，避免状态面板长期显示鉴权失效。

        仅在 EQSC 通道已启用且 token 已配置时请求；不触发业务查询。
        成功返回 True，未启用/失败返回 False。
        """
        if not self.is_channel_enabled:
            return False
        try:
            access_token = await self._token_manager.get_access_token()
            if access_token:
                logger.debug("[灾害预警] EQSC AccessToken 预热成功")
                return True
            logger.warning("[灾害预警] EQSC AccessToken 预热失败：未拿到有效令牌")
            return False
        except Exception as exc:
            logger.warning(
                f"[灾害预警] EQSC AccessToken 预热异常: {type(exc).__name__}: {exc}"
            )
            return False

    def start_token_keepalive(self, *, register_task=None) -> None:
        """启动 AccessToken 后台保活循环（幂等）。

        在过期前提前调用 get_access_token 续期，保证状态面板在无台风业务时
        也不会因内存 token 过期而长期显示“鉴权失效”。

        Args:
            register_task: 可选回调，用于把保活任务登记到服务级后台任务集合，
                便于停机时统一回收（例如 DisasterService.register_background_task）。
        """
        if not self.is_channel_enabled:
            return
        if (
            self._token_keepalive_task is not None
            and not self._token_keepalive_task.done()
        ):
            return
        self._token_keepalive_stop.clear()
        self._token_keepalive_task = asyncio.create_task(
            self._token_keepalive_loop(),
            name="dw_eqsc_token_keepalive",
        )
        if callable(register_task):
            try:
                register_task(self._token_keepalive_task)
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] 注册 EQSC token 保活任务失败: {type(exc).__name__}: {exc}"
                )

    async def stop_token_keepalive(self) -> None:
        """停止 AccessToken 后台保活循环。"""
        self._token_keepalive_stop.set()
        task = self._token_keepalive_task
        self._token_keepalive_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug(
                f"[灾害预警] 停止 EQSC token 保活任务异常: {type(exc).__name__}: {exc}"
            )

    async def _token_keepalive_loop(self) -> None:
        """周期性确保内存 AccessToken 未过期。"""
        logger.debug("[灾害预警] EQSC AccessToken 保活循环已启动")
        try:
            while not self._token_keepalive_stop.is_set():
                if not self.is_channel_enabled:
                    break

                # 先尝试获取/续期（内部会在提前刷新窗口内真正打网络）
                try:
                    token = await self._token_manager.get_access_token()
                except Exception as exc:
                    logger.debug(
                        f"[灾害预警] EQSC AccessToken 保活刷新异常: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    token = None

                if not token:
                    sleep_seconds = self._token_keepalive_retry_interval
                else:
                    # 在真正过期前 access_advance_seconds 触发续期；
                    # 再留一点缓冲，避免刚好踩在边界。
                    remaining = self._token_manager.seconds_until_expiry()
                    advance = float(self._token_manager.access_advance_seconds or 60)
                    sleep_seconds = max(
                        self._token_keepalive_min_interval,
                        remaining - advance,
                    )
                    # 上限避免极端有效期导致长时间不检查配置变更
                    sleep_seconds = min(sleep_seconds, 1800.0)

                try:
                    await asyncio.wait_for(
                        self._token_keepalive_stop.wait(),
                        timeout=sleep_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            logger.debug("[灾害预警] EQSC AccessToken 保活循环已停止")

    async def close(self) -> None:
        """关闭通道服务：停止保活循环并释放令牌管理器资源。"""
        await self.stop_token_keepalive()
        await self._token_manager.close()


__all__ = ["EqscChannelService"]
