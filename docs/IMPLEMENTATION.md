# tweetxvault — MVP Implementation Punchlist

This is the active implementation checklist. Update checkboxes as items complete. Prefer small, reviewable commits.

Hard constraints:
- `docs/initial/` is historical and intentionally frozen (do not edit).
- No credentials (cookies/tokens/session files) in git, logs, or test fixtures.
- MVP is direct GraphQL API + query-id auto-discovery. No Playwright in MVP.
- Sync loop calls the GraphQL client directly — no premature `Fetcher` protocol abstraction.
- All architectural decisions are in `docs/PLAN.md`. If something isn't specified there, check before guessing.

Definition of done: passes `uv run ruff format --check`, `uv run ruff check`, and `uv run pytest`.

Planning note (2026-03-15):
- The sections below describe the completed SQLite-backed MVP.
- The SQLite -> LanceDB migration landed on 2026-03-15.
- Capture expansion landed after the LanceDB migration.
- The active next milestone is turning the X-archive import stub into a real importer using the fresh 2026-03-16 archive fixture.
- The review-cleanup checklist lower in this file is complete and retained as historical record.

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
  - Result: the original sandboxed spike failed initialization, but a fresh full-permission re-spike showed embedded SeekDB only works reliably on non-tmpfs paths and still misses the MVP startup/footprint threshold (~3.23s cold benchmark, ~1.0GB RSS). We shipped SQLite first, then replaced it with LanceDB in Task 10.

## Task 1: Project Bootstrap

- [x] Add `pyproject.toml` (hatchling backend) with:
  - Project metadata (`name=tweetxvault`, `requires-python>=3.12`).
  - Runtime deps: `httpx`, `lancedb`, `pyarrow`, `pydantic>=2`, `typer`, `rich`, `loguru`.
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
  - Priority: env vars (`TWEETXVAULT_AUTH_TOKEN`, `TWEETXVAULT_CT0`, `TWEETXVAULT_USER_ID`) → config file → browser extraction.
  - Return a resolved auth bundle: `auth_token`, `ct0`, `user_id` (optional — only needed for Likes).
  - If nothing found: raise clear error with setup instructions.
- [x] Add archive-owner guardrails:
  - Persist local archive owner id in DB metadata on first successful sync.
  - Refuse later syncs if resolved owner id differs from stored owner id.
- [x] Implement `tweetxvault/auth/firefox.py`
  - Discover candidate profiles from `profiles.ini`, rank install-default/default profiles first, and allow explicit path/name override via config/env when a different profile is needed.
  - Copy `cookies.sqlite` to a temp snapshot before reading; see Review item 8 below for the current sidecar-copy details used on live profiles.
  - Extract `auth_token`, `ct0`, `twid`.
  - Parse `twid` (`u%3D<numeric_id>`) into numeric user_id.
- [x] Extend auth extraction to Chromium-family browsers
  - Added `tweetxvault/auth/chromium.py` using `browser-cookie3` for Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc.
  - Added generic `auth.browser`, `auth.browser_profile`, and `auth.browser_profile_path` config/env overrides.
  - Added `--browser`, `--profile`, and `--profile-path` flags to sync/auth-check commands plus `tweetxvault auth check --interactive`.
- [x] Unit tests: cookie resolution chain (mock each source), Firefox extraction with synthetic sqlite fixture, Chromium profile discovery/extraction, twid parsing, interactive auth-check selection.

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

## Task 5: Storage (SQLite Fallback Backend)

Historical note: this was the shipped MVP backend after the SeekDB spike failed the runtime/footprint gate.

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
- [x] Add `tweetxvault export html [--collection likes|bookmarks|all] [--out path]`.
- [x] Add `tweetxvault view bookmarks|likes|all [--limit N]`.

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

## Task 10: Replace SQLite Storage with LanceDB

Completed on 2026-03-15. This replaced the temporary SQLite fallback before any real archive data was loaded.

- [x] Replace runtime storage deps in `pyproject.toml`:
  - Removed `pyseekdb`.
  - Added `lancedb` and `pyarrow`.
  - Refreshed `uv.lock`.
- [x] Rename the concrete storage module to `tweetxvault/storage/backend.py`.
  - Stopped using backend-specific filenames like `seekdb.py` / `lancedb.py` for the shipped implementation.
  - Kept the public `ArchiveStore` / `SyncState` API in `tweetxvault/storage/__init__.py`.
- [x] Eliminate the current sync-loop cleanup items during the backend migration.
  - Removed double-preflight in `sync_all`.
  - Moved the final `last_head_tweet_id` update into backend-managed state semantics instead of a bare outer `commit()`.
  - Returned parsed payloads from `_fetch_and_parse_page(...)` to avoid the duplicate `response.json()` call.
- [x] Implement the LanceDB-backed archive in `tweetxvault/storage/backend.py`.
  - Use a single LanceDB table keyed by `row_key`.
  - Row types per `docs/PLAN.md`: `tweet`, `raw_capture`, `sync_state`, `metadata`.
  - Represent one persisted page as one batched table merge.
- [x] Update storage path conventions in `tweetxvault/config.py`.
  - Switched from `archive.sqlite3` to `archive.lancedb/`.
  - Preserved XDG behavior and first-run auto-create semantics.
- [x] Keep higher-level call sites stable where possible.
  - `tweetxvault/storage/__init__.py`
  - `tweetxvault/sync.py`
  - `tweetxvault/export/json_export.py`
  - `tweetxvault/cli.py`
- [x] Port the storage test suite to the LanceDB backend semantics.
  - Atomic page persistence
  - Owner guardrail
  - Sync-state reset/resume
  - Collection-scoped duplicate detection
  - Export ordering
- [x] Re-run the existing sync/integration tests against the LanceDB backend and fix behavioral regressions.
- [x] Add LanceDB-specific regression coverage:
  - one table-version increment per successful page write
  - no partial state if failure occurs before the batch write
  - filtered export/search queries over `tweet` rows only
  - `sync_all` does not reprobe collections after a successful shared preflight
- [x] Verify the landed backend:
  - `uv run ruff format --check tweetxvault tests`
  - `uv run ruff check tweetxvault tests`
  - `uv run pytest`
  - `uv run tweetxvault --help`
- [x] Update docs after migration lands.
  - `docs/PLAN.md`
  - `docs/ANALYSIS-db.md`
  - `docs/README.md`
  - `WORKLOG.md`

## Task 11: Secondary Object Extraction Foundation

This is the next real implementation milestone after the LanceDB migration. The goal is to stop treating each collection-scoped tweet row as the only normalized object in the system.

- [x] Extend the archive schema in `tweetxvault/storage/backend.py` with new `record_type` values:
  - `tweet_object`
  - `tweet_relation`
  - `media`
  - `url`
  - `url_ref`
  - `article`
- [x] Add a parser/extractor layer that takes a raw tweet object and emits:
  - canonical tweet-object fields
  - attached-tweet relations (`retweet_of`, `quote_of`)
  - media metadata (`extended_entities`, `video_info`)
  - URL refs / canonical URL candidates
  - article payloads when present
- [x] Keep collection-scoped `tweet` rows as the duplicate-detection and export-ordering layer during the transition.
- [x] Persist the new rows in the same page-sized LanceDB batch as the current raw capture + membership rows.
- [x] Extend rehydrate support so new normalized rows can be rebuilt from stored `raw_json` without refetching.
- [x] Tests:
  - quote/retweet relation extraction
  - media extraction for photos/videos/GIFs
  - URL extraction from entity/card payloads
  - one-page atomicity across the expanded record set
  - same tweet appearing in bookmarks and likes does not duplicate global secondary objects

## Task 12: Media Downloads + URL Unfurls

- [x] Add per-media download state fields and local-path metadata.
- [x] Implement photo download first:
  - deterministic on-disk layout under the XDG data dir
  - SHA-256 + byte-size verification
  - idempotent retries
- [x] Implement video/GIF download later:
  - variant selection policy (prefer highest-bitrate MP4 when present)
  - poster image capture
- [x] Implement URL canonicalization and unfurl persistence:
  - preserve original `t.co` URL
  - store expanded/final/canonical URL values
  - store metadata already present in GraphQL payloads before doing network fetches
- [x] Add a follow-on command or job-runner surface for remote unfurl fetches / snapshots without coupling them to the sync transaction.
  - Landed as inline commands: `tweetxvault media download` and `tweetxvault unfurl`
- [x] Leave ArchiveBox integration as optional queue/runner plumbing until the metadata model is stable.

## Task 13: Articles

- [x] Add a dedicated `--article-backfill` timeline rescan mode so existing collection pages can be refetched after article field toggles change, without resetting sync state.
- [x] Add an article probe fixture once we capture a real authenticated `UserArticlesTweets` or article-bearing timeline response.
  - Working example URL: `https://x.com/dimitrispapail/status/2026531440414925307`
  - Captured as `tests/fixtures/dimitris_article_tweet_detail.json` from an authenticated `TweetDetail` response on 2026-03-16
- [x] Verify whether full bodies are returned now that article field toggles are enabled on timeline requests.
  - Result on 2026-03-16: authenticated `TweetDetail` returned full `plain_text`, `content_state`, `cover_media`, and `media_entities`
- [x] Persist article rows keyed by source tweet id until a stable article-specific id is confirmed.
- [x] Export article metadata/body in JSON and HTML once extraction is stable.
- [x] Decide whether article-only fallback fetching is needed if GraphQL returns preview-only payloads.
  - Current decision: no extra fallback is needed right now; `tweetxvault articles refresh` uses authenticated `TweetDetail`, which returned full bodies for the Dimitris validation tweet on 2026-03-16

## Task 14: Own Tweet Capture

This is materially smaller than archive import because it reuses the live GraphQL sync path, the current extractor layer, and the existing media/unfurl/export follow-on jobs.

- [x] Add `UserTweets` query-id coverage and a dedicated request builder.
- [x] Reserve `UserTweetsAndReplies` for a later follow-on; start with authored tweets only.
- [x] Add CLI shape:
  - `tweetxvault sync tweets`
  - `tweetxvault view tweets`
  - `tweetxvault export json --collection tweets`
  - `tweetxvault export html --collection tweets`
- [x] Add a collection/storage label for authored tweets that reuses the current `tweet` membership rows plus secondary-object extraction.
- [x] Reuse the existing duplicate-detection, sync-state, rehydrate, media-download, URL-unfurl, and article-refresh paths for own-tweet rows.
- [x] Decide whether `tweetxvault sync all` should include own tweets, or whether authored tweets stay an explicit opt-in collection.
  - Current decision: keep authored tweets as an explicit opt-in collection via `tweetxvault sync tweets` so `sync all` does not unexpectedly expand archive size.
- [x] Add regression coverage for:
  - incremental `UserTweets` pagination
  - collection-scoped duplicate detection on authored tweets
  - export/view support for the new collection
  - same authored tweet later appearing in likes/bookmarks without duplicating secondary objects

## Task 15: Thread Expansion + Linked Tweet Capture

This is separate from attached-tweet extraction. The current extractor already stores one-level quote/retweet payloads when they are embedded in the timeline response, but it does not fetch missing parents, replies, or linked tweet URLs.

- [x] Add a follow-on `TweetDetail` expansion path for archived tweets.
- [x] Decide the initial trigger surface:
  - explicit command landed first via `tweetxvault threads expand`
  - optional sync-time expansion can be reconsidered later if the current runner stays stable
- [x] Reserve CLI shape for the first pass:
  - `tweetxvault threads expand`
  - optional `tweetxvault threads expand <tweet-id-or-status-url> ...`
  - optional `tweetxvault threads expand --refresh <tweet-id-or-status-url> ...` for explicit re-fetches
- [x] Persist thread/context tweets as global `tweet_object` rows plus `tweet_relation` edges without inventing bookmark/like/tweets memberships for them.
- [x] Add new relation types for thread context as needed (`reply_to`, `in_reply_to`, `thread_parent`, `thread_child`) once we lock the exact `TweetDetail` shape.
- [x] Expand linked X-status URLs found in `url_ref` rows:
  - detect `x.com/.../status/<id>` and `twitter.com/.../status/<id>`
  - fetch them through the same `TweetDetail` path
  - avoid duplicate fetches when the linked tweet is already present as a membership, attached tweet, or previously-expanded context tweet
- [x] Reuse rehydrate where possible for relation rebuilding, but document that remote thread expansion itself is not recoverable from local data unless the `TweetDetail` payload was already captured.
  - Current behavior: `tweetxvault rehydrate` now also rescans stored `TweetDetail` / `ThreadExpandDetail` raw captures, so previously captured detail payloads can rebuild thread/context rows without another network fetch.
- [x] Add regression coverage for:
  - parent-thread capture from `TweetDetail`
  - linked status-URL capture
  - idempotent repeated expansion runs
  - preserving collection-scoped membership boundaries while adding global thread/context rows

## Task 16: X Archive Import

Fresh fixture status (2026-03-16):
- Real sample cataloged in `docs/ANALYSIS-archive-import.md`.
- Confirmed overlap: `tweets.js`, `tweet-headers.js`, `deleted-tweets.js`, `deleted-tweet-headers.js`, `like.js`, and `tweets_media/`.
- Confirmed gaps in this sample: no bookmark dataset; `article.js`, `article-metadata.js`, `note-tweet.js`, and `community-tweet.js` are present but empty.

- [x] Catalog the real archive shape and record overlap findings.
- [x] Lock first-pass precedence rules from the sample:
  - Live GraphQL wins for richer normalized tweet/media/url/article fields when both sources overlap.
  - `x_archive` wins for deleted authored tweets and already-exported media binaries.
  - `like.js` imports are sparse membership/provenance rows until live sync enriches them.
  - Do not rely on the current new-non-empty-wins coalescing semantics for live/archive merges.
- [ ] Reserve CLI shape:
  - `tweetxvault import x-archive <zip-or-dir>`
- [ ] Add storage-layer source-aware merge logic in `ArchiveStore`, using the normalized row `source` field as the winning-source marker instead of caller-side ad hoc merges.
- [ ] Add a dedicated `import_manifest` record type keyed by archive digest with generation date, status, warnings, and per-dataset counts.
- [ ] Add a generic `parse_ytd_js(...)` / zip-directory loader for `manifest.js` plus `window.YTD.*` `data/*.js` parts, then layer per-file adapters on top.
- [ ] Import authored tweets from `tweets.js` / `deleted-tweets.js` through a YTD-to-internal adapter, including nullable `deleted_at` support on the normalized tweet rows.
- [ ] Import `like.js` into collection rows with a synthetic archive-order `sort_index` and raw provenance, while also seeding sparse global tweet placeholders for later enrichment.
- [ ] Copy `tweets_media/` exports into the managed tweetxvault media layout, then register them on `media.local_path` / `download_state`.
- [ ] Add post-import live reconciliation:
  - run normal bulk live syncs first (`tweets`, `likes`, later bookmarks if available) to upgrade overlapping rows cheaply
  - run targeted per-item GraphQL lookups only for rows that remain sparse after the bulk pass
- [ ] Track per-tweet live-enrichment status (`pending`, `done`, `transient_failure`, `terminal_unavailable`) with last-check/result metadata so permanently unavailable tweets stop requerying.
- [ ] Keep archive provenance even when a later live likes/bookmarks sync no longer includes that item; collection absence is not by itself a terminal lookup result.
- [ ] Add regression fixtures/tests for repeated imports, live+archive merges, and archive-after-live precedence behavior.

## Review Cleanup

Follow-up maintenance work after the content-expansion milestone. Land these as small, well-tested refactors instead of rolling them into feature work.

- [x] Review item 1: de-duplicate the sync CLI command implementations in `tweetxvault/cli.py`.
  - Current problem: `sync bookmarks`, `sync likes`, `sync tweets`, and `sync all` repeat the same config/auth/error-handling flow.
  - Landed approach: registered the per-collection sync commands through one factory and moved shared config/auth/error handling into one helper, while keeping the existing command names and options unchanged.
  - Coverage: CLI forwarding now exercises `sync bookmarks`, `sync likes`, `sync tweets`, and `sync all` against the shared path.
- [x] Review item 2: extract the shared locked-store batch-job skeleton used by `media.py`, `unfurl.py`, `articles.py`, and `threads.py`.
  - Current problem: each runner repeats the same config/path resolution, archive lock acquisition, store open/close handling, and conditional optimize flow.
  - Landed approach: added a shared `locked_archive_job(...)` async context plus `resolve_job_context(...)` in `tweetxvault/jobs.py`, and moved the four runners onto that helper while keeping auth resolution outside the lock where needed.
  - Optimize semantics preserved: media/unfurl mark the job dirty after any processed rows; articles/threads only mark dirty after successful updates/expansions.
  - Coverage: direct helper tests now cover close/error/conditional-optimize behavior, and the existing media/unfurl/articles/threads runner tests still pass on top.
- [x] Review item 3: reduce the repeated coalesce/timestamp boilerplate in `tweetxvault/storage/backend.py`.
  - Current problem: each secondary `_..._record` builder repeats the same row timestamp setup and `existing[\"field\"] if existing else None` coalescing pattern.
  - Landed approach: added a small internal `_RecordContext` helper plus `_coalesce_existing(...)` / `_record_with_context(...)` so the record builders share row/timestamp setup without turning into a generic mapper.
  - Coverage: storage now has a regression proving a later thinner secondary payload does not wipe richer existing media/article fields.
- [x] Review item 4: centralize the duplicate `utc_now` helper into a shared utility module.
  - Current problem: identical `_utc_now()` helpers exist in `media.py`, `unfurl.py`, and `storage/backend.py`.
  - Landed approach: moved the shared timestamp helper into `tweetxvault/utils.py` and reused it from storage, media, and unfurl.
  - Coverage: the existing storage/media/unfurl tests stayed green after the helper move.
- [x] Review item 5: unify `_canonical_url_candidate` and `_final_url_candidate` in `tweetxvault/extractor.py`.
  - Current problem: the two helpers are nearly identical but diverge in subtle ways, which is an easy future bug source if one path gets updated without the other.
  - Landed approach: replaced the parallel helpers with one `_url_candidate(...)` helper that takes the candidate key order plus a `require_absolute` switch, so the canonical-vs-final differences stay explicit in the call sites.
  - Coverage: extractor tests now exercise both unwound final-URL selection and `t.co` canonical fallback through the shared helper.
- [x] Review item 6: push state/type filtering for secondary row listings into LanceDB predicates in `tweetxvault/storage/backend.py`.
  - Current problem: `list_media_rows(...)`, `list_url_rows(...)`, and `list_article_rows(...)` currently materialize every row of that record type and then filter in Python.
  - Landed approach: moved the state/type/preview filters into shared LanceDB expression helpers so `ArchiveStore` only materializes matching media/url/article rows, while keeping the existing Python-side sort order unchanged.
  - Coverage: storage now has a real LanceDB-backed regression covering pending/done media filters, URL state filters, and preview-only article selection.
- [x] Review item 7: de-duplicate the repeated thread-expansion try/except/counting blocks in `tweetxvault/threads.py`.
  - Current problem: the explicit-target loop, membership loop, and linked-status loop all repeat the same `_expand_target(...)` error-handling and result-counting path.
  - Landed approach: extracted one `_try_expand_target(...)` helper that owns the shared processed/expanded/failed bookkeeping plus `expanded_targets` / `known_tweet_ids` updates, while leaving the loop-specific skip/selection rules unchanged.
  - Coverage: thread tests now cover both the existing membership+linked-status path and an explicit-target case that locks in duplicate skipping plus failure counting.
- [x] Review item 8: make Firefox cookie snapshotting WAL-safe in `tweetxvault/auth/firefox.py`.
  - Current problem: copying `cookies.sqlite` plus `-wal` / `-shm` sidecars separately can still race a live Firefox write and produce an inconsistent snapshot.
  - Landed approach: the first SQLite-backup snapshot attempt proved capable of hanging on busy live profiles, so the bounded shipped path copies `cookies.sqlite` plus any present `-wal` / `-shm` / `-journal` sidecars into a temp snapshot before querying cookies.
  - Coverage: auth tests now cover reading cookies from a WAL-mode Firefox DB while the source connection remains live.
- [x] Review item 9: broaden runner and extractor test coverage for error paths and edge cases.
  - Current problem: the new runner modules mostly only have happy-path tests, and extractor coverage is still thin on malformed payloads.
  - Landed approach: added focused tests for runner failure states, retries, limits, non-HTML responses, invalid detail payloads, and malformed extractor inputs without changing runner behavior.
  - Coverage: media/unfurl now cover retry + limit flows, articles/threads cover invalid-response or limit behavior, and extractor tests cover malformed article/attached/url/media payload shapes.
- [x] Review item 10: add direct unit coverage for `ExtractedTweetGraph` merge/coalesce behavior.
  - Current problem: the graph-level `add_*` methods are only exercised indirectly through extraction/storage integration tests, which makes edge-case precedence rules harder to lock down.
  - Landed approach: added focused unit tests for direct `add_tweet_object(...)`, `add_media(...)`, and `merge(...)` behavior, covering new-vs-existing precedence, empty-string handling, `min(position)`, and article status promotion.
  - Coverage: direct graph tests now cover tweet/media/url/url_ref/article coalescing plus one explicit `merge(...)` path.
- [x] Review item 11: batch media/unfurl row updates and clean up minor clarity issues.
  - Current problem: `media` and `unfurl` currently do a LanceDB read + merge per item via `update_media_download(...)` / `update_url_unfurl(...)`, which is unnecessarily expensive at larger archive sizes.
  - Landed approach: added reusable row-update builders plus `ArchiveStore.merge_rows(...)`, switched media/unfurl to flush updated rows in batches, and folded in the low-risk CLI-parentheses + media-URL safety fixes while touching those files.
  - Coverage: media/unfurl tests now prove the batched merge path is actually used, and the existing state-transition coverage stayed green on top.
- [x] Review item 12: improve long-running thread-expansion observability.
  - Current problem: `tweetxvault threads expand` only emits per-target failures plus the final summary, so long runs can appear hung while they are retrying, cooling down on 429s, or scanning a large archive.
  - Landed approach: added a shared request-status callback path in the HTTP client, then wired `threads expand` to print pass-level progress plus tweet-scoped 429 retry/cooldown and query-id-refresh messages.
  - Coverage: client tests now lock in retry/cooldown and 404-refresh status messages, and thread tests now cover visible rate-limit diagnostics during an explicit-target expansion failure.
- [x] Review item 13: show startup progress before thread-expansion preload scans and defer unnecessary archive scans.
  - Current problem: `tweetxvault threads expand` still stays silent at startup on large archives because it eagerly loads prior expansion targets, known tweet ids, and membership ids before the first progress line.
  - Landed approach: added immediate startup/preload status lines, then deferred the expensive `known_tweet_ids` scan until the linked-status pass actually needs it so explicit-target runs and some limit-bounded runs stop paying that cost up front.
  - Coverage: thread tests now lock in early preload logging for both the normal membership+linked-status path and the explicit-target/rate-limit path.
- [x] Review item 14: add an auth-resolution debug path for long-running CLI jobs.
  - Current problem: if a command stalls before the archive job starts, there is no visibility into whether browser cookie resolution or profile/keyring probing is the blocking step.
  - Landed approach: added a `--debug-auth` flag for `threads expand` and `auth check`, plus auth-resolution status callbacks that surface browser/profile probing steps from the cookie resolver.
  - Coverage: auth tests now lock in emitted browser-probe status, and CLI tests cover `--debug-auth` output plumbing for both `auth check` and `threads expand`.
- [x] Review item 15: make post-sync auto-embedding best-effort instead of failing a successful sync.
  - Current problem: `_sync_collection_ready()` persists fetched pages, then auto-embedding can still raise on model/artifact/runtime issues and flip the whole command to failure after the archive write already succeeded.
  - Landed approach: successful sync persistence now wins. Auto-embedding failures are caught, surfaced as warnings, and deferred to a later `tweetxvault embed` run or a future sync instead of failing the capture command.
  - Coverage: `tests/test_sync.py` now forces embedding initialization to fail after page persistence and proves the sync still succeeds with stored rows intact.
- [x] Review item 16: define `--browser` auth-override semantics for `user_id`.
  - Current problem: the current browser override drops explicit env/config `user_id`, which can break likes/tweets even when the user configured a numeric fallback.
  - Landed approach: browser overrides now only force cookie sourcing (`auth_token` / `ct0`) from the selected browser/profile; explicit env/config `user_id` remains a fallback for likes/tweets.
  - Coverage: CLI tests now lock in that `--browser` preserves explicit `user_id` fallback inputs.
- [x] Review item 17: tighten thread-expansion rerun/dedupe semantics.
  - Current problem: explicit `threads expand <id/url>...` currently refetches already-expanded targets, and linked-status expansion only remembers successful targets within a run, so one failing target can be retried repeatedly from multiple URL refs.
  - Landed approach: explicit targets are now idempotent by default, `--refresh` is the explicit re-fetch escape hatch, and linked status-URL targets are attempted at most once per run.
  - Coverage: thread tests now cover default explicit-target skipping, `--refresh` refetches, and duplicate linked-status failures only attempting one network call per run; CLI tests cover the new `--refresh` flag and validation.
- [x] Review item 18: decide and document the supported runtime platforms.
  - Current problem: README/path messaging implies Windows support, but core runtime pieces (`fcntl`, `resource`, `strftime("%-d")`) keep the current CLI Unix-specific.
  - Landed approach: documented the current runtime as Unix-like only until platform-specific replacements and tests land for those dependencies.
- [x] Review item 19: tighten PyPI release metadata and artifact contents.
  - Current problem: the PyPI-facing README used a repo-relative screenshot, install docs led with source checkout instead of `pip install`, and the default sdist pulled in repo-internal docs/tests/worklog files.
  - Landed approach: switched the README screenshot to a direct GitHub raw URL, moved PyPI install instructions ahead of the source-install path, added explicit project URLs plus Unix-like trove classifiers, and constrained hatchling wheel/sdist targets to the package and release files. Hatchling still auto-includes `.gitignore` in the sdist.
  - Validation: `uv build`, `uv run --with twine twine check dist/*`, and direct wheel/sdist content inspection.
- [x] Review item 20: normalize embeddings and align semantic search to cosine distance.
  - Current problem: the ONNX embedding pipeline stored raw mean-pooled vectors, and LanceDB vector/hybrid search used the default distance metric instead of explicitly matching sentence-transformer-style cosine similarity.
  - Landed approach: L2-normalized embedding outputs in `tweetxvault/embed.py`, forced cosine distance in `ArchiveStore.search_vector(...)` / `search_hybrid(...)`, and documented that existing archives should rerun `tweetxvault embed --regen` once after upgrading.
  - Validation: `uv run pytest tests/test_embed.py tests/test_storage.py tests/test_cli.py tests/test_sync.py -q`, `uv run ruff check tweetxvault/embed.py tweetxvault/storage/backend.py tests/test_embed.py tests/test_storage.py`, and `uv run ruff format --check tweetxvault/embed.py tweetxvault/storage/backend.py tests/test_embed.py tests/test_storage.py`.
- [x] Review item 21: reduce query-id coupling and clean up dead sync-state parameters.
  - Current problem: `articles.py` / `threads.py` imported the private `_resolve_query_ids(...)` helper from `sync.py`, and `_store_state_for_page(...)` still carried unused parameters from an older sync-state shape.
  - Landed approach: promoted the shared query-id resolver into `tweetxvault/utils.py`, updated the callers to use the public helper, removed the dead `_store_state_for_page(...)` parameters, and annotated the historical Firefox implementation note to point readers at the newer WAL-safe snapshot details below.
  - Validation: `uv run pytest tests/test_sync.py tests/test_articles.py tests/test_threads.py tests/test_cli.py -q`, `uv run ruff check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`, and `uv run ruff format --check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`.
- [x] Review item 22: highlight literal query matches in CLI search output.
  - Current problem: `tweetxvault search` printed matching tweets plainly, which made it harder to scan FTS and hybrid results when the literal query text was actually present in a longer tweet body.
  - Landed approach: added case-insensitive reverse-video highlighting for the whitespace-split query terms in the rendered search text column, without changing retrieval semantics or scoring.
  - Validation: `uv run pytest tests/test_cli.py -q`, `uv run ruff check tweetxvault/cli.py tests/test_cli.py`, and `uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`.
