# Documentation Index

## Plans

- **`PLAN.md`** — Current implementation plan (LanceDB backend, direct GraphQL API, query-id auto-discovery, rate limiting, phased roadmap)
- **`PLAN-FUTURE.md`** — Deferred post-v0.2 roadmap for discoverability work (link-centric search, ArchiveBox integration, VLM, clustering, knowledge graph)
- `initial/` — Historical, frozen planning snapshots (do not edit; may contradict current plan):
- `initial/PLAN-A.md` — Playwright browser automation + GraphQL interception approach
- `initial/PLAN-B.md` — Direct GraphQL API client (requests) approach
- `initial/COMPARISON.md` — Side-by-side comparison (historical)

## Research

- `research/RESEARCH-claude.md` — Landscape review of Twitter/X export tools (2026)
- `research/RESEARCH-chatgpt.md` — ChatGPT Deep Research: official API pricing, open-source tool survey, authentication flows, automation workflows

## Analysis

- `ANALYSIS-db.md` — Database choice comparison (SQLite, DuckDB, LanceDB, SeekDB), schema design, vector indexing, embedding models, and LanceDB backend notes
- `ANALYSIS-archive-import.md` — Fresh X archive sample inventory, overlap with live GraphQL crawl data, and proposed import precedence rules
- `ANALYSIS-db.py` — Reproducible Task 0 benchmark harness for SQLite, SeekDB, and LanceDB startup/footprint probes
- `ANALYSIS-agents.md` — Survey of AGENTS.md patterns across repos; informs this project's conventions

## Work Tracking

- `IMPLEMENTATION.md` — Active checklist (created when implementation begins)
- `../WORKLOG.md` — Running session log (lives in repo root)

## Release

- `PUBLISH.md` — Release/versioning punch list for version bumps, validation, tagging, and PyPI publication
- `../CHANGELOG.md` — User-facing release history with backfilled tagged versions and release validation notes
