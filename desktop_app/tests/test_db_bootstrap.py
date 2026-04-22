from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobflow_desktop_app.db.bootstrap import initialize_database
from jobflow_desktop_app.db.connection import Database


class DatabaseBootstrapTests(unittest.TestCase):
    def test_initialize_database_prepares_legacy_job_review_states_before_schema_indexes(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        schema_path = project_root / "src" / "jobflow_desktop_app" / "db" / "schema.sql"

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobflow_desktop.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("PRAGMA foreign_keys = ON;")
                connection.executescript(
                    """
                    CREATE TABLE candidates (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT NOT NULL,
                      email TEXT DEFAULT '',
                      notes TEXT DEFAULT ''
                    );
                    CREATE TABLE search_profiles (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      candidate_id INTEGER NOT NULL,
                      name TEXT NOT NULL DEFAULT '',
                      scope_profile TEXT NOT NULL DEFAULT '',
                      target_role TEXT NOT NULL DEFAULT '',
                      location_preference TEXT NOT NULL DEFAULT '',
                      is_active INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE TABLE jobs (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      canonical_url TEXT DEFAULT '',
                      title TEXT NOT NULL DEFAULT '',
                      company_name TEXT NOT NULL DEFAULT '',
                      location_text TEXT DEFAULT ''
                    );
                    CREATE TABLE job_review_states (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      candidate_id INTEGER NOT NULL,
                      search_profile_id INTEGER NOT NULL,
                      job_id INTEGER NOT NULL,
                      interest_level TEXT DEFAULT '',
                      applied_status TEXT DEFAULT '',
                      applied_date TEXT DEFAULT '',
                      response_status TEXT DEFAULT '',
                      not_interested INTEGER NOT NULL DEFAULT 0,
                      notes TEXT DEFAULT '',
                      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()

            initialize_database(Database(db_path), schema_path)

            migrated = sqlite3.connect(db_path)
            migrated.row_factory = sqlite3.Row
            try:
                column_names = [
                    str(row["name"])
                    for row in migrated.execute("PRAGMA table_info(job_review_states)").fetchall()
                ]
                indexes = [
                    str(row["name"])
                    for row in migrated.execute("PRAGMA index_list(job_review_states)").fetchall()
                ]
            finally:
                migrated.close()

            self.assertIn("job_key", column_names)
            self.assertIn("status_code", column_names)
            self.assertIn("hidden", column_names)
            self.assertIn("idx_review_states_candidate_job_key", indexes)

    def test_initialize_database_migrates_candidate_companies_away_from_pool_name(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        schema_path = project_root / "src" / "jobflow_desktop_app" / "db" / "schema.sql"

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "jobflow_desktop.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("PRAGMA foreign_keys = ON;")
                connection.executescript(
                    """
                    CREATE TABLE candidates (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT NOT NULL,
                      email TEXT DEFAULT '',
                      notes TEXT DEFAULT ''
                    );
                    INSERT INTO candidates (id, name, email, notes) VALUES (1, 'Demo Candidate', '', '');
                    CREATE TABLE candidate_companies (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      candidate_id INTEGER NOT NULL,
                      pool_name TEXT NOT NULL DEFAULT 'candidate',
                      company_key TEXT NOT NULL,
                      company_name TEXT NOT NULL DEFAULT '',
                      website TEXT DEFAULT '',
                      careers_url TEXT DEFAULT '',
                      company_json TEXT NOT NULL DEFAULT '',
                      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO candidate_companies (
                      candidate_id,
                      pool_name,
                      company_key,
                      company_name,
                      company_json
                    ) VALUES
                      (1, 'candidate', 'acme', 'Acme Hydrogen', '{"name":"Acme Hydrogen"}'),
                      (1, 'selected', 'beta', 'Beta Power', '{"name":"Beta Power"}');
                    """
                )
                connection.commit()
            finally:
                connection.close()

            initialize_database(Database(db_path), schema_path)

            migrated = sqlite3.connect(db_path)
            migrated.row_factory = sqlite3.Row
            try:
                column_names = [
                    str(row["name"])
                    for row in migrated.execute("PRAGMA table_info(candidate_companies)").fetchall()
                ]
                rows = migrated.execute(
                    """
                    SELECT candidate_id, company_key, company_name
                    FROM candidate_companies
                    ORDER BY company_name
                    """
                ).fetchall()
            finally:
                migrated.close()

            self.assertNotIn("pool_name", column_names)
            self.assertEqual(column_names[:3], ["id", "candidate_id", "company_key"])
            self.assertEqual(
                [(int(row["candidate_id"]), str(row["company_key"]), str(row["company_name"])) for row in rows],
                [(1, "acme", "Acme Hydrogen")],
            )

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
