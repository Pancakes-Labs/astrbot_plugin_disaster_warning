"""
S-Net 测站峰值仓储。

负责 snet_station_peaks 表的读写，与通用 events 事件流解耦。
DatabaseManager 仅保留建表职责；峰值 upsert/查询集中在本仓储。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...utils.converters import ScaleConverter


class SnetPeakRepository:
    """S-Net 测站峰值仓储。"""

    # 日本震度 5弱 的計測震度起点
    MAJOR_SHINDO_THRESHOLD = 4.5

    def __init__(self, db):
        """
        Args:
            db: DatabaseManager 实例（复用其连接与 initialize 生命周期）。
        """
        self.db = db

    async def _connection(self):
        return await self.db._ensure_connection()

    @staticmethod
    def _normalize_station_observation(
        station: dict[str, Any],
        *,
        observed_at: str,
        hit_threshold: float | None = None,
    ) -> dict[str, Any] | None:
        """规范化单站观测字段；非法数据返回 None。"""
        station_id = str(
            station.get("station_id")
            or station.get("name")
            or station.get("station_name")
            or ""
        ).strip()
        if not station_id:
            return None

        try:
            shindo = float(station.get("shindo"))
        except (TypeError, ValueError):
            return None

        observed_at_text = str(observed_at or "").strip()
        if not observed_at_text:
            return None

        station_name = (
            str(
                station.get("station_name") or station.get("name") or station_id
            ).strip()
            or station_id
        )
        try:
            lat = float(station.get("lat")) if station.get("lat") is not None else None
        except (TypeError, ValueError):
            lat = None
        try:
            lon = float(station.get("lon")) if station.get("lon") is not None else None
        except (TypeError, ValueError):
            lon = None

        hit_increment = 0
        if hit_threshold is not None:
            try:
                if shindo >= float(hit_threshold):
                    hit_increment = 1
            except (TypeError, ValueError):
                hit_increment = 0

        return {
            "station_id": station_id,
            "station_name": station_name,
            "lat": lat,
            "lon": lon,
            "shindo": shindo,
            "observed_at": observed_at_text,
            "hit_increment": hit_increment,
        }

    async def _upsert_station_peak_on_cursor(
        self,
        cursor,
        station: dict[str, Any],
        *,
        observed_at: str,
        hit_threshold: float | None = None,
    ) -> dict[str, Any] | None:
        """在已有 cursor 上执行单站原子 upsert（不单独 commit）。"""
        normalized = self._normalize_station_observation(
            station,
            observed_at=observed_at,
            hit_threshold=hit_threshold,
        )
        if normalized is None:
            return None

        station_id = normalized["station_id"]
        station_name = normalized["station_name"]
        lat = normalized["lat"]
        lon = normalized["lon"]
        shindo = float(normalized["shindo"])
        observed_at_text = str(normalized["observed_at"])
        hit_increment = int(normalized["hit_increment"])

        await cursor.execute(
            """
            SELECT station_id, max_shindo, max_shindo_at, hit_count
            FROM snet_station_peaks
            WHERE station_id = ?
            LIMIT 1
            """,
            (station_id,),
        )
        existing = await cursor.fetchone()
        created = existing is None
        if existing is None:
            old_max = None
            old_max_at = observed_at_text
            old_hit = 0
        else:
            old_max = float(existing["max_shindo"] or 0.0)
            old_max_at = str(existing["max_shindo_at"] or observed_at_text)
            old_hit = int(existing["hit_count"] or 0)

        peak_updated = created or (old_max is None) or (shindo > float(old_max))
        new_max = shindo if peak_updated else float(old_max or 0.0)
        new_max_at = observed_at_text if peak_updated else old_max_at
        new_hit = old_hit + hit_increment

        # 单条原子 upsert：避免 SELECT 与 UPDATE 之间被并发写穿。
        await cursor.execute(
            """
            INSERT INTO snet_station_peaks (
                station_id, station_name, lat, lon,
                max_shindo, max_shindo_at,
                last_shindo, last_seen_at,
                first_seen_at, hit_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(station_id) DO UPDATE SET
                station_name = excluded.station_name,
                lat = COALESCE(excluded.lat, snet_station_peaks.lat),
                lon = COALESCE(excluded.lon, snet_station_peaks.lon),
                max_shindo = CASE
                    WHEN excluded.max_shindo > snet_station_peaks.max_shindo
                    THEN excluded.max_shindo
                    ELSE snet_station_peaks.max_shindo
                END,
                max_shindo_at = CASE
                    WHEN excluded.max_shindo > snet_station_peaks.max_shindo
                    THEN excluded.max_shindo_at
                    ELSE snet_station_peaks.max_shindo_at
                END,
                last_shindo = excluded.last_shindo,
                last_seen_at = excluded.last_seen_at,
                hit_count = snet_station_peaks.hit_count + excluded.hit_count,
                updated_at = excluded.updated_at
            """,
            (
                station_id,
                station_name,
                lat,
                lon,
                shindo,
                observed_at_text,
                shindo,
                observed_at_text,
                observed_at_text,
                hit_increment,
                observed_at_text,
            ),
        )
        return {
            "station_id": station_id,
            "station_name": station_name,
            "lat": lat,
            "lon": lon,
            "created": created,
            "peak_updated": peak_updated,
            "max_shindo": new_max,
            "max_shindo_at": new_max_at,
            "last_shindo": shindo,
            "last_seen_at": observed_at_text,
            "hit_count": new_hit,
        }

    async def upsert_station_peak(
        self,
        station: dict[str, Any],
        *,
        observed_at: str,
        hit_threshold: float | None = None,
    ) -> dict[str, Any] | None:
        """写入/更新单个测站峰值。

        规则：
        - max_shindo / max_shindo_at：仅当新值严格更大时更新；同值保留更早时间
        - last_shindo / last_seen_at：每次观测都更新
        - hit_count：当 shindo >= hit_threshold 时累加（可选）
        """
        try:
            connection = await self._connection()
            cursor = await connection.cursor()
            row = await self._upsert_station_peak_on_cursor(
                cursor,
                station,
                observed_at=observed_at,
                hit_threshold=hit_threshold,
            )
            if row is None:
                return None
            await connection.commit()
            return row
        except Exception as e:
            logger.error(f"[灾害预警] S-Net 测站峰值写入失败: {e}")
            try:
                if getattr(self.db, "connection", None) is not None:
                    await self.db.connection.rollback()
            except Exception:
                pass
            return None

    async def upsert_station_peaks_batch(
        self,
        stations: list[dict[str, Any]],
        *,
        observed_at: str,
        hit_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """批量 upsert 测站峰值（单事务，减少往返与半批提交）。"""
        results: list[dict[str, Any]] = []
        prepared = [
            station for station in (stations or []) if isinstance(station, dict)
        ]
        if not prepared:
            return results

        try:
            connection = await self._connection()
            cursor = await connection.cursor()
            for station in prepared:
                row = await self._upsert_station_peak_on_cursor(
                    cursor,
                    station,
                    observed_at=observed_at,
                    hit_threshold=hit_threshold,
                )
                if row is not None:
                    results.append(row)
            await connection.commit()
            return results
        except Exception as e:
            logger.error(f"[灾害预警] S-Net 测站峰值批量写入失败: {e}")
            try:
                if getattr(self.db, "connection", None) is not None:
                    await self.db.connection.rollback()
            except Exception:
                pass
            return []

    async def list_peaks(
        self,
        *,
        min_shindo: float | None = None,
        limit: int | None = None,
        order_by: str = "max_shindo",
    ) -> list[dict[str, Any]]:
        """查询测站峰值列表。"""
        try:
            connection = await self._connection()
            cursor = await connection.cursor()
            clauses: list[str] = []
            params: list[Any] = []
            if min_shindo is not None:
                clauses.append("max_shindo >= ?")
                params.append(float(min_shindo))
            where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

            order_key = str(order_by or "max_shindo").strip().lower()
            if order_key == "max_shindo_at":
                order_sql = (
                    " ORDER BY max_shindo_at DESC, max_shindo DESC, station_id ASC"
                )
            else:
                order_sql = (
                    " ORDER BY max_shindo DESC, max_shindo_at DESC, station_id ASC"
                )

            limit_sql = ""
            if limit is not None:
                try:
                    safe_limit = max(1, int(limit))
                    limit_sql = " LIMIT ?"
                    params.append(safe_limit)
                except (TypeError, ValueError):
                    limit_sql = ""

            await cursor.execute(
                f"SELECT * FROM snet_station_peaks{where_sql}{order_sql}{limit_sql}",
                tuple(params),
            )
            return [dict(row) for row in await cursor.fetchall()]
        except Exception as e:
            logger.error(f"[灾害预警] 查询 S-Net 测站峰值失败: {e}")
            return []

    async def get_global_max_peak(self) -> dict[str, Any] | None:
        """获取全网历史最大震度测站。"""
        rows = await self.list_peaks(limit=1, order_by="max_shindo")
        return rows[0] if rows else None

    async def clear_all(self) -> bool:
        """清空测站峰值表。"""
        try:
            connection = await self._connection()
            cursor = await connection.cursor()
            await cursor.execute("DELETE FROM snet_station_peaks")
            await connection.commit()
            return True
        except Exception as e:
            logger.error(f"[灾害预警] 清除 S-Net 测站峰值失败: {e}")
            try:
                if getattr(self.db, "connection", None) is not None:
                    await self.db.connection.rollback()
            except Exception:
                pass
            return False

    async def build_stats_summary(self) -> dict[str, Any]:
        """构建峰值统计摘要（供内存 snet_stats / 管理端卡片）。"""
        empty = {
            "station_count": 0,
            "stations_with_peak": 0,
            "global_max": None,
            "top_peaks": [],
            "recent_peak_updates": [],
            "last_observation_at": None,
        }
        try:
            connection = await self._connection()
            cursor = await connection.cursor()
            await cursor.execute("SELECT COUNT(*) AS cnt FROM snet_station_peaks")
            row = await cursor.fetchone()
            station_count = int(row["cnt"] if row else 0)

            global_max_row = await self.get_global_max_peak()
            global_max = self._to_global_max_view(global_max_row)

            # Top-N 按历史最大震度降序，供管理端 S-Net 卡片展示
            top_rows = await self.list_peaks(limit=3, order_by="max_shindo")
            top_peaks = []
            for item in top_rows:
                view = self._to_peak_update_view(item)
                if view is not None:
                    top_peaks.append(view)

            recent_rows = await self.list_peaks(limit=20, order_by="max_shindo_at")
            recent_peak_updates = []
            for item in recent_rows:
                view = self._to_peak_update_view(item)
                if view is not None:
                    recent_peak_updates.append(view)

            last_observation_at = None
            await cursor.execute(
                """
                SELECT last_seen_at
                FROM snet_station_peaks
                WHERE last_seen_at IS NOT NULL AND TRIM(last_seen_at) != ''
                ORDER BY last_seen_at DESC
                LIMIT 1
                """
            )
            last_row = await cursor.fetchone()
            if last_row:
                last_observation_at = str(last_row["last_seen_at"] or "") or None

            return {
                "station_count": station_count,
                "stations_with_peak": station_count,
                "global_max": global_max,
                "top_peaks": top_peaks,
                "recent_peak_updates": recent_peak_updates,
                "last_observation_at": last_observation_at,
            }
        except Exception as e:
            logger.error(f"[灾害预警] 构建 S-Net 峰值统计失败: {e}")
            return empty

    async def list_major_peak_events(
        self,
        *,
        min_shindo: float = MAJOR_SHINDO_THRESHOLD,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """将达到阈值的测站峰值投影为重大事件时间轴条目。

        阈值默认 4.5（日本震度 5弱）。仅展示时间、测站名与震度。
        """
        try:
            threshold = float(min_shindo)
        except (TypeError, ValueError):
            threshold = self.MAJOR_SHINDO_THRESHOLD

        rows = await self.list_peaks(
            min_shindo=threshold,
            limit=limit,
            order_by="max_shindo_at",
        )
        events: list[dict[str, Any]] = []
        for row in rows:
            station_id = str(row.get("station_id") or "").strip()
            station_name = str(row.get("station_name") or station_id).strip()
            try:
                shindo = float(row.get("max_shindo"))
            except (TypeError, ValueError):
                continue
            peak_at = str(row.get("max_shindo_at") or "").strip()
            label = (
                ScaleConverter.format_measured_intensity_display(shindo)
                or f"{shindo:.1f}"
            )
            description = f"S-Net {station_name} 震度{label} ({shindo:.2f})"
            events.append(
                {
                    "id": f"snet-peak-{station_id}",
                    "event_id": f"snet_peak_{station_id}",
                    "real_event_id": station_id,
                    "unique_id": f"snet_peak:{station_id}",
                    "type": "snet_peak",
                    "source": "snet_msil",
                    "source_id": "snet_msil",
                    "description": description,
                    "place_name": station_name,
                    "latitude": row.get("lat"),
                    "longitude": row.get("lon"),
                    "level": label,
                    "shindo": shindo,
                    "time": peak_at,
                    "is_major": 1,
                    "update_count": 1,
                    "history": [],
                }
            )
        return events

    @staticmethod
    def _to_global_max_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        try:
            shindo_val = float(row.get("max_shindo"))
        except (TypeError, ValueError):
            return None
        return {
            "shindo": shindo_val,
            "shindo_label": ScaleConverter.format_measured_intensity_display(
                shindo_val
            ),
            "station_id": str(row.get("station_id") or ""),
            "station_name": str(row.get("station_name") or row.get("station_id") or ""),
            "at": str(row.get("max_shindo_at") or ""),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
        }

    @staticmethod
    def _to_peak_update_view(row: dict[str, Any]) -> dict[str, Any] | None:
        try:
            shindo_val = float(row.get("max_shindo"))
        except (TypeError, ValueError):
            return None
        return {
            "station_id": str(row.get("station_id") or ""),
            "station_name": str(row.get("station_name") or row.get("station_id") or ""),
            "shindo": shindo_val,
            "shindo_label": ScaleConverter.format_measured_intensity_display(
                shindo_val
            ),
            "at": str(row.get("max_shindo_at") or ""),
        }


__all__ = ["SnetPeakRepository"]
