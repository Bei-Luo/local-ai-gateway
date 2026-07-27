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
                    alias TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    upstream_model TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
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
                "SELECT * FROM model_routes WHERE alias = ? COLLATE NOCASE AND enabled = 1",
                (alias,),
            ).fetchone()
        return dict(row) if row else None

    def create_route(self, data: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO model_routes
                    (alias, upstream_model, base_url, api_key, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["alias"],
                    data["upstream_model"],
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
            self.connection.execute(
                """
                UPDATE model_routes
                SET alias = ?, upstream_model = ?, base_url = ?, api_key = ?,
                    enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data["alias"],
                    data["upstream_model"],
                    data["base_url"],
                    api_key,
                    int(data["enabled"]),
                    now,
                    route_id,
                ),
            )
        return self.get_route(route_id)

    def delete_route(self, route_id: int) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM model_routes WHERE id = ?", (route_id,)
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        self.connection.close()
