# WORKLOG

## 2026-03-16

- Landed review cleanup item 12 for long-running thread-expansion observability:
  - Added a shared request-status callback path in `tweetxvault/client/base.py` / `tweetxvault/client/timelines.py` so callers can surface 429 retry, cooldown, and query-id refresh events without hard-coding that logic into each runner
  - Updated `tweetxvault/threads.py` to print phase-level progress (`explicit`, `membership`, `linked-status`) plus tweet-scoped retry/cooldown/failure messages, so `tweetxvault threads expand` no longer looks hung during long backoff windows
  - Added client regressions in `tests/test_client.py` for 404 refresh-status and 429 retry/cooldown messages, plus a thread-runner regression in `tests/test_threads.py` that locks in visible rate-limit diagnostics for an explicit-target failure
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault/client/base.py tweetxvault/client/timelines.py tweetxvault/threads.py tests/test_client.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/client/base.py tweetxvault/client/timelines.py tweetxvault/threads.py tests/test_client.py tests/test_threads.py`

- Landed review cleanup item 11 for LanceDB batch updates in media/unfurl:
  - Added `ArchiveStore.merge_rows(...)` plus reusable media/url row-update builders in `tweetxvault/storage/backend.py`, then switched `tweetxvault/media.py` and `tweetxvault/unfurl.py` to flush row updates in batches instead of merging one item at a time
  - Added proof tests in `tests/test_media.py` and `tests/test_unfurl.py` that count `merge_rows(...)` calls so the new batched path is explicitly covered
  - Folded in the nearby minor fixes while touching those files: parenthesized the `cli.py` auth-override guard for readability and made `_preferred_media_url(...)` avoid unchecked dict indexing even though `_video_variants(...)` already sanitizes inputs
  - Validation:
    - `uv run pytest tests/test_media.py tests/test_unfurl.py tests/test_storage.py tests/test_extractor.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tweetxvault/extractor.py tests/test_media.py tests/test_unfurl.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tweetxvault/extractor.py tests/test_media.py tests/test_unfurl.py`

- Landed review cleanup item 10 for direct `ExtractedTweetGraph` coalescing coverage:
  - Expanded `tests/test_extractor.py` with direct unit tests for `ExtractedTweetGraph.add_tweet_object(...)`, `add_media(...)`, and `merge(...)`
  - Locked down the current precedence rules: new non-empty values beat existing ones, empty strings fall back to existing values, media keeps the minimum position, empty variant/raw-json payloads do not overwrite richer existing values, and articles promote to `body_present` when merged body text appears
  - Validation:
    - `uv run pytest tests/test_extractor.py`
    - `uv run ruff check tests/test_extractor.py`
    - `uv run ruff format --check tests/test_extractor.py`

- Landed review cleanup item 9 for runner/extractor coverage gaps:
  - Expanded `tests/test_media.py` with failed-download retry coverage plus `retry_failed` and `limit` behavior for photo downloads
  - Expanded `tests/test_unfurl.py` with failed-unfurl retry coverage, `retry_failed` + `limit` behavior, and a non-HTML response case
  - Expanded `tests/test_articles.py` with invalid-detail failure handling and `limit` behavior for preview article refreshes
  - Expanded `tests/test_threads.py` with a `limit` case that proves thread expansion stops before the linked-status pass once the requested count is reached
  - Expanded `tests/test_extractor.py` with malformed article/attached payload coverage plus sparse URL/media entry handling
  - Validation:
    - `uv run pytest tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`
    - `uv run ruff check tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`
    - `uv run ruff format --check tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`

- Landed review cleanup item 8 for Firefox WAL-safe cookie snapshots:
  - Reworked `tweetxvault/auth/firefox.py` so Firefox cookie extraction now snapshots `cookies.sqlite` via SQLite's backup API instead of manually copying `cookies.sqlite` plus `-wal` / `-shm` sidecars
  - Kept the extraction query unchanged after the snapshot, but removed the race-prone assumption that sidecar file copies happen at a coherent point in time for a live Firefox profile
  - Added an auth regression in `tests/test_auth.py` that reads cookies from a WAL-mode Firefox DB while the source connection remains open
  - Validation:
    - `uv run pytest tests/test_auth.py`
    - `uv run ruff check tweetxvault/auth/firefox.py tests/test_auth.py`
    - `uv run ruff format --check tweetxvault/auth/firefox.py tests/test_auth.py`

- Landed review cleanup item 7 for thread-expansion control-flow repetition:
  - Refactored `tweetxvault/threads.py` so the repeated `_expand_target(...)` try/except/result-counting logic now lives in one `_try_expand_target(...)` helper
  - Kept the loop-specific rules unchanged: explicit targets still dedupe inputs first, membership targets still skip already-expanded tweets, and linked-status targets still skip source/self/known targets before attempting expansion
  - Added an explicit-target regression in `tests/test_threads.py` that locks in duplicate skipping plus per-target failure counting alongside the existing membership + linked-status expansion coverage
  - Validation:
    - `uv run pytest tests/test_threads.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/threads.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/threads.py tests/test_threads.py`

- Landed review cleanup item 6 for secondary row filter pushdown:
  - Updated `tweetxvault/storage/backend.py` so `list_media_rows(...)`, `list_url_rows(...)`, and `list_article_rows(...)` push their state/type/preview predicates into LanceDB `.where(...)` clauses before materializing rows
  - Added small shared expression helpers for quoted `IN (...)` lists, pending-state handling, and combined `AND` filters so the LanceDB predicate logic stays explicit and reusable inside the backend
  - Kept the existing Python-side sort order and limit semantics unchanged; only the filtering step moved out of Python
  - Added a real archive-store regression in `tests/test_storage.py` proving pending/done media filters, URL state filters, and preview-only article selection all work against LanceDB-backed storage
  - Validation:
    - `uv run pytest tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_articles.py`
    - `uv run ruff check tweetxvault/storage/backend.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tests/test_storage.py`

- Landed review cleanup item 5 for extractor URL-candidate duplication:
  - Replaced the parallel `_canonical_url_candidate(...)` / `_final_url_candidate(...)` helpers in `tweetxvault/extractor.py` with one `_url_candidate(...)` helper parameterized by key order and absolute-URL requirements
  - Kept the remaining behavioral differences explicit in `_url_entries(...)`: final URLs still require absolute `unwound_url` / `expanded_url` values, while canonical selection can still fall back to the short `url`
  - Added extractor coverage for both an unwound absolute final URL and a short-link-only fallback path so future URL-selection changes exercise the shared helper from both call sites
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py`
    - `uv run ruff check tweetxvault/extractor.py tests/test_extractor.py`
    - `uv run ruff format --check tweetxvault/extractor.py tests/test_extractor.py`

- Landed review cleanup items 3 and 4 for storage record-builder boilerplate and duplicate time helpers:
  - Added `tweetxvault/utils.py` with the shared `utc_now()` helper and switched `tweetxvault/storage/backend.py`, `tweetxvault/media.py`, and `tweetxvault/unfurl.py` over to it
  - Refactored the LanceDB record builders in `tweetxvault/storage/backend.py` around a small `_RecordContext` helper plus `_coalesce_existing(...)` / `_record_with_context(...)` so row timestamp setup and existing-value coalescing are shared
  - Kept the storage refactor intentionally local to the backend instead of introducing a generic record-mapping abstraction
  - Added a storage regression proving a later thinner payload still preserves richer existing media/article secondary values
  - Validation:
    - `uv run pytest tests/test_storage.py tests/test_media.py tests/test_unfurl.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/utils.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/utils.py tests/test_storage.py`

- Landed review cleanup item 2 for runner lifecycle repetition:
  - Added `tweetxvault/jobs.py` with `resolve_job_context(...)` and `locked_archive_job(...)` so the shared config/path resolution, archive lock, store open/close, and conditional optimize flow lives in one place
  - Moved `tweetxvault/media.py`, `tweetxvault/unfurl.py`, `tweetxvault/articles.py`, and `tweetxvault/threads.py` onto the shared helper while preserving their existing optimize triggers
  - Kept auth resolution outside the archive lock for `articles` and `threads`, matching the pre-refactor behavior
  - Added direct helper coverage in `tests/test_jobs.py` for conditional optimize, store close, and missing-archive failure handling
  - Validation:
    - `uv run pytest tests/test_jobs.py tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py`
    - `uv run ruff check tweetxvault/jobs.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/articles.py tweetxvault/threads.py tests/test_jobs.py`
    - `uv run ruff format --check tweetxvault/jobs.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/articles.py tweetxvault/threads.py tests/test_jobs.py`

- Landed review cleanup item 1 for sync CLI repetition:
  - Refactored `tweetxvault/cli.py` so `sync bookmarks`, `sync likes`, and `sync tweets` are registered through one command factory and share one config/auth/error-handling helper with `sync all`
  - Kept the existing CLI command names, options, and output format stable while removing the copy-pasted sync command bodies
  - Expanded CLI coverage so the shared sync path is exercised for bookmarks, likes, tweets, and `sync all`
  - Validation:
    - `uv run pytest tests/test_cli.py tests/test_sync.py`
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`
    - `uv run tweetxvault sync --help`

- Landed Task 15 thread/context expansion:
  - Added `tweetxvault/threads.py` plus `tweetxvault threads expand`, which fetches archived tweet context through authenticated `TweetDetail` and also follows linked `x.com/.../status/...` / `twitter.com/.../status/...` URLs found in archived `url_ref` rows
  - Extended `tweetxvault/client/timelines.py` with `parse_tweet_detail_tweets(...)` so full detail payloads can be harvested, not just the focal tweet
  - Extended `tweetxvault/extractor.py` with linked-status detection plus `reply_to`, `thread_parent`, and `thread_child` relation extraction for multi-tweet detail payloads
  - Added `ArchiveStore.persist_thread_detail(...)` and discovery helpers in `tweetxvault/storage/backend.py`; thread/context tweets now persist as global `tweet_object` / `tweet_relation` rows without creating fake bookmark/like/tweet memberships
  - Expanded `tweetxvault rehydrate` so stored `TweetDetail` and `ThreadExpandDetail` raw captures can rebuild thread/context rows later without another network fetch
  - Added regression coverage for full-detail parsing, thread relation extraction, thread-detail storage, idempotent reruns, linked-status expansion, and CLI wiring
  - Validation:
    - `uv run pytest tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/client/timelines.py tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/threads.py tweetxvault/cli.py tests/conftest.py tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/client/timelines.py tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/threads.py tweetxvault/cli.py tests/conftest.py tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`

- Landed Task 14 own-tweet capture:
  - Added live `UserTweets` support with a fresh fallback query ID reverified from the public X web bundle on 2026-03-16 (`Y59DTUMfcKmUAATiT2SlTw`)
  - Added `tweetxvault sync tweets` and `tweetxvault view tweets`; generic export commands now accept `--collection tweets`
  - Reused the existing LanceDB membership rows (`collection_type='tweet'`), duplicate detection, rehydrate, article backfill, media download, URL unfurl, and article refresh paths for authored tweets
  - Kept `tweetxvault sync all` limited to bookmarks + likes for now so authored-tweet capture stays explicit and does not surprise users with larger archive growth
  - Added regression coverage for `UserTweets` URL building/parsing, authored-tweet sync pagination + duplicate detection, CLI view/export/sync support, and secondary-object deduplication when the same authored tweet later appears in another collection
  - Validation:
    - `uv run pytest tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`
    - `uv run ruff check tweetxvault/auth/cookies.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/common.py tweetxvault/query_ids/constants.py tweetxvault/sync.py tests/conftest.py tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/auth/cookies.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/common.py tweetxvault/query_ids/constants.py tweetxvault/sync.py tests/conftest.py tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`

- Fixed a LanceDB commit-conflict path exposed by `sync --article-backfill`:
  - Root cause: CLI overwrite-style archive operations (`optimize`, `rehydrate`, `embed`) and auto-optimize retries from read commands could call `store.optimize()` without taking the shared archive lock, which could race with `sync` merge writes and trigger `Commit conflict ... concurrent transaction Overwrite`
  - Added shared lock coverage in `tweetxvault/cli.py` for `optimize`, `rehydrate`, `embed`, and auto-optimize fallbacks in `view` / `export` / `search`
  - Generalized the lock error text in `tweetxvault/sync.py` from “sync” to “archive job” because the same lock now protects multiple archive-mutating commands
  - Added CLI regressions covering locked optimize/rehydrate/embed paths plus the blocked auto-optimize failure path
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault tests`
    - `uv run ruff format --check tweetxvault tests`

- Added a dedicated thread-expansion milestone ahead of archive import:
  - Inserted Task 15 in `docs/IMPLEMENTATION.md` for `TweetDetail`-based thread/context capture plus linked X-status URL expansion
  - Shifted archive import planning to Task 16
  - Clarified in `docs/PLAN.md` that current attached-tweet extraction is not full thread expansion, and added a new open question about whether thread capture should stay an explicit command or become an opt-in sync-time flag

- Reprioritized the roadmap to add authored-tweet capture before archive import:
  - Added a new Task 14 in `docs/IMPLEMENTATION.md` for `UserTweets`-backed own-tweet sync/export support
  - Initially shifted archive import planning back one slot because it is a larger parallel ingestion problem, while own tweets reuse the existing live-sync/storage/media/export stack
  - Added `sync tweets` / `view tweets` roadmap notes in `docs/PLAN.md` plus a new open UX question about whether authored tweets should join `sync all`

- Landed the remaining article support:
  - Added `tweetxvault/articles.py` plus `tweetxvault articles refresh`, which resolves explicit tweet URLs/IDs or auto-selects preview-only archived article rows and refreshes them through authenticated `TweetDetail`
  - Added `build_tweet_detail_url(...)` / `parse_tweet_detail_response(...)` in `tweetxvault/client/timelines.py`
  - Captured a trimmed real authenticated fixture for `https://x.com/dimitrispapail/status/2026531440414925307` at `tests/fixtures/dimitris_article_tweet_detail.json`
  - Verified on 2026-03-16 that `TweetDetail` currently returns full article `plain_text`, `content_state`, `cover_media`, and `media_entities`, so no Playwright-only article fallback is needed right now
  - Reworked HTML export so article bodies, article media, tweet media, and URL metadata render directly in the local viewer
  - Validation:
    - `uv run pytest tests/test_articles.py tests/test_client.py tests/test_cli.py tests/test_export.py`
    - `uv run ruff check tweetxvault/articles.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/html_export.py tests/test_articles.py tests/test_client.py tests/test_cli.py tests/test_export.py`

- Landed Task 12 media downloads + URL unfurls:
  - Extended `tweetxvault/storage/backend.py` with per-media download state/local-path/hash fields plus URL unfurl/final-url/title/description metadata fields
  - Expanded `tweetxvault/extractor.py` so article cover/body media are captured into `media` rows and URL rows preserve payload-provided metadata before any remote fetches
  - Added `tweetxvault/media.py` + `tweetxvault/unfurl.py` with lock-safe follow-on runners, wired to `tweetxvault media download` and `tweetxvault unfurl`
  - Kept downloads and unfurls outside the sync transaction while making JSON exports include nested `media`, `urls`, and `article` sections
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tests/test_extractor.py tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_cli.py`

- Added `--article-backfill` sync mode:
  - Extended `tweetxvault sync bookmarks|likes|all` with `--article-backfill`, which rewalks existing timeline pages without resetting sync state so older tweets can pick up newly-enabled article fields
  - Kept it on the normal sync persistence path, so refetched pages update `tweet.raw_json` plus normalized `article` / secondary rows directly; no follow-up `tweetxvault rehydrate` is required after the backfill itself
  - Added coverage in `tests/test_sync.py` proving article backfill reaches older duplicate pages and persists article rows, plus a CLI forwarding test in `tests/test_cli.py`

- Landed Task 11 secondary-object extraction on the LanceDB archive:
  - Added `tweetxvault/extractor.py` to normalize raw tweet payloads into canonical `tweet_object`, `tweet_relation`, `media`, `url`, `url_ref`, and `article` records
  - Expanded `tweetxvault/storage/backend.py` schema and page buffering so those secondary rows are written in the same page-atomic merge as `raw_capture`, collection-scoped `tweet`, and `sync_state`
  - Kept collection-scoped `tweet` rows as the membership/export layer while also storing richer global tweet/media/url/article objects
  - Added coalescing semantics on secondary rows so later thinner payloads do not overwrite richer previously captured fields with nulls
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py tests/test_client.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/extractor.py tweetxvault/client/timelines.py tweetxvault/client/features.py tweetxvault/cli.py tests/test_extractor.py tests/test_storage.py tests/test_client.py tests/conftest.py`

- Expanded rehydrate to rebuild normalized rows from stored `tweet.raw_json`:
  - Changed `tweetxvault rehydrate` to rescan all stored tweet rows, refresh canonical tweet fields (including note-tweet text), and rebuild secondary-object rows without refetching
  - Preserved the existing archive-upgrade path for older archives by letting rehydrate backfill secondary rows after the schema migration

- Enabled article field toggles on timeline requests and recorded a concrete article example URL:
  - Switched `withArticleRichContentState`, `withArticlePlainText`, and `withArticleSummaryText` on in `tweetxvault/client/features.py`
  - Added `https://x.com/dimitrispapail/status/2026531440414925307` to `docs/PLAN.md` / `docs/IMPLEMENTATION.md` as the working article validation target for the later article-capture task

## 2026-03-15

- Expanded auth extraction from Firefox-only to browser-family support:
  - Added `tweetxvault/auth/chromium.py` and runtime dependency `browser-cookie3>=0.20,<1` so Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc can supply `auth_token` / `ct0` / `twid`
  - Changed browser auto-resolution order to Firefox -> Chrome -> Chromium -> Brave -> Edge -> Opera -> Opera GX -> Vivaldi -> Arc, stopping at the first browser that yields valid X cookies
  - Added generic browser selection config/env (`auth.browser`, `auth.browser_profile`, `auth.browser_profile_path`, `TWEETXVAULT_BROWSER*`) while keeping `TWEETXVAULT_FIREFOX_PROFILE_PATH` as a legacy compatibility path
  - Added `--browser`, `--profile`, `--profile-path` flags to `tweetxvault sync ...` / `tweetxvault auth check`, plus `tweetxvault auth check --interactive` for an on-demand browser/profile picker
  - Tweaked Firefox auto-pick to prefer install-default/default profiles instead of erroring on every multi-profile setup
  - Validation:
    - `uv lock`
    - `uv run ruff check tweetxvault tests`
    - `uv run ruff format --check tweetxvault/auth/chromium.py tweetxvault/auth/cookies.py tweetxvault/auth/firefox.py tweetxvault/auth/__init__.py tweetxvault/cli.py tweetxvault/config.py tweetxvault/sync.py tests/test_auth.py tests/test_cli.py`
    - `uv run pytest`
  - Result: all checks passed (`42 passed`); repo-wide `uv run ruff format --check tweetxvault tests` still reports an unrelated pre-existing formatting issue in `tweetxvault/storage/backend.py`

- Scoped the next capture-expansion milestone in `docs/PLAN.md` / `docs/IMPLEMENTATION.md`:
  - Added a post-MVP design for canonical `tweet_object` rows plus `tweet_relation`, `media`, `url`, `url_ref`, and `article` record types on the existing single-table LanceDB archive
  - Split future work into concrete follow-on tasks for secondary-object extraction, media downloads, URL unfurls/snapshots, article capture, and X-archive import
  - Reserved a future `tweetxvault import x-archive <zip-or-dir>` ingest path with provenance/idempotency requirements
  - Verified current public X web-bundle signals before locking the spec:
    - `curl -L -sS --max-time 20 https://x.com/?lang=en`
    - `curl -L -sS --max-time 20 https://abs.twimg.com/responsive-web/client-web/main.8575d0ba.js`
    - Result: anonymous bundle still exposed `Likes`, `TweetDetail`, `UserArticlesTweets`, plus article field toggles/features (`withArticleRichContentState`, `withArticlePlainText`, `withArticleSummaryText`, `articles_preview_enabled`)

- Scrubbed a user-specific Firefox profile identifier from the repo before publication:
  - Replaced concrete Firefox profile IDs with generic `profile.*` placeholders in historical planning docs and Firefox profile test fixtures
  - Planned a `git filter-repo` rewrite so the old identifiers are removed from local git history before first public push

- Added FTS search and ONNX-based embeddings:
  - Added `embedding` column (384-dim float32 vector) to archive schema with auto-migration for existing archives
  - Created `tweetxvault/embed.py`: ONNX engine using all-MiniLM-L6-v2 via onnxruntime + tokenizers (~0.7ms/tweet batched, ~18s for 25k tweets on CPU)
  - Added `tweetxvault embed` command: resumes by default (skips already-embedded), `--regen` to clear and redo
  - Added `tweetxvault search <query>`: modes auto/fts/vector/hybrid, defaults to hybrid when embeddings exist
  - FTS uses LanceDB's built-in tantivy index on tweet text column
  - Embedding deps are optional via `[embed]` extra (`uv sync --extra embed`)
  - Added tqdm progress bar to embed command

- Added `--sort` flag to view commands (newest/oldest), fixed sort order bug (was using synced_at as primary key which inverted order)

- Fixed author username extraction: Twitter moved screen_name/name to `core.user_results.result.core` (not just `legacy`), added cascading fallback. Added `tweetxvault rehydrate` to backfill existing archives from stored raw_json.

- Added tqdm progress bars for long-running operations (rehydrate, embed)
  - Made rehydrate use batched merge_inserts (500 rows) instead of per-row updates (~18s vs ~1hr for 25k tweets)

- Fixed LanceDB "Too many open files" errors:
  - Added `ArchiveStore.optimize()` to compact table versions after sync
  - Added `tweetxvault optimize` CLI command for manual compaction
  - Added auto-optimize retry in view/export on too-many-open-files error
  - Raised soft file-descriptor limit to hard limit at CLI startup

- Fixed duplicate detection bug and added `--backfill` flag:
  - Root cause: `has_membership()` did per-tweet `search().where()` queries through LanceDB's vector-search API, which silently returned no results on large archives (25k+ tweets), so duplicate detection never triggered
  - Fix: replaced per-tweet queries with `get_collection_tweet_ids()` that bulk-loads all known tweet IDs for the collection into an in-memory set at sync start
  - Added `--backfill` CLI flag: continues past duplicates without resetting sync state (unlike `--full` which resets state AND skips dedup)
  - Default behavior (no flags) now correctly stops on the first page containing already-archived tweets
  - Added tests: `test_duplicate_detection_stops_sync`, `test_backfill_flag_skips_duplicate_stop`
  - Validation: `uv run pytest` (37 passed), `uv run ruff check`, `uv run ruff format --check` all clean

- Added local inspection/export commands on top of the LanceDB archive:
  - Added `tweetxvault view bookmarks|likes|all` to print recent archived rows in a terminal table
  - Added `tweetxvault export html` to write a local HTML viewer alongside the existing JSON export
  - Normalized collection names so `bookmarks`/`likes` work consistently for user-facing export/view commands
  - Added direct CLI tests covering terminal view output, JSON export aliases, and HTML export output

- Tightened Firefox profile autodiscovery for multi-profile setups:
  - Changed `discover_default_profile(...)` to scan discovered Firefox profiles and auto-pick the only profile that actually contains `x.com` session cookies
  - Added explicit ambiguity/no-cookie errors that list discovered profile paths and point users at `TWEETXVAULT_FIREFOX_PROFILE_PATH` / `auth.firefox_profile_path`
  - Added auth tests covering the real Developer Edition case, ambiguous profile selection, and no-cookie profile listing

- Tightened LanceDB export filtering after post-migration review:
  - Changed `ArchiveStore.export_rows(...)` to push `record_type = 'tweet'` and optional collection filtering into LanceDB instead of materializing the full archive table in Python
  - Added a storage regression test that monkeypatches `table.to_arrow()` to fail so export coverage proves the search path is used
  - Validation: `uv run pytest tests/test_storage.py tests/test_sync.py`, `uv run ruff check tweetxvault tests`

- Landed the LanceDB backend migration in the shipped package:
  - Replaced the SQLite fallback backend with `tweetxvault/storage/backend.py`
  - Removed `tweetxvault/storage/seekdb.py`
  - Switched runtime deps from `pyseekdb` to `lancedb` + `pyarrow`
  - Switched the archive path convention from `archive.sqlite3` to `archive.lancedb/`
- Folded the queued sync/storage cleanup into the migration:
  - Removed the redundant `sync_all` double-preflight by sharing preflight results across collections
  - Returned parsed payloads from `_fetch_and_parse_page(...)` so the sync loop no longer reparses JSON
  - Removed the outer post-loop state commit and kept head-state updates inside backend-managed page persistence
- Extended validation for LanceDB-specific semantics:
  - Added regression coverage for one table-version increment per successful `persist_page(...)`
  - Added export coverage confirming `export_rows(...)` only returns tweet records
  - Updated sync tests to seed collection-scoped duplicates through backend APIs instead of SQLite connection access
- Validation:
  - `uv lock`
  - `uv sync`
  - `uv run ruff format --check tweetxvault tests`
  - `uv run ruff check tweetxvault tests`
  - `uv run pytest`
  - `uv run tweetxvault --help`
  - Result: `28 passed in 1.43s`, lint/format clean, CLI help still works

- Queued follow-on cleanup work into the LanceDB migration plan after architecture review feedback:
  - Use `tweetxvault/storage/backend.py` as the concrete backend module name instead of a backend-specific filename
  - Remove the redundant `sync_all` double-preflight during the migration
  - Fold the final `last_head_tweet_id` write into backend-owned state semantics instead of an outer `commit()`
  - Remove stale `pyseekdb` runtime dependency as part of the backend switch
- Locked the next backend plan to LanceDB:
  - Updated `docs/PLAN.md` to make LanceDB the planned backend, with a single-table `row_key` archive model and LanceDB-native FTS/vector search phases
  - Updated `docs/IMPLEMENTATION.md` with a new active migration task for replacing the shipped SQLite backend
  - Updated `docs/ANALYSIS-db.md` and `docs/README.md` so the docs agree that SQLite is the shipped fallback and LanceDB is the planned next backend
- Spiked pure LanceDB archive viability in `dev/lancedb-test/` instead of the shipped package to keep the experiment isolated:
  - Added `dev/lancedb-test/archive_store.py`, a single-table LanceDB archive prototype keyed by `row_key`
  - Added `dev/lancedb-test/storage_spike.py` to verify current archive semantics against the prototype
  - Added `dev/lancedb-test/search_probe.py` to verify local scalar filter, FTS, and vector-index search behavior
  - Added `dev/lancedb-test/README.md` with repro commands
- Ran the isolated LanceDB spike:
  - `uv run --with lancedb python dev/lancedb-test/storage_spike.py`
  - Result: collection-scoped duplicate detection, sync-state persistence/reset, archive-owner guardrail, export ordering, and page-batched writes all worked
  - Observed that each `persist_page(...)` call advanced the Lance table by exactly one version, supporting the single-table batch-write design
  - `uv run --with lancedb python dev/lancedb-test/search_probe.py`
  - Result: scalar index, FTS index, and vector index all worked locally with filtered queries
- Conclusion from the LanceDB spike:
  - Pure LanceDB looks viable enough for a real migration if we are willing to redesign the archive into a denormalized single-table model
  - This is not a drop-in backend swap for the normalized SQLite schema; it is a storage-model change
  - The lower-risk alternative remains SQLite as source of truth with LanceDB as a search sidecar

## 2026-03-14

- Re-ran the storage spike with full sandbox permissions and added a reproducible harness in [docs/ANALYSIS-db.py](docs/ANALYSIS-db.py):
  - Verified embedded SeekDB now initializes successfully on writable `/home`-backed btrfs paths, so the original failure was not purely POSIX permissions
  - Verified `seekdb.open()` still fails on `/tmp`/`tmpfs` with `not support tmpfs directory`, and behaves as a process-global singleton (`initialized twice` when reopening a different path in the same process)
  - Benchmarked the original Task 0 cold path (`open -> create schema -> insert 1k rows -> query 10`) and recorded the results in `docs/ANALYSIS-db.md`
  - `uv run python docs/ANALYSIS-db.py sqlite` -> total `0.0433s`, max RSS `32328 KB`
  - `uv run python docs/ANALYSIS-db.py seekdb-sql` -> total `3.2295s`, max RSS `1024032 KB`
  - `uv run --with lancedb python docs/ANALYSIS-db.py lancedb` -> total `0.0668s`, max RSS `164792 KB`
  - Re-checked large raw JSON handling with the same harness (`--rows 1 --raw-bytes 200000`): SQLite OK, LanceDB OK, SeekDB OK with `MEDIUMTEXT`
  - Conclusion: SeekDB is functional now on supported filesystems, but still fails the MVP Task 0 threshold badly; SQLite remains the shipped backend and LanceDB is the strongest sidecar candidate if we later want a separate vector store
- Implemented the first end-to-end MVP codebase (`pyproject.toml`, `tweetxvault/`, `tests/`, `uv.lock`):
  - Built Typer CLI commands for `sync`, `auth check`, `auth refresh-ids`, and JSON export
  - Implemented XDG config loading, env/config/Firefox cookie resolution, query-id cache + scraper, async GraphQL client, per-page sync orchestration, and JSON export
  - Added 25 tests covering auth, query-id discovery, HTTP retry/query-id refresh, storage atomicity, incremental/head+backfill resume, `--full` resume, preflight behavior, process locking, partial `sync all`, and first-run UX
- Task 0 storage spike changed the storage backend decision for the working MVP:
  - Reproduced embedded SeekDB failures with `pylibseekdb.open/connect` across repo and non-tmpfs writable paths (`open seekdb failed 4016`, `connect failed 4006`)
  - Switched the implemented backend to SQLite in `tweetxvault/storage/seekdb.py` while preserving the planned schema and atomic page-write semantics
  - Kept raw JSON inline in the DB for the MVP; no sidecar gzip path was needed after the fallback
- Captured current X web artifacts to avoid placeholder query IDs / feature flags:
  - Used `curl` against `https://x.com/?lang=en` plus the current `main`, bookmark shared chunk, and bookmark bundle to extract current `Bookmarks`, `Likes`, `BookmarkFolderTimeline`, `TweetDetail`, and `UserArticlesTweets` query IDs
  - Sourced the committed feature-switch defaults from the same live X page fetch on 2026-03-14
- Verified the repo-level definition of done:
  - `UV_CACHE_DIR=/tmp/uv-cache uv sync --python 3.12`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault --help`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault tests`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check tweetxvault tests`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest`
  - Result: all commands passed; `uv run pytest` finished with `25 passed`

- Additional architecture sweep for idempotency/race risks in planning docs:
  - Found missing single-writer protection; added explicit process-lock requirement and atomic temp-file writes in `docs/PLAN.md`
  - Found a bigger sync-state flaw: bottom cursor cannot be the default starting point for later incremental runs because it only paginates older items; updated PLAN/IMPLEMENTATION to separate head sync from interrupted backfill resume
  - Added archive-owner guardrail so one local DB cannot silently mix data from two different X accounts
  - Added implementation/test requirements for atomic per-page DB commits, lock behavior, interrupted-first-run head+backfill correctness, and atomic raw-json sidecar writes if Task 0 chooses file-backed raw storage
- Final failure-semantics pass:
  - Clarified `auth check` exit-code semantics and `sync all` partial-failure behavior after runtime starts
  - Defined `--limit` as per-collection, not global across `sync all`
  - Defined `--full` reset/resume behavior (reset sync state only, never delete data)
  - Added explicit lock-release and partial-runtime-failure test requirements; removed duplicate “document date sourced” line in IMPLEMENTATION.md
- Critical pass on MVP planning docs after reviewing `docs/PLAN.md` + `docs/IMPLEMENTATION.md` (`git status -sb`, `sed`, `rg`):
  - PLAN.md: added shared preflight semantics (local auth + query-id resolution + lightweight remote probe) so first-run sync/auth-check behavior is explicit
  - PLAN.md: made `sync all` all-or-nothing at preflight time; clarified `--limit` excludes probe requests
  - PLAN.md + IMPLEMENTATION.md: fixed duplicate detection to be collection-scoped via `collections`, not global tweet existence (important because the same tweet can be both bookmarked and liked)
  - IMPLEMENTATION.md: added explicit handling/tests for feature-flag drift (`400`), probe-without-write behavior, and no partial writes when `sync all` preflight fails
- Filled PLAN.md gaps for OOTB first-run experience and rewrote IMPLEMENTATION.md:
  - PLAN.md: documented bearer token constant, env var names, cookie resolution chain, user_id resolution for Likes
  - PLAN.md: added Bookmarks vs Likes endpoint differences (user_id requirement)
  - PLAN.md: added "Sync Loop + Stop Conditions" section (empty page, duplicate detection, --limit, rate limit exhaustion)
  - PLAN.md: specified concrete rate limit defaults (3 retries, 2s base delay, 5min cooldown)
  - PLAN.md: added "First-Run Behavior" section (auto-create dirs, validate auth pre-sync, actionable errors)
  - PLAN.md: clarified --limit = pages, simplified Adapter Boundary (no premature Fetcher protocol)
  - IMPLEMENTATION.md: full rewrite — renamed phases to "Task N" (avoid collision with PLAN phases), merged config+auth into one task, added spike exit criteria/fallbacks with timeboxes, dropped Fetcher Protocol from MVP, added end-to-end integration test task
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
