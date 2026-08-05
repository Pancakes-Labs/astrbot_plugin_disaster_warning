"""
灾害服务缓存持久化服务。
负责地震列表缓存与 EEW 查询状态缓存的加载/保存，
减少 DisasterWarningService 中的文件持久化细节。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ...services.identity.event_identity import ensure_utc_datetime


class DisasterServiceCacheService:
    """灾害服务缓存持久化服务。"""

    # 未来时间容差窗口（秒）：历史记录被错误时区解析到“未来超过 1 小时”时视为污染。
    # 统一在此维护，避免多个恢复路径各自硬编码导致行为漂移。
    FUTURE_POLLUTION_TOLERANCE_SECONDS = 3600

    @staticmethod
    def _is_future_contaminated(
        issued_at_raw: str,
        source_id: str,
        now_utc: datetime | None = None,
    ) -> bool:
        """判断发布时间是否被错误时区污染到未来（超过容差窗口）。

        防御性工具：若恢复出的发布时间被解析到未来（超过 1 小时），
        说明该记录的时间被错误时区污染，应直接丢弃避免复活脏状态。

        Args:
            issued_at_raw: 原始发布时间字符串。
            source_id: 数据源 ID，用于时区解析。
            now_utc: 当前 UTC 时间；不传时内部实时获取。

        Returns:
            True 表示该时间被污染到未来，应丢弃。
        """
        if not issued_at_raw:
            return True
        now = now_utc if now_utc is not None else datetime.now(timezone.utc)
        issued_dt = ensure_utc_datetime(issued_at_raw, source_id=source_id)
        if issued_dt is None:
            return True
        return issued_dt > now + timedelta(
            seconds=DisasterServiceCacheService.FUTURE_POLLUTION_TOLERANCE_SECONDS
        )

    def __init__(self, service):
        self.service = service  # 主服务 DisasterWarningService 实例

    def load_earthquake_lists_cache(self) -> None:
        """从本地文件恢复已保存的地震列表历史缓存。"""
        try:
            # 校验缓存文件是否存在
            if os.path.exists(self.service.cache_file):
                with open(self.service.cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                    # 校验缓存数据的类型与字段，防止版本更新后格式错乱
                    if isinstance(data, dict) and "cenc" in data and "jma" in data:
                        self.service.earthquake_lists.clear()  # 清理旧数据
                        self.service.earthquake_lists.update(data)  # 载入新缓存
                        logger.debug("[灾害预警] 已恢复 Wolfx 地震列表本地缓存")
            else:
                logger.debug("[灾害预警] 本地缓存文件不存在，将使用空的 Wolfx 地震列表")
        except Exception as e:
            logger.warning(f"[灾害预警] 加载 Wolfx 地震列表缓存失败: {e}")

    def save_earthquake_lists_cache(self) -> None:
        """保存地震列表缓存到本地文件，并在写入成功后进行原子替换。"""
        temp_file = (
            self.service.cache_file + ".tmp"
        )  # 使用临时文件写入，避免直接写入覆盖破坏数据
        try:
            # 确保存储目录存在
            if not os.path.exists(self.service.storage_dir):
                os.makedirs(self.service.storage_dir, exist_ok=True)

            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.service.earthquake_lists, f, ensure_ascii=False)

            # 采用临时文件 + 原子替换，降低服务异常退出时写坏缓存文件的风险。
            if os.path.exists(self.service.cache_file):
                os.replace(temp_file, self.service.cache_file)
            else:
                os.rename(temp_file, self.service.cache_file)

            logger.info("[灾害预警] Wolfx 地震列表缓存已保存")
        except Exception as e:
            logger.error(f"[灾害预警] 保存 Wolfx 地震列表缓存失败: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

    def load_eew_query_cache(self) -> None:
        """从本地文件恢复紧急地震预警(EEW)的查询状态缓存。"""
        restored: dict[str, dict[str, Any]] = {}
        try:
            if os.path.exists(self.service.eew_query_cache_file):
                with open(self.service.eew_query_cache_file, encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    logger.warning("[灾害预警] EEW 查询缓存格式无效，已忽略")
                else:
                    active_state = getattr(self.service, "eew_query_state", {})
                    # 获取当前支持的发布机构集合，以支持新版本向后兼容
                    if isinstance(active_state, dict) and active_state:
                        supported_institutions = set(active_state.keys())
                    else:
                        institutions = getattr(
                            self.service.eew_query_service, "institutions", {}
                        )
                        supported_institutions = set(institutions.keys())

                    # 当前 UTC 时间，用于剔除“被错误时区污染到未来”的历史状态
                    now_utc = datetime.now(timezone.utc)

                    for key, value in data.items():
                        # 仅恢复当前仍受支持的机构键，避免历史版本缓存污染现有状态结构。
                        if supported_institutions and key not in supported_institutions:
                            continue
                        if not isinstance(value, dict):
                            continue
                        issued_at_raw = str(value.get("issued_at") or "").strip()
                        if self._is_future_contaminated(
                            issued_at_raw,
                            source_id=str(value.get("source_id") or "").strip(),
                            now_utc=now_utc,
                        ):
                            continue
                        restored[key] = value  # 校验通过，载入缓存
            else:
                logger.debug("[灾害预警] EEW 查询缓存文件不存在，将使用空状态")
        except Exception as e:
            logger.warning(f"[灾害预警] 加载 EEW 查询缓存失败: {e}")

        self.service.eew_query_state = restored
        # 兜底逻辑：如果缓存丢失或缓存内缺少部分机构的数据，从内存中的近期事件记录中尝试恢复
        self._restore_eew_query_state_from_recent_pushes()
        # 二次兜底逻辑：如果统计管理器初始化完成，还可以尝试从本地 sqlite 数据库读取近期历史来补齐
        if (
            self.service.statistics_manager
            and self.service.statistics_manager._db_initialized
        ):
            try:
                import asyncio

                loop = asyncio.get_running_loop()
                loop.create_task(self._restore_eew_query_state_from_db_recent_events())
            except RuntimeError:
                pass
        if self.service.eew_query_state:
            logger.debug("[灾害预警] 已恢复 EEW 查询缓存/近期状态")

    def _restore_eew_query_state_from_recent_pushes(self) -> None:
        """从统计管理器的内存近期推送中恢复机构级 EEW 状态。"""
        recent_pushes = getattr(self.service.statistics_manager, "stats", {}).get(
            "recent_pushes", []
        )
        if not isinstance(recent_pushes, list) or not recent_pushes:
            return

        state = self.service.eew_query_state
        institutions = getattr(self.service.eew_query_service, "institutions", {})
        # 建立“数据源ID”到“发布机构键”的映射关系，用于快速归类
        source_to_institution: dict[str, str] = {}
        for institution_key, meta in institutions.items():
            for source_id in meta.get("source_ids", []):
                source_to_institution[str(source_id).strip()] = institution_key

        # 当前 UTC 时间，用于剔除“被错误时区污染到未来”的历史状态
        now_utc = datetime.now(timezone.utc)

        # 遍历近期推送，提取其中的地震预警(earthquake_warning)事件
        for record in recent_pushes:
            if not isinstance(record, dict):
                continue
            if str(record.get("type") or "").strip() != "earthquake_warning":
                continue
            source_id = str(
                record.get("source_id") or record.get("source") or ""
            ).strip()
            institution_key = source_to_institution.get(source_id)
            # 如果对应发布机构已有最新状态，则跳过（内存列表中较早的记录代表更老的状态）
            if not institution_key or institution_key in state:
                continue
            issued_at = str(record.get("time") or "").strip()
            if self._is_future_contaminated(
                issued_at, source_id=source_id, now_utc=now_utc
            ):
                continue
            # 组装 EEW 查询状态字典
            state[institution_key] = {
                "source_id": source_id,
                "event_id": str(
                    record.get("real_event_id")
                    or record.get("event_id")
                    or record.get("unique_id")
                    or ""
                ).strip(),
                "display_place": str(
                    record.get("place_name") or record.get("description") or "未知地点"
                ).strip(),
                "display_magnitude": record.get("magnitude"),
                "updates": int(
                    record.get("report_num") or record.get("update_count") or 1
                ),
                "issued_at": issued_at,
                "expires_at": str(record.get("expires_at") or "").strip(),
                "fingerprint": str(
                    record.get("unique_id") or record.get("event_id") or ""
                ).strip(),
            }

    async def _restore_eew_query_state_from_db_recent_events(self) -> None:
        """从数据库近期历史记录中补齐缺失的机构级 EEW 状态。"""
        manager = getattr(self.service, "statistics_manager", None)
        if manager is None or not getattr(manager, "_db_initialized", False):
            return

        try:
            # 载入最多最近 1000 条事件记录
            recent_events = await manager.db.get_recent_events(1000)
        except Exception as e:
            logger.debug(f"[灾害预警] 从数据库补齐 EEW 查询状态失败: {e}")
            return

        if not isinstance(recent_events, list) or not recent_events:
            return

        state = self.service.eew_query_state
        institutions = getattr(self.service.eew_query_service, "institutions", {})
        source_to_institution: dict[str, str] = {}
        for institution_key, meta in institutions.items():
            for source_id in meta.get("source_ids", []):
                source_to_institution[str(source_id).strip()] = institution_key

        # 当前 UTC 时间，用于剔除“被错误时区污染到未来”的历史状态
        now_utc = datetime.now(timezone.utc)

        # 遍历数据库近期事件并填补状态
        for record in recent_events:
            if not isinstance(record, dict):
                continue
            if str(record.get("type") or "").strip() != "earthquake_warning":
                continue
            source_id = str(
                record.get("source_id") or record.get("source") or ""
            ).strip()
            institution_key = source_to_institution.get(source_id)
            if not institution_key or institution_key in state:
                continue
            issued_at = str(record.get("time") or "").strip()
            if self._is_future_contaminated(
                issued_at, source_id=source_id, now_utc=now_utc
            ):
                continue
            # 组装状态元数据
            state[institution_key] = {
                "source_id": source_id,
                "event_id": str(
                    record.get("real_event_id")
                    or record.get("event_id")
                    or record.get("unique_id")
                    or ""
                ).strip(),
                "display_place": str(
                    record.get("place_name") or record.get("description") or "未知地点"
                ).strip(),
                "display_magnitude": record.get("magnitude"),
                "updates": int(
                    record.get("report_num") or record.get("update_count") or 1
                ),
                "issued_at": issued_at,
                "expires_at": str(record.get("expires_at") or "").strip(),
                "fingerprint": str(
                    record.get("unique_id") or record.get("event_id") or ""
                ).strip(),
            }

    def save_eew_query_cache(self) -> None:
        """保存 EEW 查询状态到本地文件，并在写入成功后进行原子替换。"""
        temp_file = self.service.eew_query_cache_file + ".tmp"
        try:
            if not os.path.exists(self.service.storage_dir):
                os.makedirs(self.service.storage_dir, exist_ok=True)

            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.service.eew_query_state, f, ensure_ascii=False)

            # 与地震列表缓存保持一致，统一使用临时文件覆盖策略提升写入安全性。
            if os.path.exists(self.service.eew_query_cache_file):
                os.replace(temp_file, self.service.eew_query_cache_file)
            else:
                os.rename(temp_file, self.service.eew_query_cache_file)

            logger.debug("[灾害预警] EEW 查询缓存已保存")
        except Exception as e:
            logger.error(f"[灾害预警] 保存 EEW 查询缓存失败: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
