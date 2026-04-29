from __future__ import annotations

import json
import sqlite3
import unittest

from jobflow_desktop_app.db.bootstrap import initialize_database
from jobflow_desktop_app.db.repositories.search_runtime import JobAnalysisRepository
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord
from jobflow_desktop_app.db.target_role_cleanup import analysis_has_stale_target_role_binding
from jobflow_desktop_app.search.state.runtime_db_mirror import SearchRuntimeMirror

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - direct discovery fallback
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore


def _role_bound_analysis(profile_id: int, *, role_name: str = "Old Narrow Role") -> dict:
    return {
        "overallScore": 88,
        "matchScore": 88,
        "fitLevelCn": "High fit",
        "recommend": True,
        "targetRoleScore": 84,
        "targetRoleFitLevelCn": "Matched",
        "boundTargetRole": {
            "profileId": profile_id,
            "roleId": f"profile:{profile_id}",
            "displayName": role_name,
            "targetRoleText": role_name,
        },
        "targetRoleScores": [
            {
                "profileId": profile_id,
                "roleId": f"profile:{profile_id}",
                "score": 84,
                "fitLevelCn": "Matched",
                "recommend": True,
                "targetRoleText": role_name,
            }
        ],
    }


def _insert_job(connection, *, url: str = "https://example.com/jobs/1") -> int:
    cursor = connection.execute(
        """
        INSERT INTO jobs (canonical_url, title, company_name, location_text)
        VALUES (?, 'Hydrogen Systems Engineer', 'Acme Hydrogen', 'Berlin')
        """,
        (url,),
    )
    return int(cursor.lastrowid)


def _insert_stale_candidate_job(
    connection,
    *,
    candidate_id: int,
    job_id: int,
    profile_id: int,
    url: str = "https://example.com/jobs/1",
    role_name: str = "Old Narrow Role",
    recommendation_status: str = "pass",
    output_status: str = "pass",
) -> None:
    analysis = _role_bound_analysis(profile_id, role_name=role_name)
    payload = {
        "title": "Hydrogen Systems Engineer",
        "company": "Acme Hydrogen",
        "location": "Berlin",
        "url": url,
        "canonicalUrl": url,
        "analysis": dict(analysis),
    }
    connection.execute(
        """
        INSERT INTO candidate_jobs (
          candidate_id,
          job_id,
          job_key,
          canonical_url,
          source_url,
          title,
          company_name,
          location_text,
          scoring_status,
          recommendation_status,
          output_status,
          interest_level,
          applied_status,
          response_status,
          notes,
          match_score,
          analysis_json,
          job_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scored', ?, ?, 'high', 'applied', 'waiting', 'manual note', 88, ?, ?)
        """,
        (
            int(candidate_id),
            int(job_id),
            url,
            url,
            url,
            "Hydrogen Systems Engineer",
            "Acme Hydrogen",
            "Berlin",
            recommendation_status,
            output_status,
            json.dumps(analysis, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


class TargetRoleCleanupTests(unittest.TestCase):
    def test_stale_binding_detection_uses_profile_style_role_id_when_profile_id_is_missing(self) -> None:
        analysis = {
            "boundTargetRole": {
                "roleId": "profile:91",
                "targetRoleText": "Old Role",
            }
        }

        self.assertTrue(
            analysis_has_stale_target_role_binding(
                analysis,
                frozenset({92}),
            )
        )

    def test_delete_profile_marks_visible_stale_recommendation_historical_and_preserves_manual_fields(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            old_profile_id = create_profile(context, candidate_id, name="Old Narrow Role")
            create_profile(context, candidate_id, name="New Market Role")
            with context.database.session() as connection:
                job_id = _insert_job(connection)
                _insert_stale_candidate_job(
                    connection,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    profile_id=old_profile_id,
                )
                connection.execute(
                    """
                    INSERT INTO job_analyses (job_id, search_profile_id, match_score, analysis_json)
                    VALUES (?, ?, 88, ?)
                    """,
                    (
                        job_id,
                        old_profile_id,
                        json.dumps(_role_bound_analysis(old_profile_id), ensure_ascii=False),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO job_review_states (
                      candidate_id,
                      search_profile_id,
                      job_id,
                      job_key,
                      status_code,
                      interest_level,
                      applied_status,
                      notes
                    )
                    VALUES (?, ?, ?, 'https://example.com/jobs/1', 'applied', 'high', 'applied', 'manual note')
                    """,
                    (candidate_id, old_profile_id, job_id),
                )

            context.profiles.delete(old_profile_id)

            with context.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT
                      analysis_json,
                      job_json,
                      scoring_status,
                      recommendation_status,
                      output_status,
                      match_score,
                      interest_level,
                      applied_status,
                      response_status,
                      notes
                    FROM candidate_jobs
                    WHERE candidate_id = ? AND job_id = ?
                    """,
                    (candidate_id, job_id),
                ).fetchone()
                analyses_total = int(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM job_analyses WHERE search_profile_id = ?",
                        (old_profile_id,),
                    ).fetchone()["total"]
                )
                review_total = int(
                    connection.execute(
                        "SELECT COUNT(*) AS total FROM job_review_states WHERE search_profile_id = ?",
                        (old_profile_id,),
                    ).fetchone()["total"]
                )
                fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            analysis = json.loads(str(row["analysis_json"]))
            payload = json.loads(str(row["job_json"]))
            self.assertEqual(
                analysis["recommendationDisplay"]["currentFitStatus"],
                "needs_rescore",
            )
            self.assertEqual(analysis["recommendationDisplay"]["reason"], "stale_target_role")
            self.assertIn("analysis", payload)
            self.assertEqual(str(row["scoring_status"]), "scored")
            self.assertEqual(str(row["recommendation_status"]), "pass")
            self.assertEqual(str(row["output_status"]), "pass")
            self.assertEqual(int(row["match_score"]), 88)
            self.assertEqual(str(row["interest_level"]), "high")
            self.assertEqual(str(row["applied_status"]), "applied")
            self.assertEqual(str(row["response_status"]), "waiting")
            self.assertEqual(str(row["notes"]), "manual note")
            self.assertEqual(analyses_total, 0)
            self.assertEqual(review_total, 0)
            self.assertEqual(fk_rows, [])

    def test_bootstrap_repairs_fk_off_profile_deletion_leftovers(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            old_profile_id = create_profile(context, candidate_id, name="Old Narrow Role")
            create_profile(context, candidate_id, name="New Market Role")
            with context.database.session() as connection:
                job_id = _insert_job(connection)
                _insert_stale_candidate_job(
                    connection,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    profile_id=old_profile_id,
                )
                connection.execute(
                    """
                    INSERT INTO job_analyses (job_id, search_profile_id, match_score, analysis_json)
                    VALUES (?, ?, 88, ?)
                    """,
                    (
                        job_id,
                        old_profile_id,
                        json.dumps(_role_bound_analysis(old_profile_id), ensure_ascii=False),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO job_review_states (candidate_id, search_profile_id, job_id, job_key, status_code)
                    VALUES (?, ?, ?, 'https://example.com/jobs/1', 'saved')
                    """,
                    (candidate_id, old_profile_id, job_id),
                )

            raw_connection = sqlite3.connect(context.paths.db_path)
            try:
                raw_connection.execute("PRAGMA foreign_keys = OFF")
                raw_connection.execute(
                    "DELETE FROM search_profiles WHERE id = ?",
                    (old_profile_id,),
                )
                raw_connection.commit()
            finally:
                raw_connection.close()

            initialize_database(context.database, context.paths.schema_path)

            with context.database.session() as connection:
                candidate_job = connection.execute(
                    """
                    SELECT analysis_json, scoring_status, recommendation_status, output_status, match_score
                    FROM candidate_jobs
                    WHERE candidate_id = ? AND job_id = ?
                    """,
                    (candidate_id, job_id),
                ).fetchone()
                analyses_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM job_analyses").fetchone()["total"]
                )
                review_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM job_review_states").fetchone()["total"]
                )
                fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            analysis = json.loads(str(candidate_job["analysis_json"]))
            self.assertEqual(
                analysis["recommendationDisplay"]["currentFitStatus"],
                "needs_rescore",
            )
            self.assertEqual(str(candidate_job["scoring_status"]), "scored")
            self.assertEqual(str(candidate_job["recommendation_status"]), "pass")
            self.assertEqual(str(candidate_job["output_status"]), "pass")
            self.assertEqual(int(candidate_job["match_score"]), 88)
            self.assertEqual(analyses_total, 0)
            self.assertEqual(review_total, 0)
            self.assertEqual(fk_rows, [])

    def test_delete_profile_resets_unshown_stale_candidate_job_for_rescore(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            old_profile_id = create_profile(context, candidate_id, name="Old Narrow Role")
            create_profile(context, candidate_id, name="New Market Role")
            with context.database.session() as connection:
                job_id = _insert_job(connection)
                _insert_stale_candidate_job(
                    connection,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    profile_id=old_profile_id,
                    recommendation_status="pass",
                    output_status="reject",
                )

            context.profiles.delete(old_profile_id)

            with context.database.session() as connection:
                candidate_job = connection.execute(
                    """
                    SELECT analysis_json, job_json, scoring_status, recommendation_status, output_status, match_score
                    FROM candidate_jobs
                    WHERE candidate_id = ? AND job_id = ?
                    """,
                    (candidate_id, job_id),
                ).fetchone()

            self.assertEqual(str(candidate_job["analysis_json"]), "")
            self.assertNotIn("analysis", json.loads(str(candidate_job["job_json"])))
            self.assertEqual(str(candidate_job["scoring_status"]), "pending")
            self.assertEqual(str(candidate_job["recommendation_status"]), "pending")
            self.assertEqual(str(candidate_job["output_status"]), "pending")
            self.assertIsNone(candidate_job["match_score"])

    def test_profile_update_marks_visible_bound_recommendation_for_rescore(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            profile_id = create_profile(context, candidate_id, name="Fuel Cell Systems Engineer")
            with context.database.session() as connection:
                job_id = _insert_job(connection)
                _insert_stale_candidate_job(
                    connection,
                    candidate_id=candidate_id,
                    job_id=job_id,
                    profile_id=profile_id,
                    role_name="Fuel Cell Systems Engineer",
                )

            context.profiles.save(
                SearchProfileRecord(
                    profile_id=profile_id,
                    candidate_id=candidate_id,
                    name="Hydrogen Validation Engineer",
                    scope_profile="core",
                    target_role="Hydrogen validation and test roles",
                    location_preference="",
                    is_active=True,
                )
            )

            with context.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT analysis_json, recommendation_status, output_status
                    FROM candidate_jobs
                    WHERE candidate_id = ? AND job_id = ?
                    """,
                    (candidate_id, job_id),
                ).fetchone()

            analysis = json.loads(str(row["analysis_json"]))
            self.assertEqual(
                analysis["recommendationDisplay"]["currentFitStatus"],
                "needs_rescore",
            )
            self.assertEqual(analysis["recommendationDisplay"]["reason"], "target_role_changed")
            self.assertEqual(str(row["recommendation_status"]), "pass")
            self.assertEqual(str(row["output_status"]), "pass")

    def test_job_analysis_repository_skips_missing_profile_id(self) -> None:
        with make_temp_context() as context:
            with context.database.session() as connection:
                job_id = _insert_job(connection)

            JobAnalysisRepository(context.database).upsert_analysis(
                job_id=job_id,
                search_profile_id=9999,
                analysis=_role_bound_analysis(9999),
            )

            with context.database.session() as connection:
                analyses_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM job_analyses").fetchone()["total"]
                )
                fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(analyses_total, 0)
            self.assertEqual(fk_rows, [])

    def test_runtime_pool_upsert_does_not_persist_missing_profile_binding(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            mirror = SearchRuntimeMirror(context.database)
            search_run_id = mirror.create_run(
                candidate_id=candidate_id,
                run_dir=context.paths.runtime_dir / "search_runs" / f"candidate_{candidate_id}",
                status="running",
                current_stage="company_sources",
                started_at="2026-04-28T10:00:00+00:00",
            )
            missing_profile_id = 12345
            stale_job = {
                "title": "Hydrogen Systems Engineer",
                "company": "Acme Hydrogen",
                "location": "Berlin",
                "url": "https://example.com/jobs/stale",
                "canonicalUrl": "https://example.com/jobs/stale",
                "analysis": _role_bound_analysis(missing_profile_id),
            }

            mirror.replace_bucket_jobs(
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                job_bucket="found",
                jobs=[stale_job],
            )

            with context.database.session() as connection:
                candidate_job = connection.execute(
                    """
                    SELECT analysis_json, job_json, scoring_status, recommendation_status, output_status
                    FROM candidate_jobs
                    WHERE candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()
                analyses_total = int(
                    connection.execute("SELECT COUNT(*) AS total FROM job_analyses").fetchone()["total"]
                )
                fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()

            self.assertEqual(str(candidate_job["analysis_json"]), "")
            self.assertNotIn("analysis", json.loads(str(candidate_job["job_json"])))
            self.assertEqual(str(candidate_job["scoring_status"]), "pending")
            self.assertEqual(str(candidate_job["recommendation_status"]), "pending")
            self.assertEqual(str(candidate_job["output_status"]), "pending")
            self.assertEqual(analyses_total, 0)
            self.assertEqual(fk_rows, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
