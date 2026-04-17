from __future__ import annotations

import unittest

from jobflow_desktop_app.db.repositories.search_runtime import (
    CandidateCompanyRepository,
    CandidateSemanticProfileRepository,
)
from jobflow_desktop_app.search.state.runtime_candidate_state import SearchRuntimeCandidateStateStore

try:
    from ._helpers import create_candidate, make_temp_context
except ImportError:  # pragma: no cover
    from _helpers import create_candidate, make_temp_context  # type: ignore


class RuntimeCandidateStateTests(unittest.TestCase):
    def test_semantic_profile_empty_payload_clears_stored_state(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            candidate_state = SearchRuntimeCandidateStateStore(
                candidate_companies=CandidateCompanyRepository(context.database),
                semantic_profiles=CandidateSemanticProfileRepository(context.database),
            )

            candidate_state.store_semantic_profile(
                candidate_id=candidate_id,
                profile_payload={
                    "source_signature": "sig-1",
                    "summary": "Hydrogen systems and durability validation",
                    "background_keywords": ["hydrogen", "durability"],
                },
            )
            candidate_state.store_semantic_profile(candidate_id=candidate_id, profile_payload={})

            with context.database.session() as connection:
                row = connection.execute(
                    """
                    SELECT source_signature, summary
                    FROM candidate_semantic_profiles
                    WHERE candidate_id = ?
                    """,
                    (candidate_id,),
                ).fetchone()

            self.assertIsNone(row)

    def test_candidate_company_pool_round_trip(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(context)
            candidate_state = SearchRuntimeCandidateStateStore(
                candidate_companies=CandidateCompanyRepository(context.database),
                semantic_profiles=CandidateSemanticProfileRepository(context.database),
            )

            candidate_state.replace_candidate_company_pool(
                candidate_id=candidate_id,
                companies=[
                    {"name": "Acme Hydrogen", "website": "https://acme.example"},
                    {"name": "Beta Systems", "website": "https://beta.example"},
                ],
            )

            self.assertEqual(candidate_state.count_candidate_company_pool(candidate_id), 2)
            self.assertEqual(
                [item.get("name") for item in candidate_state.load_candidate_company_pool(candidate_id=candidate_id)],
                ["Acme Hydrogen", "Beta Systems"],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
