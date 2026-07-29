import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class RouteStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_routes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_name TEXT NOT NULL DEFAULT '',
                    alias TEXT NOT NULL COLLATE NOCASE,
                    upstream_model TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    health_status TEXT NOT NULL DEFAULT 'unchecked',
                    health_checked_at TEXT,
                    health_latency_ms INTEGER,
                    health_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(model_routes)").fetchall()
            }
            if "note" not in columns:
                self.connection.execute(
                    "ALTER TABLE model_routes ADD COLUMN note TEXT NOT NULL DEFAULT ''"
                )
            if "site_name" not in columns:
                self.connection.execute(
                    "ALTER TABLE model_routes ADD COLUMN site_name TEXT NOT NULL DEFAULT ''"
                )
            unique_alias = any(
                index["unique"]
                and [
                    column["name"]
                    for column in self.connection.execute(
                        f"PRAGMA index_info('{index['name']}')"
                    ).fetchall()
                ]
                == ["alias"]
                for index in self.connection.execute(
                    "PRAGMA index_list(model_routes)"
                ).fetchall()
            )
            if unique_alias:
                self.connection.execute("ALTER TABLE model_routes RENAME TO model_routes_old")
                self.connection.execute(
                    """
                    CREATE TABLE model_routes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_name TEXT NOT NULL DEFAULT '',
                        alias TEXT NOT NULL COLLATE NOCASE,
                        upstream_model TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        base_url TEXT NOT NULL,
                        api_key TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        health_status TEXT NOT NULL DEFAULT 'unchecked',
                        health_checked_at TEXT,
                        health_latency_ms INTEGER,
                        health_error TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self.connection.execute(
                    """
                    INSERT INTO model_routes
                        (id, site_name, alias, upstream_model, note, base_url,
                         api_key, enabled, created_at, updated_at)
                    SELECT id, site_name, alias, upstream_model, note, base_url,
                           api_key, enabled, created_at, updated_at
                    FROM model_routes_old
                    """
                )
                self.connection.execute("DROP TABLE model_routes_old")
            route_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(model_routes)").fetchall()
            }
            route_migrations = {
                "health_status": "TEXT NOT NULL DEFAULT 'unchecked'",
                "health_checked_at": "TEXT",
                "health_latency_ms": "INTEGER",
                "health_error": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in route_migrations.items():
                if column not in route_columns:
                    self.connection.execute(
                        f"ALTER TABLE model_routes ADD COLUMN {column} {definition}"
                    )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    model_alias TEXT NOT NULL,
                    upstream_model TEXT NOT NULL,
                    site_name TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    request_type TEXT NOT NULL DEFAULT '',
                    streamed INTEGER NOT NULL DEFAULT 0,
                    status_code INTEGER NOT NULL,
                    ttft_ms INTEGER,
                    duration_ms INTEGER NOT NULL
                )
                """
            )
            usage_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(usage_records)").fetchall()
            }
            usage_migrations = {
                "site_name": "TEXT NOT NULL DEFAULT ''",
                "request_type": "TEXT NOT NULL DEFAULT ''",
                "streamed": "INTEGER NOT NULL DEFAULT 0",
                "ttft_ms": "INTEGER",
            }
            for column, definition in usage_migrations.items():
                if column not in usage_columns:
                    self.connection.execute(
                        f"ALTER TABLE usage_records ADD COLUMN {column} {definition}"
                    )

    def list_routes(self) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM model_routes ORDER BY alias COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_route(self, route_id: int) -> dict | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM model_routes WHERE id = ?", (route_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_by_alias(self, alias: str) -> dict | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM model_routes
                WHERE alias = ? COLLATE NOCASE AND enabled = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (alias,),
            ).fetchone()
        return dict(row) if row else None

    def create_route(self, data: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            if data["enabled"]:
                self.connection.execute(
                    "UPDATE model_routes SET enabled = 0, updated_at = ? WHERE alias = ? COLLATE NOCASE",
                    (now, data["alias"]),
                )
            cursor = self.connection.execute(
                """
                INSERT INTO model_routes
                    (site_name, alias, upstream_model, note, base_url, api_key, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["site_name"],
                    data["alias"],
                    data["upstream_model"],
                    data["note"],
                    data["base_url"],
                    data["api_key"],
                    int(data["enabled"]),
                    now,
                    now,
                ),
            )
        return self.get_route(cursor.lastrowid)

    def update_route(self, route_id: int, data: dict) -> dict | None:
        current = self.get_route(route_id)
        if not current:
            return None
        api_key = data.get("api_key") or current["api_key"]
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            if data["enabled"]:
                self.connection.execute(
                    """
                    UPDATE model_routes SET enabled = 0, updated_at = ?
                    WHERE alias = ? COLLATE NOCASE AND id != ?
                    """,
                    (now, data["alias"], route_id),
                )
            self.connection.execute(
                """
                UPDATE model_routes
                SET site_name = ?, alias = ?, upstream_model = ?, note = ?, base_url = ?, api_key = ?,
                    enabled = ?, health_status = 'unchecked', health_checked_at = NULL,
                    health_latency_ms = NULL, health_error = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    data["site_name"],
                    data["alias"],
                    data["upstream_model"],
                    data["note"],
                    data["base_url"],
                    api_key,
                    int(data["enabled"]),
                    now,
                    route_id,
                ),
            )
        return self.get_route(route_id)

    def set_health(
        self,
        route_id: int,
        status: str,
        latency_ms: int | None,
        error: str = "",
    ) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE model_routes
                SET health_status = ?, health_checked_at = ?, health_latency_ms = ?,
                    health_error = ?
                WHERE id = ?
                """,
                (status, now, latency_ms, error[:500], route_id),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_route(route_id)

    def delete_route(self, route_id: int) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM model_routes WHERE id = ?", (route_id,)
            )
        return cursor.rowcount > 0

    def set_enabled(self, route_id: int, enabled: bool) -> dict | None:
        current = self.get_route(route_id)
        if not current:
            return None
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            if enabled:
                self.connection.execute(
                    """
                    UPDATE model_routes SET enabled = 0, updated_at = ?
                    WHERE alias = ? COLLATE NOCASE AND id != ?
                    """,
                    (now, current["alias"], route_id),
                )
            cursor = self.connection.execute(
                "UPDATE model_routes SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), now, route_id),
            )
        return self.get_route(route_id)

    def get_setting(self, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM settings WHERE key = ?", (key,))

    def record_usage(
        self,
        model_alias: str,
        upstream_model: str,
        site_name: str,
        path: str,
        request_type: str,
        streamed: bool,
        status_code: int,
        ttft_ms: int | None,
        duration_ms: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO usage_records
                    (created_at, model_alias, upstream_model, site_name, path,
                     request_type, streamed, status_code, ttft_ms, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    model_alias,
                    upstream_model,
                    site_name,
                    path,
                    request_type,
                    int(streamed),
                    status_code,
                    ttft_ms,
                    duration_ms,
                ),
            )
            self.connection.execute(
                """
                DELETE FROM usage_records
                WHERE id NOT IN (
                    SELECT id FROM usage_records ORDER BY id DESC LIMIT 1000
                )
                """
            )

    def list_usage(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM usage_records ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_usage(self) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM usage_records")

    def close(self) -> None:
        self.connection.close()
