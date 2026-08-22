"""
历史脏数据清理服务。

启动期一次性修复（标记版本递增后会再跑）：
1. 海啸历史：旧版「同事件多次 insert」折叠为单主表行 + event_updates 报次快照
2. CWA 地震报告：上游 id 复用（如 115000）导致多场地震被错误合并的污染簇删除
3. Wolfx 列表轮询刷屏：同内容 event_updates 压缩，并修正 update_count
4. 同 unique_id 多主表行：按 source+unique_id 折叠，保留最新
5. unknown_location 撞键：删除 unique_id 退化为全局常量的历史脏行

不写入 DatabaseManager 本体，仅复用其连接生命周期。
完成后写入磁盘标记，避免插件重载反复扫描/刷日志。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger

from .source_compat import normalize_source_name

# 标记版本：清理逻辑变更时可递增，强制再跑一轮
_MARKER_VERSION = "v3"
_MARKER_NAME = f".history_dirty_data_cleanup_{_MARKER_VERSION}.done"
# 兼容旧版海啸清理标记（仅表示海啸段曾跑过；新版本仍会再跑一轮幂等修复）
_LEGACY_TSUNAMI_MARKER_NAME = ".tsunami_history_cleanup_v1.done"

# 已知被上游复用、不可作为稳定事件键的 CWA 报告 id
_CWA_REPORT_POISON_IDS = frozenset({"115000", "0", "null", "none", "undefined"})

# Wolfx HTTP 列表补偿源：同一测定会被定时重复拉取
_WOLFX_LIST_SOURCES = frozenset(
    {
        "cenc_wolfx",
        "jma_wolfx_info",
        "wolfx_cenc_eq",
        "wolfx_jma_eq",
    }
)


def _normalize_snapshot_value(value: Any) -> str:
    """把快照字段规范成可比较字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        return f"{number:.6f}".rstrip("0").rstrip(".")
    return str(value).strip()


def _update_content_fingerprint(row: dict[str, Any]) -> str:
    """event_updates 内容指纹（与 DatabaseManager 口径一致）。"""
    return "|".join(
        [
            _normalize_snapshot_value(row.get("report_num")),
            _normalize_snapshot_value(row.get("magnitude")),
            _normalize_snapshot_value(row.get("depth")),
            _normalize_snapshot_value(row.get("description")),
            _normalize_snapshot_value(row.get("level")),
            _normalize_snapshot_value(row.get("wind_speed")),
            _normalize_snapshot_value(row.get("pressure")),
            _normalize_snapshot_value(row.get("latitude")),
            _normalize_snapshot_value(row.get("longitude")),
            _normalize_snapshot_value(row.get("time")),
        ]
    )


# SQLite 默认变量上限约 999；批量 IN 查询按此切片，留余量
_IN_BATCH_SIZE = 500


def _iter_id_batches(
    ids: list[int], *, batch_size: int = _IN_BATCH_SIZE
) -> list[list[int]]:
    """去重后按 batch_size 切片，避免单次 IN 参数过多。"""
    unique_ids = list(dict.fromkeys(int(item) for item in ids))
    if not unique_ids:
        return []
    return [
        unique_ids[start : start + batch_size]
        for start in range(0, len(unique_ids), batch_size)
    ]


async def _execute_in_batches(
    cursor,
    sql_template: str,
    ids: list[int],
    *,
    batch_size: int = _IN_BATCH_SIZE,
) -> None:
    """执行带 {placeholders} 的 SQL；ids 分批绑定，值不拼进语句。"""
    for chunk in _iter_id_batches(ids, batch_size=batch_size):
        placeholders = ",".join("?" for _ in chunk)
        await cursor.execute(
            sql_template.format(placeholders=placeholders),
            tuple(chunk),
        )


async def _count_in_batches(
    cursor,
    sql_template: str,
    ids: list[int],
    *,
    batch_size: int = _IN_BATCH_SIZE,
) -> int:
    """分批 COUNT(*) 并求和；sql_template 需含 {placeholders}。"""
    total = 0
    for chunk in _iter_id_batches(ids, batch_size=batch_size):
        placeholders = ",".join("?" for _ in chunk)
        await cursor.execute(
            sql_template.format(placeholders=placeholders),
            tuple(chunk),
        )
        row = await cursor.fetchone()
        total += int(row[0] if row else 0)
    return total


async def _fetch_updates_for_event_ids(
    cursor, event_ids: list[int]
) -> list[dict[str, Any]]:
    """按 event_id 分批拉取 event_updates 行。"""
    rows: list[dict[str, Any]] = []
    for chunk in _iter_id_batches(event_ids):
        placeholders = ",".join("?" for _ in chunk)
        await cursor.execute(
            f"""
            SELECT id, event_id, source_event_id, report_num, magnitude, depth,
                   description, level, wind_speed, pressure, latitude, longitude,
                   time, recorded_at
            FROM event_updates
            WHERE event_id IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(chunk),
        )
        rows.extend(dict(item) for item in await cursor.fetchall())
    rows.sort(key=lambda item: int(item.get("id") or 0))
    return rows


class HistoryDirtyDataCleanupService:
    """历史脏数据清理服务。"""

    CN_SOURCE_ALIASES = ("fan_studio_tsunami", "china_tsunami_fanstudio")
    CWA_REPORT_SOURCE_ALIASES = (
        "cwa_fanstudio_report",
        "fan_studio_cwa_report",
        "taiwan_cwa_report",
    )

    def __init__(self, db):
        """
        Args:
            db: DatabaseManager 实例（复用连接与 initialize 生命周期）。
        """
        self.db = db
        self._done = False

    def _marker_path(self) -> Path | None:
        """按数据库文件名区分标记，避免多库共享目录时互相覆盖。"""
        db_path = getattr(self.db, "db_path", None)
        if db_path is None:
            return None
        path = Path(db_path)
        return (
            path.parent
            / f".history_dirty_data_cleanup_{_MARKER_VERSION}_{path.stem}.done"
        )

    def _legacy_marker_paths(self) -> list[Path]:
        """旧版标记路径（仅文档/排查用，不用于跳过 v3）。"""
        db_path = getattr(self.db, "db_path", None)
        if db_path is None:
            return []
        path = Path(db_path)
        parent = path.parent
        return [
            parent / _MARKER_NAME,
            parent / f".history_dirty_data_cleanup_v2_{path.stem}.done",
            parent / f".tsunami_history_cleanup_v1_{path.stem}.done",
            parent / _LEGACY_TSUNAMI_MARKER_NAME,
        ]

    def _is_marked_done(self) -> bool:
        marker = self._marker_path()
        return bool(marker and marker.is_file())

    def _write_marker(self) -> None:
        marker = self._marker_path()
        if marker is None:
            return
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("done\n", encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[灾害预警] 写入历史脏数据清理标记失败: {exc}")

    @staticmethod
    def _normalize_event_key(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "|" in text:
            return text.split("|", 1)[-1].strip()
        return text

    @classmethod
    def _group_key(cls, row: dict[str, Any]) -> str:
        unique_id = str(row.get("unique_id") or "").strip()
        real_event_id = str(row.get("real_event_id") or "").strip()
        bare_unique = cls._normalize_event_key(unique_id)
        if bare_unique:
            return f"uid:{bare_unique}"
        if real_event_id:
            return f"rid:{real_event_id}"
        return f"id:{row.get('id')}"

    @staticmethod
    def _row_sort_key(row: dict[str, Any]) -> tuple:
        return (
            str(row.get("updated_at") or ""),
            str(row.get("time") or ""),
            str(row.get("created_at") or ""),
            int(row.get("id") or 0),
        )

    @classmethod
    def _preferred_source(cls, rows: list[dict[str, Any]]) -> str:
        for row in reversed(rows):
            source = str(row.get("source") or row.get("source_id") or "").strip()
            if not source:
                continue
            normalized = normalize_source_name(source) or source
            if normalized:
                return normalized
        return "china_tsunami_fanstudio"

    @classmethod
    def _preferred_real_event_id(cls, rows: list[dict[str, Any]]) -> str:
        for row in reversed(rows):
            real_event_id = str(row.get("real_event_id") or "").strip()
            if real_event_id:
                return real_event_id
            bare = cls._normalize_event_key(row.get("unique_id"))
            if bare:
                return bare
        return ""

    @classmethod
    def _preferred_unique_id(
        cls, rows: list[dict[str, Any]], *, source: str, real_event_id: str
    ) -> str:
        for row in reversed(rows):
            unique_id = str(row.get("unique_id") or "").strip()
            if unique_id and "|" in unique_id:
                return unique_id
        bare = cls._normalize_event_key(real_event_id) or cls._normalize_event_key(
            rows[-1].get("unique_id") if rows else ""
        )
        if bare and source:
            return f"{source}|{bare}"
        return bare

    @classmethod
    def _needs_normalize(
        cls,
        keep: dict[str, Any],
        *,
        source: str,
        real_event_id: str,
        unique_id: str,
        update_count: int,
    ) -> bool:
        cur_source = str(keep.get("source") or "").strip()
        cur_source_id = str(keep.get("source_id") or "").strip()
        cur_real = str(keep.get("real_event_id") or "").strip()
        cur_unique = str(keep.get("unique_id") or "").strip()
        cur_update = int(keep.get("update_count", 1) or 1)
        cur_report = keep.get("report_num")

        if cur_source != source:
            return True
        if cur_source_id != source:
            return True
        if real_event_id and cur_real != real_event_id:
            return True
        if unique_id and cur_unique != unique_id:
            return True
        if cur_update != update_count:
            return True
        if cur_report is None and update_count:
            return True
        return False

    @classmethod
    def _is_cwa_report_source(
        cls, source: str | None, source_id: str | None = None
    ) -> bool:
        for raw in (source, source_id):
            normalized = normalize_source_name(str(raw or "").strip())
            if normalized in cls.CWA_REPORT_SOURCE_ALIASES:
                return True
            if normalized == "cwa_fanstudio_report":
                return True
        return False

    @classmethod
    def _is_poison_cwa_event_id(cls, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        bare = cls._normalize_event_key(text)
        lower = bare.lower()
        return lower in _CWA_REPORT_POISON_IDS

    @classmethod
    def _is_wolfx_list_source(
        cls, source: str | None, source_id: str | None = None
    ) -> bool:
        for raw in (source, source_id):
            key = str(raw or "").strip().lower()
            if key in _WOLFX_LIST_SOURCES:
                return True
            normalized = normalize_source_name(key)
            if normalized in _WOLFX_LIST_SOURCES:
                return True
        return False

    @staticmethod
    def _empty_result() -> dict[str, int]:
        return {
            "kept": 0,
            "deleted": 0,
            "groups": 0,
            "skipped": 0,
            "updated": 0,
            "cwa_deleted_events": 0,
            "cwa_deleted_updates": 0,
            "wolfx_compressed_events": 0,
            "wolfx_deleted_updates": 0,
            "dup_unique_deleted": 0,
            "unknown_location_deleted": 0,
        }

    async def run_once(self, *, force: bool = False) -> dict[str, int]:
        """执行一次清理；默认进程内 + 磁盘标记只跑一次。"""
        if self._done and not force:
            result = self._empty_result()
            result["skipped"] = 1
            return result
        if not force and self._is_marked_done():
            self._done = True
            logger.debug("[灾害预警] 历史脏数据清理：已有完成标记，跳过")
            result = self._empty_result()
            result["skipped"] = 1
            return result

        connection = await self.db._ensure_connection()
        cursor = await connection.cursor()
        try:
            tsunami_result = await self._cleanup_tsunami_history(cursor)
            cwa_result = await self._cleanup_cwa_report_poison(cursor)
            wolfx_result = await self._compress_wolfx_list_poll_spam(cursor)
            dup_result = await self._fold_duplicate_unique_id_rows(cursor)
            unknown_result = await self._cleanup_unknown_location_rows(cursor)

            await connection.commit()
            self._done = True
            self._write_marker()

            try:
                from ..network.admin.api.events_routes import invalidate_sources_cache

                invalidate_sources_cache()
            except Exception as exc:
                logger.debug(f"[灾害预警] 无法失效源缓存: {exc}")

            result = {
                **self._empty_result(),
                **tsunami_result,
                **cwa_result,
                **wolfx_result,
                **dup_result,
                **unknown_result,
                "skipped": 0,
            }

            changed = any(
                int(result.get(key) or 0) > 0
                for key in (
                    "deleted",
                    "groups",
                    "updated",
                    "cwa_deleted_events",
                    "cwa_deleted_updates",
                    "wolfx_compressed_events",
                    "wolfx_deleted_updates",
                    "dup_unique_deleted",
                    "unknown_location_deleted",
                )
            )
            if changed:
                logger.info(
                    "[灾害预警] 历史脏数据清理完成: "
                    f"海啸保留 {result.get('kept', 0)}, "
                    f"海啸删除 {result.get('deleted', 0)}, "
                    f"海啸多报折叠 {result.get('groups', 0)}, "
                    f"CWA污染事件 {result.get('cwa_deleted_events', 0)}, "
                    f"CWA污染更新 {result.get('cwa_deleted_updates', 0)}, "
                    f"Wolfx压缩事件 {result.get('wolfx_compressed_events', 0)}, "
                    f"Wolfx删除更新 {result.get('wolfx_deleted_updates', 0)}, "
                    f"同unique折叠删除 {result.get('dup_unique_deleted', 0)}, "
                    f"unknown_location删除 {result.get('unknown_location_deleted', 0)}"
                )
            else:
                logger.debug("[灾害预警] 历史脏数据清理：无需变更")
            return result
        except Exception as exc:
            logger.error(f"[灾害预警] 历史脏数据清理失败: {exc}")
            await connection.rollback()
            raise

    async def _cleanup_tsunami_history(self, cursor) -> dict[str, int]:
        """折叠海啸历史重复主表行。"""
        await cursor.execute(
            """
            SELECT *
            FROM events
            WHERE type='tsunami'
            ORDER BY id ASC
            """
        )
        rows = [dict(item) for item in await cursor.fetchall()]
        if not rows:
            return {"kept": 0, "deleted": 0, "groups": 0, "updated": 0}

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(self._group_key(row), []).append(row)

        multi_groups = [items for items in groups.values() if len(items) > 1]
        delete_ids: list[int] = []
        kept = 0
        updated = 0

        for items in groups.values():
            if len(items) == 1:
                only = items[0]
                if await self._normalize_keep_row(cursor, [only], keep=only):
                    updated += 1
                kept += 1
                continue

            items_sorted = sorted(items, key=self._row_sort_key)
            keep = items_sorted[-1]
            if await self._fold_group_to_keep(cursor, items_sorted, keep=keep):
                updated += 1
            kept += 1
            for item in items_sorted[:-1]:
                delete_ids.append(int(item["id"]))

        if delete_ids:
            # 折叠时已把历史迁入 keep；此处仅清理被折叠主表及其残留 updates
            await _execute_in_batches(
                cursor,
                "DELETE FROM event_updates WHERE event_id IN ({placeholders})",
                delete_ids,
            )
            await _execute_in_batches(
                cursor,
                "DELETE FROM events WHERE id IN ({placeholders})",
                delete_ids,
            )

        return {
            "kept": kept,
            "deleted": len(delete_ids),
            "groups": len(multi_groups),
            "updated": updated,
        }

    async def _cleanup_cwa_report_poison(self, cursor) -> dict[str, int]:
        """删除 CWA 地震报告因上游 id 复用产生的污染簇。"""
        await cursor.execute(
            """
            SELECT id, source, source_id, real_event_id, unique_id
            FROM events
            WHERE type IN ('earthquake', 'earthquake_warning', 'earthquake_info')
            """
        )
        rows = [dict(item) for item in await cursor.fetchall()]
        poison_ids: list[int] = []
        for row in rows:
            if not self._is_cwa_report_source(row.get("source"), row.get("source_id")):
                continue
            real_id = str(row.get("real_event_id") or "").strip()
            unique_id = str(row.get("unique_id") or "").strip()
            if self._is_poison_cwa_event_id(real_id) or self._is_poison_cwa_event_id(
                unique_id
            ):
                poison_ids.append(int(row["id"]))

        if not poison_ids:
            return {"cwa_deleted_events": 0, "cwa_deleted_updates": 0}

        poison_ids = list(dict.fromkeys(poison_ids))
        updates_count = await _count_in_batches(
            cursor,
            "SELECT COUNT(*) FROM event_updates WHERE event_id IN ({placeholders})",
            poison_ids,
        )
        await _execute_in_batches(
            cursor,
            "DELETE FROM event_updates WHERE event_id IN ({placeholders})",
            poison_ids,
        )
        await _execute_in_batches(
            cursor,
            "DELETE FROM events WHERE id IN ({placeholders})",
            poison_ids,
        )

        logger.info(
            "[灾害预警] CWA 报告污染清理："
            f"删除事件 {len(poison_ids)} 条，删除更新快照 {updates_count} 条 "
            f"（污染编号集合 {sorted(_CWA_REPORT_POISON_IDS)}）"
        )
        return {
            "cwa_deleted_events": len(poison_ids),
            "cwa_deleted_updates": updates_count,
        }

    async def _compress_wolfx_list_poll_spam(self, cursor) -> dict[str, int]:
        """压缩 Wolfx 列表轮询产生的同内容 event_updates 刷屏。

        对 cenc_wolfx / jma_wolfx_info：
        - 按内容指纹去重 updates，只保留每组最早一条
        - 主表 update_count 回写为去重后的真实条数
        """
        await cursor.execute(
            """
            SELECT id, source, source_id, update_count
            FROM events
            WHERE type IN ('earthquake', 'earthquake_warning', 'earthquake_info')
            """
        )
        events = [dict(item) for item in await cursor.fetchall()]
        target_ids = [
            int(row["id"])
            for row in events
            if self._is_wolfx_list_source(row.get("source"), row.get("source_id"))
        ]
        if not target_ids:
            return {"wolfx_compressed_events": 0, "wolfx_deleted_updates": 0}

        compressed_events = 0
        deleted_updates = 0

        for event_id in target_ids:
            await cursor.execute(
                """
                SELECT id, report_num, magnitude, depth, description, level,
                       wind_speed, pressure, latitude, longitude, time
                FROM event_updates
                WHERE event_id=?
                ORDER BY id ASC
                """,
                (event_id,),
            )
            updates = [dict(item) for item in await cursor.fetchall()]
            if len(updates) <= 1:
                # 仍可能 update_count 虚高，校正为 max(1, updates)
                desired = max(1, len(updates))
                await cursor.execute(
                    "UPDATE events SET update_count=? WHERE id=? AND update_count!=?",
                    (desired, event_id, desired),
                )
                continue

            seen: set[str] = set()
            keep_ids: list[int] = []
            drop_ids: list[int] = []
            for item in updates:
                fp = _update_content_fingerprint(item)
                if fp in seen:
                    drop_ids.append(int(item["id"]))
                else:
                    seen.add(fp)
                    keep_ids.append(int(item["id"]))

            if drop_ids:
                await _execute_in_batches(
                    cursor,
                    "DELETE FROM event_updates WHERE id IN ({placeholders})",
                    drop_ids,
                )
                deleted_updates += len(drop_ids)
                compressed_events += 1

            desired_count = max(1, len(keep_ids))
            await cursor.execute(
                "UPDATE events SET update_count=? WHERE id=?",
                (desired_count, event_id),
            )

        if compressed_events or deleted_updates:
            logger.info(
                "[灾害预警] Wolfx 列表刷屏压缩: "
                f"压缩事件 {compressed_events} 条, 删除重复 updates {deleted_updates} 条"
            )
        return {
            "wolfx_compressed_events": compressed_events,
            "wolfx_deleted_updates": deleted_updates,
        }

    async def _fold_duplicate_unique_id_rows(self, cursor) -> dict[str, int]:
        """折叠同一 source+unique_id 的多主表行（保留最新）。"""
        await cursor.execute(
            """
            SELECT id, source, source_id, unique_id, real_event_id,
                   updated_at, time, created_at, update_count
            FROM events
            WHERE unique_id IS NOT NULL AND unique_id != ''
              AND unique_id != 'unknown_location'
            ORDER BY id ASC
            """
        )
        rows = [dict(item) for item in await cursor.fetchall()]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            raw_source = str(row.get("source") or row.get("source_id") or "").strip()
            # 历史别名归一化后再分组，避免 fan_studio_tsunami / china_tsunami_fanstudio 漏并
            source = normalize_source_name(raw_source) or raw_source
            unique_id = str(row.get("unique_id") or "").strip()
            if not source or not unique_id:
                continue
            groups.setdefault((source, unique_id), []).append(row)

        delete_ids: list[int] = []
        for items in groups.values():
            if len(items) <= 1:
                continue
            items_sorted = sorted(items, key=self._row_sort_key)
            keep = items_sorted[-1]
            keep_id = int(keep["id"])
            # 把被删行的 updates 迁到 keep（避免丢历史），再删重复主表
            for item in items_sorted[:-1]:
                old_id = int(item["id"])
                await cursor.execute(
                    "UPDATE event_updates SET event_id=? WHERE event_id=?",
                    (keep_id, old_id),
                )
                delete_ids.append(old_id)

            # 压缩 keep 上可能因迁移产生的同内容 updates
            await cursor.execute(
                """
                SELECT id, report_num, magnitude, depth, description, level,
                       wind_speed, pressure, latitude, longitude, time
                FROM event_updates
                WHERE event_id=?
                ORDER BY id ASC
                """,
                (keep_id,),
            )
            updates = [dict(item) for item in await cursor.fetchall()]
            seen: set[str] = set()
            drop_ids: list[int] = []
            keep_update_count = 0
            for item in updates:
                fp = _update_content_fingerprint(item)
                if fp in seen:
                    drop_ids.append(int(item["id"]))
                else:
                    seen.add(fp)
                    keep_update_count += 1
            if drop_ids:
                await _execute_in_batches(
                    cursor,
                    "DELETE FROM event_updates WHERE id IN ({placeholders})",
                    drop_ids,
                )
            desired = max(1, keep_update_count)
            await cursor.execute(
                "UPDATE events SET update_count=? WHERE id=?",
                (desired, keep_id),
            )

        if delete_ids:
            # updates 已迁移；这里只删主表
            await _execute_in_batches(
                cursor,
                "DELETE FROM events WHERE id IN ({placeholders})",
                delete_ids,
            )
            logger.info(
                f"[灾害预警] 同 unique_id 多主表折叠: 删除重复事件 {len(delete_ids)} 条"
            )

        return {"dup_unique_deleted": len(delete_ids)}

    async def _cleanup_unknown_location_rows(self, cursor) -> dict[str, int]:
        """删除 unique_id 退化为全局常量 unknown_location 的历史脏行。"""
        # 不用 LIKE：下划线是单字符通配，易误匹配。精确等值 + 安全后缀匹配。
        await cursor.execute(
            """
            SELECT id, unique_id, real_event_id
            FROM events
            WHERE unique_id = 'unknown_location'
               OR unique_id LIKE '%|unknown\\_location' ESCAPE '\\'
               OR real_event_id = 'unknown_location'
            """
        )
        ids: list[int] = []
        for row in await cursor.fetchall():
            row_dict = dict(row)
            unique_id = str(row_dict.get("unique_id") or "").strip()
            real_event_id = str(row_dict.get("real_event_id") or "").strip()
            if (
                unique_id == "unknown_location"
                or unique_id.endswith("|unknown_location")
                or real_event_id == "unknown_location"
            ):
                ids.append(int(row_dict["id"]))
        if not ids:
            return {"unknown_location_deleted": 0}

        await _execute_in_batches(
            cursor,
            "DELETE FROM event_updates WHERE event_id IN ({placeholders})",
            ids,
        )
        await _execute_in_batches(
            cursor,
            "DELETE FROM events WHERE id IN ({placeholders})",
            ids,
        )
        logger.info(f"[灾害预警] unknown_location 撞键清理: 删除事件 {len(ids)} 条")
        return {"unknown_location_deleted": len(ids)}

    async def _normalize_keep_row(
        self,
        cursor,
        rows: list[dict[str, Any]],
        *,
        keep: dict[str, Any],
    ) -> bool:
        """规范化保留行；有实际变更返回 True。"""
        source = self._preferred_source(rows)
        real_event_id = self._preferred_real_event_id(rows)
        unique_id = self._preferred_unique_id(
            rows, source=source, real_event_id=real_event_id
        )
        update_count = max(len(rows), int(keep.get("update_count", 1) or 1))
        if not self._needs_normalize(
            keep,
            source=source,
            real_event_id=real_event_id,
            unique_id=unique_id,
            update_count=update_count,
        ):
            return False

        await cursor.execute(
            """
            UPDATE events
            SET source = ?,
                source_id = ?,
                real_event_id = COALESCE(NULLIF(?, ''), real_event_id),
                unique_id = COALESCE(NULLIF(?, ''), unique_id),
                update_count = ?,
                report_num = COALESCE(report_num, ?)
            WHERE id = ?
            """,
            (
                source,
                source,
                real_event_id or None,
                unique_id or None,
                update_count,
                update_count,
                keep["id"],
            ),
        )
        return True

    async def _fold_group_to_keep(
        self,
        cursor,
        items_sorted: list[dict[str, Any]],
        *,
        keep: dict[str, Any],
    ) -> bool:
        """把同组历史行折叠到 keep，并合并 event_updates 报次快照。

        不再无条件清空 keep 上已有 updates：先迁移同组 updates，
        再按主表行补缺失报次，最后按内容指纹去重，避免丢失 wind/pressure 等字段。
        """
        await self._normalize_keep_row(cursor, items_sorted, keep=keep)

        keep_id = int(keep["id"])
        # 1) 把同组其它主表上的 updates 迁到 keep
        for item in items_sorted:
            old_id = int(item["id"])
            if old_id == keep_id:
                continue
            await cursor.execute(
                "UPDATE event_updates SET event_id=? WHERE event_id=?",
                (keep_id, old_id),
            )

        # 2) 读取 keep 上现有 updates（含刚迁移的）
        existing_updates = await _fetch_updates_for_event_ids(cursor, [keep_id])
        seen_fps = {_update_content_fingerprint(item) for item in existing_updates}

        # 3) 用主表行补缺失快照（仅当内容指纹尚未存在）
        for index, item in enumerate(items_sorted, start=1):
            description = item.get("description")
            level = item.get("level")
            magnitude = item.get("magnitude")
            depth = item.get("depth")
            latitude = item.get("latitude")
            longitude = item.get("longitude")
            event_time = item.get("time")
            source_event_id = (
                str(item.get("real_event_id") or "").strip()
                or self._normalize_event_key(item.get("unique_id"))
                or str(item.get("id") or "")
            )
            recorded_at = (
                item.get("updated_at") or item.get("created_at") or item.get("time")
            )
            candidate = {
                "report_num": index,
                "magnitude": magnitude,
                "depth": depth,
                "description": description,
                "level": level,
                "wind_speed": item.get("max_wave_height") or item.get("wind_speed"),
                "pressure": item.get("pressure"),
                "latitude": latitude,
                "longitude": longitude,
                "time": event_time,
            }
            fp = _update_content_fingerprint(candidate)
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            await cursor.execute(
                """
                INSERT INTO event_updates
                    (event_id, source_event_id, report_num, magnitude, depth,
                     description, level, wind_speed, pressure, latitude, longitude,
                     time, recorded_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    keep_id,
                    source_event_id,
                    index,
                    magnitude,
                    depth,
                    description,
                    level,
                    candidate["wind_speed"],
                    candidate["pressure"],
                    latitude,
                    longitude,
                    event_time,
                    recorded_at,
                ),
            )

        # 4) 压缩 keep 上同内容重复 updates，并回写 update_count
        await cursor.execute(
            """
            SELECT id, report_num, magnitude, depth, description, level,
                   wind_speed, pressure, latitude, longitude, time
            FROM event_updates
            WHERE event_id=?
            ORDER BY id ASC
            """,
            (keep_id,),
        )
        updates = [dict(item) for item in await cursor.fetchall()]
        seen: set[str] = set()
        drop_ids: list[int] = []
        keep_update_count = 0
        for item in updates:
            fp = _update_content_fingerprint(item)
            if fp in seen:
                drop_ids.append(int(item["id"]))
            else:
                seen.add(fp)
                keep_update_count += 1
        if drop_ids:
            await _execute_in_batches(
                cursor,
                "DELETE FROM event_updates WHERE id IN ({placeholders})",
                drop_ids,
            )
        desired = max(1, keep_update_count, len(items_sorted))
        await cursor.execute(
            "UPDATE events SET update_count=? WHERE id=?",
            (desired, keep_id),
        )
        return True


__all__ = [
    "HistoryDirtyDataCleanupService",
]
