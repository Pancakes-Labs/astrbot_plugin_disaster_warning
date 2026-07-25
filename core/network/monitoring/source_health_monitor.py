"""
数据源健康探测器。
负责 Web 管理端的数据源主机映射、TCP 延迟探测与后台缓存刷新，减少 WebAdminServer 的职责聚合。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger


class SourceHealthMonitor:
    """数据源健康探测器。"""

    # 静态映射：数据源标识 -> 探测目标主机名/IP 地址
    HOST_MAP: dict[str, str] = {
        "fan_studio_all": "ws.fanstudio.tech",
        "fan_studio_cenc_ir": "ws.fanstudio.tech",
        "p2p_main": "api.p2pquake.net",
        "wolfx_all": "ws-api.wolfx.jp",
        "global_quake": "api.aloys23.link",
        # EQSC 为 HTTP 辅助通道，默认探测官方主机；可被 host_overrides 覆盖
        "eqsc": "equake.top",
        # S-Net：MSIL 瓦片源（TCP 443 探测连通性）
        "snet_msil": "www.msil.go.jp",
    }

    # 静态映射：数据源标识 -> 在管理后台展示的可读中文名称
    DISPLAY_MAP: dict[str, str] = {
        "fan_studio_all": "FAN Studio",
        "fan_studio_cenc_ir": "FAN Studio 烈度速报",
        "p2p_main": "P2P地震情報",
        "wolfx_all": "Wolfx",
        "global_quake": "Global Quake",
        "eqsc": "EQSC API",
        "snet_msil": "NIED S-Net",
    }

    def __init__(
        self,
        latency_cache: dict[str, float | None] | None = None,
        host_overrides: dict[str, str] | None = None,
    ):
        """初始化延迟缓存容器。

        Args:
            latency_cache: 共享延迟缓存字典。
            host_overrides: 可选主机覆盖表（如 EQSC base_url 解析出的主机名）。
        """
        self.latency_cache = latency_cache if latency_cache is not None else {}
        self._host_overrides: dict[str, str] = {
            str(key): str(value).strip()
            for key, value in (host_overrides or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def set_host_override(self, source_name: str, host: str | None) -> None:
        """动态更新某个数据源的探测主机。"""
        key = str(source_name or "").strip()
        value = str(host or "").strip()
        if not key:
            return
        if value:
            self._host_overrides[key] = value
        else:
            self._host_overrides.pop(key, None)

    def get_expected_data_sources(self) -> dict[str, str]:
        """返回管理端面板预期展示的数据源列表。"""
        return dict(self.DISPLAY_MAP)

    def get_data_source_host(self, source_name: str) -> str | None:
        """获取指定数据源对应的探测主机名。"""
        if source_name in self._host_overrides:
            return self._host_overrides[source_name]
        return self.HOST_MAP.get(source_name)

    async def ping_host(
        self, host: str, port: int = 443, timeout: float = 3.0
    ) -> float | None:
        """使用 TCP 连接测试主机延迟。"""
        try:
            # 记录连接开始时间
            start_time = asyncio.get_running_loop().time()
            # 建立异步 TCP 连接
            future = asyncio.open_connection(host, port)
            _reader, writer = await asyncio.wait_for(future, timeout=timeout)
            # 记录连接建立完成时间
            end_time = asyncio.get_running_loop().time()

            # 关闭写流并确保连接释放
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            # 返回以毫秒(ms)为单位的往返时间差值
            return (end_time - start_time) * 1000
        except (asyncio.TimeoutError, OSError):
            # 连接超时或套接字异常返回 None 代表不可达
            return None
        except Exception as e:
            logger.error(
                "[灾害预警] TCP 延迟探测发生非预期异常，主机为 %s，端口为 %s，错误为 %s",
                host,
                port,
                e,
                exc_info=True,
            )
            return None

    async def run_background_ping_loop(self, interval_seconds: float = 30.0):
        """后台定期更新延迟缓存。"""
        logger.debug("[灾害预警] 启动后台延迟检测任务")
        # 记录每个数据源连续 ping 失败的次数，用于平滑去噪
        ping_failures: dict[str, int] = {}

        while True:
            try:
                expected_sources = self.get_expected_data_sources()
                ping_tasks: dict[str, Any] = {}

                # 构造所有数据源主机的延迟检测协程
                for source_name in expected_sources.keys():
                    host = self.get_data_source_host(source_name)
                    if host:
                        ping_tasks[source_name] = self.ping_host(
                            host, port=443, timeout=2.0
                        )

                # 并发执行所有 ping 任务，并回收异常
                if ping_tasks:
                    results = await asyncio.gather(
                        *ping_tasks.values(), return_exceptions=True
                    )
                    # 处理每个源的测速结果
                    for source_name, result in zip(ping_tasks.keys(), results):
                        if isinstance(result, Exception) or result is None:
                            # 累加失败计数器
                            ping_failures[source_name] = (
                                ping_failures.get(source_name, 0) + 1
                            )
                            # 连续失败达到 3 次，代表主机离线，才将延迟缓存标记为 None
                            if ping_failures[source_name] >= 3:
                                self.latency_cache[source_name] = None
                        else:
                            # 探测成功，重置失败次数并缓存当前测算出的毫秒级延迟
                            ping_failures[source_name] = 0
                            self.latency_cache[source_name] = result

                # 等待设定的间隔时间进入下一轮探测
                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                logger.info("[灾害预警] 后台延迟检测任务已停止")
                break
            except Exception as e:
                logger.error(f"[灾害预警] 后台延迟检测出错: {e}")
                await asyncio.sleep(interval_seconds)
