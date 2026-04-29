from __future__ import annotations

from dataclasses import dataclass

from ..connection import Database


@dataclass(frozen=True)
class SearchProfileRecord:
    profile_id: int | None
    candidate_id: int
    name: str
    scope_profile: str
    target_role: str
    location_preference: str
    role_name_i18n: str = ""
    keyword_focus: str = ""
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


class SearchProfileRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _safe_text(value: object) -> str:
        return str(value or "").strip()

    def list_for_candidate(self, candidate_id: int) -> list[SearchProfileRecord]:
        query = """
        SELECT
          sp.id AS profile_id,
          sp.candidate_id AS candidate_id,
          sp.name AS name,
          sp.scope_profile AS scope_profile,
          sp.target_role AS target_role,
          sp.location_preference AS location_preference,
          sp.role_name_i18n AS role_name_i18n,
          sp.keyword_focus AS keyword_focus,
          sp.is_active AS is_active,
          sp.created_at AS created_at,
          sp.updated_at AS updated_at
        FROM search_profiles sp
        WHERE sp.candidate_id = ?
        ORDER BY sp.updated_at DESC, sp.id DESC
        """
        with self.database.session() as connection:
            rows = connection.execute(query, (candidate_id,)).fetchall()
            profiles = []
            for row in rows:
                profiles.append(
                    SearchProfileRecord(
                        profile_id=int(row["profile_id"]),
                        candidate_id=int(row["candidate_id"]),
                        name=str(row["name"] or ""),
                        scope_profile=str(row["scope_profile"] or ""),
                        target_role=str(row["target_role"] or ""),
                        location_preference=str(row["location_preference"] or ""),
                        role_name_i18n=str(row["role_name_i18n"] or ""),
                        keyword_focus=str(row["keyword_focus"] or ""),
                        is_active=bool(row["is_active"]),
                        created_at=str(row["created_at"] or ""),
                        updated_at=str(row["updated_at"] or ""),
                    )
                )
        return profiles

    def get(self, profile_id: int) -> SearchProfileRecord | None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT
                  id AS profile_id,
                  candidate_id,
                  name,
                  scope_profile,
                  target_role,
                  location_preference,
                  role_name_i18n,
                  keyword_focus,
                  is_active,
                  created_at,
                  updated_at
                FROM search_profiles
                WHERE id = ?
                """,
                (profile_id,),
            ).fetchone()
            if row is None:
                return None
        return SearchProfileRecord(
            profile_id=int(row["profile_id"]),
            candidate_id=int(row["candidate_id"]),
            name=str(row["name"] or ""),
            scope_profile=str(row["scope_profile"] or ""),
            target_role=str(row["target_role"] or ""),
            location_preference=str(row["location_preference"] or ""),
            role_name_i18n=str(row["role_name_i18n"] or ""),
            keyword_focus=str(row["keyword_focus"] or ""),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def save(self, record: SearchProfileRecord) -> int:
        candidate_id = int(getattr(record, "candidate_id", 0) or 0)
        name = self._safe_text(getattr(record, "name", ""))
        if candidate_id <= 0:
            raise ValueError("Candidate must be selected before saving a profile.")
        if not name:
            raise ValueError("Profile name is required.")
        scope_profile = self._safe_text(getattr(record, "scope_profile", ""))
        target_role = self._safe_text(getattr(record, "target_role", ""))
        location_preference = self._safe_text(getattr(record, "location_preference", ""))
        role_name_i18n = self._safe_text(getattr(record, "role_name_i18n", ""))
        keyword_focus = self._safe_text(getattr(record, "keyword_focus", ""))
        with self.database.session() as connection:
            if record.profile_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO search_profiles (
                      candidate_id, name, scope_profile, target_role, location_preference,
                      role_name_i18n, keyword_focus,
                      is_active, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        candidate_id,
                        name,
                        scope_profile,
                        target_role,
                        location_preference,
                        role_name_i18n,
                        keyword_focus,
                        1 if record.is_active else 0,
                    ),
                )
                profile_id = int(cursor.lastrowid)
            else:
                profile_id = int(record.profile_id)
                existing = connection.execute(
                    """
                    SELECT
                      name,
                      scope_profile,
                      target_role,
                      location_preference,
                      role_name_i18n,
                      keyword_focus,
                      is_active
                    FROM search_profiles
                    WHERE id = ?
                    """,
                    (profile_id,),
                ).fetchone()
                changed = False
                if existing is not None:
                    old_values = (
                        self._safe_text(existing["name"]),
                        self._safe_text(existing["scope_profile"]),
                        self._safe_text(existing["target_role"]),
                        self._safe_text(existing["location_preference"]),
                        self._safe_text(existing["role_name_i18n"]),
                        self._safe_text(existing["keyword_focus"]),
                        bool(existing["is_active"]),
                    )
                    new_values = (
                        name,
                        scope_profile,
                        target_role,
                        location_preference,
                        role_name_i18n,
                        keyword_focus,
                        bool(record.is_active),
                    )
                    changed = old_values != new_values
                connection.execute(
                    """
                    UPDATE search_profiles
                    SET candidate_id = ?, name = ?, scope_profile = ?, target_role = ?,
                        location_preference = ?, role_name_i18n = ?, keyword_focus = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        candidate_id,
                        name,
                        scope_profile,
                        target_role,
                        location_preference,
                        role_name_i18n,
                        keyword_focus,
                        1 if record.is_active else 0,
                        profile_id,
                    ),
                )
                if changed:
                    from ..target_role_cleanup import mark_candidate_job_target_role_changed

                    mark_candidate_job_target_role_changed(
                        connection,
                        candidate_id=candidate_id,
                        profile_id=profile_id,
                    )
        return profile_id

    def delete(self, profile_id: int) -> None:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT candidate_id FROM search_profiles WHERE id = ?",
                (int(profile_id),),
            ).fetchone()
            connection.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))
            if row is not None:
                from ..target_role_cleanup import cleanup_stale_target_role_references

                cleanup_stale_target_role_references(
                    connection,
                    candidate_id=int(row["candidate_id"]),
                )
