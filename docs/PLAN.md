# tweetxvault — Implementation Plan

## Goal

Build a Python tool for regular, unattended export of Twitter/X bookmarks and likes into a local embedded database with:
- raw GraphQL capture (never lose data)
- incremental sync with checkpoint/resume
- local search (full-text + semantic) and eventually hybrid search
- first-class media archival (images + video + GIF) as a later phase
- a future offline ingest path for downloaded X account archives

Implementation note (2026-03-15):
- Task 0 ruled out embedded SeekDB for the MVP on runtime/footprint grounds.
- The first working MVP ships on SQLite as a safe fallback.
- A pure LanceDB backend spike on 2026-03-15 validated the archive/search semantics well enough that the next planned implementation step is to migrate from SQLite to LanceDB before real archive data is loaded.

This is part of the broader [attention-export](~/github/lhl/attention-export) system.

## Scope and Non-Goals

### MVP (what we will implement first)

- Direct GraphQL API sync for `Bookmarks` and `Likes` using browser cookies
- Query ID (query hash) auto-discovery from Twitter web JS bundles with TTL cache + static fallback IDs
- Rate-limit handling with exponential backoff + cooldown
- Local embedded storage:
  - append-only raw page captures
  - upserted per-tweet records
  - collection membership (like/bookmark) and sync checkpoints
- Minimal exports (JSON first; CSV/Markdown later)

### Explicitly out of scope for MVP

- Playwright-based scraping CLI (we will keep the architecture ready for it, but not implement it now)
- Media downloads
- HTML export UI
- Extended collections (tweets/reposts/replies/feed) beyond likes/bookmarks
- Multi-account support
- Archive-import implementation beyond the current requirements/fixture analysis (fresh sample cataloged on 2026-03-16; importer still pending)

## Why Build From Scratch

Several open-source Twitter/X exporters exist (see `reference/README.md`). We build from scratch because:

- Our differentiators (LanceDB-backed search/embeddings, media archival, attention-export integration) cut across every layer. Adapting any existing tool would become a long-lived divergence.
- We want async-first architecture (httpx) with a clean adapter boundary (Playwright as optional fallback, not a core dependency).
- The hard reverse-engineering problems (query ID discovery, feature flags, cursor formats) are well-documented across multiple open-source projects. We learn from their empirical findings without inheriting their architectural decisions.

The `reference/` directory contains third-party snapshots for study — see `reference/README.md` for the full inventory.

## Key Decisions (Locked In)

These are our architectural choices, made to serve tweetxvault's goals (unattended sync, raw data preservation, embedded search, attention-export integration). They are independent decisions, not inherited from any existing tool.

- **DB**: the shipped backend is pure LanceDB. The archive uses a denormalized single-table model that preserves page-atomic sync semantics while unlocking local FTS/vector search.
- **Storage module shape**: use `tweetxvault/storage/backend.py` as the concrete backend module. The LanceDB design changes storage semantics enough that pretending we have a thin interchangeable database adapter would be misleading; keep one real backend implementation behind the `ArchiveStore` API.
- **Primary capture approach**: Direct GraphQL API calls (httpx, async), not Playwright interception.
- **Query IDs**: Auto-discover query IDs from Twitter web JS bundles with an on-disk TTL cache + fallback static IDs (avoid manual weekly updates).
- **Rate limiting/backoff**: Exponential backoff and cooldown on repeated `429` (parameters adjustable).
- **Auth**: Cookie-based session auth (no username/password automation).
  - Shipped: env vars + config file + Firefox extraction + Chromium-family extraction (Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, Arc).
  - Auto mode tries browsers in a fixed order and stops after the first valid X session; CLI flags and `auth check --interactive` provide explicit profile selection.
- **CLI framework**: Typer + Rich (keep it minimal; no sprawling command surface).
- **Long-running CLI UX**: any command that can spend more than a few seconds hashing archives, scanning local state, or waiting on network retries must emit immediate startup feedback plus phase/progress updates on interactive TTY runs; silent long-running work is not acceptable. Non-interactive runs (cron/pipes) should stay quiet by default apart from warnings/errors and final summaries.
- **Data models**: Pydantic v2 for boundary types (config, parsed tweet records, sync state); raw JSON stored as-is in DB.
- **Logging**: loguru.
- **Project tooling**: uv (package management), ruff (lint + format), hatchling (build backend).
- **Playwright**: Reserved as a future fallback adapter (debugging/CAPTCHA/JS challenge), not implemented in MVP.

## Twitter API Reverse Engineering

The Twitter/X web app uses an undocumented internal GraphQL API. Several areas require reverse-engineered knowledge. We consult existing open-source tools in `reference/` as **empirical data sources for Twitter’s API behavior**, not as architectural or code references.

> **Rule**: if the only rationale for a design choice is “another tool does it this way”, stop and justify it against our own requirements or delete it. Reference code is for understanding Twitter’s undocumented API, not for copying implementation patterns.

Key areas where prior art provides useful empirical data:

1. **Query ID rotation** — Twitter periodically rotates GraphQL query hashes. Known approaches in the wild:
   - JS bundle parsing + regex extraction (TweetHoarder, bird) — robust, automated
   - XHR interception with operation-name regex matching (twitter-web-exporter) — never hardcodes hashes, but requires in-browser execution
   - Hardcoded hashes from DevTools (twitter-likes-export, twitter-advanced-scraper) — simple but breaks on rotation
2. **Feature flags** — Each operation expects ~60 feature flags; wrong flags return 400. Empirical flag sets can be sourced from our own browser DevTools captures or cross-referenced against `reference/` snapshots.
3. **Cursor formats** — Timeline responses use nested instruction entries (`cursor-bottom-*`, `tweet-*` entry IDs). Response shapes vary between operations.
4. **Required headers** — The internal API expects specific headers (`Authorization`, `x-csrf-token`, `x-twitter-auth-type`, etc.) observable in any browser’s DevTools network tab.

## Architecture

### Package Layout (Planned)

```
tweetxvault/
├── tweetxvault/
│   ├── __init__.py
│   ├── config.py              # XDG paths, config file, constants
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── firefox.py         # Firefox cookies.sqlite reader (copy-to-temp)
│   │   ├── chromium.py        # Chromium-family extraction + profile discovery
│   │   └── cookies.py         # Resolution chain (env -> config -> browsers)
│   ├── query_ids/
│   │   ├── __init__.py
│   │   ├── constants.py       # fallback query IDs + target ops
│   │   ├── scraper.py         # bundle discovery + regex extraction
│   │   └── store.py           # TTL cache on disk
│   ├── client/
│   │   ├── __init__.py
│   │   ├── base.py            # httpx client, headers, error classification
│   │   ├── features.py        # per-operation feature flag builders
│   │   └── timelines.py       # URL builders, fetch_page(), parse_page()
│   ├── storage/
│   │   ├── __init__.py
│   │   └── backend.py         # concrete archive backend (planned: LanceDB)
│   ├── export/
│   │   ├── __init__.py
│   │   └── json_export.py
│   ├── sync.py                # orchestrates sync loops (likes/bookmarks)
│   └── cli.py                 # CLI entry point
└── tests/
```

### Adapter Boundary

The MVP sync loop calls the GraphQL client directly — no abstraction layer needed with only one implementation. If we later add Playwright as a fallback fetcher, we can introduce a `Fetcher` protocol at that point.

### Data Flow

```
cookies (env/config/browser extraction)
  -> auth (auth_token + ct0 + twid)
      -> query_ids (cache -> refresh -> fallback)
          -> client (httpx)
              -> store raw captures (append)
              -> parse tweets + cursors
                  -> upsert tweets + collections
                      -> update checkpoints
```

### Concurrency Model

MVP assumes a single local account archive and **exactly one writer process at a time**.

- `sync` and export commands that mutate local state must acquire a process lock in the XDG data dir before touching the DB or checkpoint state.
- If another sync is already running, exit quickly with a clear message instead of attempting concurrent writes.
- Cache/config writes must use atomic temp-file + rename semantics so `auth check` / `refresh-ids` cannot leave partially written JSON/TOML behind.
- DB page persistence should use one transaction per fetched page so checkpoints never advance ahead of durable tweet/membership writes.
- If raw JSON is stored as gzipped sidecar files instead of inline DB blobs, write the file via temp-file + rename and only commit the DB pointer after the file is durable.

## Direct GraphQL API Design

### Auth + Headers (MVP)

We rely on browser session cookies:
- `auth_token` (session)
- `ct0` (CSRF; also sent as `x-csrf-token`)
- `twid` (contains numeric user id as `u%3D<id>`; required for Likes endpoint)

Cookie resolution chain (in priority order):
1. **Env vars**: `TWEETXVAULT_AUTH_TOKEN`, `TWEETXVAULT_CT0`, `TWEETXVAULT_USER_ID` (numeric)
2. **Config file**: `~/.config/tweetxvault/config.toml` (`[auth]` section)
3. **Browser extraction**:
   - Firefox: inspect discovered Firefox profiles, prefer install-default/default profiles, and copy `cookies.sqlite` to temp before reading
   - Chromium-family browsers: use `browser-cookie3` for cookie decryption/keyring access across Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc
   - Auto mode tries browsers in this order: Firefox -> Chrome -> Chromium -> Brave -> Edge -> Opera -> Opera GX -> Vivaldi -> Arc
   - Explicit selection is available via config/env (`auth.browser`, `auth.browser_profile`, `auth.browser_profile_path`) and CLI flags (`--browser`, `--profile`, `--profile-path`)
   - When `--browser`/browser overrides are used, they force cookie sourcing (`auth_token` / `ct0`) from that browser/profile, but explicit env/config `user_id` remains a valid fallback for Likes/UserTweets

User ID resolution (needed for Likes only):
- Parsed from `twid` cookie (`u%3D<numeric_id>` → `<numeric_id>`)
- Or set explicitly via `TWEETXVAULT_USER_ID` env var or `user_id` in config
- If user_id can't be resolved, `sync likes` fails with actionable error; `sync bookmarks` works fine

Firefox cookie extraction rules (Linux):
- Always copy `cookies.sqlite` to a temp file before reading (Firefox often holds WAL locks on the live DB)
- Open the copied DB in read-only mode (`mode=ro`) via sqlite URI
- Never log raw cookie values; treat them as secrets

Chromium-family extraction rules:
- Delegate cookie decryption and OS keyring handling to `browser-cookie3` instead of carrying our own per-OS crypto implementation
- Keep browser ordering and profile-selection UX inside tweetxvault so `auth check --interactive` and sync flags stay consistent across browsers

Required request headers:
- `Authorization: Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA` (public web bearer token — same static constant for all users, hardcode it)
- `x-csrf-token: <ct0 cookie value>`
- `x-twitter-active-user: yes`
- `x-twitter-auth-type: OAuth2Session`
- `x-twitter-client-language: en`
- Standard browser-like `User-Agent`, `Referer: https://x.com`, `Origin: https://x.com`

### Operations (Phase 1)

Both operations use `GET https://x.com/i/api/graphql/{query_id}/{OperationName}?variables=...&features=...`

- **`Bookmarks`** — authenticated user’s bookmarks. No user_id needed. Variables: `{count, includePromotedContent, ...}`
- **`Likes`** — requires `userId` in variables. Variables: `{userId, count, includePromotedContent, ...}`

Default page size: 20 tweets per request.

Recommended additional operations to include in query-id targets even if we don’t sync them yet:
- `BookmarkFolderTimeline` (folders)
- `UserArticlesTweets` (probe later)
- `TweetDetail` (thread/context or media variants later)

### Query ID Auto-Discovery

- Maintain `FALLBACK_QUERY_IDS` for the target operation set.
- Maintain a disk cache file (JSON) with:
  - `fetched_at` (UTC)
  - `ttl_seconds` (24h)
  - `ids` mapping `{operation_name: query_id}`
- Refresh behavior:
  - On startup, use cached IDs if fresh.
  - If missing/stale, fetch a discovery page (e.g., `https://x.com/?lang=en`), extract bundle URLs matching the `abs.twimg.com/responsive-web/client-web/*.js` regex, fetch bundles, extract operationName/queryId pairs via regex patterns.
  - If refresh fails but we still have fallback IDs for the requested operations, continue with a warning instead of failing first-run sync.
  - If an API call returns `404`, treat it as “stale query id”, refresh once, retry.
  - Fail only when neither cache nor fallback can satisfy the requested operation, or when the post-refresh retry still returns `404`.
  - Provide `tweetxvault auth refresh-ids` to force refresh.
- Verification note (2026-03-15): the public anonymous web bundle still exposed live `Likes`, `TweetDetail`, and `UserArticlesTweets` query IDs plus article field toggles, but not `Bookmarks` / `BookmarkFolderTimeline`. Keep fallback IDs for auth-only operations even if anonymous bundle scraping misses them.

### Feature Flags

Keep per-operation builders, not a single shared dict. Implementation plan:
- Start from a known working flag set (captured from our own browser; prior art only as cross-check) and commit those static per-operation dicts in code for MVP.
- MVP does **not** attempt automatic feature-flag discovery.
- If requests begin returning `400` after auth and query IDs look healthy, treat it as likely feature drift and fail with an actionable error instead of retrying indefinitely.

### Preflight + Validation

Before any sync writes data:
- Resolve local auth inputs (env → config → browser extraction).
- Resolve query IDs (cache → refresh → fallback).
- Resolve the current archive owner identity (numeric user id from `twid` / config / env when available).
- Run a lightweight authenticated probe for each requested collection (`count=1`, no checkpoint updates, no `raw_captures` write) so we distinguish missing credentials from expired sessions, stale query IDs, or feature-flag drift.
- Validate the resolved owner identity against local DB metadata if the archive already exists; refuse to mix two X accounts into one archive.

Command semantics:
- `tweetxvault auth check` runs this shared preflight without creating or writing the DB; it reports which collections are ready.
- `tweetxvault auth check` exit codes: `0` = all probed collections ready, `1` = local auth/config input missing or inconsistent, `2` = remote/API validation failed for at least one probed collection.
- `tweetxvault sync all` is **all-or-nothing at preflight time**: if bookmarks or likes fail validation, abort before syncing either collection.
- Once `tweetxvault sync all` has started syncing, it is **not** cross-collection atomic: if bookmarks finish and likes later fail, bookmark writes remain committed and the command exits non-zero with a partial-failure summary.
- `--limit N` counts persisted sync pages only; preflight probes do not consume the limit.

### Pagination + Cursor Extraction

Per-operation parsers that:
- find tweet entries (`entryId` prefix `tweet-...`)
- find bottom cursor entries (`entryId` prefix `cursor-bottom-...`) and return cursor value
- extract `sortIndex` when available so we can preserve timeline order

### Sync Loop + Stop Conditions

Each `sync` invocation runs a page-at-a-time loop. Important distinction:

- **Head sync**: always starts with `cursor=None` to fetch newest items first.
- **Backfill resume**: if a previous run stopped before reaching the historical tail, continue from stored `backfill_cursor` *after* the head pass so we do not miss new items added since the interruption.

This means `sync_state` is **not** “start next run from the last stored cursor.” A bottom cursor only paginates older results, so using it as the default starting point would skip newly added likes/bookmarks.

Per collection, persist:
- `last_head_tweet_id` (or equivalent top-of-timeline anchor) for normal incremental stop detection
- `backfill_cursor` (nullable) for interrupted history scans / first-run continuation
- `backfill_incomplete` flag (or equivalent derived state)

`--full` semantics:
- Reset `sync_state` for the targeted collection(s) only after preflight succeeds and the process lock is acquired.
- Do **not** delete existing `tweets`, `collections`, or `raw_captures`; full sync is an idempotent re-walk implemented via upserts plus new append-only raw captures.
- If a `--full` run is interrupted, leave `backfill_incomplete` / `backfill_cursor` set so a later run can resume safely.

1. Run shared preflight for the requested collection(s)
2. Acquire the process lock
3. Run a head pass from `cursor=None`
4. If `backfill_incomplete` is set, continue an older-history pass from stored `backfill_cursor`
5. For each fetched page:
   a. Fetch one page with current cursor
   b. Parse tweet entries and bottom cursor
   c. In one DB transaction: append raw response to `raw_captures`, upsert tweets, upsert collection memberships, update sync state
   d. Check stop conditions (see below)
   e. Polite delay between pages (default 2s, configurable)
6. Release the process lock (always, via `finally`)
7. Print summary (tweets synced, pages fetched)

Stop conditions:
- **Empty page**: response contains zero tweet entries → done
- **Head-pass duplicate detection**: encountered a tweet that already has membership in the same collection (and folder, when relevant) from a previous sync → stop the head pass (caught up to known items)
- **Backfill completion**: stored `backfill_cursor` becomes `null` / no further cursor → historical scan is complete
- **`--full` mode**: ignores duplicate detection, only stops on empty page (full re-scan)
- **`--limit N`**: stop after N persisted pages **per collection** (for testing or cautious first runs)
- **Rate limit exhaustion**: after cooldown, if still getting 429s, stop gracefully and preserve checkpoint for resume

### Rate Limiting / Backoff

Defaults (configurable via `config.toml`):
- **Max retries per request**: 3
- **Base delay**: 2 seconds (exponential: `base_delay * 2^attempt`)
- **Consecutive 429 cooldown threshold**: 3 (after 3 consecutive 429s, enter cooldown)
- **Cooldown duration**: 5 minutes
- **Inter-page delay**: 2 seconds (polite pause between successful page fetches)

Behavior:
- For `429`: retry with exponential backoff up to max retries, then enter cooldown. After cooldown, resume. If cooldown fails again, stop gracefully and preserve checkpoint.
- For `401/403`: fail fast with actionable error (cookies expired, missing ct0, account locked, etc.).
- For `404` on a known operation: refresh query IDs once, retry. If still 404, fail with "query ID refresh failed" error.
- For any non-retryable or exhausted failure after sync has started: the last fully committed page remains durable, the in-progress page does not advance `sync_state`, and the command exits non-zero.

## Storage

See [ANALYSIS-db.md](ANALYSIS-db.md) for the full DB comparison and schema thoughts.

Current implementation status:
- The shipped backend stores data in LanceDB in `tweetxvault/storage/backend.py`.
- Archive state is expressed as a single `archive` table keyed by `row_key`.
- The source of truth below is the live LanceDB archive model used by the package.

### Minimal Archive Model

- Use a **single LanceDB table** named `archive`, keyed by `row_key`.
- Every row also carries `record_type`, which determines which subset of columns is meaningful.
- This is intentionally denormalized. The goal is to express one fetched page as **one LanceDB `merge_insert` batch**, so page persistence advances the archive by one table version or not at all.

- `raw_capture` rows:
  - `row_key = raw_capture:{uuid}`
  - `record_type = "raw_capture"`
  - `operation`, `cursor_in`, `cursor_out`
  - `captured_at`, `http_status`, `source`
  - `raw_json`

- `tweet` rows:
  - `row_key = tweet:{collection_type}:{folder_id}:{tweet_id}`
  - `record_type = "tweet"`
  - `tweet_id`, `collection_type`, `folder_id`
  - `sort_index`
  - `source`
  - `text`
  - `author_id`, `author_username`, `author_display_name`
  - `created_at`
  - `deleted_at` (nullable; used for archive-imported deleted authored tweets)
  - `raw_json`
  - `first_seen_at`, `last_seen_at`
  - `added_at`, `synced_at`
  - These rows are the source of truth for collection-scoped duplicate detection; do **not** use global tweet existence for incremental stop logic.

- `sync_state` rows:
  - `row_key = sync_state:{collection_type}:{folder_id}`
  - `record_type = "sync_state"`
  - `collection_type`, `folder_id`
  - `last_head_tweet_id`
  - `backfill_cursor`
  - `backfill_incomplete`
  - `updated_at`

- `metadata` rows:
  - `row_key = metadata:{key}`
  - `record_type = "metadata"`
  - `key`, `value`
  - `updated_at`

- `import_manifest` rows (planned for Task 16 archive import):
  - `row_key = import_manifest:{archive_digest}`
  - `record_type = "import_manifest"`
  - `archive_digest`, `archive_generation_date`
  - `import_started_at`, `import_completed_at`, `status`
  - `warnings_json`, `counts_json`
  - Used for resumable/idempotent archive imports; live GraphQL sync does not write these rows.

- Page persistence rules:
  - One persisted page issues one LanceDB `merge_insert` batch containing:
    - one new `raw_capture` row
    - all touched `tweet` rows for that page
    - the updated `sync_state` row
  - If anything fails **before** the batch write, no partial state should be left behind.
  - The "latest head tweet id" update must live inside backend-managed state semantics, not as a separate outer commit after the sync loop.
  - `export` reads only `tweet` rows and reconstructs the existing JSON export shape from them.

### Migration Cleanup Results

The backend migration also closed the main storage/sync cleanup items identified during review:

- Removed `pyseekdb` from runtime dependencies and replaced the shipped backend with LanceDB.
- Eliminated the redundant double-preflight path in `sync_all`, so bookmarks/likes are probed once per run after shared preflight.
- Moved the final head-state update into backend-owned page persistence semantics instead of issuing a separate outer `commit()`.
- Renamed the concrete storage implementation to `tweetxvault/storage/backend.py`, matching the fact that we carry one real backend rather than a generic SQL adapter.
- Folded the duplicate `response.json()` parse in the sync loop into `_fetch_and_parse_page(...)` by returning the parsed payload along with the tweets/cursor.
- Left `_iter_entries(...)` alone; it remains acceptable until profiling proves otherwise.

### Embeddings (Phase 3)

- Use LanceDB indexes instead of a second search engine:
  - FTS index on tweet text
  - scalar indexes on common filter fields (`collection_type`, later `author_username` / `created_at` if needed)
  - vector index on tweet embeddings
- Add an `embedding` column to `tweet` rows once the embedding pipeline is implemented.
- Prefer LanceDB hybrid search (FTS + vector + metadata filters, with reranking as needed).
- Lock the embedding model during the Phase 3 spike; the backend no longer depends on a database-provided built-in model.

## Content Expansion (Post-MVP)

The current archive preserves raw page captures and collection-scoped tweet rows, which is enough to avoid data loss. The next layer is to normalize secondary objects so exports, search, and later download jobs can reason about media, attached tweets, article bodies, and URLs without reparsing every raw payload.

### Capture Principles

- Keep raw GraphQL payloads as the source of truth. If extraction is incomplete or Twitter changes shape again, we can rehydrate from stored `raw_json`.
- Preserve the current collection-scoped `tweet` rows for duplicate detection, ordering, and export compatibility.
- Add global secondary-object rows keyed by stable tweet/media/url identifiers so the same tweet bookmarked and liked does not duplicate downstream media/unfurl metadata.
- Keep sync page persistence page-atomic. Media downloads, URL fetches, and snapshots should run as follow-on jobs, not inline in the GraphQL request transaction.

### Proposed Extended Archive Model

Extend the single-table LanceDB archive with additional `record_type` values:

- `tweet_object`
  - `row_key = tweet_object:{tweet_id}`
  - Canonical snapshot for the underlying tweet object across collections.
  - Stores the latest raw tweet object plus normalized text fields that are global to the tweet (`text`, `created_at`, author fields, later `conversation_id`, `lang`, note-tweet text if present).
  - Carries `source` for the current winning normalized snapshot plus nullable `deleted_at` when the only surviving payload comes from the official archive.
  - Archive-seeded sparse placeholders should also track:
    - `enrichment_state` (`pending`, `done`, `transient_failure`, `terminal_unavailable`)
    - `enrichment_checked_at`
    - `enrichment_http_status`
    - `enrichment_reason` (`deleted`, `suspended`, `not_found`, etc.)
  - Consumers should prefer this row over collection-scoped `tweet.raw_json` once it exists, but the existing `tweet` row remains the membership/projection layer.

- `tweet_relation`
  - `row_key = tweet_relation:{source_tweet_id}:{relation_type}:{target_tweet_id}`
  - `relation_type` initially: `retweet_of`, `quote_of`.
  - Lets us archive attached tweets without pretending they are independent bookmark/like memberships.

- `media`
  - `row_key = media:{owner_tweet_id}:{media_key_or_index}`
  - Holds media metadata extracted from the tweet object or attached tweet object:
    - `media_type` (`photo`, `video`, `animated_gif`)
    - image/video URLs, poster URL, width/height, duration, variant list
    - download state (`pending`, `done`, `failed`), local path, SHA-256, byte size, content type

- `url`
  - `row_key = url:{canonical_url_hash}`
  - Global canonical URL metadata:
    - canonical URL, final expanded URL, host/domain
    - title/description/site-name when known
    - snapshot status for future ArchiveBox integration

- `url_ref`
  - `row_key = url_ref:{tweet_id}:{position}`
  - Per-tweet URL occurrence:
    - original `t.co` URL, display URL, expanded/unwound URL
    - resolved canonical URL hash pointing at the global `url` row

- `article`
  - `row_key = article:{source_tweet_id}` until a stable article-level ID is confirmed
  - Stores article-specific content when available:
    - title, summary, plain text, rich-content JSON, article URL/permalink
    - extraction status (`body_present`, `preview_only`, `shape_unverified`)

### Articles

Current verification from the public X web client on 2026-03-15:

- `UserArticlesTweets` is still present as a GraphQL operation.
- Article-related field toggles still exist in the web client: `withArticleRichContentState`, `withArticlePlainText`, `withArticleSummaryText`.
- Article-related features still exist in the web client: `articles_preview_enabled`, `responsive_web_twitter_article_tweet_consumption_enabled`.
- Working example tweet for article validation once we have an authenticated capture: `https://x.com/dimitrispapail/status/2026531440414925307`

Authenticated verification on 2026-03-16:

- `TweetDetail` for `https://x.com/dimitrispapail/status/2026531440414925307` returned:
  - full article `plain_text` (17,308 chars)
  - `content_state`
  - `cover_media`
  - `media_entities`
- We captured a trimmed real fixture at `tests/fixtures/dimitris_article_tweet_detail.json`.

Design implications:

- Treat articles as first-class tweet-adjacent objects, not just another expanded URL.
- Add an article-specific parser path for:
  - article-bearing tweet payloads returned by bookmarks/likes when article field toggles are enabled
  - dedicated article timelines fetched through `UserArticlesTweets`
- Older archives can be refreshed through a timeline rewalk mode (`tweetxvault sync ... --article-backfill`) before we add a narrower tweet-level article fetch path.
- Key `article` rows by source tweet id and preserve the full raw tweet/article block for later migration.
- The current targeted refresh path is authenticated `TweetDetail` (`tweetxvault articles refresh`); no Playwright-only article fallback is needed unless `TweetDetail` regresses back to preview-only payloads later.

### Attachments, Media, and Attached Tweets

Parse and persist the following from the tweet object and one level of attached tweet objects:

- `legacy.extended_entities.media`
- `video_info` variants and poster/thumb URLs
- `note_tweet` long-text payloads
- `retweeted_status_result`
- `quoted_status_result`

Rules:

- A bookmarked/liked retweet remains a membership on the wrapper tweet. The original tweet becomes a related `tweet_object` linked by `tweet_relation`.
- Download policy should be staged:
  - Phase 1: extract metadata only
  - Phase 2: download photo originals
  - Phase 3: download best video/GIF variant plus poster image
- Media found on quoted/retweeted tweets should be attributable to the attached tweet object, not flattened onto the wrapper tweet without a relation.

### URL Unfurls and Snapshots

Unfurling should use GraphQL payloads first and network fetches second.

- Extract URL candidates from `legacy.entities.urls`, card/unwound URL fields, and other explicit expanded-URL fields present in the tweet object.
- Store both:
  - per-tweet URL mentions (`url_ref`)
  - canonical URL metadata (`url`)
- Canonicalization should normalize host casing, strip default ports, and remove obvious tracking parameters (`utm_*`, etc.) while preserving semantically meaningful query strings.
- Future follow-on jobs can:
  - fetch page metadata when GraphQL does not already provide it
  - enqueue/snapshot canonical URLs in ArchiveBox or an equivalent archiver
  - index URL/domain/title fields for search and filtering
- The initial runner surface is inline CLI commands:
  - `tweetxvault media download`
  - `tweetxvault unfurl`

### Archive Import Requirements

We want a second ingestion path for downloaded X account archives. A fresh sample is available locally in `data/`; see `docs/ANALYSIS-archive-import.md` for the concrete file inventory and merge notes.

- Reserve a future CLI surface:
  - `tweetxvault import x-archive <zip-or-dir>`
- Treat archive import as an ingestion path parallel to live GraphQL sync, not as a one-off converter.
- Imported rows must preserve provenance (`live_graphql` vs `x_archive`) and be idempotent/resumable.
- Archive import should map into the same `tweet_object`, collection-scoped `tweet`, `media`, `url`, and `article` rows so search/export code does not care where data came from.
- The objective is the most complete local archive possible from the union of both sources; archive import seeds the archive, and live GraphQL follow-up enriches any still-available rows.
- Never delete or downgrade richer live-captured data when importing thinner archive data later.
- Record one dedicated `import_manifest` row per imported archive digest (generation date, started/completed timestamps, status, warnings, counts) so repeated imports can short-circuit safely.
- Archive import must apply the same archive-owner guardrail as live sync by validating `account.js.account.accountId` against stored owner metadata before writing.

Current sample-driven scope:

- Overlap confirmed in this sample:
  - `tweets.js` / `tweet-headers.js` for authored tweets
  - `deleted-tweets.js` / `deleted-tweet-headers.js` for deleted authored tweets
  - `like.js` for likes
  - `tweets_media/` for exported authored-tweet media binaries
- Not present in this sample:
  - no bookmark dataset in `manifest.js` or `data/`
- Present but empty in this sample:
  - `article.js`
  - `article-metadata.js`
  - `note-tweet.js`
  - `community-tweet.js`

Concrete merge rules from the first real fixture:

- `tweets.js` rows are close enough to Twitter legacy tweet payloads that archive import should adapt them into the existing tweet/extractor path rather than inventing a second tweet model.
- `like.js` rows are intentionally much thinner (`tweetId`, `fullText`, `expandedUrl` only), so likes import should create sparse collection/provenance rows plus sparse global tweet placeholders, then let later live sync upgrade tweet metadata when available.
- `tweets_media/` should be copied into the managed tweetxvault media layout during import, then registered against `media` rows via `local_path` / `download_state`.
- Live GraphQL stays authoritative for richer normalized tweet/media/url/article metadata when both sources overlap; archive data fills null gaps and covers deleted/offline-only content.
- Because the current extractor/storage coalescing behavior prefers the newest non-empty value, archive import must add explicit source-aware merge logic inside `ArchiveStore` instead of relying on naive re-use of the existing upsert path from the caller side.
- For backward compatibility, existing rows with `source = NULL` should be treated as `live_graphql` in precedence logic until an explicit backfill/migration populates them.
- Reuse one generic `parse_ytd_js(...)` loader for `window.YTD.*` files, then map specific files through small adapters.
- Import `like.js` with a stable synthetic archive-order `sort_index` encoded as negative numeric strings (`-1`, `-2`, ... in file order) so the values stay compatible with current integer-based sorting and fall behind real live timeline sort keys in newest-first views.
- Import deleted authored tweets into the normal `tweets` collection membership and surface nullable `deleted_at` on both the membership `tweet` row and the normalized `tweet_object` row rather than inventing a separate tombstone collection.
- Do not perform an unconditional per-item GraphQL fetch inline during import; instead, run normal bulk collection syncs first, then targeted per-item lookups only for rows that remain sparse after import.
- Shipped import behavior: if auth is available, `tweetxvault import x-archive ...` runs the bulk `tweets` / `likes` follow-up automatically, but explicit per-item `TweetDetail` lookups stay operator-bounded via `--detail-lookups` (default `0`) so large archives do not fan out into an unbounded reconciliation crawl.
- Track per-tweet live-enrichment status/result so explicit terminal misses stop retrying; only item-level lookup failures should mark `terminal_unavailable`.
- Absence from a later live likes/bookmarks collection does **not** by itself mean the tweet is unavailable or that archive provenance should be removed.

### Parser Boundary

Do not add a generic fetcher abstraction just to support archive import. The current sync loop can stay GraphQL-specific. Instead:

- keep one parser/extractor layer that turns a raw tweet object into normalized `tweet_object` / relation / media / URL / article records
- let both live sync and future archive import call that extractor layer

## CLI Design

Primary commands:

```
tweetxvault sync bookmarks            # incremental
tweetxvault sync likes                # incremental
tweetxvault sync tweets               # your own authored tweets
tweetxvault sync all                  # preflight both, then sync bookmarks + likes
tweetxvault sync all --full           # full resync (ignore duplicates)
tweetxvault sync bookmarks --limit 5  # stop after 5 pages (testing)

tweetxvault view bookmarks            # show recent bookmarks in the terminal
tweetxvault view likes                # show recent likes in the terminal
tweetxvault view tweets               # show your authored tweets in the terminal

tweetxvault export json               # (phase 2+) export all collections
tweetxvault export html               # export a local HTML viewer
tweetxvault media download            # fetch archived media files
tweetxvault unfurl                    # fetch final/canonical URL metadata
tweetxvault articles refresh          # refresh article-bearing tweets via TweetDetail
tweetxvault threads expand            # capture parent/context tweets + linked status URLs
tweetxvault threads expand --refresh TWEET_ID  # re-fetch an explicit target

tweetxvault auth check                # run shared preflight, report local + remote readiness
tweetxvault auth refresh-ids          # force query id refresh

tweetxvault import x-archive ARCHIVE  # zip or extracted directory
tweetxvault import x-archive ARCHIVE --detail-lookups 100
```

`--limit N` limits persisted sync pagination to N pages per collection (useful for testing or cautious first runs).

### First-Run Behavior

On first invocation, tweetxvault:
1. Auto-creates XDG directories (`~/.config/tweetxvault/`, `~/.local/share/tweetxvault/`, `~/.cache/tweetxvault/`)
2. Attempts cookie resolution (env vars → config file → browser extraction)
3. If no cookies found: prints clear error with setup instructions (which env vars to set, or ensure a supported browser is logged into x.com)
4. If cookies found: resolves query IDs (first run has no cache, so fetches from JS bundles and falls back to static IDs if refresh fails)
5. Runs a lightweight API probe for the requested collection(s); on failure, exits with actionable error before any checkpoint or DB writes
6. Acquires the local process lock so overlapping cron/manual runs cannot race
7. Auto-creates DB on first write, records archive owner metadata, and begins sync

No `init` command needed — everything auto-creates on demand. Config file is optional; the tool works with browser cookies or env vars. `tweetxvault auth check` uses the same preflight path as `sync`, and `tweetxvault auth check --interactive` gives a manual browser/profile picker before any data is written.

Reserved for future (not implemented in MVP):
- `tweetxvault sync ... --playwright` (adapter fallback)

## Feature Roadmap

### Phase 1: Core Sync (MVP)

- [x] Auth extraction (env vars, config file, Firefox + Chromium-family browsers)
- [x] Query ID auto-discovery + fallback + TTL cache
- [x] GraphQL client (httpx async) + per-operation feature flags
- [x] Bookmarks sync
- [x] Likes sync
- [x] Append raw captures + upsert tweets + collections
- [x] Checkpoint/resume for interrupted sync
- [x] Single-process locking + atomic page commits
- [x] Rate limit handling (backoff + cooldown)
- [x] Minimal JSON export (optional but helpful early)

### Phase 2: Export + Media Foundations

- [x] Export: JSON
- [x] Terminal view command
- [x] HTML export viewer
- [ ] Export: CSV / Markdown
- [x] Add canonical `tweet_object` rows alongside collection-scoped membership rows
- [x] Extract media metadata to DB (types, dimensions, variants, note-tweet text)
- [x] Extract attached tweet relations (retweets, quotes)
- [x] Extract URL metadata / per-tweet URL refs
- [x] Media download (photos first; video later)

### Phase 3: Search + Embeddings

- [x] Embeddings (local model + LanceDB vector column)
- [x] Semantic search CLI
- [x] Hybrid search (LanceDB FTS + vector + metadata)
- [ ] Similar tweet lookup
- [ ] Search/filter by URL/domain/media/article metadata

### Phase 4: Extended Collections + Polish

- [ ] Bookmark folders
- [x] Own tweet timeline sync (`UserTweets`)
- [x] Thread/context expansion (`TweetDetail`) for archived tweets
- [x] Linked X-status URL expansion
- [x] Articles capture / export (`UserArticlesTweets` + article-bearing tweet payloads)
- [ ] URL snapshot queue / ArchiveBox integration
- [ ] Following/followers lists
- [x] HTML export viewer
- [ ] X archive import (`tweetxvault import x-archive ...`)
- [ ] attention-export integration

### Near-Term Cleanup

- [x] Reduce command-layer repetition after the content-expansion milestone.
  - First cleanup item landed: de-duplicate the `sync bookmarks|likes|tweets|all` command implementations while preserving the existing CLI UX and option set.
- [x] Reduce repetition across the post-sync batch runners (`media`, `unfurl`, `articles`, `threads`).
  - Next cleanup item landed: extracted the shared locked-store lifecycle while keeping each runner’s current “optimize only when changed” behavior.
- [x] Reduce boilerplate in the LanceDB record builders and centralize common time helpers.
  - Next cleanup items landed: added a small storage-record helper layer for coalescing/timestamp setup and moved the shared UTC timestamp helper into a proper utility module.
- [x] Reduce extractor duplication in the URL candidate selection logic.
  - Next cleanup item landed: replaced the parallel canonical/final URL candidate helpers with one shared helper that keeps their remaining behavioral differences explicit at the call sites.
- [x] Push secondary row filtering into LanceDB for media/url/article batch runners.
  - Next cleanup item landed: moved state/type/preview filters out of Python loops and into `ArchiveStore` `.where(...)` clauses while preserving the existing sorted return order.
- [x] Reduce repetition in thread-expansion target handling.
  - Next cleanup item landed: extracted the shared `_expand_target(...)` try/except/result-counting path so the explicit-target, membership, and linked-status loops no longer carry three copies of the same expansion logic.
- [x] Make Firefox cookie extraction WAL-safe.
  - Update: the first SQLite-backup snapshot attempt could hang on busy live profiles, so the bounded shipped path copies `cookies.sqlite` plus any present `-wal` / `-shm` / `-journal` sidecars into a temp snapshot before querying.
- [x] Expand failure-path coverage for post-sync runners and extractor edge cases.
  - Next cleanup item landed: added targeted tests for retries, limits, invalid responses, and malformed payload handling across media, unfurl, articles, threads, and extractor.
- [x] Add direct unit coverage for `ExtractedTweetGraph` coalescing rules.
  - Next cleanup item landed: locked down the graph-level merge precedence rules with focused unit tests instead of relying only on extraction/storage integrations.
- [x] Batch media/unfurl row merges for LanceDB and clean up nearby minor issues.
  - Next cleanup item landed: stopped doing one merge per media/url update in the runners, and folded in the small readability/safety fixes in `cli.py` and `extractor.py`.
- [x] Improve long-running runner observability where network retries make the CLI look stalled.
  - Next cleanup item landed: `tweetxvault threads expand` now prints phase/progress output plus visible 429 retry/cooldown diagnostics so large thread-expansion jobs no longer look dead while the HTTP layer is backing off.
- [x] Reduce startup silence and unnecessary preload scans in `tweetxvault threads expand`.
  - Next cleanup item landed: the runner now prints preload progress before the archive scans begin, and it defers the expensive known-tweet-id scan until the linked-status pass actually needs it.
- [x] Add an auth-resolution debug flag for commands that can stall before the archive job starts.
  - Next cleanup item landed: `threads expand --debug-auth` and `auth check --debug-auth` now surface browser/profile probing steps when cookie extraction or keyring access is the slow step.
- [x] Decouple post-sync auto-embedding from sync success.
  - Next cleanup item landed: archive capture success now wins. If auto-embedding fails, sync warns and leaves the new tweets for a later `tweetxvault embed` run or the next sync instead of failing the completed sync.
- [x] Clarify browser-override auth semantics for `user_id`.
  - Next cleanup item landed: `--browser` now only forces cookie sourcing (`auth_token` / `ct0`) from the selected browser/profile; explicit env/config `user_id` remains a fallback for likes and authored-tweet sync.
- [x] Tighten thread-expansion rerun and dedupe semantics.
  - Next cleanup item landed: explicit `threads expand <id/url>...` is now idempotent by default, `--refresh` is the explicit re-fetch escape hatch, and linked status-URL targets are attempted at most once per run even when repeated across many URL refs.
- [x] Decide whether the current CLI is Unix-only or needs first-class Windows support.
  - Next cleanup item landed: the current runtime is documented as Unix-like only for now because the CLI still depends on `fcntl`, `resource`, and `strftime("%-d")`; Windows support stays deferred until platform-specific replacements and tests land.

## Open Questions (Remaining)

1. **Embedding runtime**: which local embedding model/runtime do we standardize on for Phase 3 (quality, footprint, licensing)?
2. **Search table shape**: do we keep embeddings/indexes directly on `tweet` rows, or split out a derived LanceDB search table later if indexing mixed record types becomes awkward?
3. **Canonical tweet layer migration**: do we keep long-term duplication between collection-scoped `tweet` rows and global `tweet_object` rows, or eventually slim the membership rows down to just collection/order state once downstream consumers move over?
4. **Articles endpoint shape**: authenticated `TweetDetail` returned full article `plain_text` on 2026-03-16; remaining question is whether `UserArticlesTweets` adds anything we need beyond the current targeted refresh path.
5. **URL snapshot runner**: inline CLI commands landed for the first pass (`tweetxvault media download`, `tweetxvault unfurl`); decide later whether ArchiveBox/snapshotting needs a queue table or can stay command-driven.
6. **Thread-expansion trigger**: do we keep thread/context capture as an explicit follow-on command, or add an opt-in sync-time expansion flag after the first stable implementation?
7. **Bookmark archive coverage**: the 2026-03-16 X archive sample contains likes, authored tweets, deleted tweets, and exported media, but no bookmark dataset. Is bookmarks export unsupported by X, or just absent from this specific archive?

## Dependencies (Planned)

### Runtime
```
browser-cookie3    # Chromium-family cookie extraction + keyring integration
httpx              # async HTTP client
lancedb            # embedded archive + search engine
pyarrow            # LanceDB schemas / table payloads
pydantic>=2        # data models, config validation
typer              # CLI framework (built on Click)
rich               # terminal formatting, progress bars
loguru             # logging
```

### Dev
```
ruff               # lint + format
pytest             # testing
pytest-asyncio     # async test support
mypy               # type checking (optional, can add later)
```

### Optional (deferred)
```
sentence-transformers  # future local embedding pipeline
onnxruntime            # possible lighter local embedding runtime
playwright         # future fallback adapter
```
