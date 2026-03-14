# tweetxvault — IMPLEMENTATION Punchlist (MVP)

This is the active implementation checklist. Update checkboxes as items complete. Prefer small, reviewable commits.

Hard constraints:
- `docs/initial/` is historical and intentionally frozen (do not edit).
- No credentials (cookies/tokens/session files) in git, logs, or test fixtures.
- MVP is direct GraphQL API + query-id auto-discovery. No Playwright CLI in MVP (adapter boundary only).

Definition of done for a “done” item: passes `uv run ruff format`, `uv run ruff check`, and `uv run pytest` (or note why not).

## Phase 0: Spikes (Resolve Current Open Questions)

- [ ] SeekDB install/startup spike (cron realism)
  - Measure cold-start time and RSS for: open DB, create schema, insert 1k rows, query 10 rows.
  - Decide where the embedded DB lives (XDG data dir) and the on-disk file naming.
  - Record results and decisions in `WORKLOG.md`.
- [ ] Raw JSON storage spike (SeekDB limits/perf)
  - Try storing realistic-sized JSON blobs (page capture and tweet-level blocks).
  - Decide: store raw JSON directly in SeekDB vs store gzipped JSON files on disk with `raw_json_path` + hash in DB.
  - Record decision and update `docs/PLAN.md` if it changes schema.
- [ ] SeekDB API choice spike (SQL tables vs Collection API)
  - Pick one for Phase 1 and document why (schema ergonomics, upserts, query support, hybrid search path).
  - Record decision in `WORKLOG.md` and update `docs/PLAN.md` minimal schema section accordingly.

## Phase 1: Repo + Tooling Bootstrap

- [ ] Add `pyproject.toml` (hatchling backend) with:
  - Project metadata (`name=tweetxvault`, `requires-python>=3.12`).
  - Runtime deps: `httpx`, `pyseekdb`, `pydantic>=2`, `typer`, `rich`, `loguru`.
  - Dev deps: `ruff`, `pytest`, `pytest-asyncio` (and `mypy` optional).
  - Console entrypoint: `tweetxvault = tweetxvault.cli:app`.
- [ ] Add ruff configuration (format + lint) in `pyproject.toml` (or `ruff.toml`).
- [ ] Add pytest configuration (asyncio mode, test discovery) in `pyproject.toml`.
- [ ] Create package skeleton: `tweetxvault/__init__.py`, `tweetxvault/cli.py`, `tests/`.
- [ ] Add a minimal CI-smoke script section (documented command list is enough for now).

## Phase 2: Paths + Config

- [ ] Implement `tweetxvault/config.py`
  - XDG dirs: config/data/cache (support `XDG_*_HOME` overrides).
  - Central constants: base URLs, user agent, bearer token location, cache filenames.
- [ ] Define config model(s) (Pydantic v2) for:
  - Auth (optional cookie overrides).
  - Sync defaults (page size, backoff params).
  - Optional user id override for Likes if `twid` cookie parse fails.
- [ ] Implement config loading:
  - Read `config.toml` from XDG config dir.
  - Env var overrides (prefix `TWEETXVAULT_...`).
  - Never print secrets in logs/errors.

## Phase 3: Auth (Firefox First)

- [ ] Implement `tweetxvault/auth/cookies.py` resolution chain:
  - Env vars -> config file -> Firefox extraction.
  - Return a cookies dict and derived values (ct0, auth_token, twid, user_id if present).
- [ ] Implement `tweetxvault/auth/firefox.py`
  - Discover default profile from `profiles.ini` (and allow explicit path override).
  - Copy `cookies.sqlite` to a temp file; open read-only; query `moz_cookies` for `x.com` / `twitter.com`.
  - Extract `auth_token`, `ct0`, `twid` (and keep other cookies for the jar).
  - Parse `twid` (`u%3D123...`) into numeric user id when possible.
- [ ] Add unit tests for cookie extraction logic using a synthetic sqlite DB fixture (no real cookies).

## Phase 4: Query IDs (Auto-Discovery + Cache + Fallback)

- [ ] Implement `tweetxvault/query_ids/constants.py`
  - Discovery pages list.
  - Bundle URL regex.
  - Target operations list for Phase 1 (Bookmarks, Likes) plus reserved (BookmarkFolderTimeline, TweetDetail, UserArticlesTweets).
  - Fallback query IDs (document provenance as “prior art baseline”, not correctness guarantee).
- [ ] Implement `tweetxvault/query_ids/store.py`
  - Cache JSON file: `fetched_at`, `ttl_seconds`, `ids`.
  - Freshness check and get-with-fallback behavior.
- [ ] Implement `tweetxvault/query_ids/scraper.py`
  - Extract bundle URLs from HTML.
  - Extract (operationName, queryId) pairs from JS bundles with multiple regex strategies.
  - Refresh flow: fetch discovery page(s) -> bundles -> update cache.
- [ ] Add unit tests for:
  - Bundle URL extraction.
  - Operation/queryId extraction from synthetic JS snippets matching each regex pattern.
  - Cache TTL freshness logic.

## Phase 5: HTTP Client + GraphQL Timelines

- [ ] Implement `tweetxvault/client/base.py`
  - Build an `httpx.AsyncClient` with cookie jar + required headers.
  - Error classification helpers (429 vs auth vs stale query id vs transient).
  - Backoff/cooldown policy (configurable).
- [ ] Implement `tweetxvault/client/features.py`
  - Per-operation feature flag builders for Bookmarks and Likes.
  - Source the initial flag set from a known working request (browser capture or prior art), then keep it minimal.
- [ ] Implement `tweetxvault/client/timelines.py`
  - URL builders for Bookmarks and Likes.
  - Fetch-page functions with:
    - Retry/backoff on 429.
    - Single refresh-once behavior on 404 (stale query id), then retry.
    - Fail-fast on 401/403 with actionable error.
  - Response parsing:
    - Extract tweet entries (`tweet_results.result`) and `sortIndex`.
    - Extract bottom cursor.
- [ ] Add unit tests for:
  - Cursor extraction for both Bookmarks and Likes shapes (use minimal JSON fixtures).
  - Backoff logic (use `httpx.MockTransport` to simulate 429/404/200 sequences).

## Phase 6: Storage (SeekDB)

- [ ] Implement `tweetxvault/storage/seekdb.py`
  - Open embedded DB in XDG data dir.
  - Create minimal schema/collections:
    - `raw_captures` append-only.
    - `tweets` upsert by `tweet_id`.
    - `collections` membership upsert.
    - `sync_state` checkpointing.
  - Provide methods used by the sync loop:
    - append raw capture
    - upsert tweet
    - upsert membership
    - get/set checkpoint
    - reset checkpoint for `--full`
- [ ] Decide and implement raw JSON persistence:
  - Option A: store JSON blobs directly.
  - Option B: store gzipped files + DB pointer/hash.
- [ ] Add storage unit tests using a temp data dir (no network, no real embeddings).

## Phase 7: Sync Orchestration

- [ ] Implement `tweetxvault/sync.py`
  - `sync_bookmarks(full: bool, limit: int | None)` and `sync_likes(full: bool, limit: int | None)`.
  - Incremental by default using `sync_state.cursor`.
  - Persist checkpoint after each page.
  - Idempotent upserts (re-running should not duplicate memberships).
  - Minimal progress output (counts and current page) via Rich.
- [ ] Add unit tests that run sync against mocked HTTP responses and verify:
  - raw_captures appended per page
  - tweets upserted
  - collections membership created
  - cursor checkpoint advances/resumes

## Phase 8: CLI (Sane Commands)

- [ ] Implement `tweetxvault/cli.py` (Typer) with commands:
  - `tweetxvault sync bookmarks [--full] [--limit N]`
  - `tweetxvault sync likes [--full] [--limit N]`
  - `tweetxvault sync all [--full] [--limit N]`
  - `tweetxvault auth check`
  - `tweetxvault auth refresh-ids`
- [ ] Ensure CLI exits non-zero on auth/query-id failures with actionable messages (no secrets).

## Phase 9: Minimal Export (Optional Early Value)

- [ ] Implement `tweetxvault/export/json_export.py`
  - Export by collection type (likes/bookmarks/all) to a JSON file.
  - Include enough metadata to rehydrate (tweet_id, text, author, created_at, membership info, raw_json or raw_json_path).
- [ ] Add `tweetxvault export json --collection <...> --out <path>`.

## Phase 10: Polish + Guardrails

- [ ] Add a “no Playwright in MVP” guardrail:
  - Keep adapter interface (`Fetcher`) but do not ship a Playwright implementation yet.
- [ ] Ensure logs never include cookie values (audit any exception printing).
- [ ] Update `WORKLOG.md` as milestones land (especially spike decisions).
- [ ] Keep `docs/PLAN.md` in sync if any “locked” decision changes.

