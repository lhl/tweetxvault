# WORKLOG

## 2026-03-14

- Rewrote PLAN.md to remove all clone-any-tool framing. Key changes:
  - "Why a Clean Rewrite (Not a Fork)" -> "Why Build From Scratch" — no longer singles out one tool
  - "Prior Art (Loose Reference)" -> "Twitter API Reverse Engineering" — reframed as empirical data about Twitter's undocumented API, not patterns to adopt from a specific tool
  - Added multi-tool reference notes (TweetHoarder, twitter-web-exporter, twitter-likes-export, twitter-advanced-scraper) for query ID approaches instead of treating one as canonical
  - Removed "Adopt TweetHoarder's spirit", "(TweetHoarder Approach)", "ported from bird" and similar language
  - Added framing to Key Decisions: "independent decisions, not inherited from any existing tool"
  - Added rule: reference code is for understanding Twitter's undocumented API, not for copying implementation patterns
- Created `docs/IMPLEMENTATION.md` as a detailed MVP punchlist (including Phase 0 spikes for SeekDB open questions).
- Clarified in PLAN.md that TweetHoarder is a loose prior-art reference (not a reference implementation); updated wording/rationale accordingly.
- Locked in Python tooling decisions in PLAN.md: Typer + Rich (CLI), Pydantic v2 (data models), loguru (logging), uv/ruff/hatchling (project tooling). Reviewed shisad (Click) and TweetHoarder (Typer) for reference; chose Typer for this project.
- Restructured PLAN.md Dependencies section into Runtime / Dev / Optional.
- Assessed open questions: SeekDB perf questions become first implementation spike; articles endpoint is Phase 4, not blocking.
- Reviewed existing docs and `reference/tweethoarder/` patterns (query-id scraping + caching, feature flags, cursor parsing, backoff).
- Rewrote [docs/PLAN.md](docs/PLAN.md) to lock decisions: SeekDB, TweetHoarder-style direct GraphQL API as primary, Playwright as deferred fallback adapter (no Playwright CLI in MVP), and added a clearer MVP scope + remaining open questions.
- Updated [docs/README.md](docs/README.md) to explicitly mark `docs/initial/` as historical/frozen planning snapshots.
- Slimmed [AGENTS.md](AGENTS.md) (and `CLAUDE.md` symlink) back to repo workflow/safety rules and pointed implementation specifics to [docs/PLAN.md](docs/PLAN.md).
- Tightened `.gitignore` to ignore Python caches/venvs and common credential/db artifacts (`*.sqlite`, `*.db`, session JSON).
- Fixed `AGENTS.md` pointer to reference `docs/PLAN.md`/`docs/README.md` instead of a missing `README.md`.
