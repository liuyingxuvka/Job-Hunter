from __future__ import annotations

from pathlib import Path

from .connection import Database


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]) == column_name for row in rows)


def _ensure_column(connection, table_name: str, column_name: str, column_sql: str) -> None:
    if _column_exists(connection, table_name, column_name):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def initialize_database(database: Database, schema_path: Path) -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    with database.session() as connection:
        connection.executescript(schema_sql)
        _ensure_column(connection, "candidates", "base_location", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "preferred_locations", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "base_location_struct", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "preferred_locations_struct", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "target_directions", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "company_focus", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "company_keyword_focus", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "role_name_i18n", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "keyword_focus", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "company_seed_list", "TEXT NOT NULL DEFAULT ''")
