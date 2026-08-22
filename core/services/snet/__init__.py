"""S-Net 海底震度服务子系统。"""

from .snet_poll_service import SnetPollService

__all__ = ["SnetPeakService", "SnetPollService"]


def __getattr__(name: str):
    """延迟导出 SnetPeakService，避免与 storage 形成包级循环导入。"""
    if name == "SnetPeakService":
        from .snet_peak_service import SnetPeakService

        return SnetPeakService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
