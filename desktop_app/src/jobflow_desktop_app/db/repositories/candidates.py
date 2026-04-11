from __future__ import annotations

from dataclasses import dataclass

from ..connection import Database


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: int | None
    name: str
    email: str
    base_location: str
    preferred_locations: str
    target_directions: str
    notes: str
    active_resume_path: str
    created_at: str
    updated_at: str
    base_location_struct: str = ""
    preferred_locations_struct: str = ""


@dataclass(frozen=True)
class CandidateSummary:
    candidate_id: int
    name: str
    active_resume_path: str
    profile_count: int
    updated_at: str


class CandidateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def _resume_path_subquery(self) -> str:
        return """
        COALESCE(
          (
            SELECT r.file_path
            FROM resumes r
            WHERE r.candidate_id = c.id AND r.is_active = 1
            ORDER BY r.created_at DESC
            LIMIT 1
          ),
          ''
        )
        """

    def list_summaries(self) -> list[CandidateSummary]:
        query = """
        SELECT
          c.id AS candidate_id,
          c.name AS name,
          {resume_path} AS active_resume_path,
          (
            SELECT COUNT(*)
            FROM search_profiles sp
            WHERE sp.candidate_id = c.id
          ) AS profile_count,
          c.updated_at AS updated_at
        FROM candidates c
        ORDER BY c.updated_at DESC, c.id DESC
        """.format(resume_path=self._resume_path_subquery())
        with self.database.session() as connection:
            rows = connection.execute(query).fetchall()
        return [
            CandidateSummary(
                candidate_id=int(row["candidate_id"]),
                name=str(row["name"] or ""),
                active_resume_path=str(row["active_resume_path"] or ""),
                profile_count=int(row["profile_count"] or 0),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
        ]

    def list_records(self) -> list[CandidateRecord]:
        query = """
        SELECT
          c.id AS candidate_id,
          c.name AS name,
          c.email AS email,
          c.base_location AS base_location,
          c.preferred_locations AS preferred_locations,
          c.base_location_struct AS base_location_struct,
          c.preferred_locations_struct AS preferred_locations_struct,
          c.target_directions AS target_directions,
          c.notes AS notes,
          {resume_path} AS active_resume_path,
          c.created_at AS created_at,
          c.updated_at AS updated_at
        FROM candidates c
        ORDER BY c.updated_at DESC, c.id DESC
        """.format(resume_path=self._resume_path_subquery())
        with self.database.session() as connection:
            rows = connection.execute(query).fetchall()
        return [
            CandidateRecord(
                candidate_id=int(row["candidate_id"]),
                name=str(row["name"] or ""),
                email=str(row["email"] or ""),
                base_location=str(row["base_location"] or ""),
                preferred_locations=str(row["preferred_locations"] or ""),
                target_directions=str(row["target_directions"] or ""),
                notes=str(row["notes"] or ""),
                active_resume_path=str(row["active_resume_path"] or ""),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
                base_location_struct=str(row["base_location_struct"] or ""),
                preferred_locations_struct=str(row["preferred_locations_struct"] or ""),
            )
            for row in rows
        ]

    def get(self, candidate_id: int) -> CandidateRecord | None:
        query = """
        SELECT
          c.id AS candidate_id,
          c.name AS name,
          c.email AS email,
          c.base_location AS base_location,
          c.preferred_locations AS preferred_locations,
          c.base_location_struct AS base_location_struct,
          c.preferred_locations_struct AS preferred_locations_struct,
          c.target_directions AS target_directions,
          c.notes AS notes,
          {resume_path} AS active_resume_path,
          c.created_at AS created_at,
          c.updated_at AS updated_at
        FROM candidates c
        WHERE c.id = ?
        """.format(resume_path=self._resume_path_subquery())
        with self.database.session() as connection:
            row = connection.execute(query, (candidate_id,)).fetchone()
        if row is None:
            return None
        return CandidateRecord(
            candidate_id=int(row["candidate_id"]),
            name=str(row["name"] or ""),
            email=str(row["email"] or ""),
            base_location=str(row["base_location"] or ""),
            preferred_locations=str(row["preferred_locations"] or ""),
            target_directions=str(row["target_directions"] or ""),
            notes=str(row["notes"] or ""),
            active_resume_path=str(row["active_resume_path"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            base_location_struct=str(row["base_location_struct"] or ""),
            preferred_locations_struct=str(row["preferred_locations_struct"] or ""),
        )

    def save(self, record: CandidateRecord) -> int:
        name = record.name.strip()
        if not name:
            raise ValueError("Candidate name is required.")
        resume_path = record.active_resume_path.strip()
        with self.database.session() as connection:
            if record.candidate_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO candidates (
                      name, email, base_location, preferred_locations, base_location_struct, preferred_locations_struct,
                      target_directions, notes, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        name,
                        record.email.strip(),
                        record.base_location.strip(),
                        record.preferred_locations.strip(),
                        record.base_location_struct.strip(),
                        record.preferred_locations_struct.strip(),
                        record.target_directions.strip(),
                        record.notes.strip(),
                    ),
                )
                candidate_id = int(cursor.lastrowid)
            else:
                candidate_id = int(record.candidate_id)
                connection.execute(
                    """
                    UPDATE candidates
                    SET name = ?, email = ?, base_location = ?, preferred_locations = ?,
                        base_location_struct = ?, preferred_locations_struct = ?,
                        target_directions = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        name,
                        record.email.strip(),
                        record.base_location.strip(),
                        record.preferred_locations.strip(),
                        record.base_location_struct.strip(),
                        record.preferred_locations_struct.strip(),
                        record.target_directions.strip(),
                        record.notes.strip(),
                        candidate_id,
                    ),
                )

            if resume_path:
                current_row = connection.execute(
                    """
                    SELECT file_path
                    FROM resumes
                    WHERE candidate_id = ? AND is_active = 1
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (candidate_id,),
                ).fetchone()
                current_resume_path = str(current_row["file_path"] or "") if current_row else ""
                connection.execute(
                    "UPDATE resumes SET is_active = 0 WHERE candidate_id = ?",
                    (candidate_id,),
                )
                if current_resume_path == resume_path:
                    connection.execute(
                        """
                        UPDATE resumes
                        SET is_active = 1
                        WHERE candidate_id = ? AND file_path = ?
                        """,
                        (candidate_id, resume_path),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO resumes (candidate_id, file_path, source_type, raw_text, is_active)
                        VALUES (?, ?, 'file', '', 1)
                        """,
                        (candidate_id, resume_path),
                    )
        return candidate_id

    def delete(self, candidate_id: int) -> None:
        with self.database.session() as connection:
            connection.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))

    def count(self) -> int:
        with self.database.session() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM candidates").fetchone()
        return int(row["total"] or 0)
