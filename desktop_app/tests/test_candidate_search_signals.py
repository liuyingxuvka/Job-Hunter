from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

try:
    from ._helpers import create_candidate, create_profile, make_temp_context
except ImportError:  # pragma: no cover - unittest discover from tests dir
    from _helpers import create_candidate, create_profile, make_temp_context  # type: ignore

DESKTOP_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = DESKTOP_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.ai.role_recommendations import CandidateSemanticProfile  # noqa: E402
from jobflow_desktop_app.search.orchestration.candidate_search_signals import (  # noqa: E402
    CandidateInputSignals,
    CandidateSearchSignals,
    ProfileSearchSignals,
    SemanticSearchSignals,
    collect_candidate_search_signals,
)


class CandidateSearchSignalsTests(unittest.TestCase):
    def test_collect_candidate_search_signals_dedupes_and_prefers_active_profiles(self) -> None:
        with make_temp_context() as context:
            candidate_id = create_candidate(
                context,
                name="Demo Candidate",
                notes="hydrogen durability\nhydrogen durability\nbattery systems",
            )
            active_profile_id = create_profile(
                context,
                candidate_id,
                name="Hydrogen Reliability Engineer",
                scope_profile="hydrogen_mainline",
                keyword_focus="hydrogen diagnostics\nfuel cell validation",
                is_active=True,
                role_name_i18n="氢能可靠性工程师 | Hydrogen Reliability Engineer",
            )
            inactive_profile_id = create_profile(
                context,
                candidate_id,
                name="Battery Systems Engineer",
                scope_profile="adjacent_mbse",
                keyword_focus="battery analytics",
                is_active=False,
            )
            candidate = context.candidates.get(candidate_id)
            self.assertIsNotNone(candidate)
            context.candidates.save(
                replace(
                    candidate,
                    target_directions="hydrogen systems\nhydrogen systems\nfuel cell diagnostics",
                )
            )

            active_profile = context.profiles.get(active_profile_id)
            inactive_profile = context.profiles.get(inactive_profile_id)
            self.assertIsNotNone(active_profile)
            self.assertIsNotNone(inactive_profile)
            context.profiles.save(
                replace(
                    active_profile,
                    queries=["hydrogen platform companies", "fuel cell diagnostics companies"],
                )
            )
            context.profiles.save(
                replace(
                    inactive_profile,
                    company_focus="battery platforms",
                )
            )

            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen diagnostics background",
                background_keywords=("hydrogen systems", "hydrogen systems"),
                target_direction_keywords=("fuel cell diagnostics",),
                core_business_areas=("electrochemical durability",),
                adjacent_business_areas=("reliability engineering",),
                exploration_business_areas=("industrial technology",),
                strong_capabilities=("degradation analytics",),
                avoid_business_areas=("pure sales",),
            )

            signals = collect_candidate_search_signals(
                candidate=context.candidates.get(candidate_id),
                profiles=context.profiles.list_for_candidate(candidate_id),
                semantic_profile=semantic_profile,
            )

            self.assertEqual(signals.semantic.background_keywords, ["hydrogen systems"])
            self.assertEqual(
                signals.candidate.target_directions,
                ["hydrogen systems", "fuel cell diagnostics"],
            )
            self.assertIn("Hydrogen Reliability Engineer", signals.profile.role_names)
            self.assertIn("hydrogen diagnostics", signals.profile.keyword_focus)
            self.assertIn("hydrogen platform companies", signals.profile.queries)
            self.assertNotIn("battery platforms", signals.profile.company_focus)
            semantic_anchor_terms = signals.semantic_core_anchor_terms()
            self.assertEqual(
                semantic_anchor_terms,
                [
                    "fuel cell diagnostics",
                    "hydrogen systems",
                    "electrochemical durability",
                    "degradation analytics",
                ],
            )
            self.assertIn("industrial automation", signals.business_hint_terms())

    def test_signal_group_methods_expose_expected_candidate_and_semantic_terms(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Hydrogen profile",
                target_direction_keywords=["fuel cell diagnostics"],
                background_keywords=["hydrogen systems"],
                core_business_areas=["electrochemical durability"],
                strong_capabilities=["degradation analytics"],
                adjacent_business_areas=["reliability engineering"],
                exploration_business_areas=["industrial technology"],
                avoid_business_areas=["pure sales"],
            ),
            candidate=CandidateInputSignals(
                target_directions=["hydrogen systems engineer"],
                notes=["diagnostics background"],
            ),
            profile=ProfileSearchSignals(
                role_names=["Hydrogen Systems Engineer"],
                target_roles=["Hydrogen Systems Engineer"],
                keyword_focus=["hydrogen diagnostics"],
                company_focus=["industrial automation"],
                company_keyword_focus=["systems integration"],
                queries=["hydrogen companies"],
            ),
        )

        self.assertIn("fuel cell diagnostics", signals.semantic_core_anchor_terms())
        self.assertIn("electrochemical durability", signals.anchor_source_terms())
        self.assertIn("systems integration", signals.business_hint_terms())
        self.assertIn("hydrogen companies", signals.anchor_source_terms())


if __name__ == "__main__":
    unittest.main()
