from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from ..bootstrap import bootstrap_application
from ..db.repositories.profiles import SearchProfileRecord
from ..ai.role_recommendations import (
    OpenAIRoleRecommendationService,
    RoleRecommendationError,
    decode_bilingual_description,
    decode_bilingual_role_name,
)


EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_FAILURE = 4


class CommandError(Exception):
    def __init__(self, code: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised by argparse internals
        raise CommandError("invalid_arguments", message, EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        context = bootstrap_application()
        payload = dispatch(args, context)
    except CommandError as exc:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                },
            },
            stream=sys.stderr,
        )
        return exc.exit_code
    except RoleRecommendationError as exc:
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "recommendation_failed",
                    "message": str(exc),
                },
            },
            stream=sys.stderr,
        )
        return EXIT_FAILURE
    except Exception as exc:  # pragma: no cover - defensive boundary for CLI usage
        _write_json(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": f"{exc.__class__.__name__}: {exc}",
                },
            },
            stream=sys.stderr,
        )
        return EXIT_FAILURE

    _write_json(payload, stream=sys.stdout)
    return EXIT_OK


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="jobflow-agent",
        description="Headless CLI for Jobflow desktop app data and AI helpers.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("overview", help="Show summary counts and runtime paths.")
    subparsers.add_parser("list-candidates", help="List candidates as JSON.")

    get_candidate = subparsers.add_parser("get-candidate", help="Get one candidate by ID.")
    get_candidate.add_argument("--candidate-id", type=int, required=True)

    list_profiles = subparsers.add_parser("list-profiles", help="List profiles for one candidate.")
    list_profiles.add_argument("--candidate-id", type=int, required=True)

    recommend_roles = subparsers.add_parser("recommend-roles", help="Recommend roles for one candidate.")
    recommend_roles.add_argument("--candidate-id", type=int, required=True)

    return parser


def dispatch(args: argparse.Namespace, context: Any) -> dict[str, Any]:
    command = str(args.command)
    if command == "overview":
        return handle_overview(context)
    if command == "list-candidates":
        return handle_list_candidates(context)
    if command == "get-candidate":
        return handle_get_candidate(context, candidate_id=args.candidate_id)
    if command == "list-profiles":
        return handle_list_profiles(context, candidate_id=args.candidate_id)
    if command == "recommend-roles":
        return handle_recommend_roles(context, candidate_id=args.candidate_id)
    raise CommandError("invalid_arguments", f"Unknown command: {command}", EXIT_USAGE)


def handle_overview(context: Any) -> dict[str, Any]:
    stats = context.overview.load_stats()
    return {
        "ok": True,
        "command": "overview",
        "data": {
            "stats": {
                "candidate_count": stats.candidate_count,
                "profile_count": stats.profile_count,
                "job_count": stats.job_count,
                "run_count": stats.run_count,
            },
            "paths": {
                "project_root": str(context.paths.project_root),
                "runtime_dir": str(context.paths.runtime_dir),
                "data_dir": str(context.paths.data_dir),
                "db_path": str(context.paths.db_path),
                "exports_dir": str(context.paths.exports_dir),
                "logs_dir": str(context.paths.logs_dir),
            },
        },
    }


def handle_list_candidates(context: Any) -> dict[str, Any]:
    candidates = [candidate_summary_to_payload(item) for item in context.candidates.list_summaries()]
    return {
        "ok": True,
        "command": "list-candidates",
        "data": {
            "count": len(candidates),
            "candidates": candidates,
        },
    }


def handle_get_candidate(context: Any, candidate_id: int) -> dict[str, Any]:
    candidate = get_candidate_or_raise(context, candidate_id)
    profiles = context.profiles.list_for_candidate(candidate_id)
    return {
        "ok": True,
        "command": "get-candidate",
        "data": {
            "candidate": candidate_to_payload(candidate, profile_count=len(profiles)),
            "profiles": [profile_to_payload(profile) for profile in profiles],
            "profile_count": len(profiles),
        },
    }


def handle_list_profiles(context: Any, candidate_id: int) -> dict[str, Any]:
    get_candidate_or_raise(context, candidate_id)
    profiles = context.profiles.list_for_candidate(candidate_id)
    return {
        "ok": True,
        "command": "list-profiles",
        "data": {
            "candidate_id": candidate_id,
            "count": len(profiles),
            "profiles": [profile_to_payload(profile) for profile in profiles],
        },
    }


def handle_recommend_roles(context: Any, candidate_id: int) -> dict[str, Any]:
    candidate = get_candidate_or_raise(context, candidate_id)
    profiles = context.profiles.list_for_candidate(candidate_id)
    settings = context.settings.get_effective_openai_settings()
    if not settings.api_key.strip():
        raise CommandError(
            "missing_openai_api_key",
            "OpenAI API key is required for recommend-roles.",
            EXIT_FAILURE,
        )

    existing_roles = [
        (profile_display_name(profile), profile_description(profile))
        for profile in profiles
    ]
    service = OpenAIRoleRecommendationService()
    suggestions = service.recommend_roles(
        candidate=candidate,
        settings=settings,
        api_base_url=context.settings.get_openai_base_url(),
        existing_roles=existing_roles,
    )
    return {
        "ok": True,
        "command": "recommend-roles",
        "data": {
            "candidate_id": candidate_id,
            "model": settings.model.strip() or "gpt-5",
            "count": len(suggestions),
            "roles": [asdict(item) for item in suggestions],
        },
    }


def get_candidate_or_raise(context: Any, candidate_id: int):
    if candidate_id <= 0:
        raise CommandError("invalid_arguments", "candidate-id must be a positive integer.", EXIT_USAGE)
    candidate = context.candidates.get(candidate_id)
    if candidate is None:
        raise CommandError("candidate_not_found", f"Candidate {candidate_id} not found.", EXIT_NOT_FOUND)
    return candidate


def candidate_summary_to_payload(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "active_resume_path": candidate.active_resume_path,
        "profile_count": candidate.profile_count,
        "updated_at": candidate.updated_at,
    }


def candidate_to_payload(candidate: Any, profile_count: int = 0) -> dict[str, Any]:
    payload = {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "email": candidate.email,
        "base_location": candidate.base_location,
        "preferred_locations": candidate.preferred_locations,
        "target_directions": candidate.target_directions,
        "notes": candidate.notes,
        "active_resume_path": candidate.active_resume_path,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "base_location_struct": candidate.base_location_struct,
        "preferred_locations_struct": candidate.preferred_locations_struct,
    }
    payload["profile_count"] = profile_count
    return payload


def profile_to_payload(profile: SearchProfileRecord) -> dict[str, Any]:
    role_name_zh, role_name_en = decode_bilingual_role_name(profile.role_name_i18n, fallback_name=profile.name)
    description_zh, description_en = decode_bilingual_description(profile.keyword_focus)
    return {
        "profile_id": profile.profile_id,
        "candidate_id": profile.candidate_id,
        "name": profile.name,
        "scope_profile": profile.scope_profile,
        "target_role": profile.target_role,
        "location_preference": profile.location_preference,
        "company_focus": profile.company_focus,
        "company_keyword_focus": profile.company_keyword_focus,
        "role_name_i18n": profile.role_name_i18n,
        "keyword_focus": profile.keyword_focus,
        "role_name_zh": role_name_zh,
        "role_name_en": role_name_en,
        "description_zh": description_zh,
        "description_en": description_en,
        "is_active": profile.is_active,
        "queries": profile.queries,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def profile_display_name(profile: SearchProfileRecord) -> str:
    role_name_zh, role_name_en = decode_bilingual_role_name(profile.role_name_i18n, fallback_name=profile.name)
    return role_name_en or role_name_zh or profile.name


def profile_description(profile: SearchProfileRecord) -> str:
    description_zh, description_en = decode_bilingual_description(profile.keyword_focus)
    return description_en or description_zh


def _write_json(payload: dict[str, Any], *, stream: Any) -> None:
    json.dump(payload, stream, ensure_ascii=False, indent=2)
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
