from __future__ import annotations

from dataclasses import dataclass, field

from ..connection import Database


@dataclass(frozen=True)
class SearchProfileRecord:
    profile_id: int | None
    candidate_id: int
    name: str
    scope_profile: str
    target_role: str
    location_preference: str
    company_focus: str = ""
    company_keyword_focus: str = ""
    role_name_i18n: str = ""
    keyword_focus: str = ""
    is_active: bool = True
    queries: list[str] = field(default_factory=list)
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
          sp.company_focus AS company_focus,
          sp.company_keyword_focus AS company_keyword_focus,
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
                queries = [
                    str(item["query_text"] or "")
                    for item in connection.execute(
                        """
                        SELECT query_text
                        FROM search_profile_queries
                        WHERE search_profile_id = ?
                        ORDER BY sort_order ASC, id ASC
                        """,
                        (row["profile_id"],),
                    ).fetchall()
                    if str(item["query_text"] or "").strip()
                ]
                profiles.append(
                    SearchProfileRecord(
                        profile_id=int(row["profile_id"]),
                        candidate_id=int(row["candidate_id"]),
                        name=str(row["name"] or ""),
                        scope_profile=str(row["scope_profile"] or ""),
                        target_role=str(row["target_role"] or ""),
                        location_preference=str(row["location_preference"] or ""),
                        company_focus=str(row["company_focus"] or ""),
                        company_keyword_focus=str(row["company_keyword_focus"] or ""),
                        role_name_i18n=str(row["role_name_i18n"] or ""),
                        keyword_focus=str(row["keyword_focus"] or ""),
                        is_active=bool(row["is_active"]),
                        queries=queries,
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
                  company_focus,
                  company_keyword_focus,
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
            query_rows = connection.execute(
                """
                SELECT query_text
                FROM search_profile_queries
                WHERE search_profile_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (profile_id,),
            ).fetchall()
        return SearchProfileRecord(
            profile_id=int(row["profile_id"]),
            candidate_id=int(row["candidate_id"]),
            name=str(row["name"] or ""),
            scope_profile=str(row["scope_profile"] or ""),
            target_role=str(row["target_role"] or ""),
            location_preference=str(row["location_preference"] or ""),
            company_focus=str(row["company_focus"] or ""),
            company_keyword_focus=str(row["company_keyword_focus"] or ""),
            role_name_i18n=str(row["role_name_i18n"] or ""),
            keyword_focus=str(row["keyword_focus"] or ""),
            is_active=bool(row["is_active"]),
            queries=[str(item["query_text"] or "") for item in query_rows if str(item["query_text"] or "").strip()],
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
        scope_profile = self._safe_text(getattr(record, "scope_profile", "")) or "hydrogen_mainline"
        target_role = self._safe_text(getattr(record, "target_role", ""))
        location_preference = self._safe_text(getattr(record, "location_preference", ""))
        company_focus = self._safe_text(getattr(record, "company_focus", ""))
        company_keyword_focus = self._safe_text(getattr(record, "company_keyword_focus", ""))
        role_name_i18n = self._safe_text(getattr(record, "role_name_i18n", ""))
        keyword_focus = self._safe_text(getattr(record, "keyword_focus", ""))
        raw_queries = getattr(record, "queries", [])
        if not isinstance(raw_queries, (list, tuple)):
            raw_queries = []
        queries = [
            self._safe_text(item)
            for item in raw_queries
            if self._safe_text(item)
        ]
        with self.database.session() as connection:
            if record.profile_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO search_profiles (
                      candidate_id, name, scope_profile, target_role, location_preference,
                      company_focus, company_keyword_focus, role_name_i18n, keyword_focus,
                      is_active, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        candidate_id,
                        name,
                        scope_profile,
                        target_role,
                        location_preference,
                        company_focus,
                        company_keyword_focus,
                        role_name_i18n,
                        keyword_focus,
                        1 if record.is_active else 0,
                    ),
                )
                profile_id = int(cursor.lastrowid)
            else:
                profile_id = int(record.profile_id)
                connection.execute(
                    """
                    UPDATE search_profiles
                    SET candidate_id = ?, name = ?, scope_profile = ?, target_role = ?,
                        location_preference = ?, company_focus = ?, company_keyword_focus = ?,
                        role_name_i18n = ?, keyword_focus = ?, company_seed_list = '', is_active = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        candidate_id,
                        name,
                        scope_profile,
                        target_role,
                        location_preference,
                        company_focus,
                        company_keyword_focus,
                        role_name_i18n,
                        keyword_focus,
                        1 if record.is_active else 0,
                        profile_id,
                    ),
                )

            connection.execute(
                "DELETE FROM search_profile_queries WHERE search_profile_id = ?",
                (profile_id,),
            )
            for sort_order, query_text in enumerate(queries, start=1):
                connection.execute(
                    """
                    INSERT INTO search_profile_queries (search_profile_id, query_text, sort_order, is_enabled)
                    VALUES (?, ?, ?, 1)
                    """,
                    (profile_id, query_text, sort_order),
                )
        return profile_id

    def delete(self, profile_id: int) -> None:
        with self.database.session() as connection:
            connection.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))
