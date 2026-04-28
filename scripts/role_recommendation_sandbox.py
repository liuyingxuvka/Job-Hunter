from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "desktop_app" / "src"
sys.path.insert(0, str(SRC_ROOT))

from jobflow_desktop_app.ai.client import extract_output_text  # noqa: E402
from jobflow_desktop_app.ai.role_recommendations import (  # noqa: E402
    decode_bilingual_description,
    decode_bilingual_role_name,
)
from jobflow_desktop_app.ai.role_recommendations_models import (  # noqa: E402
    CandidateSemanticProfile,
    ResumeReadResult,
)
from jobflow_desktop_app.ai.role_recommendations_parse import parse_role_suggestions  # noqa: E402
from jobflow_desktop_app.ai.role_recommendations_parse import parse_refined_manual_role  # noqa: E402
from jobflow_desktop_app.ai.role_recommendations_prompts import (  # noqa: E402
    MANUAL_ROLE_ENRICH_PROMPT,
    SYSTEM_PROMPT,
    build_manual_role_enrich_prompt,
    build_role_recommendation_prompt,
)
from jobflow_desktop_app.app.pages.target_direction_recommendations import (  # noqa: E402
    build_role_recommendation_mix_plan,
)
from jobflow_desktop_app.db.repositories.candidates import CandidateRecord  # noqa: E402
from jobflow_desktop_app.db.repositories.profiles import SearchProfileRecord  # noqa: E402


DEFAULT_DB_PATH = REPO_ROOT / "desktop_app" / "runtime" / "data" / "jobflow_desktop.db"


def _default_db_path() -> Path:
    env_path = os.environ.get("JOBFLOW_SANDBOX_DB", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Database not found: {resolved}")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _load_candidate(connection: sqlite3.Connection, candidate_id: int) -> CandidateRecord:
    row = connection.execute(
        """
        SELECT
          c.id AS candidate_id,
          c.name AS name,
          c.email AS email,
          c.base_location AS base_location,
          c.preferred_locations AS preferred_locations,
          c.target_directions AS target_directions,
          c.notes AS notes,
          COALESCE(
            (
              SELECT r.file_path
              FROM resumes r
              WHERE r.candidate_id = c.id AND r.is_active = 1
              ORDER BY r.created_at DESC
              LIMIT 1
            ),
            ''
          ) AS active_resume_path,
          c.created_at AS created_at,
          c.updated_at AS updated_at,
          c.base_location_struct AS base_location_struct,
          c.preferred_locations_struct AS preferred_locations_struct
        FROM candidates c
        WHERE c.id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Candidate {candidate_id} not found in sandbox database.")
    return CandidateRecord(**dict(row))


def _load_profiles(connection: sqlite3.Connection, candidate_id: int) -> list[SearchProfileRecord]:
    rows = connection.execute(
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
          is_active
        FROM search_profiles
        WHERE candidate_id = ?
        ORDER BY id
        """,
        (candidate_id,),
    ).fetchall()
    profiles: list[SearchProfileRecord] = []
    for row in rows:
        payload = dict(row)
        payload["is_active"] = bool(payload["is_active"])
        profiles.append(SearchProfileRecord(**payload))
    return profiles


def _filter_profiles_by_id(
    profiles: list[SearchProfileRecord],
    raw_profile_ids: str,
) -> list[SearchProfileRecord]:
    raw_text = str(raw_profile_ids or "").strip()
    if not raw_text:
        return profiles
    selected_ids: set[int] = set()
    for item in raw_text.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            selected_ids.add(int(text))
        except ValueError as exc:
            raise ValueError(f"Invalid profile id in --existing-profile-ids: {text}") from exc
    if not selected_ids:
        return profiles
    return [profile for profile in profiles if int(profile.profile_id or 0) in selected_ids]


def _load_semantic_profile(
    connection: sqlite3.Connection,
    candidate_id: int,
) -> CandidateSemanticProfile | None:
    row = connection.execute(
        """
        SELECT profile_json
        FROM candidate_semantic_profiles
        WHERE candidate_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["profile_json"] or ""))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return CandidateSemanticProfile(
        source_signature=str(payload.get("source_signature") or ""),
        summary=str(payload.get("summary") or ""),
        career_and_education_history=str(payload.get("career_and_education_history") or ""),
        company_discovery_primary_anchors=tuple(payload.get("company_discovery_primary_anchors") or ()),
        company_discovery_secondary_anchors=tuple(payload.get("company_discovery_secondary_anchors") or ()),
        job_fit_core_terms=tuple(payload.get("job_fit_core_terms") or ()),
        job_fit_support_terms=tuple(payload.get("job_fit_support_terms") or ()),
        avoid_business_areas=tuple(payload.get("avoid_business_areas") or ()),
    )


def _existing_role_context(profiles: list[SearchProfileRecord]) -> list[tuple[str, str]]:
    context: list[tuple[str, str]] = []
    for profile in profiles:
        _, name_en = decode_bilingual_role_name(profile.role_name_i18n)
        role_name = name_en or profile.name
        if not str(role_name or "").strip():
            continue
        desc_zh, desc_en = decode_bilingual_description(profile.keyword_focus)
        description_lines = []
        if desc_zh:
            description_lines.append(f"ZH: {desc_zh}")
        if desc_en:
            description_lines.append(f"EN: {desc_en}")
        context.append((str(role_name).strip(), "\n".join(description_lines)))
    return context


def _post_responses_request(payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    request = urllib.request.Request(
        os.environ.get("OPENAI_RESPONSES_API_URL", "https://api.openai.com/v1/responses"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI API request failed: HTTP {exc.code}. {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc.reason}") from exc
    except http.client.RemoteDisconnected as exc:
        raise RuntimeError("OpenAI API request failed: remote end closed connection.") from exc


def _write_report(
    *,
    output_path: Path,
    db_path: Path,
    candidate: CandidateRecord,
    model: str,
    run_outputs: list[dict[str, Any]],
    existing_roles: list[tuple[str, str]],
    prompt_text: str,
    save_prompt: bool,
) -> None:
    output_path.mkdir(parents=True, exist_ok=True)
    prompt_file = ""
    if save_prompt:
        prompt_file = "prompt.txt"
        (output_path / prompt_file).write_text(prompt_text, encoding="utf-8")
    (output_path / "raw_runs.json").write_text(
        json.dumps(run_outputs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Role Recommendation Sandbox",
        "",
        f"- Created: {datetime.now().isoformat(timespec='seconds')}",
        f"- Database: `{db_path}`",
        f"- Candidate: {candidate.name} (id {candidate.candidate_id})",
        f"- Model: `{model}`",
        f"- Existing roles: {len(existing_roles)}",
    ]
    if prompt_file:
        lines.append(f"- Prompt file: `{prompt_file}`")
    lines.extend(
        [
            "",
            "## Existing Roles",
            "",
        ]
    )
    for role_name, _ in existing_roles:
        lines.append(f"- {role_name}")
    lines.extend(
        [
            "",
            "## Run Outputs",
            "",
        ]
    )
    for index, run in enumerate(run_outputs, start=1):
        lines.append(f"### Run {index}")
        suggestions = run.get("suggestions") or []
        if not suggestions:
            lines.append("")
            if run.get("error"):
                lines.append(f"- Error: {run.get('error')}")
            else:
                lines.append("- No parsed roles returned.")
            lines.append("")
            continue
        lines.append("")
        lines.append("| Title | Scope | Market-facing | Not too narrow | Distinct from existing | Distinct in batch | Candidate fit | Notes |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for suggestion in suggestions:
            title = str(suggestion.get("name_en") or suggestion.get("name") or "").replace("|", "\\|")
            scope = str(suggestion.get("scope_profile") or "").replace("|", "\\|")
            lines.append(f"| {title} | {scope} |  |  |  |  |  |  |")
        lines.append("")
    lines.extend(
        [
            "## Manual Review Rubric",
            "",
            "- Market-facing: title looks like a role found on job boards or employer career pages.",
            "- Not too narrow: title is not a thesis/research topic or a stack of acronyms and methods.",
            "- Distinct from existing: not a synonym, seniority variant, acronym swap, or nested rewrite of saved roles.",
            "- Distinct in batch: each returned title is a different hiring lane.",
            "- Candidate fit: still credibly connected to the candidate's demonstrated background and target direction.",
        ]
    )
    (output_path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manual_scopes(raw_scope: str) -> list[str]:
    text = str(raw_scope or "").strip().lower()
    if not text or text == "all":
        return ["core", "adjacent", "exploratory"]
    scopes: list[str] = []
    for item in text.split(","):
        scope = item.strip().lower()
        if scope not in {"core", "adjacent", "exploratory"}:
            raise ValueError(f"Invalid --manual-scope: {scope}")
        if scope not in scopes:
            scopes.append(scope)
    return scopes or ["core", "adjacent", "exploratory"]


def _write_manual_report(
    *,
    output_path: Path,
    db_path: Path,
    candidate: CandidateRecord,
    model: str,
    manual_role_name: str,
    manual_description: str,
    run_outputs: list[dict[str, Any]],
    save_prompt: bool,
) -> None:
    output_path.mkdir(parents=True, exist_ok=True)
    if save_prompt:
        for run in run_outputs:
            scope = str(run.get("requested_scope") or "scope")
            prompt_text = str(run.get("prompt_text") or "")
            (output_path / f"manual_prompt_{scope}.txt").write_text(prompt_text, encoding="utf-8")
    for run in run_outputs:
        run.pop("prompt_text", None)
    (output_path / "raw_runs.json").write_text(
        json.dumps(run_outputs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# Manual Role Enrichment Sandbox",
        "",
        f"- Created: {datetime.now().isoformat(timespec='seconds')}",
        f"- Database: `{db_path}`",
        f"- Candidate: {candidate.name} (id {candidate.candidate_id})",
        f"- Model: `{model}`",
        f"- Manual role name: {manual_role_name}",
        f"- Manual rough notes: {manual_description or 'N/A'}",
        "",
        "## Run Outputs",
        "",
        "| Requested Scope | Returned Scope | Title | Scope Visible | Market-facing | Candidate fit | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in run_outputs:
        requested_scope = str(run.get("requested_scope") or "")
        if run.get("error"):
            lines.append(f"| {requested_scope} |  |  |  |  |  | Error: {run.get('error')} |")
            continue
        suggestion = run.get("suggestion") or {}
        title = str(suggestion.get("name_en") or suggestion.get("name") or "").replace("|", "\\|")
        returned_scope = str(suggestion.get("scope_profile") or "").replace("|", "\\|")
        lines.append(f"| {requested_scope} | {returned_scope} | {title} |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Manual Review Rubric",
            "",
            "- Requested Scope: the user-selected role type supplied to the manual-add flow.",
            "- Returned Scope: the AI must return the same scope_profile.",
            "- Scope Visible: the title and descriptions should make the selected scope plausible, not just echo the label.",
            "- Market-facing: title looks like a real job title.",
            "- Candidate fit: role remains credibly anchored in the candidate background.",
        ]
    )
    (output_path / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run role-recommendation prompt sandbox without writing app data.")
    parser.add_argument("--db", default=str(_default_db_path()), help="Path to a Jobflow SQLite database.")
    parser.add_argument("--candidate-id", type=int, default=2)
    parser.add_argument("--model", default=os.environ.get("JOBFLOW_SANDBOX_MODEL", "gpt-5.4"))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--existing-profile-ids",
        default="",
        help="Comma-separated profile ids to use as the simulated existing-role context.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build the prompt and report context without calling AI.")
    parser.add_argument("--save-prompt", action="store_true", help="Save the full private prompt in the runtime report folder.")
    parser.add_argument(
        "--manual-role-name",
        default="",
        help="Run manual role enrichment sandbox instead of automatic recommendations.",
    )
    parser.add_argument("--manual-description", default="", help="Rough notes for manual role enrichment.")
    parser.add_argument(
        "--manual-scope",
        default="all",
        help="Manual scope to test: core, adjacent, exploratory, comma list, or all.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "runtime" / "role_recommendation_sandbox"),
        help="Directory for sandbox artifacts.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    connection = _connect_readonly(db_path)
    candidate = _load_candidate(connection, int(args.candidate_id))
    profiles = _filter_profiles_by_id(
        _load_profiles(connection, int(args.candidate_id)),
        str(args.existing_profile_ids),
    )
    existing_roles = _existing_role_context(profiles)
    semantic_profile = _load_semantic_profile(connection, int(args.candidate_id))
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.out_dir) / timestamp

    manual_role_name = str(args.manual_role_name or "").strip()
    if manual_role_name:
        run_outputs: list[dict[str, Any]] = []
        for scope in _manual_scopes(str(args.manual_scope)):
            prompt_text = build_manual_role_enrich_prompt(
                candidate,
                role_name=manual_role_name,
                rough_description=str(args.manual_description or ""),
                desired_scope_profile=scope,
                resume_result=ResumeReadResult(text="", source_type="sandbox"),
                semantic_profile=semantic_profile,
            )
            if args.dry_run:
                run_outputs.append(
                    {
                        "requested_scope": scope,
                        "raw_text": "",
                        "suggestion": None,
                        "prompt_text": prompt_text,
                    }
                )
                continue
            payload = {
                "model": str(args.model).strip() or "gpt-5.4",
                "input": [
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": MANUAL_ROLE_ENRICH_PROMPT}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt_text}],
                    },
                ],
            }
            try:
                response_payload = _post_responses_request(payload, timeout=int(args.timeout))
                raw_text = extract_output_text(response_payload)
                suggestion = parse_refined_manual_role(
                    raw_text,
                    fallback_name=manual_role_name,
                    fallback_description=str(args.manual_description or ""),
                )
                run_outputs.append(
                    {
                        "requested_scope": scope,
                        "raw_text": raw_text,
                        "suggestion": None
                        if suggestion is None
                        else {
                            "name": suggestion.name,
                            "name_en": suggestion.name_en,
                            "name_zh": suggestion.name_zh,
                            "scope_profile": suggestion.scope_profile,
                            "description_zh": suggestion.description_zh,
                            "description_en": suggestion.description_en,
                        },
                        "prompt_text": prompt_text,
                    }
                )
            except Exception as exc:
                run_outputs.append(
                    {
                        "requested_scope": scope,
                        "raw_text": "",
                        "suggestion": None,
                        "error": str(exc),
                        "prompt_text": prompt_text,
                    }
                )
        _write_manual_report(
            output_path=output_path,
            db_path=db_path,
            candidate=candidate,
            model=str(args.model),
            manual_role_name=manual_role_name,
            manual_description=str(args.manual_description or ""),
            run_outputs=run_outputs,
            save_prompt=bool(args.save_prompt),
        )
        print(f"Manual sandbox report: {output_path / 'report.md'}")
        for run in run_outputs:
            scope = run.get("requested_scope")
            suggestion = run.get("suggestion") or {}
            if run.get("error"):
                print(f"{scope}: error: {run.get('error')}")
            elif suggestion:
                print(f"{scope}: {suggestion.get('name_en') or suggestion.get('name')} [{suggestion.get('scope_profile')}]")
            else:
                print(f"{scope}: no parsed role")
        return 0

    mix_plan = build_role_recommendation_mix_plan(profiles)
    prompt_text = build_role_recommendation_prompt(
        candidate,
        existing_roles=existing_roles,
        resume_result=ResumeReadResult(text="", source_type="sandbox"),
        semantic_profile=semantic_profile,
        mix_plan=mix_plan,
    )

    run_outputs: list[dict[str, Any]] = []
    if args.dry_run:
        run_outputs.append({"raw_text": "", "suggestions": []})
    else:
        for run_index in range(max(1, int(args.runs))):
            payload = {
                "model": str(args.model).strip() or "gpt-5.4",
                "input": [
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt_text}],
                    },
                ],
            }
            try:
                response_payload = _post_responses_request(payload, timeout=int(args.timeout))
                raw_text = extract_output_text(response_payload)
                suggestions = [
                    {
                        "name": suggestion.name,
                        "name_en": suggestion.name_en,
                        "name_zh": suggestion.name_zh,
                        "scope_profile": suggestion.scope_profile,
                        "description_zh": suggestion.description_zh,
                        "description_en": suggestion.description_en,
                    }
                    for suggestion in parse_role_suggestions(raw_text, max_items=mix_plan.request_total or 3)
                ]
                run_outputs.append(
                    {
                        "run_index": run_index + 1,
                        "raw_text": raw_text,
                        "suggestions": suggestions,
                    }
                )
            except Exception as exc:
                run_outputs.append(
                    {
                        "run_index": run_index + 1,
                        "raw_text": "",
                        "suggestions": [],
                        "error": str(exc),
                    }
                )

    _write_report(
        output_path=output_path,
        db_path=db_path,
        candidate=candidate,
        model=str(args.model),
        run_outputs=run_outputs,
        existing_roles=existing_roles,
        prompt_text=prompt_text,
        save_prompt=bool(args.save_prompt),
    )
    print(f"Sandbox report: {output_path / 'report.md'}")
    for index, run in enumerate(run_outputs, start=1):
        print(f"Run {index}:")
        suggestions = run.get("suggestions") or []
        if not suggestions:
            if run.get("error"):
                print(f"  - error: {run.get('error')}")
            else:
                print("  - no parsed roles")
            continue
        for suggestion in suggestions:
            print(f"  - {suggestion.get('name_en') or suggestion.get('name')} [{suggestion.get('scope_profile')}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
