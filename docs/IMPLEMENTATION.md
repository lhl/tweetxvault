# tweetxvault — MVP Implementation Punchlist

This is the active implementation checklist. Update checkboxes as items complete. Prefer small, reviewable commits.

Hard constraints:
- `docs/initial/` is historical and intentionally frozen (do not edit).
- No credentials (cookies/tokens/session files) in git, logs, or test fixtures.
- MVP is direct GraphQL API + query-id auto-discovery. No Playwright in MVP.
- Sync loop calls the GraphQL client directly — no premature `Fetcher` protocol abstraction.
- All architectural decisions are in `docs/PLAN.md`. If something isn't specified there, check before guessing.

Definition of done: passes `uv run ruff format --check`, `uv run ruff check`, and `uv run pytest`.

---

## Task 0: SeekDB Spikes

Resolve the open questions from PLAN.md before building the storage layer. Timebox each spike to ~2 hours.

- [x] **Startup/footprint spike**
  - Measure cold-start time and RSS for: open DB, create schema, insert 1k rows, query 10 rows.
  - Decide on-disk location (XDG data dir) and file naming.
  - **Exit criteria**: if cold-start > 3s or RSS > 200MB for an empty DB, evaluate alternatives (SQLite fallback) and flag to the lead.
  - Record results in `WORKLOG.md`.
- [x] **Raw JSON storage spike**
  - Try storing realistic-sized JSON blobs (a full page capture ~50-200KB, and individual tweet blocks ~2-10KB).
  - **Exit criteria**: if insert/query perf is unacceptable or SeekDB rejects large TEXT fields, switch to gzipped JSON files on disk with `raw_json_path` + hash in DB. Update PLAN.md schema if changed.
  - Record decision in `WORKLOG.md`.
- [x] **API surface spike** (SQL tables vs Collection API)
  - Determine which SeekDB API to use for Phase 1: SQL-style tables or the Collection/document API.
  - **Exit criteria**: pick whichever supports upsert-by-key and basic queries without friction. Document choice in `WORKLOG.md` and update PLAN.md schema section.
  - Result: embedded SeekDB failed initialization during the spike, so the working MVP uses SQLite while preserving the planned schema/transaction semantics.

## Task 1: Project Bootstrap

- [x] Add `pyproject.toml` (hatchling backend) with:
  - Project metadata (`name=tweetxvault`, `requires-python>=3.12`).
  - Runtime deps: `httpx`, `pyseekdb`, `pydantic>=2`, `typer`, `rich`, `loguru`.
  - Dev deps: `ruff`, `pytest`, `pytest-asyncio` (and `mypy` optional).
  - Console entrypoint: `tweetxvault = tweetxvault.cli:app`.
- [x] Add ruff configuration (format + lint) in `pyproject.toml`.
- [x] Add pytest configuration (asyncio mode, test discovery) in `pyproject.toml`.
- [x] Create package skeleton: `tweetxvault/__init__.py`, `tweetxvault/cli.py` (stub), `tests/`.
- [x] Verify: `uv sync && uv run tweetxvault --help` works.

## Task 2: Config + Auth

Config and auth are tightly coupled — build them together.

- [x] Implement `tweetxvault/config.py`
  - XDG dirs: config (`~/.config/tweetxvault/`), data (`~/.local/share/tweetxvault/`), cache (`~/.cache/tweetxvault/`). Support `XDG_*_HOME` overrides. Auto-create on first access.
  - Central constants: API base URL (`https://x.com/i/api/graphql`), bearer token (see PLAN.md Auth section), user agent string, cache filenames.
- [x] Define Pydantic v2 config model(s):
  - Auth: optional `auth_token`, `ct0`, `user_id` overrides.
  - Sync: `page_delay` (default 2s), `max_retries` (default 3), `backoff_base` (default 2s), `cooldown_threshold` (default 3), `cooldown_duration` (default 300s).
- [x] Implement config loading: read `config.toml` from XDG config dir (optional — tool works without it). Env var overrides with `TWEETXVAULT_` prefix.
- [x] Implement `tweetxvault/auth/cookies.py` — cookie resolution chain:
  - Priority: env vars (`TWEETXVAULT_AUTH_TOKEN`, `TWEETXVAULT_CT0`, `TWEETXVAULT_USER_ID`) → config file → Firefox extraction.
  - Return a resolved auth bundle: `auth_token`, `ct0`, `user_id` (optional — only needed for Likes).
  - If nothing found: raise clear error with setup instructions.
- [x] Add archive-owner guardrails:
  - Persist local archive owner id in DB metadata on first successful sync.
  - Refuse later syncs if resolved owner id differs from stored owner id.
- [x] Implement `tweetxvault/auth/firefox.py`
  - Discover default profile from `profiles.ini` (allow explicit path override via config/env).
  - Copy `cookies.sqlite` to temp file; open read-only; query `moz_cookies` for `.x.com` / `.twitter.com`.
  - Extract `auth_token`, `ct0`, `twid`.
  - Parse `twid` (`u%3D<numeric_id>`) into numeric user_id.
- [x] Unit tests: cookie resolution chain (mock each source), Firefox extraction with synthetic sqlite fixture, twid parsing.

## Task 3: Query ID Discovery

- [x] Implement `tweetxvault/query_ids/constants.py`
  - Discovery page URL(s).
  - Bundle URL regex pattern (`abs.twimg.com/responsive-web/client-web/*.js`).
  - Target operations: `Bookmarks`, `Likes` (Phase 1), plus `BookmarkFolderTimeline`, `TweetDetail`, `UserArticlesTweets` (reserved).
  - `FALLBACK_QUERY_IDS` dict — source current values from browser DevTools. Document date sourced.
- [x] Implement `tweetxvault/query_ids/store.py`
  - Cache JSON file in XDG cache dir: `{fetched_at, ttl_seconds, ids}`.
  - `get(operation) -> str`: returns cached ID if fresh, else fallback.
  - `is_fresh() -> bool`: check `fetched_at + ttl_seconds > now`.
- [x] Implement `tweetxvault/query_ids/scraper.py`
  - Fetch discovery page HTML, extract JS bundle URLs.
  - Fetch bundles, extract `(operationName, queryId)` pairs via regex.
  - Use multiple regex patterns (the format has varied over time).
  - Update cache on success.
- [x] Unit tests: bundle URL extraction, queryId regex extraction from synthetic JS snippets, cache TTL logic, fallback behavior.

## Task 4: GraphQL Client

- [x] Implement `tweetxvault/client/base.py`
  - Build `httpx.AsyncClient` with cookie jar + required headers (see PLAN.md Auth section for full header list including bearer token).
  - Error classification: `is_rate_limit(resp)`, `is_auth_error(resp)`, `is_stale_query_id(resp)`, `is_feature_flag_error(resp)`.
  - Backoff engine: retry with exponential delay on 429, configurable via config model.
- [x] Implement `tweetxvault/client/features.py`
  - `build_bookmarks_features() -> dict` and `build_likes_features() -> dict`.
  - Source initial flag sets from a browser DevTools capture. Keep per-operation (not shared), commit them as static code data for MVP, and document date sourced in code comments.
- [x] Implement `tweetxvault/client/timelines.py`
  - `build_bookmarks_url(query_id, cursor=None) -> str` — variables: `{count: 20, ...}`.
  - `build_likes_url(query_id, user_id, cursor=None) -> str` — variables: `{userId, count: 20, ...}`.
  - `fetch_page(client, url) -> httpx.Response` with retry/backoff on 429 and refresh-once on 404.
  - Add a lightweight probe path (`count=1`) that reuses the same request builders but does **not** write captures or checkpoints.
  - `parse_timeline_response(data, operation) -> (tweets: list, cursor: str | None)` — extract tweet entries and bottom cursor. Per-operation parsing since response shapes differ.
- [x] Unit tests: URL building, cursor extraction for both Bookmarks and Likes response shapes (minimal JSON fixtures), `400/404/429` classification, backoff logic (httpx.MockTransport to simulate 429/404/200 sequences).

## Task 5: Storage (SeekDB)

Depends on Task 0 spike results. Adjust schema/approach based on spike decisions.

- [x] Implement `tweetxvault/storage/seekdb.py`
  - Open/create embedded DB in XDG data dir.
  - Schema/collections per PLAN.md: `raw_captures`, `tweets`, `collections`, `sync_state`, `archive_metadata`.
  - Methods:
    - `append_raw_capture(operation, cursor_in, cursor_out, http_status, raw_json)`
    - `upsert_tweet(tweet_id, text, author_id, author_username, author_display_name, created_at, raw_json)`
    - `upsert_membership(tweet_id, collection_type, sort_index=None, folder_id=None)`
    - `get_sync_state(collection_type) -> SyncState`
    - `set_sync_state(collection_type, *, last_head_tweet_id=None, backfill_cursor=None, backfill_incomplete=False)`
    - `reset_sync_state(collection_type)` (for `--full`)
    - `has_membership(tweet_id, collection_type, folder_id=None) -> bool` (for collection-scoped incremental duplicate detection)
    - `get_archive_owner_id() -> str | None`
    - `set_archive_owner_id(user_id: str)`
  - Use one DB transaction per persisted page so raw capture, tweet upserts, membership upserts, and sync-state updates commit atomically.
- [x] Raw JSON persistence: implement based on Task 0 spike decision (inline blobs or gzipped files).
  - If using gzipped sidecar files, write them atomically (temp file + rename) and only commit DB references after the file exists.
- [x] Unit tests using a temp data dir (no network, no real embeddings), including atomic page-write behavior and owner-id mismatch handling.

## Task 6: Sync Orchestration

- [x] Implement `tweetxvault/sync.py`
  - Add a shared preflight helper used by both `auth check` and `sync`: resolve auth, resolve query IDs, and run lightweight remote probes with **no DB writes**.
  - `async def sync_collection(collection: str, *, full: bool, limit: int | None)` — the main sync loop per PLAN.md "Sync Loop + Stop Conditions" section.
  - Validates auth before first API call (auth_token + ct0 present; user_id present if syncing likes) and performs a remote readiness probe before opening the main loop.
  - Incremental by default: do a head pass from `cursor=None`; if `backfill_incomplete`, continue from stored `backfill_cursor` after the head pass.
  - `--full` resets only the targeted collection sync state after preflight + lock acquisition; it does not delete existing tweet/membership data.
  - Stop conditions: empty page, collection-scoped duplicate detection during head pass (unless `--full`), `--limit`, rate limit exhaustion.
  - Persist sync state after each page in the same DB transaction as tweet/membership writes (crash-safe resume).
  - Ensure the process lock is released via `try/finally`, including on `429` exhaustion or unexpected exceptions.
  - Progress output via Rich (tweets synced, pages fetched, current status).
  - `sync_all(full, limit)` — preflights both requested collections before any writes, then runs bookmarks followed by likes; runtime failures are reported as partial failure rather than rolled back across collections.
- [x] Add a process lock helper (lock file in XDG data dir) so overlapping sync commands fail fast instead of racing.
- [x] Unit tests: run sync against mocked HTTP responses and verify raw_captures appended, tweets upserted, memberships created, head-pass + backfill state advance correctly, stop conditions trigger correctly, preflight probes do not count against `--limit`, `--limit` applies per collection, and `sync all` does not partially write if one collection fails preflight.

## Task 7: CLI

- [x] Implement `tweetxvault/cli.py` (Typer) with commands:
  - `tweetxvault sync bookmarks [--full] [--limit N]`
  - `tweetxvault sync likes [--full] [--limit N]`
  - `tweetxvault sync all [--full] [--limit N]`
  - `tweetxvault auth check` — run shared preflight without DB writes, print local credential status plus remote readiness for bookmarks/likes, exit 0/1/2.
  - `tweetxvault auth refresh-ids` — force query ID refresh from JS bundles.
- [x] First-run UX: all commands auto-create XDG dirs. `sync` commands validate auth before API calls, probe the target collection(s) before writing data, and print actionable errors (not stack traces) on failure.
- [x] Exit codes: 0 success, 1 auth/config error, 2 API/network/runtime sync error. `sync all` uses 2 for partial runtime failure after reporting per-collection results.

## Task 8: JSON Export

Optional but useful early.

- [x] Implement `tweetxvault/export/json_export.py`
  - Export by collection type (likes/bookmarks/all) to a JSON file.
  - Include: tweet_id, text, author info, created_at, collection membership, raw_json (or path).
- [x] Add `tweetxvault export json [--collection likes|bookmarks|all] [--out path]`.

## Task 9: Integration Test + Polish

- [x] **End-to-end integration test**: mock HTTP transport that returns realistic multi-page Bookmarks + Likes responses. Run full `sync_collection` → verify raw_captures, tweets, memberships, and checkpoints are all correct. Verify resume after simulated interruption.
- [x] **Collection-scoped duplicate test**: a tweet that already exists in `tweets` but not yet in the current collection must not stop sync early.
- [x] **Preflight behavior test**: `auth check` and `sync all` share the same probe path; failed likes preflight must abort `sync all` before bookmark writes.
- [x] **Incremental-vs-backfill test**: after an interrupted first run with stored `backfill_cursor`, the next sync must still fetch new head items before resuming older pages.
- [x] **Single-writer lock test**: a second sync process/instance must fail cleanly without mutating DB or cache state.
- [x] **Atomic checkpoint test**: simulated write failure must not leave `sync_state` advanced past durable tweet/membership writes.
- [x] **`--full` resume test**: interrupted `--full` sync leaves resumable backfill state and does not require deleting prior data.
- [x] **Lock release test**: failures during sync still release the process lock for the next run.
- [x] **Partial `sync all` test**: if bookmarks succeed and likes later fail, bookmark writes remain committed and the command exits with partial-failure status.
- [x] Security audit: ensure logs never include cookie values (grep for auth_token/ct0 in any logging/exception paths).
- [x] Verify first-run UX: run against empty XDG dirs with no config → confirm dirs created, clear error message about missing cookies.
- [x] Update `WORKLOG.md` with milestone completions.
- [x] Keep `docs/PLAN.md` in sync if any decision changed during implementation.
