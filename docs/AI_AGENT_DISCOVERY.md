# AI Agent Discovery

> Product type: local-first Windows job discovery workspace
>  
> Target user: experienced professionals with real domain expertise
>  
> Machine surface: experimental JSON-oriented CLI via `jobflow-agent`
>  
> Privacy boundary: real candidate data, resumes, search outputs, logs, exports, and backups must remain local

Job Hunter is a local-first job discovery workspace for experienced professionals.
It is designed for candidates who already have domain experience and want to identify better-fit companies and roles based on real skills, industry context, and transferable expertise.

This page is intentionally written in English and optimized for search, indexing, and AI/tool discovery.

For a shorter machine-readable index, see [../llms.txt](../llms.txt).

## Relevant Search Phrases

This repository may be relevant if you are looking for:

- AI agent job search tool
- local-first job search workspace
- candidate profile and role recommendation CLI
- Windows desktop job search app with local data
- AI-assisted target-role recommendation workflow
- job search automation foundation for experienced professionals
- Windows desktop app for local-first job discovery
- experimental CLI for candidate and role workflow automation

## What This Repository Can Do

Current capability signals:

- local candidate profile management
- bilingual target-role setup and refinement
- AI-assisted role recommendation flows
- company-first search workflow through the current Python-native discovery engine
- local result review and state tracking
- Windows desktop distribution for non-developer users
- experimental JSON-oriented CLI for headless automation

## Current Machine-Friendly Surface

The current experimental CLI is `jobflow-agent`.

Available commands include:

- `overview`
- `list-candidates`
- `get-candidate --candidate-id <id>`
- `list-profiles --candidate-id <id>`
- `recommend-roles --candidate-id <id>`

## Who This Is For

This project is not aimed at broad mass-market job recommendation.
It is more relevant for:

- experienced professionals with real domain depth
- career search workflows that need local data control
- AI/tool builders exploring job-search automation surfaces
- developer workflows that prefer machine-readable outputs over UI-only flows

## Privacy Boundary

Important boundary:

- real resumes, candidate records, search outputs, SQLite databases, logs, exports, and backups must remain local
- the public repository should be treated as source code, docs, and safe demo/default content only
- any integration should respect the local-data boundary before attempting automation

## Best Next Documents

- [Repository README](../README.md)
- [AI Integration Notes](./AI_INTEGRATION.md)
- [Repository Boundary](./REPOSITORY_BOUNDARY.md)
- [Architecture Overview](./ARCHITECTURE.md)
