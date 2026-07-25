"""
灾害预警插件 - 数据库管理模块
使用 SQLite 存储历史事件数据（异步版本，使用 aiosqlite）

Schema v2：
  events        - 每个物理事件一行（按 real_event_id+source 去重）
  event_updates - 每次推送/更新一行（原 history JSON 拆解）
"""

import json
from pathlib import Path
from typing import Any

import aiosqlite

from astrbot.api import logger

from ...utils.time_converter import TimeConverter
from ..domain.typhoon.typhoon_ids import to_eqsc_id, to_fan_id
from ..domain.typhoon.typhoon_peaks import resolve_storage_peak_fields
from ..services.identity.event_classifier import (
    MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD,
    MAJOR_WEATHER_LEVEL_KEYWORD,
    MAJOR_WEATHER_TEXT_PHRASES,
    is_major_record,
)
from .source_compat import (
    build_cenc_intensity_report_sql_predicate,
    build_source_stats_key,
    expand_source_aliases,
    format_source_name,
    is_cenc_intensity_report,
    normalize_source_name,
)


def normalize_event_type(event_type: str | None) -> str | None:
    """统一规范化事件类型，将历史遗留的 'weather' 类型映射为标准的 'weather_alarm'。"""
    if not event_type:
        return event_type
    stripped = str(event_type).strip()
    return "weather_alarm" if stripped == "weather" else stripped


class DatabaseManager:
    """数据库管理器。

    负责事件历史的建库、迁移、写入、查询与统计，
    同时维护主事件表与事件更新表之间的配套关系。
    """

    def __init__(self, db_path: Path):
        """
        初始化数据库管理器

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.connection: aiosqlite.Connection | None = None

    # ──────────────────────────── 初始化 / 迁移 ────────────────────────────

    async def initialize(self):
        """异步初始化数据库，检测并执行必要的结构迁移。"""
        try:
            # 已初始化且连接可用时直接复用，避免重复建连。
            if self.connection is not None:
                try:
                    await self.connection.execute("SELECT 1")
                    return
                except Exception:
                    # 连接失效时重建
                    try:
                        await self.connection.close()
                    except Exception:
                        pass
                    self.connection = None

            # 先确保数据库目录存在，再建立连接并统一使用字典风格行对象。
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = await aiosqlite.connect(str(self.db_path))
            self.connection.row_factory = aiosqlite.Row

            cursor = await self.connection.cursor()
            await self._ensure_schema(cursor)
            await self.connection.commit()
            logger.info(f"[灾害预警] 数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"[灾害预警] 数据库初始化失败: {e}")
            raise

    async def _ensure_connection(self) -> aiosqlite.Connection:
        """确保数据库连接可用；未初始化时自动建连。"""
        if self.connection is None:
            await self.initialize()
        if self.connection is None:
            raise RuntimeError("数据库连接尚未建立")
        return self.connection

    async def _ensure_schema(self, cursor):
        """检测并补齐数据表字段，再创建表和索引。"""
        # 检查 events 主表是否存在
        await cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        events_exists = bool(await cursor.fetchone())

        if events_exists:
            # 补齐早期 v2 版本可能缺失的列，避免由于 schema 差异造成运行故障
            await cursor.execute("PRAGMA table_info(events)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "source_id" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN source_id TEXT")
            if "subtitle" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN subtitle TEXT")
            if "weather_detail" not in columns:
                await cursor.execute(
                    "ALTER TABLE events ADD COLUMN weather_detail TEXT"
                )
            if "info_type" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN info_type TEXT")
            if "place_name" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN place_name TEXT")
            if "wind_speed" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN wind_speed REAL")
            if "pressure" not in columns:
                # 台风主表 pressure 语义为历史最低中心气压，供气压榜/风王榜重建。
                await cursor.execute("ALTER TABLE events ADD COLUMN pressure REAL")
            # 海啸专用摘要列：避免复用台风 wind_speed 等语义冲突字段
            if "max_wave_height" not in columns:
                await cursor.execute(
                    "ALTER TABLE events ADD COLUMN max_wave_height REAL"
                )
            if "area_count" not in columns:
                await cursor.execute("ALTER TABLE events ADD COLUMN area_count INTEGER")
            if "immediate_area_count" not in columns:
                await cursor.execute(
                    "ALTER TABLE events ADD COLUMN immediate_area_count INTEGER"
                )
            if "is_cancelled" not in columns:
                await cursor.execute(
                    "ALTER TABLE events ADD COLUMN is_cancelled INTEGER DEFAULT 0"
                )
            if "is_training" not in columns:
                await cursor.execute(
                    "ALTER TABLE events ADD COLUMN is_training INTEGER DEFAULT 0"
                )

        # 检查 event_updates 报次更新表是否存在
        await cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='event_updates'"
        )
        updates_exists = bool(await cursor.fetchone())
        if updates_exists:
            await cursor.execute("PRAGMA table_info(event_updates)")
            updates_columns = {row[1] for row in await cursor.fetchall()}
            if "level" not in updates_columns:
                await cursor.execute("ALTER TABLE event_updates ADD COLUMN level TEXT")
            if "wind_speed" not in updates_columns:
                await cursor.execute(
                    "ALTER TABLE event_updates ADD COLUMN wind_speed REAL"
                )
            if "pressure" not in updates_columns:
                # 报次快照记录当次中心气压，供历史最低气压重建。
                await cursor.execute(
                    "ALTER TABLE event_updates ADD COLUMN pressure REAL"
                )
            if "latitude" not in updates_columns:
                await cursor.execute(
                    "ALTER TABLE event_updates ADD COLUMN latitude REAL"
                )
            if "longitude" not in updates_columns:
                await cursor.execute(
                    "ALTER TABLE event_updates ADD COLUMN longitude REAL"
                )

        # 创建不存在的表
        await self._create_tables(cursor)

    async def _create_tables(self, cursor):
        """创建当前版本所需的表结构与索引。"""
        # 主事件表：保存每个物理事件的最新综合状态
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                real_event_id   TEXT,
                unique_id       TEXT,
                type            TEXT NOT NULL,
                source          TEXT NOT NULL,
                source_id       TEXT,
                description     TEXT,
                subtitle        TEXT,
                weather_detail  TEXT,
                info_type       TEXT,
                place_name      TEXT,
                latitude        REAL,
                longitude       REAL,
                magnitude       REAL,
                depth           REAL,
                report_num      INTEGER,
                weather_type_code TEXT,
                level           TEXT,
                wind_speed      REAL,
                pressure        REAL,
                max_wave_height REAL,
                area_count      INTEGER,
                immediate_area_count INTEGER,
                is_cancelled    INTEGER DEFAULT 0,
                is_training     INTEGER DEFAULT 0,
                time            TEXT,
                is_major        INTEGER DEFAULT 0,
                update_count    INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # 事件更新表：保存每次历史报次的详细快照，用于重建更新轨迹
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_updates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                source_event_id TEXT,
                report_num      INTEGER,
                magnitude       REAL,
                depth           REAL,
                description     TEXT,
                level           TEXT,
                wind_speed      REAL,
                pressure        REAL,
                latitude        REAL,
                longitude       REAL,
                time            TEXT,
                recorded_at     TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # S-Net 测站峰值状态表：每个测站一行，持续 upsert，不进入通用 events 事件流
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS snet_station_peaks (
                station_id      TEXT PRIMARY KEY,
                station_name    TEXT NOT NULL,
                lat             REAL,
                lon             REAL,
                max_shindo      REAL NOT NULL,
                max_shindo_at   TEXT NOT NULL,
                last_shindo     REAL,
                last_seen_at    TEXT,
                first_seen_at   TEXT,
                hit_count       INTEGER DEFAULT 0,
                updated_at      TEXT NOT NULL
            )
            """
        )

        # 索引集中覆盖事件标识、来源、类型、时间等高频检索维度，加速分页与汇总查询
        for sql in (
            "CREATE INDEX IF NOT EXISTS idx_ev_real_id   ON events(real_event_id)",
            "CREATE INDEX IF NOT EXISTS idx_ev_unique_id ON events(unique_id)",
            "CREATE INDEX IF NOT EXISTS idx_ev_source    ON events(source)",
            "CREATE INDEX IF NOT EXISTS idx_ev_type      ON events(type)",
            "CREATE INDEX IF NOT EXISTS idx_ev_source_id ON events(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_ev_time      ON events(time)",
            "CREATE INDEX IF NOT EXISTS idx_ev_wind_speed ON events(wind_speed)",
            "CREATE INDEX IF NOT EXISTS idx_ev_is_major  ON events(is_major)",
            "CREATE INDEX IF NOT EXISTS idx_upd_event_id ON event_updates(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_snet_peaks_shindo ON snet_station_peaks(max_shindo DESC)",
            "CREATE INDEX IF NOT EXISTS idx_snet_peaks_time ON snet_station_peaks(max_shindo_at DESC)",
        ):
            await cursor.execute(sql)

    # ──────────────────────────── 写操作 ────────────────────────────

    async def insert_event(self, event_data: dict[str, Any]) -> int:
        """
        插入新事件，同时在 event_updates 记录首次推送。
        返回新记录的数据库 id。

        可选字段：
        - updated_at / created_at：用于历史回填场景，避免把旧事件写成“刚刚更新”
          从而打乱事件列表按 updated_at 的时间线排序。
        """
        try:
            connection = await self._ensure_connection()
            # 插入前确保将历史遗留的 'weather' 类型归一化为标准的 'weather_alarm' 存储
            evt_type = normalize_event_type(event_data.get("type")) or ""

            cursor = await connection.cursor()
            # 是否重大事件既允许外部直接传入，也允许在入库前重新按规则补判一次
            is_major = bool(event_data.get("is_major")) or is_major_record(event_data)

            event_time = event_data.get("time")
            # 历史回填可显式指定 created_at/updated_at；缺省时回退到事件时间，
            # 再缺省才走数据库 CURRENT_TIMESTAMP。
            created_at = (
                event_data.get("created_at")
                or event_data.get("updated_at")
                or event_time
            )
            updated_at = event_data.get("updated_at") or created_at

            # 向 events 表插入主记录
            await cursor.execute(
                """
                INSERT INTO events (
                    real_event_id, unique_id, type, source, source_id,
                    description, subtitle, weather_detail, info_type, place_name, latitude, longitude,
                    magnitude, depth, report_num,
                    weather_type_code, level, wind_speed, pressure,
                    max_wave_height, area_count, immediate_area_count, is_cancelled, is_training,
                    time, is_major, update_count, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_data.get("real_event_id"),
                    event_data.get("unique_id"),
                    evt_type,
                    event_data.get("source"),
                    event_data.get("source_id"),
                    event_data.get("description"),
                    event_data.get("subtitle"),
                    event_data.get("weather_detail"),
                    event_data.get("info_type"),
                    event_data.get("place_name"),
                    event_data.get("latitude"),
                    event_data.get("longitude"),
                    event_data.get("magnitude"),
                    event_data.get("depth"),
                    event_data.get("report_num"),
                    event_data.get("weather_type_code"),
                    event_data.get("level"),
                    event_data.get("wind_speed"),
                    event_data.get("pressure"),
                    event_data.get("max_wave_height"),
                    event_data.get("area_count"),
                    event_data.get("immediate_area_count"),
                    1 if event_data.get("is_cancelled") else 0,
                    1 if event_data.get("is_training") else 0,
                    event_time,
                    1 if is_major else 0,
                    event_data.get("update_count", 1),
                    created_at,
                    updated_at,
                ),
            )
            new_id = cursor.lastrowid

            # 首次写入主事件表后，同步写入一条更新记录，保证历史链条从首报开始完整。
            # 台风快照优先使用当次观测值，避免与主表峰值语义混淆。
            snapshot_level = event_data.get("_snapshot_level", event_data.get("level"))
            snapshot_wind = event_data.get(
                "_snapshot_wind_speed", event_data.get("wind_speed")
            )
            snapshot_pressure = event_data.get(
                "_snapshot_pressure", event_data.get("pressure")
            )
            await cursor.execute(
                """
                INSERT INTO event_updates
                    (event_id, source_event_id, report_num, magnitude, depth, description, level, wind_speed, pressure, latitude, longitude, time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_id,
                    event_data.get("event_id"),
                    event_data.get("report_num"),
                    event_data.get("magnitude"),
                    event_data.get("depth"),
                    event_data.get("description"),
                    snapshot_level,
                    snapshot_wind,
                    snapshot_pressure,
                    event_data.get("latitude"),
                    event_data.get("longitude"),
                    event_data.get("time"),
                ),
            )

            await connection.commit()

            # 清理缓存，保证接口能够立刻加载出最新写入的数据
            try:
                from ..network.admin.api.events_routes import invalidate_sources_cache

                invalidate_sources_cache()
            except Exception:
                pass

            return new_id
        except Exception as e:
            logger.error(f"[灾害预警] 插入事件失败: {e}")
            if self.connection is not None:
                await self.connection.rollback()
            raise

    @staticmethod
    def _resolve_typhoon_peak_update_fields(
        event_data: dict[str, Any],
        *,
        existing_level: Any,
        existing_wind_speed: Any,
        existing_pressure: Any,
    ) -> dict[str, Any]:
        """台风记录策略：主表峰值 + updates 当次快照。

        峰值公式由 domain.resolve_storage_peak_fields 统一维护，
        数据库层只负责调用并落库，不再内嵌业务公式。
        返回 dict 可直接 update 到 event_data。
        """
        (
            level_to_store,
            wind_speed_to_store,
            pressure_to_store,
            snapshot_level,
            snapshot_wind,
            snapshot_pressure,
        ) = resolve_storage_peak_fields(
            existing_level=existing_level,
            existing_wind=existing_wind_speed,
            existing_pressure=existing_pressure,
            event_data=event_data,
        )
        return {
            "level": level_to_store,
            "wind_speed": wind_speed_to_store,
            "pressure": pressure_to_store,
            "_snapshot_level": snapshot_level,
            "_snapshot_wind_speed": snapshot_wind,
            "_snapshot_pressure": snapshot_pressure,
        }

    async def update_event(self, source: str, event_data: dict[str, Any]) -> bool:
        """
        更新已有事件（以 real_event_id+source 或 unique_id+source 查找），
        同时在 event_updates 追加一条更新记录。
        """
        try:
            # 更新前确保将历史遗留的 'weather' 类型归一化为标准的 'weather_alarm' 存储
            evt_type = normalize_event_type(event_data.get("type")) or ""

            connection = await self._ensure_connection()
            cursor = await connection.cursor()
            real_event_id = event_data.get("real_event_id")
            unique_id = event_data.get("unique_id")
            # 台风的 is_major 只增不减：一旦标记为重大事件，即使后续减弱也保留，
            # 以保证时间轴上已有的重大事件点不被降级移除。
            incoming_is_major = bool(event_data.get("is_major")) or is_major_record(
                event_data
            )
            existing_is_major = False
            if evt_type == "typhoon" and (real_event_id or unique_id):
                existing_is_major = await self._query_existing_is_major(
                    real_event_id, unique_id, source
                )
            is_major = incoming_is_major or existing_is_major

            # 先在主事件表中找到对应物理记录，再决定是更新还是返回未命中。
            # 台风峰值语义由 domain.resolve_storage_peak_fields 统一解析；
            # 数据库层只落库最终字段，不再内嵌峰值公式。
            db_id = None
            existing_level = None
            existing_wind_speed = None
            existing_pressure = None
            if real_event_id:
                await cursor.execute(
                    """
                    SELECT id, level, wind_speed, pressure
                    FROM events
                    WHERE real_event_id=? AND source=?
                    LIMIT 1
                    """,
                    (real_event_id, source),
                )
                r = await cursor.fetchone()
                if r:
                    db_id = r[0]
                    existing_level = r[1]
                    existing_wind_speed = r[2]
                    existing_pressure = r[3]
            if db_id is None and unique_id:
                await cursor.execute(
                    """
                    SELECT id, level, wind_speed, pressure
                    FROM events
                    WHERE unique_id=? AND source=?
                    LIMIT 1
                    """,
                    (unique_id, source),
                )
                r = await cursor.fetchone()
                if r:
                    db_id = r[0]
                    existing_level = r[1]
                    existing_wind_speed = r[2]
                    existing_pressure = r[3]

            if db_id is None:
                return False

            # event_updates 始终写当次观测快照；主表写最终峰值字段。
            level_to_store = event_data.get("level")
            wind_speed_to_store = event_data.get("wind_speed")
            pressure_to_store = event_data.get("pressure")
            update_snapshot_level = event_data.get(
                "_snapshot_level", event_data.get("level")
            )
            update_snapshot_wind = event_data.get(
                "_snapshot_wind_speed", event_data.get("wind_speed")
            )
            update_snapshot_pressure = event_data.get(
                "_snapshot_pressure", event_data.get("pressure")
            )
            if evt_type == "typhoon":
                peak_fields = self._resolve_typhoon_peak_update_fields(
                    event_data,
                    existing_level=existing_level,
                    existing_wind_speed=existing_wind_speed,
                    existing_pressure=existing_pressure,
                )
                event_data.update(peak_fields)
                level_to_store = event_data["level"]
                wind_speed_to_store = event_data["wind_speed"]
                pressure_to_store = event_data["pressure"]
                update_snapshot_level = event_data["_snapshot_level"]
                update_snapshot_wind = event_data["_snapshot_wind_speed"]
                update_snapshot_pressure = event_data["_snapshot_pressure"]

            # 更新主表中的事件字段
            await cursor.execute(
                """
                UPDATE events SET
                    source_id         = ?,
                    type              = ?,
                    description       = ?,
                    subtitle          = ?,
                    weather_detail    = ?,
                    info_type         = ?,
                    place_name        = ?,
                    latitude          = ?,
                    longitude         = ?,
                    magnitude         = ?,
                    depth             = ?,
                    report_num        = ?,
                    time              = ?,
                    update_count      = ?,
                    weather_type_code = ?,
                    level             = ?,
                    wind_speed        = ?,
                    pressure          = ?,
                    max_wave_height   = ?,
                    area_count        = ?,
                    immediate_area_count = ?,
                    is_cancelled      = ?,
                    is_training       = ?,
                    is_major          = ?,
                    updated_at        = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    event_data.get("source_id"),
                    evt_type,
                    event_data.get("description"),
                    event_data.get("subtitle"),
                    event_data.get("weather_detail"),
                    event_data.get("info_type"),
                    event_data.get("place_name"),
                    event_data.get("latitude"),
                    event_data.get("longitude"),
                    event_data.get("magnitude"),
                    event_data.get("depth"),
                    event_data.get("report_num"),
                    event_data.get("time"),
                    event_data.get("update_count", 1),
                    event_data.get("weather_type_code"),
                    level_to_store,
                    wind_speed_to_store,
                    pressure_to_store,
                    event_data.get("max_wave_height"),
                    event_data.get("area_count"),
                    event_data.get("immediate_area_count"),
                    1 if event_data.get("is_cancelled") else 0,
                    1 if event_data.get("is_training") else 0,
                    1 if is_major else 0,
                    db_id,
                ),
            )

            # 主事件表字段更新后，再追加一条报次快照记录，保留每次演进轨迹。
            # 台风快照写入本次观测值，避免把“已抬升的峰值”误记成当前观测。
            await cursor.execute(
                """
                INSERT INTO event_updates
                    (event_id, source_event_id, report_num, magnitude, depth, description, level, wind_speed, pressure, latitude, longitude, time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    db_id,
                    event_data.get("event_id"),
                    event_data.get("report_num"),
                    event_data.get("magnitude"),
                    event_data.get("depth"),
                    event_data.get("description"),
                    update_snapshot_level,
                    update_snapshot_wind,
                    update_snapshot_pressure,
                    event_data.get("latitude"),
                    event_data.get("longitude"),
                    event_data.get("time"),
                ),
            )

            await self.connection.commit()

            # 更新完毕后清理缓存
            try:
                from ..network.admin.api.events_routes import invalidate_sources_cache

                invalidate_sources_cache()
            except Exception:
                pass

            return True
        except Exception as e:
            logger.error(f"[灾害预警] 更新事件失败: {e}")
            await self.connection.rollback()
            raise

    async def insert_typhoon_track_updates(
        self,
        event_db_id: int,
        track_nodes: list[dict[str, Any]],
        *,
        source_event_id: str | None = None,
    ) -> int:
        """批量插入台风路径点到 event_updates 表。

        用于 EQSC 历史重建场景，把 historyTrack 中的每个有效观测节点
        作为一条 event_updates 记录入库，使前端能展示完整路径点。
        返回实际插入的记录数。
        """
        if not track_nodes:
            return 0
        try:
            connection = await self._ensure_connection()
            cursor = await connection.cursor()
            # 先清除 insert_event 已写入的首报 event_updates 记录，
            # 避免首报与 historyTrack 第一个路径点重复。
            await cursor.execute(
                "DELETE FROM event_updates WHERE event_id = ?",
                (event_db_id,),
            )
            inserted = 0
            for idx, node in enumerate(track_nodes):
                if not isinstance(node, dict):
                    continue
                node_time = node.get("time")
                node_level = str(
                    node.get("level") or node.get("typeNameCN") or ""
                ).strip()
                node_wind = node.get("wind_speed")
                node_pressure = node.get("pressure")
                await cursor.execute(
                    """
                    INSERT INTO event_updates
                        (event_id, source_event_id, report_num, magnitude, depth, description, level, wind_speed, pressure, latitude, longitude, time, recorded_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_db_id,
                        source_event_id,
                        idx + 1,
                        None,
                        None,
                        node.get("description"),
                        node_level,
                        node_wind,
                        node_pressure,
                        node.get("latitude"),
                        node.get("longitude"),
                        node_time,
                        # recorded_at 使用路径点自身的观测时间，
                        # 避免前端展示为重建入库的当前时间。
                        node_time,
                    ),
                )
                inserted += 1
            # 更新主表 update_count 以匹配实际路径点数
            if inserted > 0:
                await cursor.execute(
                    "UPDATE events SET update_count = ? WHERE id = ?",
                    (inserted, event_db_id),
                )
            await connection.commit()
            return inserted
        except Exception as e:
            logger.error(f"[灾害预警] 批量插入台风路径点失败: {e}")
            await self.connection.rollback()
            return 0

    async def _query_existing_is_major(
        self,
        real_event_id: str | None,
        unique_id: str | None,
        source: str,
    ) -> bool:
        """查询已有记录的 is_major 状态（用于台风只增不减逻辑）。"""
        try:
            cursor = await self.connection.cursor()
            if real_event_id:
                await cursor.execute(
                    "SELECT is_major FROM events WHERE real_event_id=? AND source=? LIMIT 1",
                    (real_event_id, source),
                )
                row = await cursor.fetchone()
                if row:
                    return bool(row[0])
            if unique_id:
                await cursor.execute(
                    "SELECT is_major FROM events WHERE unique_id=? AND source=? LIMIT 1",
                    (unique_id, source),
                )
                row = await cursor.fetchone()
                if row:
                    return bool(row[0])
        except Exception:
            pass
        return False

    # ──────────────────────────── 读操作 ────────────────────────────

    async def _attach_history(self, events: list[dict]) -> list[dict]:
        """为事件列表批量附加更新历史记录。"""
        if not events:
            return events

        # 仅对 update_count > 1 的事件查询历史更新记录，update_count <= 1 的事件 history 必然为空
        events_need_history = [e for e in events if e.get("update_count", 1) > 1]

        for event in events:
            event["history"] = []

        if not events_need_history:
            return events

        # 用 json_each(?) 传递编号列表，避免动态拼接 IN 子句带来的复杂性。
        ids = json.dumps([e["id"] for e in events_need_history])
        cursor = await self.connection.cursor()
        await cursor.execute(
            """
            SELECT * FROM event_updates
            WHERE event_id IN (SELECT value FROM json_each(?))
            ORDER BY event_id, recorded_at ASC
            """,
            (ids,),
        )
        rows = await cursor.fetchall()

        updates_by_event: dict[int, list] = {}
        for row in rows:
            r = dict(row)
            updates_by_event.setdefault(r["event_id"], []).append(r)

        for event in events_need_history:
            updates = updates_by_event.get(event["id"], [])
            if len(updates) > 1:
                event_type = str(event.get("type") or "").strip()
                if event_type == "typhoon":
                    # 台风主表 level 存峰值，最新观测等级在 event_updates 最后一条。
                    # 保留全部 updates（含最新），前端从 history[0] 取当前观测等级。
                    event["history"] = list(reversed(updates))
                else:
                    # 其他事件类型：历史链条中去掉当前最新报本身，只保存以前的变更快照
                    event["history"] = list(reversed(updates[:-1]))

        return events

    def _append_source_filter_clause(
        self,
        sources: list[str] | None,
        clauses: list[str],
        params: list[Any],
    ) -> None:
        """追加数据源过滤子句：按原值、标准化值与展示名兼容匹配 source/source_id。"""
        normalized_sources = [
            str(s or "").strip() for s in (sources or []) if str(s or "").strip()
        ]
        if not normalized_sources:
            return

        expanded_sources = expand_source_aliases(normalized_sources)
        normalized_aliases = sorted(
            {
                normalize_source_name(item)
                for item in expanded_sources
                if str(item or "").strip()
            }
        )

        raw_placeholders = ",".join(["?"] * len(expanded_sources))
        normalized_placeholders = ",".join(["?"] * len(normalized_aliases))
        clauses.append(
            "("
            "COALESCE(NULLIF(source_id, ''), source) IN (" + raw_placeholders + ") "
            "OR source IN (" + raw_placeholders + ") "
            "OR lower(COALESCE(NULLIF(source_id, ''), source)) IN ("
            + normalized_placeholders
            + ") "
            "OR lower(source) IN (" + normalized_placeholders + ")"
            ")"
        )
        params.extend(expanded_sources)
        params.extend(expanded_sources)
        params.extend(normalized_aliases)
        params.extend(normalized_aliases)

    async def get_recent_events(self, limit: int = 500) -> list[dict[str, Any]]:
        """获取最近事件（含 history），按业务时间线倒序。"""
        try:
            cursor = await self.connection.cursor()
            await cursor.execute(
                """
                SELECT * FROM events
                ORDER BY
                    CASE WHEN NULLIF(time, '') IS NULL THEN 1 ELSE 0 END ASC,
                    time DESC,
                    updated_at DESC,
                    id DESC
                LIMIT ?
                """,
                (limit,),
            )
            events = [dict(row) for row in await cursor.fetchall()]
            return await self._attach_history(events)
        except Exception as e:
            logger.error(f"[灾害预警] 查询最近事件失败: {e}")
            return []

    async def find_event_by_real_id(
        self, real_event_id: str, source: str
    ) -> dict[str, Any] | None:
        """按 real_event_id + source 查找事件。"""
        try:
            connection = await self._ensure_connection()
            cursor = await connection.cursor()
            await cursor.execute(
                "SELECT * FROM events WHERE real_event_id=? AND source=? LIMIT 1",
                (real_event_id, source),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            events = await self._attach_history([dict(row)])
            return events[0]
        except Exception as e:
            logger.error(f"[灾害预警] 查找事件失败: {e}")
            return None

    async def find_event_by_unique_id(
        self, unique_id: str, source: str
    ) -> dict[str, Any] | None:
        """按 unique_id + source 查找事件。"""
        try:
            connection = await self._ensure_connection()
            cursor = await connection.cursor()
            await cursor.execute(
                "SELECT * FROM events WHERE unique_id=? AND source=? LIMIT 1",
                (unique_id, source),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            events = await self._attach_history([dict(row)])
            return events[0]
        except Exception as e:
            logger.error(f"[灾害预警] 按 unique_id 查找事件失败: {e}")
            return None

    async def find_weather_event_by_alarm_id(
        self, alarm_id: str
    ) -> dict[str, Any] | None:
        """按气象预警 ID（unique_id/real_event_id）查找事件。"""
        try:
            cursor = await self.connection.cursor()
            await cursor.execute(
                """
                SELECT *
                FROM events
                WHERE (type='weather' OR type='weather_alarm')
                  AND (unique_id=? OR real_event_id=?)
                ORDER BY updated_at DESC, time DESC, id DESC
                LIMIT 1
                """,
                (alarm_id, alarm_id),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            events = await self._attach_history([dict(row)])
            return events[0]
        except Exception as e:
            logger.error(f"[灾害预警] 按预警ID查找气象事件失败: {e}")
            return None

    async def get_recent_weather_events(
        self, limit: int = 5000
    ) -> list[dict[str, Any]]:
        """获取最近气象预警事件（含 history），按更新时间倒序。"""
        try:
            cursor = await self.connection.cursor()
            await cursor.execute(
                """
                SELECT *
                FROM events
                WHERE type='weather' OR type='weather_alarm'
                ORDER BY updated_at DESC, time DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            events = [dict(row) for row in await cursor.fetchall()]
            return await self._attach_history(events)
        except Exception as e:
            logger.error(f"[灾害预警] 查询最近气象事件失败: {e}")
            return []

    async def find_typhoon_event_by_id(self, typhoon_id: str) -> dict[str, Any] | None:
        """按台风编号查找事件，兼容 4 位 EQSC / 6 位 Fan 编号。"""
        try:
            raw_id = str(typhoon_id or "").strip()
            if not raw_id:
                return None

            # 编号互转统一复用领域 API，避免数据库层维护第二套 4/6 位规则。
            candidates: list[str] = []
            for item in (raw_id, to_fan_id(raw_id), to_eqsc_id(raw_id)):
                text = str(item or "").strip()
                if text and text not in candidates:
                    candidates.append(text)
            if not candidates:
                return None

            cursor = await self.connection.cursor()
            placeholders = ",".join("?" for _ in candidates)
            await cursor.execute(
                f"""
                SELECT *
                FROM events
                WHERE type='typhoon'
                  AND (
                    unique_id IN ({placeholders})
                    OR real_event_id IN ({placeholders})
                  )
                ORDER BY updated_at DESC, time DESC, id DESC
                LIMIT 1
                """,
                tuple(candidates + candidates),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            events = await self._attach_history([dict(row)])
            return events[0]
        except Exception as e:
            logger.error(f"[灾害预警] 按台风编号查找事件失败: {e}")
            return None

    async def get_recent_typhoon_events(self, limit: int = 200) -> list[dict[str, Any]]:
        """获取最近台风事件（含 history），按事件时间倒序。"""
        try:
            safe_limit = max(1, min(int(limit or 200), 1000))
            cursor = await self.connection.cursor()
            await cursor.execute(
                """
                SELECT *
                FROM events
                WHERE type='typhoon'
                ORDER BY
                    CASE WHEN NULLIF(time, '') IS NULL THEN 1 ELSE 0 END ASC,
                    time DESC,
                    updated_at DESC,
                    id DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            events = [dict(row) for row in await cursor.fetchall()]
            return await self._attach_history(events)
        except Exception as e:
            logger.error(f"[灾害预警] 查询最近台风事件失败: {e}")
            return []

    async def _build_typhoon_major_transition_events(self) -> list[dict[str, Any]]:
        """从台风观测快照生成重大事件时间轴点。

        重大点定义为：
        - 从阈值以下进入强台风及以上；
        - 在重大区间内发生等级变化（强台风 <-> 超强台风）；
        - 跌破阈值后再次进入重大区间。
        连续相同等级的观测不重复生成点。
        """
        from ..domain.typhoon.typhoon_levels import level_weight

        cursor = await self.connection.cursor()
        await cursor.execute(
            """
            SELECT e.*, eu.id AS update_id, eu.report_num AS update_report_num,
                   eu.level AS update_level, eu.wind_speed AS update_wind_speed,
                   eu.pressure AS update_pressure, eu.latitude AS update_latitude,
                   eu.longitude AS update_longitude, eu.time AS update_time,
                   eu.recorded_at AS update_recorded_at
            FROM events e
            JOIN event_updates eu ON eu.event_id = e.id
            WHERE e.type = 'typhoon'
            ORDER BY e.source, e.real_event_id, eu.id ASC
            """
        )
        rows = await cursor.fetchall()
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            item = dict(row)
            key = (
                str(item.get("source") or ""),
                str(item.get("real_event_id") or item.get("unique_id") or ""),
            )
            grouped.setdefault(key, []).append(item)

        def snapshot_time(snapshot: dict[str, Any]) -> tuple[float, int]:
            """按观测时间排序；无法解析时退回 event_updates 自增 ID。"""
            value = snapshot.get("update_time") or snapshot.get("time")
            parsed = TimeConverter.parse_datetime(value)
            if parsed is None:
                return (0.0, int(snapshot.get("update_id") or 0))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))
            return (parsed.timestamp(), int(snapshot.get("update_id") or 0))

        transitions: list[dict[str, Any]] = []
        for _, snapshots in grouped.items():
            snapshots.sort(key=snapshot_time)
            previous_level = ""
            previous_major = False
            for snapshot in snapshots:
                level = str(snapshot.get("update_level") or "").strip()
                current_major = level_weight(level) >= 5
                is_transition = current_major and (
                    not previous_major or level != previous_level
                )
                if is_transition:
                    event = dict(snapshot)
                    event["id"] = (
                        f"typhoon-major-{snapshot.get('id')}-{snapshot.get('update_id')}"
                    )
                    event["event_id"] = event["id"]
                    event["real_event_id"] = snapshot.get("real_event_id")
                    event["unique_id"] = snapshot.get("unique_id")
                    event["time"] = snapshot.get("update_time") or snapshot.get("time")
                    event["timestamp"] = event["time"]
                    event["updated_at"] = (
                        snapshot.get("update_recorded_at") or event["time"]
                    )
                    event["level"] = level
                    event["_snapshot_level"] = level
                    # 主表 description 保存的是历史峰值，重大点必须使用本次快照等级。
                    name = str(
                        snapshot.get("subtitle")
                        or snapshot.get("place_name")
                        or snapshot.get("real_event_id")
                        or "未知台风"
                    ).strip()
                    event["subtitle"] = name
                    event["description"] = f"{level} {name}".strip()
                    event["wind_speed"] = snapshot.get("update_wind_speed")
                    event["pressure"] = snapshot.get("update_pressure")
                    event["latitude"] = snapshot.get("update_latitude")
                    event["longitude"] = snapshot.get("update_longitude")
                    event["report_num"] = snapshot.get("update_report_num")
                    event["is_major"] = 1
                    event["history"] = []
                    event["update_count"] = 1
                    transitions.append(event)
                previous_level = level
                previous_major = current_major

        return transitions

    async def get_major_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """获取重大事件，并将台风等级转折投影为独立时间轴点。

        注意：S-Net 峰值重大条目不在本方法内拼接，由上层（events_routes /
        StatisticsManager）通过 SnetPeakService 单独注入，避免仓储职责回膨胀。
        """
        try:
            cursor = await self.connection.cursor()
            await cursor.execute(
                """
                WITH ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY source, COALESCE(real_event_id, unique_id, CAST(id AS TEXT))
                            ORDER BY updated_at DESC, time DESC, id DESC
                        ) AS rn
                    FROM events
                    WHERE is_major = 1
                      AND (
                          type NOT IN ('typhoon', 'earthquake', 'earthquake_warning', 'weather', 'weather_alarm')
                          OR (type IN ('earthquake', 'earthquake_warning') AND magnitude IS NOT NULL AND magnitude >= ?)
                          OR ((type = 'weather' OR type = 'weather_alarm') AND (
                              (COALESCE(TRIM(level), '') != '' AND level LIKE ?)
                              OR (COALESCE(TRIM(level), '') = '' AND description LIKE ?)
                          ))
                      )
                )
                SELECT * FROM ranked WHERE rn = 1
                ORDER BY time DESC, updated_at DESC
                """,
                (
                    MAJOR_EARTHQUAKE_MAGNITUDE_THRESHOLD,
                    f"%{MAJOR_WEATHER_LEVEL_KEYWORD}%",
                    *(f"%{phrase}%" for phrase in MAJOR_WEATHER_TEXT_PHRASES),
                ),
            )
            events = [dict(row) for row in await cursor.fetchall()]
            events = await self._attach_history(events)
            typhoon_events = await self._build_typhoon_major_transition_events()
            events.extend(typhoon_events)

            def event_time(item: dict[str, Any]) -> tuple[float, int]:
                parsed = TimeConverter.parse_datetime(item.get("time"))
                if parsed is None:
                    parsed = TimeConverter.parse_datetime(item.get("updated_at"))
                if parsed is None:
                    return (
                        0.0,
                        int(item.get("id") or 0)
                        if str(item.get("id") or "").isdigit()
                        else 0,
                    )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))
                return (
                    parsed.timestamp(),
                    int(item.get("id") or 0)
                    if str(item.get("id") or "").isdigit()
                    else 0,
                )

            events.sort(key=event_time, reverse=True)
            return events[: max(1, int(limit or 100))]
        except Exception as e:
            logger.error(f"[灾害预警] 查询重大事件失败: {e}")
            return []

    def _append_level_filter_clause(
        self,
        level_filter: str | None,
        clauses: list[str],
        params: list[Any],
    ) -> None:
        """追加气象颜色、海啸级别或台风强度等级筛选条件。"""
        normalized = str(level_filter or "").strip().lower()
        weather_color_map = {
            "weather_white": "白色",
            "weather_blue": "蓝色",
            "weather_yellow": "黄色",
            "weather_orange": "橙色",
            "weather_red": "红色",
        }
        if normalized in weather_color_map:
            color = weather_color_map[normalized]
            like = f"%{color}%"
            clauses.append(
                "((type='weather' OR type='weather_alarm') AND ("
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ?"
                "))"
            )
            params.extend([like, like, like])
            return

        if normalized == "tsunami_info":
            clauses.append(
                "(type='tsunami' AND ("
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ? OR "
                "COALESCE(info_type, '') LIKE ?"
                "))"
            )
            params.extend(
                ["%信息%", "%Unknown%", "%津波予報%", "%津波予報%", "%津波予报%"]
            )
            return

        if normalized == "tsunami_warning":
            clauses.append(
                "(type='tsunami' AND ("
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ?"
                "))"
            )
            params.extend(
                [
                    "%Warning%",
                    "%Watch%",
                    "%警报%",
                    "%警報%",
                    "%预警%",
                    "%海啸预警%",
                    "%津波警報%",
                    "%大津波警報%",
                    "%海啸预警%",
                    "%津波警報%",
                    "%大津波警報%",
                ]
            )
            return

        typhoon_levels = {
            "typhoon_tropical_depression": "热带低压",
            "typhoon_tropical_storm": "热带风暴",
            "typhoon_severe_tropical_storm": "强热带风暴",
            "typhoon": "台风",
            "typhoon_severe_typhoon": "强台风",
            "typhoon_super_typhoon": "超强台风",
        }
        if normalized in typhoon_levels:
            like = f"%{typhoon_levels[normalized]}%"
            clauses.append(
                "(type='typhoon' AND ("
                "COALESCE(level, '') = ? OR "
                "(COALESCE(level, '') = '' AND ("
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ?"
                "))))"
            )
            params.extend([typhoon_levels[normalized], like, like])

    @staticmethod
    def _normalize_filter_time(value: str | None) -> str | None:
        """将前端传入的时间过滤值规整为可比较的 ISO 文本。"""
        text = str(value or "").strip()
        if not text:
            return None
        # 兼容 datetime-local（YYYY-MM-DDTHH:MM）与空格分隔格式。
        text = text.replace(" ", "T")
        if len(text) == 16 and text[10] == "T":
            text = f"{text}:00"
        if text.endswith("Z"):
            text = text[:-1]
        # 仅剥离时间部分后的时区偏移（含负偏移），避免破坏日期中的 '-'。
        # 例：2026-07-18T12:00:00+08:00 / 2026-07-18T12:00:00-05:00
        body = text
        if len(text) > 10 and text[10] == "T":
            date_part = text[:10]
            time_part = text[11:]
            for marker in ("+", "-"):
                idx = time_part.find(marker)
                if idx > 0:
                    time_part = time_part[:idx]
                    break
            body = f"{date_part}T{time_part}"
        return body

    def _append_common_event_filters(
        self,
        *,
        event_type: str | None,
        sources: list[str] | None,
        min_magnitude: float | None,
        keyword: str | None,
        level_filter: str | None,
        min_wind_speed: float | None,
        time_from: str | None = None,
        time_to: str | None = None,
        min_depth: float | None = None,
        max_depth: float | None = None,
        min_intensity: float | None = None,
        intensity_system: str | None = None,
        max_pressure: float | None = None,
        active_only: bool = False,
        clauses: list[str],
        params: list[Any],
    ) -> None:
        """统一装配事件列表筛选条件，避免 count / paginated 两套逻辑漂移。"""
        if event_type:
            # 兼容 "weather" => "weather_alarm"
            norm_type = normalize_event_type(event_type) or ""
            if norm_type == "weather_alarm":
                clauses.append("(type='weather' OR type='weather_alarm')")
            else:
                clauses.append("type=?")
                params.append(norm_type)

        self._append_source_filter_clause(sources, clauses, params)

        if min_magnitude is not None:
            clauses.append(
                "(type IN ('earthquake', 'earthquake_warning') AND magnitude IS NOT NULL AND magnitude >= ?)"
            )
            params.append(min_magnitude)

        self._append_level_filter_clause(level_filter, clauses, params)

        if min_wind_speed is not None:
            clauses.append(
                "(type='typhoon' AND wind_speed IS NOT NULL AND wind_speed >= ?)"
            )
            params.append(min_wind_speed)

        if max_pressure is not None and max_pressure > 0:
            # pressure 存历史最低中心气压，阈值越小表示越强。
            clauses.append(
                "(type='typhoon' AND pressure IS NOT NULL AND pressure > 0 AND pressure <= ?)"
            )
            params.append(max_pressure)

        if active_only:
            # 活跃态以 weather_detail 中的状态标记为准。
            # 注意：不要一刀切排除 eqsc_rebuild——重建结果里也可能包含仍在编报的活跃台风。
            # 旧数据没有状态标记时，仅排除明确“停编”文本，避免误伤。
            clauses.append(
                "("
                "type='typhoon' AND "
                "COALESCE(weather_detail, '') NOT LIKE '%状态 停编%' AND "
                "("
                "COALESCE(weather_detail, '') LIKE '%状态 活跃%' OR "
                "("
                "COALESCE(weather_detail, '') NOT LIKE '%状态 %' AND "
                "COALESCE(weather_detail, '') NOT LIKE '%停编%'"
                ")"
                ")"
                ")"
            )

        if min_depth is not None:
            clauses.append(
                "(type IN ('earthquake', 'earthquake_warning') AND depth IS NOT NULL AND depth >= ?)"
            )
            params.append(min_depth)

        if max_depth is not None:
            clauses.append(
                "(type IN ('earthquake', 'earthquake_warning') AND depth IS NOT NULL AND depth <= ?)"
            )
            params.append(max_depth)

        if min_intensity is not None:
            # 地震 level 列存震度/烈度数值（TEXT），统一 CAST 后比较。
            # intensity_system 用于隔离中国烈度与 JMA/CWA 震度，避免混比。
            system = str(intensity_system or "").strip().lower()
            source_expr = (
                "LOWER(COALESCE(source, '') || ' ' || COALESCE(source_id, ''))"
            )
            jma_source_clause = (
                f"({source_expr} LIKE '%jma%' OR "
                f"{source_expr} LIKE '%cwa%' OR "
                f"{source_expr} LIKE '%p2p%' OR "
                f"{source_expr} LIKE '%snet%')"
            )
            cn_source_clause = f"(NOT {jma_source_clause})"

            system_clause = ""
            if system in {"jma", "shindo", "cwa"}:
                system_clause = f" AND {jma_source_clause}"
            elif system in {"cn", "china", "intensity"}:
                system_clause = f" AND {cn_source_clause}"

            clauses.append(
                "("
                "type IN ('earthquake', 'earthquake_warning') AND "
                "COALESCE(TRIM(level), '') != '' AND "
                "CAST(level AS REAL) >= ?"
                f"{system_clause}"
                ")"
            )
            params.append(min_intensity)

        normalized_time_from = self._normalize_filter_time(time_from)
        if normalized_time_from:
            clauses.append(
                "REPLACE(COALESCE(NULLIF(time, ''), updated_at, ''), ' ', 'T') >= ?"
            )
            params.append(normalized_time_from)

        normalized_time_to = self._normalize_filter_time(time_to)
        if normalized_time_to:
            clauses.append(
                "REPLACE(COALESCE(NULLIF(time, ''), updated_at, ''), ' ', 'T') <= ?"
            )
            params.append(normalized_time_to)

        normalized_keyword = str(keyword or "").strip()
        if normalized_keyword:
            keyword_like = f"%{normalized_keyword}%"
            clauses.append(
                "("
                "COALESCE(description, '') LIKE ? OR "
                "COALESCE(subtitle, '') LIKE ? OR "
                "COALESCE(place_name, '') LIKE ? OR "
                "COALESCE(level, '') LIKE ? OR "
                "COALESCE(info_type, '') LIKE ? OR "
                "COALESCE(source, '') LIKE ? OR "
                "COALESCE(source_id, '') LIKE ?"
                ")"
            )
            params.extend([keyword_like] * 7)

    async def get_events_count(
        self,
        event_type: str | None = None,
        sources: list[str] | None = None,
        min_magnitude: float | None = None,
        keyword: str | None = None,
        level_filter: str | None = None,
        min_wind_speed: float | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        min_depth: float | None = None,
        max_depth: float | None = None,
        min_intensity: float | None = None,
        intensity_system: str | None = None,
        max_pressure: float | None = None,
        active_only: bool = False,
    ) -> int:
        """获取事件总数（支持多维过滤）"""
        try:
            cursor = await self.connection.cursor()
            clauses: list[str] = []
            params: list[Any] = []

            self._append_common_event_filters(
                event_type=event_type,
                sources=sources,
                min_magnitude=min_magnitude,
                keyword=keyword,
                level_filter=level_filter,
                min_wind_speed=min_wind_speed,
                time_from=time_from,
                time_to=time_to,
                min_depth=min_depth,
                max_depth=max_depth,
                min_intensity=min_intensity,
                intensity_system=intensity_system,
                max_pressure=max_pressure,
                active_only=active_only,
                clauses=clauses,
                params=params,
            )

            where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            # 前端列表按稳定事件键去重计数，避免海啸等同 unique_id 多行被重复统计
            dedup_group_expr = "COALESCE(NULLIF(unique_id, ''), NULLIF(real_event_id, ''), CAST(id AS TEXT))"
            await cursor.execute(
                f"SELECT COUNT(DISTINCT {dedup_group_expr}) FROM events{where_sql}",
                tuple(params),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[灾害预警] 查询事件总数失败: {e}")
            return 0

    async def get_events_paginated(
        self,
        page: int = 1,
        limit: int = 50,
        event_type: str | None = None,
        sources: list[str] | None = None,
        min_magnitude: float | None = None,
        magnitude_order: str | None = None,
        keyword: str | None = None,
        level_filter: str | None = None,
        min_wind_speed: float | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        min_depth: float | None = None,
        max_depth: float | None = None,
        min_intensity: float | None = None,
        intensity_system: str | None = None,
        max_pressure: float | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """分页获取事件（含 history，支持多维过滤与震级排序）"""
        try:
            offset = (page - 1) * limit
            cursor = await self.connection.cursor()

            clauses: list[str] = []
            params: list[Any] = []

            self._append_common_event_filters(
                event_type=event_type,
                sources=sources,
                min_magnitude=min_magnitude,
                keyword=keyword,
                level_filter=level_filter,
                min_wind_speed=min_wind_speed,
                time_from=time_from,
                time_to=time_to,
                min_depth=min_depth,
                max_depth=max_depth,
                min_intensity=min_intensity,
                intensity_system=intensity_system,
                max_pressure=max_pressure,
                active_only=active_only,
                clauses=clauses,
                params=params,
            )

            where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

            # 默认按“事件时间线”排序：优先业务时间 time，再回退 updated_at。
            # 这样历史回填记录即使刚刚写入，也不会因为 updated_at=now 整块顶到列表最前。
            timeline_order = (
                "CASE WHEN NULLIF(time, '') IS NULL THEN 1 ELSE 0 END ASC, "
                "time DESC, "
                "updated_at DESC, "
                "id DESC"
            )
            normalized_order = (magnitude_order or "").lower().strip()
            if normalized_order in {"asc", "desc"}:
                order_sql = (
                    "CASE WHEN magnitude IS NULL THEN 1 ELSE 0 END ASC, "
                    f"magnitude {normalized_order.upper()}, "
                    f"{timeline_order}"
                )
            else:
                order_sql = timeline_order

            # 按稳定事件键去重后再分页，避免同 unique_id 海啸多行刷屏
            dedup_group_expr = "COALESCE(NULLIF(unique_id, ''), NULLIF(real_event_id, ''), CAST(id AS TEXT))"
            sql = f"""
                WITH ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY {dedup_group_expr}
                            ORDER BY {order_sql}
                        ) AS rn
                    FROM events
                    {where_sql}
                )
                SELECT * FROM ranked
                WHERE rn = 1
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            await cursor.execute(sql, tuple(params))

            events = [dict(row) for row in await cursor.fetchall()]
            # 去掉窗口函数辅助列
            for event in events:
                event.pop("rn", None)
            return await self._attach_history(events)
        except Exception as e:
            logger.error(f"[灾害预警] 分页查询失败: {e}")
            return []

    async def get_event_source_options(
        self, event_type: str | None = None
    ) -> list[dict[str, str]]:
        """获取事件数据源选项（value/label），按最终展示语义去重。"""
        try:
            cursor = await self.connection.cursor()
            if event_type:
                # 兼容 "weather" => "weather_alarm"
                norm_type = normalize_event_type(event_type) or ""
                if norm_type == "weather_alarm":
                    await cursor.execute(
                        """
                        SELECT
                            COALESCE(NULLIF(source_id, ''), '') AS source_id_value,
                            COALESCE(NULLIF(source, ''), '') AS source_label
                        FROM events
                        WHERE type='weather' OR type='weather_alarm'
                        GROUP BY source_id_value, source_label
                        """
                    )
                else:
                    await cursor.execute(
                        """
                        SELECT
                            COALESCE(NULLIF(source_id, ''), '') AS source_id_value,
                            COALESCE(NULLIF(source, ''), '') AS source_label
                        FROM events
                        WHERE type=?
                        GROUP BY source_id_value, source_label
                        """,
                        (norm_type,),
                    )
            else:
                await cursor.execute(
                    """
                    SELECT
                        COALESCE(NULLIF(source_id, ''), '') AS source_id_value,
                        COALESCE(NULLIF(source, ''), '') AS source_label
                    FROM events
                    GROUP BY source_id_value, source_label
                    """
                )
            rows = await cursor.fetchall()

            result_map: dict[str, dict[str, str]] = {}
            for row in rows:
                source_id_value = str(row[0] or "").strip()
                source_label = str(row[1] or "").strip()
                raw_source = source_id_value or source_label
                if not raw_source:
                    continue

                normalized_source = normalize_source_name(raw_source)
                display_label = format_source_name(raw_source)
                current = result_map.get(display_label)
                candidate = {
                    "source_value": raw_source,
                    "source_label": display_label,
                    "normalized_source": normalized_source,
                }

                if current is None:
                    result_map[display_label] = candidate
                    continue

                current_value = str(current.get("source_value") or "")
                current_normalized = str(current.get("normalized_source") or "")
                prefers_source_id = bool(source_id_value)
                current_is_raw_label = (
                    current_value.casefold()
                    == str(source_label or "").strip().casefold()
                )
                normalized_changed = (
                    normalized_source and normalized_source != current_normalized
                )

                if prefers_source_id or current_is_raw_label or normalized_changed:
                    result_map[display_label] = candidate

            return [
                {
                    "source_value": str(item.get("source_value") or ""),
                    "source_label": str(item.get("source_label") or ""),
                }
                for item in sorted(
                    result_map.values(),
                    key=lambda item: str(item.get("source_label") or "").casefold(),
                )
            ]
        except Exception as e:
            logger.error(f"[灾害预警] 查询数据源选项失败: {e}")
            return []

    async def get_event_sources(self, event_type: str | None = None) -> list[str]:
        """获取事件数据源列表（可按类型过滤，兼容旧前端）"""
        options = await self.get_event_source_options(event_type)
        return [
            opt.get("source_label", "") for opt in options if opt.get("source_label")
        ]

    @staticmethod
    def _is_cenc_intensity_report_row(
        source: str | None = None,
        source_id: str | None = None,
        info_type: str | None = None,
    ) -> bool:
        """判断数据库行是否为 CENC 烈度速报。"""
        return is_cenc_intensity_report(
            source_id or source or "",
            info_type=info_type,
        )

    @staticmethod
    def _cenc_intensity_report_sql_predicate(
        *,
        source_expr: str = "source",
        source_id_expr: str = "source_id",
        info_type_expr: str = "info_type",
    ) -> str:
        """SQL 侧烈度速报判定表达式（静态别名，无用户输入拼接）。"""
        return build_cenc_intensity_report_sql_predicate(
            source_expr=source_expr,
            source_id_expr=source_id_expr,
            info_type_expr=info_type_expr,
        )

    async def get_statistics(self) -> dict[str, Any]:
        """获取数据库统计信息（按稳定事件集合去重，而非按物理行计数）。"""
        try:
            connection = await self._ensure_connection()
            cursor = await connection.cursor()

            dedup_group_expr = "COALESCE(NULLIF(unique_id, ''), NULLIF(real_event_id, ''), CAST(id AS TEXT))"
            intensity_pred = self._cenc_intensity_report_sql_predicate()

            # 去重时优先保留非烈度速报行；聚合在 SQL 完成，避免全量拉回 Python。
            # 烈度速报不计入 total_events / by_type，与运行时聚合口径一致。
            await cursor.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        type,
                        {dedup_group_expr} AS dedup_key,
                        CASE WHEN {intensity_pred} THEN 1 ELSE 0 END AS is_intensity_report,
                        ROW_NUMBER() OVER (
                            PARTITION BY {dedup_group_expr}
                            ORDER BY
                                CASE WHEN {intensity_pred} THEN 1 ELSE 0 END ASC,
                                CASE WHEN NULLIF(updated_at, '') IS NULL THEN 1 ELSE 0 END ASC,
                                updated_at DESC,
                                time DESC,
                                id DESC
                        ) AS rn
                    FROM events
                )
                SELECT
                    type,
                    COUNT(*) AS event_count
                FROM ranked
                WHERE rn = 1
                  AND is_intensity_report = 0
                GROUP BY type
                """
            )
            by_type_raw = {
                str(row[0] or "unknown"): int(row[1] or 0)
                for row in await cursor.fetchall()
            }
            total = sum(by_type_raw.values())

            # 将数据库中历史遗留的 'weather' 类型统一归并到 standards 中的 'weather_alarm'
            by_type: dict[str, int] = {}
            for k, v in by_type_raw.items():
                norm_key = normalize_event_type(k) or k
                by_type[norm_key] = by_type.get(norm_key, 0) + int(v or 0)

            # 贡献统计：台风 fan/enriched → typhoon_fanstudio；
            # EQSC 实时轮询 → typhoon_eqsc；
            # eqsc_rebuild → typhoon_eqsc_rebuild。
            # 注意：by_source 仍统计烈度速报，保留来源贡献可见性。
            await cursor.execute(
                f"""
                SELECT COALESCE(NULLIF(source_id, ''), source) AS source_key,
                       type AS event_type,
                       info_type AS info_type,
                       COUNT(DISTINCT {dedup_group_expr}) AS source_count
                FROM events
                GROUP BY source_key, event_type, info_type
                """
            )
            by_source: dict[str, int] = {}
            for row in await cursor.fetchall():
                stats_key = build_source_stats_key(
                    str(row[0] or ""),
                    event_type=str(row[1] or ""),
                    info_type=str(row[2] or ""),
                )
                by_source[stats_key] = by_source.get(stats_key, 0) + int(row[3] or 0)

            db_size_mb = self.db_path.stat().st_size / (1024 * 1024)
            return {
                "total_events": total,
                "by_type": by_type,
                "by_source": by_source,
                "database_size_mb": round(db_size_mb, 2),
            }
        except Exception as e:
            logger.error(f"[灾害预警] 获取统计信息失败: {e}")
            return {}

    async def get_statistics_rebuild_events(self) -> list[dict[str, Any]]:
        """获取去重后的全量事件，用于从数据库重建内存派生统计。

        台风峰值直接读取主表 level / wind_speed / pressure 列。
        """
        try:
            cursor = await self.connection.cursor()
            dedup_group_expr = "COALESCE(NULLIF(unique_id, ''), NULLIF(real_event_id, ''), CAST(id AS TEXT))"
            source_group_expr = "COALESCE(NULLIF(source_id, ''), source)"
            await cursor.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY type, {source_group_expr}, {dedup_group_expr}
                            ORDER BY
                                CASE WHEN NULLIF(updated_at, '') IS NULL THEN 1 ELSE 0 END ASC,
                                updated_at DESC,
                                time DESC,
                                id DESC
                        ) AS rn
                    FROM events
                )
                SELECT
                    id,
                    type,
                    source,
                    source_id,
                    description,
                    subtitle,
                    weather_detail,
                    info_type,
                    place_name,
                    magnitude,
                    depth,
                    level,
                    wind_speed,
                    pressure,
                    weather_type_code,
                    time,
                    unique_id,
                    real_event_id,
                    update_count
                FROM ranked
                WHERE rn = 1
                """
            )
            return [dict(row) for row in await cursor.fetchall()]
        except Exception as e:
            logger.error(f"[灾害预警] 获取统计重建事件失败: {e}")
            return []

    async def get_time_series_counts(self) -> dict[str, dict[str, int]]:
        """按数据库全量事件重建趋势图/热力图所需的小时桶与天桶。"""
        try:
            cursor = await self.connection.cursor()

            dedup_group_expr = "COALESCE(NULLIF(unique_id, ''), NULLIF(real_event_id, ''), CAST(id AS TEXT))"
            normalized_time_expr = "COALESCE(NULLIF(time, ''), NULLIF(updated_at, ''), NULLIF(created_at, ''))"
            intensity_pred = self._cenc_intensity_report_sql_predicate()

            # 去重优先非烈度速报，并在 SQL 侧直接排除烈度速报，减少 Python 过滤开销。
            await cursor.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        {dedup_group_expr} AS dedup_key,
                        {normalized_time_expr} AS event_time,
                        CASE WHEN {intensity_pred} THEN 1 ELSE 0 END AS is_intensity_report,
                        ROW_NUMBER() OVER (
                            PARTITION BY {dedup_group_expr}
                            ORDER BY
                                CASE WHEN {intensity_pred} THEN 1 ELSE 0 END ASC,
                                CASE WHEN NULLIF(updated_at, '') IS NULL THEN 1 ELSE 0 END ASC,
                                updated_at DESC,
                                time DESC,
                                id DESC
                        ) AS rn
                    FROM events
                    WHERE {normalized_time_expr} IS NOT NULL
                )
                SELECT event_time
                FROM ranked
                WHERE rn = 1
                  AND is_intensity_report = 0
                """
            )
            rows = await cursor.fetchall()

            hourly_counts: dict[str, int] = {}
            daily_counts: dict[str, int] = {}
            for row in rows:
                raw_time = row[0]
                if not raw_time:
                    continue
                try:
                    from datetime import datetime, timezone

                    normalized_time = str(raw_time).replace("Z", "+00:00")
                    event_time = datetime.fromisoformat(normalized_time)
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=timezone.utc)
                    event_time_utc = event_time.astimezone(timezone.utc)
                except Exception:
                    continue

                hour_key = event_time_utc.strftime("%Y-%m-%d %H:00")
                day_key = event_time_utc.strftime("%Y-%m-%d")
                hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + 1
                daily_counts[day_key] = daily_counts.get(day_key, 0) + 1

            return {
                "hourly_counts": hourly_counts,
                "daily_counts": daily_counts,
            }
        except Exception as e:
            logger.error(f"[灾害预警] 获取时间序列统计失败: {e}")
            return {
                "hourly_counts": {},
                "daily_counts": {},
            }

    async def clear_all_events(self) -> bool:
        """清除所有事件记录"""
        try:
            cursor = await self.connection.cursor()
            await cursor.execute("DELETE FROM event_updates")
            await cursor.execute("DELETE FROM events")
            await self.connection.commit()
            logger.info("[灾害预警] 数据库所有事件记录已清除")
            return True
        except Exception as e:
            logger.error(f"[灾害预警] 清除失败: {e}")
            await self.connection.rollback()
            return False

    # ──────────────────────────── 生命周期 ────────────────────────────

    async def close(self):
        """关闭数据库连接"""
        if self.connection:
            await self.connection.close()
            self.connection = None
            logger.info("[灾害预警] 数据库连接已关闭")

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close()
