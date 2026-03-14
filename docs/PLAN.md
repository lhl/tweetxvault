# tweetxvault — Implementation Plan

## Goal

Build a Python tool for regular, unattended export of Twitter/X bookmarks and likes into a local embedded database with:
- raw GraphQL capture (never lose data)
- incremental sync with checkpoint/resume
- local search (full-text + semantic) and eventually hybrid search
- first-class media archival (images + video + GIF) as a later phase

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
  - MVP: env vars + config file + Firefox cookie extraction (Linux).
- **Chrome cookie extraction**: Defer until someone actually needs it (it adds keyring/decryption complexity).
- **CLI framework**: Typer + Rich (keep it minimal; no sprawling command surface).
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
│   │   └── cookies.py         # Resolution chain (env -> config -> firefox)
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
cookies (env/config/firefox)
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
3. **Firefox extraction**: auto-discover default profile, copy `cookies.sqlite` to temp, read `x.com` cookies

User ID resolution (needed for Likes only):
- Parsed from `twid` cookie (`u%3D<numeric_id>` → `<numeric_id>`)
- Or set explicitly via `TWEETXVAULT_USER_ID` env var or `user_id` in config
- If user_id can't be resolved, `sync likes` fails with actionable error; `sync bookmarks` works fine

Firefox cookie extraction rules (Linux):
- Always copy `cookies.sqlite` to a temp file before reading (Firefox often holds WAL locks on the live DB)
- Open the copied DB in read-only mode (`mode=ro`) via sqlite URI
- Never log raw cookie values; treat them as secrets

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

### Feature Flags

Keep per-operation builders, not a single shared dict. Implementation plan:
- Start from a known working flag set (captured from our own browser; prior art only as cross-check) and commit those static per-operation dicts in code for MVP.
- MVP does **not** attempt automatic feature-flag discovery.
- If requests begin returning `400` after auth and query IDs look healthy, treat it as likely feature drift and fail with an actionable error instead of retrying indefinitely.

### Preflight + Validation

Before any sync writes data:
- Resolve local auth inputs (env → config → Firefox).
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
  - `text`
  - `author_id`, `author_username`, `author_display_name`
  - `created_at`
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

## CLI Design

Primary commands:

```
tweetxvault sync bookmarks            # incremental
tweetxvault sync likes                # incremental
tweetxvault sync all                  # preflight both, then sync bookmarks + likes
tweetxvault sync all --full           # full resync (ignore duplicates)
tweetxvault sync bookmarks --limit 5  # stop after 5 pages (testing)

tweetxvault export json               # (phase 2+) export all collections

tweetxvault auth check                # run shared preflight, report local + remote readiness
tweetxvault auth refresh-ids          # force query id refresh
```

`--limit N` limits persisted sync pagination to N pages per collection (useful for testing or cautious first runs).

### First-Run Behavior

On first invocation, tweetxvault:
1. Auto-creates XDG directories (`~/.config/tweetxvault/`, `~/.local/share/tweetxvault/`, `~/.cache/tweetxvault/`)
2. Attempts cookie resolution (env vars → config file → Firefox)
3. If no cookies found: prints clear error with setup instructions (which env vars to set, or ensure Firefox is logged into x.com)
4. If cookies found: resolves query IDs (first run has no cache, so fetches from JS bundles and falls back to static IDs if refresh fails)
5. Runs a lightweight API probe for the requested collection(s); on failure, exits with actionable error before any checkpoint or DB writes
6. Acquires the local process lock so overlapping cron/manual runs cannot race
7. Auto-creates DB on first write, records archive owner metadata, and begins sync

No `init` command needed — everything auto-creates on demand. Config file is optional; the tool works with just Firefox cookies or env vars. `tweetxvault auth check` uses the same preflight path as `sync`, so the first-run experience is testable before any data is written.

Reserved for future (not implemented in MVP):
- `tweetxvault sync ... --playwright` (adapter fallback)

## Feature Roadmap

### Phase 1: Core Sync (MVP)

- [ ] Auth extraction (env vars, config file, Firefox)
- [ ] Query ID auto-discovery + fallback + TTL cache
- [ ] GraphQL client (httpx async) + per-operation feature flags
- [ ] Bookmarks sync
- [ ] Likes sync
- [ ] Append raw captures + upsert tweets + collections
- [ ] Checkpoint/resume for interrupted sync
- [ ] Single-process locking + atomic page commits
- [ ] Rate limit handling (backoff + cooldown)
- [ ] Minimal JSON export (optional but helpful early)

### Phase 2: Export + Media Foundations

- [ ] Export: JSON / CSV / Markdown
- [ ] Media metadata extraction to DB (URLs, types, dimensions, variants)
- [ ] Media download (photos first; video later)

### Phase 3: Search + Embeddings

- [ ] Embeddings (local model + LanceDB vector column)
- [ ] Semantic search CLI
- [ ] Hybrid search (LanceDB FTS + vector + metadata)
- [ ] Similar tweet lookup

### Phase 4: Extended Collections + Polish

- [ ] Bookmark folders
- [ ] Thread expansion (TweetDetail)
- [ ] Articles export (`UserArticlesTweets`)
- [ ] Following/followers lists
- [ ] HTML export viewer (optional)
- [ ] attention-export integration

## Open Questions (Remaining)

1. **Embedding runtime**: which local embedding model/runtime do we standardize on for Phase 3 (quality, footprint, licensing)?
2. **Search table shape**: do we keep embeddings/indexes directly on `tweet` rows, or split out a derived LanceDB search table later if indexing mixed record types becomes awkward?
3. **Articles endpoint shape**: Does `UserArticlesTweets` include full body? If not, decide whether we will implement a targeted Playwright scrape for articles only.

## Dependencies (Planned)

### Runtime
```
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
cryptography       # Chrome cookie decryption
secretstorage      # Chrome cookie decryption (Linux keyring)
sentence-transformers  # future local embedding pipeline
onnxruntime            # possible lighter local embedding runtime
playwright         # future fallback adapter
```
