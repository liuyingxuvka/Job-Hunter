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

            semantic_profile = CandidateSemanticProfile(
                summary="Hydrogen diagnostics background",
                company_discovery_primary_anchors=("fuel cell durability", "electrolyzer reliability"),
                company_discovery_secondary_anchors=("industrial gas decarbonization",),
                job_fit_core_terms=("hydrogen systems", "fuel cell diagnostics", "electrochemical durability"),
                job_fit_support_terms=("reliability engineering", "degradation analytics"),
                avoid_business_areas=("pure sales",),
            )

            signals = collect_candidate_search_signals(
                profiles=context.profiles.list_for_candidate(candidate_id),
                semantic_profile=semantic_profile,
            )

            self.assertEqual(signals.semantic.job_fit_core_terms[:2], ["hydrogen systems", "fuel cell diagnostics"])
            self.assertIn("Hydrogen Reliability Engineer", signals.profile.role_names)
            self.assertIn("Hydrogen Reliability Engineer", signals.profile.target_roles)
            self.assertEqual(
                signals.profile.keyword_focus_terms[:2],
                ["hydrogen diagnostics", "fuel cell validation"],
            )
            core_terms = signals.core_discovery_terms()
            self.assertEqual(
                core_terms[:5],
                [
                    "fuel cell durability",
                    "electrolyzer reliability",
                    "hydrogen diagnostics",
                    "fuel cell validation",
                    "Hydrogen Reliability Engineer",
                ],
            )
            self.assertIn("reliability engineering", signals.adjacent_discovery_terms())
            self.assertIn("industrial gas decarbonization", signals.adjacent_discovery_terms())
            self.assertEqual(signals.explore_discovery_terms(), [])

    def test_signal_group_methods_expose_expected_candidate_and_semantic_terms(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Hydrogen profile",
                company_discovery_primary_anchors=["fuel cell diagnostics"],
                company_discovery_secondary_anchors=["reliability engineering"],
                job_fit_core_terms=["hydrogen systems", "electrochemical durability"],
                job_fit_support_terms=["degradation analytics", "systems integration"],
                avoid_business_areas=["pure sales"],
            ),
            profile=ProfileSearchSignals(
                role_names=["Hydrogen Systems Engineer"],
                target_roles=["Hydrogen Systems Engineer"],
            ),
        )

        self.assertIn("fuel cell diagnostics", signals.core_discovery_terms())
        self.assertIn("electrochemical durability", signals.core_discovery_terms())
        self.assertIn("hydrogen systems", signals.core_discovery_terms())
        self.assertIn("degradation analytics", signals.adjacent_discovery_terms())
        self.assertIn("systems integration", signals.adjacent_discovery_terms())
        self.assertEqual(signals.explore_discovery_terms(), [])

    def test_core_discovery_terms_prioritize_business_focus_over_tools(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Localization profile",
                company_discovery_primary_anchors=["software localization", "translation management systems"],
                company_discovery_secondary_anchors=["content operations"],
                job_fit_core_terms=["localization operations", "software localization"],
                job_fit_support_terms=["memoQ", "Smartling", "linguistic QA"],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=["Localization Project Manager"],
                target_roles=["Localization Project Manager"],
            ),
        )

        core_terms = signals.core_discovery_terms()
        self.assertEqual(
            core_terms[:4],
            [
                "software localization",
                "translation management systems",
                "Localization Project Manager",
                "localization operations",
            ],
        )
        self.assertEqual(
            signals.adjacent_discovery_terms()[:3],
            [
                "content operations",
                "Localization Project Manager",
                "memoQ",
            ],
        )
        self.assertIn("linguistic QA", signals.adjacent_discovery_terms())

    def test_core_discovery_terms_prioritize_profile_targets_over_semantic_broad_terms(self) -> None:
        signals = CandidateSearchSignals(
            semantic=SemanticSearchSignals(
                summary="Localization operations and global content tooling profile",
                company_discovery_primary_anchors=["Localization platforms", "translation management systems"],
                company_discovery_secondary_anchors=["content operations"],
                job_fit_core_terms=["global content tooling", "software localization", "localization operations"],
                job_fit_support_terms=["memoQ"],
                avoid_business_areas=[],
            ),
            profile=ProfileSearchSignals(
                role_names=["Localization Project Manager"],
                target_roles=["Localization Project Manager"],
            ),
        )

        core_terms = signals.core_discovery_terms()
        self.assertEqual(
            core_terms[:4],
            [
                "Localization platforms",
                "translation management systems",
                "Localization Project Manager",
                "global content tooling",
            ],
        )
        self.assertIn("global content tooling", core_terms)
        self.assertLess(core_terms.index("Localization platforms"), core_terms.index("global content tooling"))


if __name__ == "__main__":
    unittest.main()
