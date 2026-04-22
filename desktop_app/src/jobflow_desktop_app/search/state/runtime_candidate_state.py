from __future__ import annotations

from typing import Any


class SearchRuntimeCandidateStateStore:
    def __init__(
        self,
        *,
        candidate_companies,
        semantic_profiles,
    ) -> None:
        self.candidate_companies = candidate_companies
        self.semantic_profiles = semantic_profiles

    def store_semantic_profile(
        self,
        *,
        candidate_id: int,
        profile_payload: dict[str, Any] | None,
    ) -> None:
        if not isinstance(profile_payload, dict) or not profile_payload:
            self.semantic_profiles.delete_profile(
                candidate_id=candidate_id,
            )
            return
        self.semantic_profiles.upsert_profile(
            candidate_id=candidate_id,
            payload=profile_payload,
        )

    def count_candidate_company_pool(self, candidate_id: int) -> int:
        return self.candidate_companies.count_candidate_pool(
            candidate_id=candidate_id,
        )

    def load_candidate_company_pool(
        self,
        *,
        candidate_id: int,
    ) -> list[dict[str, Any]]:
        return self.candidate_companies.load_candidate_pool(
            candidate_id=candidate_id,
        )

    def replace_candidate_company_pool(
        self,
        *,
        candidate_id: int,
        companies: list[dict[str, Any]],
    ) -> None:
        self.candidate_companies.replace_candidate_pool(
            candidate_id=candidate_id,
            companies=companies,
        )

__all__ = ["SearchRuntimeCandidateStateStore"]
