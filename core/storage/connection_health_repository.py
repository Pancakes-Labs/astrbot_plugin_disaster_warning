"""
连接健康仓储。

负责 connection_health_samples / connection_health_days / connection_incidents
三张表的读写，与通用 events 事件流解耦。DatabaseManager 仅保留建表职责。
"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger


class ConnectionHealthRepository:
    """连接健康采样、日聚合与事故仓储。"""

    def __init__(self, db):
        """
        Args:
            db: DatabaseManager 实例（复用其连接与 initialize 生命周期）。
        """
        self.db = db

    async def _connection(self):
        return await self.db._ensure_connection()

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        if row is None:
            return {}
        try:
            return dict(row)
        except Exception:
            return {}

    async def insert_sample(self, sample: dict[str, Any]) -> int:
        """写入一条健康采样，返回 rowid。"""
        connection = await self._connection()
        detail = sample.get("detail")
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        cursor = await connection.cursor()
        await cursor.execute(
            """
            INSERT INTO connection_health_samples (
                group_key, ts, state, enabled, connected,
                latency_ms, retry_count, circuit_open, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(sample.get("group_key") or "").strip(),
                str(sample.get("ts") or "").strip(),
                str(sample.get("state") or "not_monitored").strip(),
                1 if sample.get("enabled") else 0,
                1 if sample.get("connected") else 0,
                sample.get("latency_ms"),
                int(sample.get("retry_count") or 0),
                1 if sample.get("circuit_open") else 0,
                detail,
            ),
        )
        await connection.commit()
        return int(cursor.lastrowid or 0)

    async def insert_samples_batch(self, samples: list[dict[str, Any]]) -> int:
        """批量写入采样，返回写入条数。"""
        if not samples:
            return 0
        connection = await self._connection()
        rows = []
        for sample in samples:
            detail = sample.get("detail")
            if detail is not None and not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False)
            rows.append(
                (
                    str(sample.get("group_key") or "").strip(),
                    str(sample.get("ts") or "").strip(),
                    str(sample.get("state") or "not_monitored").strip(),
                    1 if sample.get("enabled") else 0,
                    1 if sample.get("connected") else 0,
                    sample.get("latency_ms"),
                    int(sample.get("retry_count") or 0),
                    1 if sample.get("circuit_open") else 0,
                    detail,
                )
            )
        cursor = await connection.cursor()
        await cursor.executemany(
            """
            INSERT INTO connection_health_samples (
                group_key, ts, state, enabled, connected,
                latency_ms, retry_count, circuit_open, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await connection.commit()
        return len(rows)

    async def upsert_day_aggregate(self, day_row: dict[str, Any]) -> None:
        """按 (group_key, day) 累加日聚合分钟数。"""
        connection = await self._connection()
        group_key = str(day_row.get("group_key") or "").strip()
        day = str(day_row.get("day") or "").strip()
        if not group_key or not day:
            return

        add_monitored = int(day_row.get("minutes_monitored") or 0)
        add_major = int(day_row.get("minutes_major") or 0)
        add_partial = int(day_row.get("minutes_partial") or 0)
        add_degraded = int(day_row.get("minutes_degraded") or 0)
        add_samples = int(day_row.get("sample_count") or 1)
        candidate_worst = str(day_row.get("worst_state") or "not_monitored")
        updated_at = str(day_row.get("updated_at") or "").strip() or None

        cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT minutes_monitored, minutes_major, minutes_partial,
                   minutes_degraded, worst_state, sample_count
            FROM connection_health_days
            WHERE group_key = ? AND day = ?
            LIMIT 1
            """,
            (group_key, day),
        )
        existing = await cursor.fetchone()

        rank = {
            "not_monitored": 0,
            "operational": 1,
            "degraded": 2,
            "partial_outage": 3,
            "major_outage": 4,
            "maintenance": 2,
        }

        if existing is None:
            monitored = add_monitored
            major = add_major
            partial = add_partial
            degraded = add_degraded
            worst = candidate_worst
            samples = add_samples
        else:
            monitored = int(existing["minutes_monitored"] or 0) + add_monitored
            major = int(existing["minutes_major"] or 0) + add_major
            partial = int(existing["minutes_partial"] or 0) + add_partial
            degraded = int(existing["minutes_degraded"] or 0) + add_degraded
            samples = int(existing["sample_count"] or 0) + add_samples
            prev_worst = str(existing["worst_state"] or "not_monitored")
            worst = (
                candidate_worst
                if rank.get(candidate_worst, 0) >= rank.get(prev_worst, 0)
                else prev_worst
            )

        # degraded 不扣 uptime；partial/major 按 100% 计入中断
        if monitored > 0:
            outage = min(monitored, major + partial)
            uptime_ratio = max(0.0, min(1.0, 1.0 - (outage / float(monitored))))
        else:
            uptime_ratio = None

        await cursor.execute(
            """
            INSERT INTO connection_health_days (
                group_key, day, minutes_monitored, minutes_major, minutes_partial,
                minutes_degraded, worst_state, uptime_ratio, sample_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(group_key, day) DO UPDATE SET
                minutes_monitored = excluded.minutes_monitored,
                minutes_major = excluded.minutes_major,
                minutes_partial = excluded.minutes_partial,
                minutes_degraded = excluded.minutes_degraded,
                worst_state = excluded.worst_state,
                uptime_ratio = excluded.uptime_ratio,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
            """,
            (
                group_key,
                day,
                monitored,
                major,
                partial,
                degraded,
                worst,
                uptime_ratio,
                samples,
                updated_at,
            ),
        )
        await connection.commit()

    async def list_day_aggregates(
        self,
        *,
        days: int = 90,
        group_keys: list[str] | None = None,
        since_day: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询日聚合列表。"""
        connection = await self._connection()
        cursor = await connection.cursor()
        params: list[Any] = []
        clauses: list[str] = []

        if since_day:
            clauses.append("day >= ?")
            params.append(str(since_day))
        if group_keys:
            placeholders = ",".join("?" for _ in group_keys)
            clauses.append(f"group_key IN ({placeholders})")
            params.extend([str(k) for k in group_keys])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # 多取一些再在服务层按 90 天窗口裁剪
        limit = max(int(days) * 12, 90)
        await cursor.execute(
            f"""
            SELECT group_key, day, minutes_monitored, minutes_major, minutes_partial,
                   minutes_degraded, worst_state, uptime_ratio, sample_count, updated_at
            FROM connection_health_days
            {where_sql}
            ORDER BY day ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def create_incident(self, incident: dict[str, Any]) -> int:
        """创建事故，返回 id。"""
        connection = await self._connection()
        timeline = incident.get("timeline") or []
        if not isinstance(timeline, str):
            timeline = json.dumps(timeline, ensure_ascii=False)
        cursor = await connection.cursor()
        await cursor.execute(
            """
            INSERT INTO connection_incidents (
                group_key, severity, status, title, started_at, ended_at, timeline_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(incident.get("group_key") or "").strip(),
                str(incident.get("severity") or "major_outage").strip(),
                str(incident.get("status") or "investigating").strip(),
                str(incident.get("title") or "").strip(),
                str(incident.get("started_at") or "").strip(),
                incident.get("ended_at"),
                timeline,
            ),
        )
        await connection.commit()
        return int(cursor.lastrowid or 0)

    async def update_incident(self, incident_id: int, fields: dict[str, Any]) -> None:
        """更新事故字段。"""
        if not fields:
            return
        connection = await self._connection()
        allowed = {
            "severity",
            "status",
            "title",
            "started_at",
            "ended_at",
            "timeline_json",
            "timeline",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed and key != "timeline":
                continue
            col = "timeline_json" if key in {"timeline", "timeline_json"} else key
            if key == "timeline" and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{col} = ?")
            params.append(value)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        params.append(int(incident_id))
        cursor = await connection.cursor()
        await cursor.execute(
            f"UPDATE connection_incidents SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await connection.commit()

    async def get_open_incident(self, group_key: str) -> dict[str, Any] | None:
        """获取某连接组当前未关闭的事故。"""
        connection = await self._connection()
        cursor = await connection.cursor()
        await cursor.execute(
            """
            SELECT id, group_key, severity, status, title, started_at, ended_at,
                   timeline_json, created_at, updated_at
            FROM connection_incidents
            WHERE group_key = ?
              AND status != 'resolved'
              AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (str(group_key or "").strip(),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        data = self._row_to_dict(row)
        raw_timeline = data.get("timeline_json")
        if raw_timeline:
            try:
                data["timeline"] = json.loads(raw_timeline)
            except Exception:
                data["timeline"] = []
        else:
            data["timeline"] = []
        return data

    async def list_incidents(
        self,
        *,
        days: int = 14,
        limit: int = 100,
        group_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出近期事故（含已解决）。"""
        connection = await self._connection()
        cursor = await connection.cursor()
        params: list[Any] = []
        clauses: list[str] = []
        if group_key:
            clauses.append("group_key = ?")
            params.append(str(group_key).strip())
        # 用 started_at 字符串比较：ISO 格式可字典序比较
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        await cursor.execute(
            f"""
            SELECT id, group_key, severity, status, title, started_at, ended_at,
                   timeline_json, created_at, updated_at
            FROM connection_incidents
            {where_sql}
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        )
        rows = await cursor.fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            data = self._row_to_dict(row)
            raw_timeline = data.get("timeline_json")
            if raw_timeline:
                try:
                    data["timeline"] = json.loads(raw_timeline)
                except Exception:
                    data["timeline"] = []
            else:
                data["timeline"] = []
            result.append(data)
        # days 过滤在服务层按时区做更稳妥；这里先返回 limit 条
        _ = days
        return result

    async def purge_old_samples(self, *, keep_days: int = 14) -> int:
        """清理过期原始采样，返回删除行数。"""
        connection = await self._connection()
        cursor = await connection.cursor()
        # SQLite datetime 对 ISO 文本可用；用 julianday 近似
        await cursor.execute(
            """
            DELETE FROM connection_health_samples
            WHERE julianday('now') - julianday(substr(ts, 1, 19)) > ?
            """,
            (max(1, int(keep_days)),),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        await connection.commit()
        if deleted:
            logger.debug(f"[灾害预警] 已清理连接健康采样 {deleted} 条")
        return int(deleted or 0)

    async def purge_old_days(self, *, keep_days: int = 180) -> int:
        """清理过期日聚合。"""
        connection = await self._connection()
        cursor = await connection.cursor()
        await cursor.execute(
            """
            DELETE FROM connection_health_days
            WHERE julianday('now') - julianday(day) > ?
            """,
            (max(90, int(keep_days)),),
        )
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        await connection.commit()
        return int(deleted or 0)
