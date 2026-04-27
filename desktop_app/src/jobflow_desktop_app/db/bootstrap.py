from __future__ import annotations

from pathlib import Path

from .connection import Database


def _table_exists(connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]) == column_name for row in rows)


def _ensure_column(connection, table_name: str, column_name: str, column_sql: str) -> None:
    if _column_exists(connection, table_name, column_name):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def _migrate_candidate_companies_drop_pool_name(connection) -> None:
    if not _column_exists(connection, "candidate_companies", "pool_name"):
        return
    connection.execute("ALTER TABLE candidate_companies RENAME TO candidate_companies_legacy")
    connection.execute(
        """
        CREATE TABLE candidate_companies (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_id INTEGER NOT NULL,
          company_key TEXT NOT NULL,
          company_name TEXT NOT NULL DEFAULT '',
          website TEXT DEFAULT '',
          careers_url TEXT DEFAULT '',
          fit_status TEXT NOT NULL DEFAULT 'pending',
          careers_url_status TEXT NOT NULL DEFAULT 'unknown',
          job_fetch_status TEXT NOT NULL DEFAULT 'pending',
          search_status TEXT NOT NULL DEFAULT 'pending',
          pool_status TEXT NOT NULL DEFAULT 'active',
          user_status TEXT NOT NULL DEFAULT '',
          first_seen_at TEXT NOT NULL DEFAULT '',
          last_seen_at TEXT NOT NULL DEFAULT '',
          last_searched_at TEXT NOT NULL DEFAULT '',
          last_run_id INTEGER,
          company_json TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (candidate_id) REFERENCES candidates(id) ON DELETE CASCADE,
          UNIQUE (candidate_id, company_key)
        )
        """
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO candidate_companies (
          candidate_id,
          company_key,
          company_name,
          website,
          careers_url,
          fit_status,
          careers_url_status,
          job_fetch_status,
          search_status,
          pool_status,
          user_status,
          first_seen_at,
          last_seen_at,
          last_searched_at,
          last_run_id,
          company_json,
          updated_at
        )
        SELECT
          candidate_id,
          company_key,
          company_name,
          website,
          careers_url,
          'pending',
          'unknown',
          'pending',
          'pending',
          'active',
          '',
          COALESCE(NULLIF(updated_at, ''), CURRENT_TIMESTAMP),
          COALESCE(NULLIF(updated_at, ''), CURRENT_TIMESTAMP),
          '',
          NULL,
          company_json,
          updated_at
        FROM candidate_companies_legacy
        WHERE COALESCE(pool_name, 'candidate') = 'candidate'
        ORDER BY updated_at ASC, id ASC
        """
    )
    connection.execute("DROP TABLE candidate_companies_legacy")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_companies_candidate_id ON candidate_companies(candidate_id)"
    )


def initialize_database(database: Database, schema_path: Path) -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    with database.session() as connection:
        # Older local databases can have pre-migration job_review_states rows.
        # Ensure the new indexed columns exist before schema.sql creates indexes.
        if _table_exists(connection, "job_review_states"):
            _ensure_column(connection, "job_review_states", "job_key", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "job_review_states", "status_code", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "job_review_states", "hidden", "INTEGER NOT NULL DEFAULT 0")
        connection.executescript(schema_sql)
        _ensure_column(connection, "candidates", "base_location", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "preferred_locations", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "base_location_struct", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "preferred_locations_struct", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidates", "target_directions", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "role_name_i18n", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_profiles", "keyword_focus", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_runs", "run_dir", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_runs", "current_stage", "TEXT NOT NULL DEFAULT 'queued'")
        _ensure_column(connection, "search_runs", "last_message", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_runs", "last_event", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "search_runs", "updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        _ensure_column(connection, "search_runs", "cancelled", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "search_runs", "config_json", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "job_review_states", "job_key", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "job_review_states", "status_code", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "job_review_states", "hidden", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "candidate_companies", "fit_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(connection, "candidate_companies", "careers_url_status", "TEXT NOT NULL DEFAULT 'unknown'")
        _ensure_column(connection, "candidate_companies", "job_fetch_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(connection, "candidate_companies", "search_status", "TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(connection, "candidate_companies", "pool_status", "TEXT NOT NULL DEFAULT 'active'")
        _ensure_column(connection, "candidate_companies", "user_status", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidate_companies", "first_seen_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidate_companies", "last_seen_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidate_companies", "last_searched_at", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "candidate_companies", "last_run_id", "INTEGER")
        _migrate_candidate_companies_drop_pool_name(connection)
        connection.execute(
            """
            UPDATE job_review_states
            SET job_key = COALESCE(
              NULLIF(job_key, ''),
              (
                SELECT COALESCE(NULLIF(jobs.canonical_url, ''), '')
                FROM jobs
                WHERE jobs.id = job_review_states.job_id
              ),
              ''
            )
            WHERE COALESCE(job_key, '') = ''
            """
        )
