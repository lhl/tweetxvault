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
- Query ID (query hash) auto-discovery from Twitter web JS bundles (TweetHoarder approach) with TTL cache + static fallback IDs
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

## Why a Clean Rewrite (Not a Fork)

We are **building from scratch**, not forking [TweetHoarder](https://github.com/tfriedel/tweethoarder) (MIT).

Rationale:
- Our differentiators (SeekDB, embeddings/hybrid search, media archival, attention-export integration) cut across every layer. A fork would become a long-lived divergence.
- TweetHoarder’s hard problems are solved and well-documented (query ID refresh, feature flags, cursor parsing, rate limit handling). We can re-implement those patterns cleanly without inheriting unrelated decisions (SQLite schema, Typer UX, etc.).
- We want an architecture that treats Playwright as an optional adapter rather than a core requirement.

TweetHoarder is used as prior art and is vendored only under `reference/` for study, not as a code source.

## Key Decisions (Locked In)

- **DB**: SeekDB (`pyseekdb`) in embedded mode.
- **Primary capture approach**: Direct GraphQL API calls (httpx, async), not Playwright interception.
- **Query IDs**: Auto-discovered from Twitter JS bundles + on-disk TTL cache + fallback static IDs (TweetHoarder approach).
- **Rate limiting/backoff**: TweetHoarder-style exponential backoff and cooldown on repeated `429`.
- **Auth**: Cookie-based session auth (no username/password automation).
  - MVP: env vars + config file + Firefox cookie extraction (Linux).
- **Chrome cookie extraction**: Defer until someone actually needs it (it adds keyring/decryption complexity).
- **CLI framework**: Typer (keep it minimal; no sprawling command surface).
- **Playwright**: Reserved as a future fallback adapter (debugging/CAPTCHA/JS challenge), not implemented in MVP.

## Prior Art We’re Adopting from TweetHoarder

This is the minimal set of patterns we should port conceptually:

1. **Query ID auto-discovery**: Fetch a discovery page, extract bundle URLs, regex operationName <-> queryId pairs, cache with TTL, refresh on `404`.
2. **Feature flags**: Maintain per-operation feature flag builders (ported from bird); don’t send a single “one size fits all” blob.
3. **Cursor extraction**: Timeline instruction walking (cursor-bottom entries), with operation-specific parsers.
4. **Rate limit handling**:
   - per-request retries with exponential delay
   - cooldown after N consecutive `429`
5. **Checkpoint/resume**: Persist cursor and “where we were” per collection and resume automatically.

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

### Adapter Boundary (Playwright Future-Proofing)

Define a small internal interface:

```python
class Fetcher(Protocol):
    async def sync_collection(self, collection: str, *, full: bool) -> None: ...
```

MVP implements `GraphQLAPIFetcher`. A future `PlaywrightFetcher` can implement the same interface and share storage + export.

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
- `twid` (contains numeric user id; useful for Likes without extra lookup)

Firefox cookie extraction rules (Linux):
- Always copy `cookies.sqlite` to a temp file before reading (Firefox often holds WAL locks on the live DB)
- Open the copied DB in read-only mode (`mode=ro`) via sqlite URI
- Never log raw cookie values; treat them as secrets

Expected request headers (aligned with TweetHoarder patterns):
- `Authorization: Bearer <public web bearer token>`
- `x-csrf-token: <ct0>`
- `x-twitter-active-user: yes`
- `x-twitter-auth-type: OAuth2Session`
- `x-twitter-client-language: en`
- plus basic browser-like `User-Agent`, `Referer`, `Origin`

### Operations (Phase 1)

Minimum operations needed:
- `Bookmarks`
- `Likes`

Recommended additional operations to include in query-id targets even if we don’t sync them yet:
- `BookmarkFolderTimeline` (folders)
- `UserArticlesTweets` (probe later)
- `TweetDetail` (thread/context or media variants later)

### Query ID Auto-Discovery (TweetHoarder Approach)

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
- Start by porting TweetHoarder’s current flag sets for `Bookmarks` and `Likes`.
- If requests begin returning `400`, treat it as likely feature drift and update from reference/ prior art.

### Pagination + Cursor Extraction

Per-operation parsers that:
- find tweet entries (`entryId` prefix `tweet-...`)
- find bottom cursor entries (`entryId` prefix `cursor-bottom-...`) and return cursor value
- extract `sortIndex` when available so we can preserve timeline order

### Rate Limiting / Backoff

Adopt TweetHoarder’s spirit:
- For timeline page fetches: retry up to N times on `429`, with exponential backoff (`base_delay * 2^attempt`) and a cooldown (e.g., 5 minutes) after 3 consecutive `429`.
- For other hard failures (403/401): fail fast with actionable output (cookies expired, missing ct0, etc.).
- For `404` on a known operation: refresh query IDs once, retry.

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
tweetxvault sync bookmarks          # incremental
tweetxvault sync likes              # incremental
tweetxvault sync all                # sync likes + bookmarks
tweetxvault sync all --full         # full resync

tweetxvault export json             # (phase 2+) export all collections

tweetxvault auth check              # verify cookies are present/valid
tweetxvault auth refresh-ids        # force query id refresh
```

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

```
httpx
pyseekdb

typer
rich

# Optional (only if we add Chrome support):
cryptography
secretstorage

# Optional (future fallback adapter):
playwright
```
