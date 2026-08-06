"""
统计加载服务。
负责统计管理器的 JSON / 数据库加载、历史迁移与内存状态恢复，
减少主管理器中的持久化读取与迁移编排职责。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from astrbot.api import logger

from ...domain.typhoon import format_display_name
from ...message.presenters.weather_constants import (
    COLOR_LEVEL_EMOJI,
    SORTED_WEATHER_TYPES,
)
from ...services.identity.event_classifier import is_major_record
from .typhoon_stats_accumulator import record_typhoon_observation


class StatsLoadService:
    """统计加载与迁移服务。"""

    def __init__(self, manager):
        self.manager = manager

    async def load(self) -> None:
        """加载统计数据。"""
        # 当前加载顺序是“先从 JSON 补基础状态，再从数据库恢复 recent_pushes 与聚合结果”，
        # 同时兼容历史版本仅使用 JSON 的迁移场景。
        json_has_events = False
        if self.manager.stats_file.exists():
            try:
                saved_stats = self.manager.repository.load_stats_file()
                json_has_events = bool(saved_stats.get("recent_pushes"))

                # 先剥离旧的近期事件列表，只把基础统计字段合并回当前内存态。
                recent_pushes_backup = saved_stats.pop("recent_pushes", None)
                self.manager._merge_stats(self.manager.stats, saved_stats)
                # 合并完成后恢复去重集合，避免历史记录在启动后被重复计数。
                self._restore_recorded_ids()

                if recent_pushes_backup:
                    saved_stats["recent_pushes"] = recent_pushes_backup
            except Exception as e:
                logger.error(f"[灾害预警] 加载统计数据失败: {e}")

        try:
            db_events = await self.manager.db.get_recent_events(500)
            time_series_counts = await self.manager.db.get_time_series_counts()
            rebuild_events = await self.manager.db.get_statistics_rebuild_events()
            if db_events:
                # 数据库是当前版本的主要历史来源，命中后优先以数据库结果覆盖近期事件缓存。
                logger.debug(f"[灾害预警] 从数据库加载了 {len(db_events)} 条历史记录")
                self.manager.stats["recent_pushes"] = db_events
                self._restore_recorded_ids_from_db(db_events)
                self._restore_time_series_counts(time_series_counts)

                db_stats = await self.manager.db.get_statistics()
                by_source_from_db = db_stats.get("by_source", {}) if db_stats else {}
                by_type_from_db = db_stats.get("by_type", {}) if db_stats else {}
                total_events_from_db = (
                    db_stats.get("total_events", 0) if db_stats else 0
                )
                if by_source_from_db or by_type_from_db or total_events_from_db:
                    # 把数据库聚合结果重新包回 defaultdict，保持后续写入逻辑无需额外判空。
                    if by_source_from_db:
                        self.manager.stats["by_source"] = defaultdict(
                            int, by_source_from_db
                        )
                    if by_type_from_db:
                        # 兼容处理并归并历史遗留的 "weather" 类型为 "weather_alarm"（即“气象预警”）
                        normalized_by_type = defaultdict(int)
                        for k, v in by_type_from_db.items():
                            key_norm = "weather_alarm" if k == "weather" else k
                            normalized_by_type[key_norm] += v
                        self.manager.stats["by_type"] = normalized_by_type
                    if total_events_from_db:
                        self.manager.stats["total_events"] = int(total_events_from_db)

                await self._restore_derived_stats_from_db(
                    rebuild_events,
                    allow_weather_fallback=False,
                )

                if json_has_events and self.manager.stats_file.exists():
                    # 数据库恢复成功后，清理 JSON 中残留的旧事件列表，避免双份历史来源并存。
                    self._cleanup_json_recent_pushes()
            elif json_has_events:
                logger.info("[灾害预警] 检测到 JSON 历史记录，开始迁移到数据库...")
                await self.migrate_json_from_file()
        except Exception as e:
            logger.error(f"[灾害预警] 从数据库加载失败: {e}")

        # S-Net 峰值档案独立于 events 表：即使通用事件恢复失败也要尝试恢复。
        await self._restore_snet_stats()

    async def migrate_json_from_file(self) -> None:
        """将 JSON 文件中的历史记录一次性迁移到数据库。"""
        try:
            saved_stats = self.manager.repository.load_stats_file()
            recent_pushes = saved_stats.get("recent_pushes", [])
            # 没有旧 recent_pushes 时说明无需迁移，直接返回。
            if not recent_pushes:
                return

            logger.info(
                f"[灾害预警] 开始迁移 {len(recent_pushes)} 条历史记录到数据库..."
            )
            migrated = 0
            failed_records = []
            for record in recent_pushes:
                try:
                    record["is_major"] = is_major_record(record)
                    # 迁移时将 "weather" 转换为标准 "weather_alarm" 类型
                    if record.get("type") == "weather":
                        record["type"] = "weather_alarm"
                    await self.manager.db.insert_event(record)
                    migrated += 1
                except Exception as e:
                    logger.warning(f"[灾害预警] 迁移记录失败: {e}")
                    failed_records.append(record)

            logger.info(f"[灾害预警] 历史记录迁移完成，成功 {migrated} 条")

            saved_stats["recent_pushes"] = failed_records
            self.manager.repository.save_stats(saved_stats)
        except Exception as e:
            logger.error(f"[灾害预警] 迁移 JSON 历史记录失败: {e}")

    def _restore_recorded_ids(self) -> None:
        # 从 JSON 恢复最近事件集合，避免重启后短时间内重复统计同一批历史事件。
        if "recent_event_ids" in self.manager.stats:
            self.manager._recorded_event_ids.update(
                self.manager.stats["recent_event_ids"]
            )
        if "recent_source_event_ids" in self.manager.stats:
            self.manager._recorded_source_event_ids.update(
                self.manager.stats["recent_source_event_ids"]
            )

    def _restore_recorded_ids_from_db(self, db_events: list[dict[str, Any]]) -> None:
        """根据数据库事件列表恢复去重集合。"""
        for evt in db_events:
            unique_id = evt.get("unique_id")
            source_key = evt.get("source_id") or evt.get("source")
            if unique_id:
                self.manager._recorded_event_ids.add(unique_id)
                if source_key:
                    self.manager._recorded_source_event_ids.add(
                        f"{source_key}:{unique_id}"
                    )

    async def refresh_derived_stats_from_database(self) -> None:
        """从数据库全量刷新统计卡片依赖的聚合与派生统计。"""
        db_stats = await self.manager.db.get_statistics()
        if db_stats:
            by_source_from_db = db_stats.get("by_source", {})
            by_type_from_db = db_stats.get("by_type", {})
            total_events_from_db = db_stats.get("total_events", 0)
            self.manager.stats["by_source"] = defaultdict(
                int,
                {
                    str(key): int(value)
                    for key, value in dict(by_source_from_db or {}).items()
                    if str(key)
                },
            )
            # 兼容处理并归并历史遗留的 "weather" 类型为 "weather_alarm"
            normalized_by_type = defaultdict(int)
            for k, v in dict(by_type_from_db or {}).items():
                if str(k):
                    key_norm = "weather_alarm" if k == "weather" else k
                    normalized_by_type[key_norm] += int(v)
            self.manager.stats["by_type"] = normalized_by_type
            self.manager.stats["total_events"] = int(total_events_from_db or 0)

        rebuild_events = await self.manager.db.get_statistics_rebuild_events()
        await self._restore_derived_stats_from_db(
            rebuild_events,
            allow_weather_fallback=False,
        )
        await self._restore_snet_stats()

    async def _restore_snet_stats(self) -> None:
        """从 snet_station_peaks 恢复内存 snet_stats。"""
        peak_service = getattr(self.manager, "snet_peak_service", None)
        if peak_service is None:
            return
        try:
            await peak_service.refresh_stats_from_database()
        except Exception as e:
            logger.warning(f"[灾害预警] 恢复 S-Net 峰值统计失败: {e}")

    def _restore_time_series_counts(self, counts: dict[str, Any] | None) -> None:
        """恢复数据库全量聚合得到的时间序列桶。"""
        counts = counts or {}
        self.manager.stats["hourly_counts"] = defaultdict(
            int,
            {
                str(key): int(value)
                for key, value in dict(counts.get("hourly_counts") or {}).items()
                if str(key)
            },
        )
        self.manager.stats["daily_counts"] = defaultdict(
            int,
            {
                str(key): int(value)
                for key, value in dict(counts.get("daily_counts") or {}).items()
                if str(key)
            },
        )

    async def _restore_derived_stats_from_db(
        self,
        events: list[dict[str, Any]],
        *,
        allow_weather_fallback: bool,
    ) -> None:
        """基于数据库全量事件重建前端统计卡片所需的派生指标。"""
        earthquake_regions: defaultdict[str, int] = defaultdict(int)
        weather_levels: defaultdict[str, int] = defaultdict(int)
        weather_types: defaultdict[str, int] = defaultdict(int)
        weather_regions: defaultdict[str, int] = defaultdict(int)
        typhoon_levels: defaultdict[str, int] = defaultdict(int)
        typhoon_max_levels: defaultdict[str, int] = defaultdict(int)
        # 风王榜：{name: {"wind_speed": float, "pressure": float|None}}
        typhoon_max_wind: dict[str, dict[str, Any]] = {}
        # 气压榜：{name: min_pressure}
        typhoon_min_pressure: dict[str, float] = {}
        # 台风个体最高等级映射表，用于 by_max_level 去重统计。
        typhoon_max_level_map: dict[str, str] = {}
        self.manager._recorded_cenc_official_region_ids.clear()

        for event in events or []:
            event_type = str(event.get("type") or "").strip()
            # 兼容历史遗留的 type 为 "weather" 记录，在统计分类中做一致性还原处理
            if event_type == "weather":
                event_type = "weather_alarm"

            if event_type == "earthquake":
                if not self._is_cenc_official_record(event):
                    continue
                place_text = self._resolve_earthquake_place_text(event)
                region = self.manager.event_support_service.extract_region(
                    place_text,
                    strict=True,
                )
                if region:
                    earthquake_regions[region] += 1
                    region_key = self._build_cenc_official_region_key(event)
                    if region_key:
                        self.manager._recorded_cenc_official_region_ids.add(region_key)
                continue

            if event_type == "weather_alarm":
                await self._record_weather_rebuild_stats(
                    event,
                    weather_levels=weather_levels,
                    weather_types=weather_types,
                    weather_regions=weather_regions,
                    allow_fallback=allow_weather_fallback,
                )
            elif event_type == "typhoon":
                self._record_typhoon_rebuild_stats(
                    event,
                    typhoon_levels=typhoon_levels,
                    typhoon_max_levels=typhoon_max_levels,
                    typhoon_max_wind=typhoon_max_wind,
                    typhoon_min_pressure=typhoon_min_pressure,
                    typhoon_max_level_map=typhoon_max_level_map,
                )

        self.manager.stats["earthquake_stats"]["by_region"] = earthquake_regions
        self.manager.stats["weather_stats"]["by_level"] = weather_levels
        self.manager.stats["weather_stats"]["by_type"] = weather_types
        self.manager.stats["weather_stats"]["by_region"] = weather_regions
        self.manager.stats["typhoon_stats"]["by_level"] = typhoon_levels
        self.manager.stats["typhoon_stats"]["by_max_level"] = typhoon_max_levels
        self.manager.stats["typhoon_stats"]["max_wind_typhoons"] = typhoon_max_wind
        self.manager.stats["typhoon_stats"]["min_pressure_typhoons"] = (
            typhoon_min_pressure
        )
        self.manager.stats["typhoon_stats"]["_typhoon_max_level_map"] = (
            typhoon_max_level_map
        )

    def _is_cenc_official_record(self, event: dict[str, Any]) -> bool:
        """判断数据库记录是否属于 CENC 正式测定统计口径。"""
        source_key = str(event.get("source_id") or event.get("source") or "").strip()
        if source_key not in {"cenc_fanstudio", "cenc_wolfx"}:
            return False

        info_type = str(event.get("info_type") or "").strip()
        if "正式" in info_type:
            return True

        # 兼容旧库：历史记录未持久化 info_type，但 earthquake 类型的 cenc_* 来源就是测定链路。
        return True

    def _resolve_earthquake_place_text(self, event: dict[str, Any]) -> str:
        """解析数据库地震记录中的原始震中文本。"""
        place_name = str(event.get("place_name") or "").strip()
        if place_name:
            return place_name

        description = str(event.get("description") or "").strip()
        if description.startswith("M") and " " in description:
            return description.split(" ", 1)[1].strip()
        return description

    def _build_cenc_official_region_key(self, event: dict[str, Any]) -> str:
        """构造数据库重建使用的 CENC 正式测定地区统计去重键。"""
        event_key = str(
            event.get("real_event_id")
            or event.get("unique_id")
            or event.get("id")
            or ""
        ).strip()
        return f"cenc_official_region:{event_key}" if event_key else ""

    async def _record_weather_rebuild_stats(
        self,
        event: dict[str, Any],
        *,
        weather_levels: defaultdict[str, int],
        weather_types: defaultdict[str, int],
        weather_regions: defaultdict[str, int],
        allow_fallback: bool,
    ) -> None:
        """从数据库气象事件记录恢复级别、类型与地区统计。"""
        title_text = str(event.get("description") or "").strip()
        headline_text = str(event.get("subtitle") or "").strip()
        detail_text = str(event.get("weather_detail") or "").strip()
        combined_text = " ".join(
            item for item in (title_text, headline_text, detail_text) if item
        )

        level = self._normalize_weather_level(event.get("level"), combined_text)
        weather_levels[level] += 1

        weather_type = "其他"
        # 仅从标题层面的文本（description 即 title_text，或者 subtitle 即 headline_text）提取气象类型，排除详细描述的影响
        search_text = headline_text if headline_text else title_text
        for name in SORTED_WEATHER_TYPES:
            if name in search_text:
                weather_type = name
                break
        weather_types[weather_type] += 1

        region = self.manager._weather_region_resolver.extract_province(combined_text)
        if not region and allow_fallback:
            region = await self.manager._weather_region_resolver.extract_province_with_fallback(
                title_text,
                " ".join(item for item in (headline_text, detail_text) if item),
            )
        if region:
            weather_regions[region] += 1

    def _record_typhoon_rebuild_stats(
        self,
        event: dict[str, Any],
        *,
        typhoon_levels: defaultdict[str, int],
        typhoon_max_levels: defaultdict[str, int],
        typhoon_max_wind: dict[str, dict[str, Any]],
        typhoon_min_pressure: dict[str, float],
        typhoon_max_level_map: dict[str, str],
    ) -> None:
        """从数据库峰值字段恢复台风统计，公式与实时聚合完全一致。"""
        # subtitle 已由入库链路写入 format_display_name 结果；
        # 这里只做缺省回退，不再复制一套中英文拼接规则。
        subtitle = str(event.get("subtitle") or "").strip()
        place_name = str(event.get("place_name") or "").strip()
        typhoon_id = str(
            event.get("real_event_id")
            or event.get("unique_id")
            or event.get("event_id")
            or ""
        ).strip()
        display_name = format_display_name(
            subtitle or place_name,
            "",
            typhoon_id,
            fallback=typhoon_id or "未知台风",
        )
        display_name = display_name.replace("TD No.", "TD")
        # 主表已经保存历史峰值，因此重建时把它作为一次完整观测输入即可。
        record_typhoon_observation(
            {
                "by_level": typhoon_levels,
                "by_max_level": typhoon_max_levels,
                "max_wind_typhoons": typhoon_max_wind,
                "min_pressure_typhoons": typhoon_min_pressure,
                "_typhoon_max_level_map": typhoon_max_level_map,
            },
            display_name=display_name,
            level=str(event.get("level") or "未知").strip() or "未知",
            wind_speed=event.get("wind_speed"),
            pressure=event.get("pressure"),
        )

    def _normalize_weather_level(self, raw_level: Any, text: str) -> str:
        """把数据库中的气象级别恢复为前端统计使用的展示键。"""
        level_text = str(raw_level or "").strip()
        for color, emoji in COLOR_LEVEL_EMOJI.items():
            if color in level_text or color in text:
                return f"{emoji}{color}"
        return "未知"

    def _cleanup_json_recent_pushes(self) -> None:
        """清理 JSON 文件中已迁移或已被数据库接管的近期事件列表。"""
        try:
            saved_on_disk = self.manager.repository.load_stats_file()
            if saved_on_disk.get("recent_pushes"):
                saved_on_disk["recent_pushes"] = []
                self.manager.repository.save_stats(saved_on_disk)
                logger.debug("[灾害预警] 已清理 JSON 文件中残留的近期推送")
        except Exception as e:
            logger.debug(f"[灾害预警] 清理 JSON 的近期推送失败（非致命）: {e}")
