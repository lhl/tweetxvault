# tweetxvault — Implementation Plan

## Goal

Build a Python tool for regular, unattended export of Twitter/X bookmarks and likes into a local embedded database (SeekDB) with:
- raw GraphQL capture (never lose data)
- incremental sync with checkpoint/resume
- local search (full-text + semantic) and eventually hybrid search
- first-class media archival (images + video + GIF) as a later phase

This is part of the broader [attention-export](~/github/lhl/attention-export) system.

## Scope and Non-Goals

### MVP (what we will implement first)

- Direct GraphQL API sync for `Bookmarks` and `Likes` using browser cookies
- Query ID (query hash) auto-discovery from Twitter web JS bundles with TTL cache + static fallback IDs
- Rate-limit handling with exponential backoff + cooldown
- SeekDB storage:
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

- Our differentiators (SeekDB, embeddings/hybrid search, media archival, attention-export integration) cut across every layer. Adapting any existing tool would become a long-lived divergence.
- We want async-first architecture (httpx) with a clean adapter boundary (Playwright as optional fallback, not a core dependency).
- The hard reverse-engineering problems (query ID discovery, feature flags, cursor formats) are well-documented across multiple open-source projects. We learn from their empirical findings without inheriting their architectural decisions.

The `reference/` directory contains third-party snapshots for study — see `reference/README.md` for the full inventory.

## Key Decisions (Locked In)

These are our architectural choices, made to serve tweetxvault's goals (unattended sync, raw data preservation, embedded search, attention-export integration). They are independent decisions, not inherited from any existing tool.

- **DB**: SeekDB (`pyseekdb`) in embedded mode.
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
│   │   └── seekdb.py          # schema + upserts + checkpoints
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
  - If an API call returns `404`, treat it as “stale query id”, refresh once, retry.
  - Provide `tweetxvault auth refresh-ids` to force refresh.

### Feature Flags

Keep per-operation builders, not a single shared dict. Implementation plan:
- Start from a known working flag set (either from our own browser capture or prior art) and prune it down to what’s required.
- If requests begin returning `400`, treat it as likely feature drift and refresh the flag set by diffing against a working browser request.

### Pagination + Cursor Extraction

Per-operation parsers that:
- find tweet entries (`entryId` prefix `tweet-...`)
- find bottom cursor entries (`entryId` prefix `cursor-bottom-...`) and return cursor value
- extract `sortIndex` when available so we can preserve timeline order

### Sync Loop + Stop Conditions

Each `sync` invocation runs a page-at-a-time loop:

1. Resolve cookies → validate `auth_token` + `ct0` present (fail with actionable error if not)
2. Resolve query IDs (cache → refresh → fallback)
3. Load checkpoint (cursor) from `sync_state` if incremental
4. Loop:
   a. Fetch one page with current cursor
   b. Append raw response to `raw_captures`
   c. Parse tweet entries and bottom cursor
   d. Upsert tweets + collection memberships
   e. Update `sync_state` checkpoint with new cursor
   f. Check stop conditions (see below)
   g. Polite delay between pages (default 2s, configurable)
5. Print summary (tweets synced, pages fetched)

Stop conditions:
- **Empty page**: response contains zero tweet entries → done
- **Incremental duplicate detection**: encountered a tweet_id already in DB from a previous sync → stop (caught up to where we left off)
- **`--full` mode**: ignores duplicate detection, only stops on empty page (full re-scan)
- **`--limit N`**: stop after N pages (for testing or cautious first runs)
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

## Storage (SeekDB)

See [ANALYSIS-db.md](ANALYSIS-db.md) for the full DB comparison and schema thoughts. The project decision is SeekDB.

### Minimal Schema (Phase 1)

- `raw_captures` (append-only):
  - `id` (UUID)
  - `operation` (Bookmarks/Likes)
  - `cursor_in`, `cursor_out`
  - `captured_at` (UTC)
  - `http_status`, `source` ("api")
  - `raw_json` (TEXT or JSON)

- `tweets` (upsert by `tweet_id`):
  - `tweet_id` (rest_id; PK)
  - `text`
  - `author_id`, `author_username`, `author_display_name`
  - `created_at`
  - `raw_json` (tweet-level raw block)
  - `first_seen_at`, `last_seen_at`

- `collections` (upsert by `(tweet_id, collection_type, folder_id)`):
  - `tweet_id`
  - `collection_type` ("bookmark" | "like")
  - `bookmark_folder_id` (nullable)
  - `sort_index` (nullable)
  - `added_at` (sync-time for now; if we later find a real “liked/bookmarked at” timestamp we can backfill)
  - `synced_at`

- `sync_state` (checkpoint/resume):
  - `collection_type` (+ folder_id where relevant)
  - `cursor`
  - `last_tweet_id` (optional)
  - `updated_at`

### Embeddings (Phase 3)

- Use SeekDB’s built-in local embedding by default (`all-MiniLM-L6-v2`, 384d).
- Store embeddings alongside tweets and build a vector index (HNSW).
- Prefer hybrid search via SeekDB’s API (vector + FTS + metadata filters).

## CLI Design

Primary commands:

```
tweetxvault sync bookmarks            # incremental
tweetxvault sync likes                # incremental
tweetxvault sync all                  # sync likes + bookmarks
tweetxvault sync all --full           # full resync (ignore duplicates)
tweetxvault sync bookmarks --limit 5  # stop after 5 pages (testing)

tweetxvault export json               # (phase 2+) export all collections

tweetxvault auth check                # verify cookies are present/valid
tweetxvault auth refresh-ids          # force query id refresh
```

`--limit N` limits pagination to N pages (useful for testing or cautious first runs).

### First-Run Behavior

On first invocation, tweetxvault:
1. Auto-creates XDG directories (`~/.config/tweetxvault/`, `~/.local/share/tweetxvault/`, `~/.cache/tweetxvault/`)
2. Attempts cookie resolution (env vars → config file → Firefox)
3. If no cookies found: prints clear error with setup instructions (which env vars to set, or ensure Firefox is logged into x.com)
4. If cookies found: discovers query IDs (first run has no cache, so fetches from JS bundles)
5. Auto-creates DB on first write
6. Begins sync

No `init` command needed — everything auto-creates on demand. Config file is optional; the tool works with just Firefox cookies or env vars.

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
- [ ] Rate limit handling (backoff + cooldown)
- [ ] Minimal JSON export (optional but helpful early)

### Phase 2: Export + Media Foundations

- [ ] Export: JSON / CSV / Markdown
- [ ] Media metadata extraction to DB (URLs, types, dimensions, variants)
- [ ] Media download (photos first; video later)

### Phase 3: Search + Embeddings

- [ ] Embeddings (SeekDB built-in)
- [ ] Semantic search CLI
- [ ] Hybrid search (vector + full-text + metadata)
- [ ] Similar tweet lookup

### Phase 4: Extended Collections + Polish

- [ ] Bookmark folders
- [ ] Thread expansion (TweetDetail)
- [ ] Articles export (`UserArticlesTweets`)
- [ ] Following/followers lists
- [ ] HTML export viewer (optional)
- [ ] attention-export integration

## Open Questions (Remaining)

These are the only “unknowns” we still need to answer before implementation punchlisting:

1. **SeekDB embedded footprint/startup**: What are startup time and memory footprint for a typical cron run? (Need a small prototype before we commit too hard to schema choices.)
2. **SeekDB raw JSON storage limits/perf**: Are large JSON blobs practical in a SeekDB table/collection? If not, store `raw_json_path` to gzipped files on disk and keep a hash in DB.
3. **Articles endpoint shape**: Does `UserArticlesTweets` include full body? If not, decide whether we will implement a targeted Playwright scrape for articles only.

## Dependencies (Planned)

### Runtime
```
httpx              # async HTTP client
pyseekdb           # embedded DB
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
playwright         # future fallback adapter
```
