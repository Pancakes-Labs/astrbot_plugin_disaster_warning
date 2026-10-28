"""
接入后副作用服务。
负责在网络接入层解析消息后处理与业务无关但与接入结果相关的旁路副作用，
例如 Wolfx 地震列表缓存更新与摘要日志记录。
"""

from __future__ import annotations

from typing import Any


# Map each list message type to its cache key and its log source name.
_EQLIST_TARGETS = {
    "cenc_eqlist": ("cenc", "wolfx_cenc_eqlist"),
    "jma_eqlist": ("jma", "wolfx_jma_eqlist"),
}


class SourceIngressSideEffectService:
    """接入后副作用服务。"""

    def __init__(self, service):
        """保存灾害服务引用，供旁路副作用访问共享状态。"""
        self.service = service

    async def process_message(
        self,
        *,
        source_id: str,
        message_type: str,
        payload_data: dict[str, Any] | None = None,
    ) -> None:
        """处理解析前后即可确定的旁路副作用。"""
        payload = payload_data if isinstance(payload_data, dict) else {}
        if not payload:
            return

        list_target = _EQLIST_TARGETS.get(message_type)
        if list_target is None:
            return
        list_key, log_source = list_target
        # Wolfx 的列表消息在正式事件解析前，就可以先刷新本地缓存并写摘要日志。
        self.service.earthquake_list_service.update_earthquake_list(list_key, payload)
        if self.service.message_logger:
            self.service.message_logger.log_earthquake_list_summary(
                source=log_source,
                earthquake_list=payload,
            )


__all__ = ["SourceIngressSideEffectService"]
