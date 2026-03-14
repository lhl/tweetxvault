# twitter-export — Implementation Plan

## Goal

Build a Python tool for regular, unattended export of Twitter/X bookmarks and likes into a local embedded database with semantic search capabilities and full media archival. Part of the broader [attention-export](~/github/lhl/attention-export) system.

## Prior Art: TweetHoarder Analysis

[TweetHoarder](https://github.com/tfriedel/tweethoarder) is the closest existing tool to what we're building. Key facts:

- **Origin**: Started Jan 2, 2026. 100 commits, 65 on the first day. Built with Claude Code (Opus 4.5), using beads for coordination. Essentially entirely AI-generated code (~5,600 lines Python). Under current copyright law, AI-generated code has no copyright protection, so redistribution of derivative works is not a legal concern — but we'd rather build clean anyway.
- **Stack**: Python 3.13+, async (httpx), SQLite, Typer CLI, cookie-based auth
- **Architecture ported from**: [bird](https://github.com/steipete/bird) (TypeScript Twitter client)

### What TweetHoarder Does Well

| Area | Details |
|------|---------|
| **Auth extraction** | Firefox + Chrome cookie auto-detection with fallback chain (env vars → config file → Firefox → Chrome w/ keyring decryption). Extracts `auth_token`, `ct0`, `twid`. |
| **Query hash discovery** | Dynamic: fetches Twitter's JS bundles, regex-extracts operation→hash mappings. Falls back to hardcoded list. 24h TTL cache. Auto-refreshes on 404. This solves the #1 problem with our Plan B. |
| **Feature flags** | ~60 flags ported from bird reference implementation. Per-endpoint flag sets. |
| **Checkpoint/resume** | Tracks sync progress (cursor, last_tweet_id, sort_index) per collection type. Interrupted syncs resume automatically. `--full` flag for complete re-sync. |
| **Rate limiting** | Adaptive exponential backoff: 0.2s initial → 60s max, 2x multiplier, resets after 5 successes. 5-minute cooldown after 3 consecutive 429s. |
| **Raw data preservation** | Full GraphQL JSON stored in `raw_json` column alongside parsed fields. |
| **DB schema** | 5 tables: tweets (denormalized + JSON columns for media/urls/hashtags), collections (many-to-many with collection_type), sync_progress, threads, thread_context, metadata. UPSERT preserves first_seen_at while updating metrics. |
| **Export formats** | JSON (nested), Markdown (thread-aware, rich text), CSV (minimal), HTML (single-file viewer with virtual scrolling, theme switcher, faceted search, copy-as-markdown). |
| **Collection types** | likes, bookmarks (with folder support), tweets, reposts, replies, feed |
| **Thread fetching** | On-demand TweetDetail fetching with configurable depth. Adaptive rate limiting specific to thread requests. |

### TweetHoarder Gaps (Our Differentiators)

| Gap | Impact | Our Approach |
|-----|--------|-------------|
| **No media downloads** | URLs stored but never fetched. Images/videos lost if tweets deleted. | First-class media archival: images, videos (MP4 + m3u8/ffmpeg), GIFs. Local mirror. |
| **No search/embeddings** | Regex-only client-side search in HTML export. No semantic similarity. | LanceDB or DuckDB+vss for vector search. Generate embeddings for tweet text. Semantic "find similar" queries. |
| **No scheduling** | CLI-only, user must set up cron externally. | Design for cron from day one. Incremental by default, clear exit codes, log rotation. Possibly systemd timer template. |
| **SQLite only** | Fine for storage, but no native path to vector/semantic features. | Embedded DB with native vector support (LanceDB) or DuckDB with vss extension. |
| **No attention-export integration** | Standalone tool. | Designed as a module in the attention-export ecosystem. Shared DB conventions, cross-platform search. |
| **Playwright fallback** | Direct API only. If cookies fail or JS challenges appear, no recourse. | Playwright as fallback/debug mode (`--headful`). Primary: direct API. |
| **No media dedup** | N/A (doesn't download). | Content-hash based media dedup across tweets sharing same media. |

### Patterns to Learn From (Not Copy)

These are areas where TweetHoarder solved non-obvious problems. We should study their approach and write our own implementations:

1. **Query hash auto-discovery** (`query_ids/scraper.py`): 4 regex patterns against JS bundles from multiple discovery pages. Fallback list + 24h TTL cache.
2. **Feature flag sets** (`client/features.py`): ~60 flags, different per endpoint. Ported from bird reference.
3. **Cursor extraction**: Timeline response walking for pagination cursors across different GraphQL response shapes.
4. **Chrome cookie decryption** (`auth/chrome.py`): AES-128-CBC with PBKDF2, key from GNOME Keyring via secretstorage.
5. **Sort index tracking** (`sync/sort_index.py`): Preserving Twitter's timeline ordering for resume.
6. **HTML export dedup logic**: Merging retweets with originals, deduplicating self-reply thread chains.

## Architecture

### Approach: Direct API (primary) + Playwright (fallback)

TweetHoarder's query hash auto-discovery changes the Plan A vs Plan B calculus from our initial comparison. With reliable hash discovery, the direct API approach loses its main disadvantage (hash maintenance) while keeping its advantages (fast, lightweight, precise rate control, cronnable).

**Primary**: Direct HTTP API client (httpx, async) with auto-discovered query hashes
**Fallback**: Playwright browser automation for debugging, CAPTCHA handling, or when direct API fails

### File Tree

```
twitter-export/
├── twitter/
│   ├── NEW  __init__.py
│   ├── NEW  config.py           # Constants, XDG paths, TOML config
│   ├── NEW  auth/
│   │   ├── NEW  __init__.py
│   │   ├── NEW  firefox.py      # Firefox cookie extraction
│   │   ├── NEW  chrome.py       # Chrome cookie extraction + keyring
│   │   └── NEW  cookies.py      # Resolution chain (env → config → browser)
│   ├── NEW  client/
│   │   ├── NEW  __init__.py
│   │   ├── NEW  base.py         # HTTP client, headers, bearer token
│   │   ├── NEW  features.py     # GraphQL feature flags per endpoint
│   │   ├── NEW  timelines.py    # Endpoint implementations + pagination
│   │   └── NEW  query_ids.py    # Hash discovery, caching, fallback
│   ├── NEW  scraper.py          # Playwright fallback (--headful, --browser)
│   ├── NEW  db.py               # DB layer (LanceDB or DuckDB)
│   ├── NEW  models.py           # Data models / schemas
│   ├── NEW  media.py            # Media download (images, video, m3u8)
│   ├── NEW  export/
│   │   ├── NEW  __init__.py
│   │   ├── NEW  json.py
│   │   ├── NEW  csv.py
│   │   └── NEW  markdown.py
│   └── NEW  cli.py              # CLI entry point
├── tests/
│   └── ...
├── data/                         # Local data (gitignored)
│   ├── db/                       # Database files
│   └── media/                    # Downloaded media
└── README.md
```

### Data Flow

```
Browser cookies ──> auth/ ──> auth_token + ct0 + twid
                                    │
          query_ids.py ─────────────┤ (discovers current hashes)
                                    │
                                    v
                            client/ (httpx async)
                            ┌─────────────────────┐
                            │  1. Build headers     │
                            │  2. Discover hashes   │
                            │  3. Paginate endpoint │
                            │  4. Store raw JSON    │
                            └────────┬──────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    v                v                 v
                 db.py          media.py          export/
              (raw JSON +     (images, video,    (JSON, CSV,
               parsed fields   m3u8 → local)      Markdown)
               + embeddings)
```

### DB Choice: LanceDB vs DuckDB

| | LanceDB | DuckDB |
|--|---------|--------|
| **Vector search** | Native, first-class | Via vss extension |
| **Embeddings** | Built-in support | Manual |
| **SQL** | Limited (Lance query API) | Full SQL |
| **Maturity** | Newer, evolving | Battle-tested |
| **Size** | Small | Small |
| **Ecosystem** | Growing | Large |

**Decision**: TBD — needs hands-on evaluation. LanceDB is more natural for embeddings-first; DuckDB is more natural for ad-hoc queries. Could use both (DuckDB for structured queries, LanceDB for vector search) but that's probably over-engineering for v1.

## Feature Roadmap

### Phase 1: Core Sync (MVP)

- [ ] Auth extraction (Firefox, Chrome, env var fallback)
- [ ] Query hash auto-discovery + fallback list + cache
- [ ] GraphQL client with feature flags and pagination
- [ ] Bookmarks sync
- [ ] Likes sync
- [ ] Raw JSON storage in DB
- [ ] Parsed tweet fields (id, text, author, timestamps, media URLs, engagement)
- [ ] Checkpoint/resume for interrupted syncs
- [ ] Rate limit handling (adaptive backoff)
- [ ] CLI: `sync bookmarks`, `sync likes`, `sync all`
- [ ] Incremental sync by default, `--full` for complete re-sync

### Phase 2: Media + Export

- [ ] Image download (photos, profile images)
- [ ] Video download (MP4 direct, m3u8 via ffmpeg)
- [ ] GIF download
- [ ] Media dedup by content hash
- [ ] Export: JSON
- [ ] Export: CSV
- [ ] Export: Markdown
- [ ] CLI: `export json`, `export csv`, `export md`

### Phase 3: Search + Embeddings

- [ ] Embedding generation for tweet text
- [ ] Vector storage (LanceDB or DuckDB+vss)
- [ ] Semantic search CLI: `search "topic"`
- [ ] Similar tweet lookup: `similar <tweet_id>`

### Phase 4: Extended Collections + Polish

- [ ] User's own tweets sync
- [ ] Reposts sync
- [ ] Following/followers lists
- [ ] Thread expansion (on-demand)
- [ ] Bookmark folders
- [ ] HTML export with search/filter
- [ ] Systemd timer template
- [ ] attention-export integration

## CLI Design

```
twitter-export sync bookmarks          # incremental bookmark sync
twitter-export sync likes              # incremental likes sync
twitter-export sync all                # sync everything
twitter-export sync all --full         # full re-sync from scratch

twitter-export export json             # export all to JSON
twitter-export export csv --collection likes
twitter-export export md --collection bookmarks

twitter-export search "machine learning"    # semantic search
twitter-export similar 1234567890           # find similar tweets

twitter-export auth check              # verify cookies are valid
twitter-export auth refresh-ids        # force query hash refresh

twitter-export --headful sync likes    # Playwright fallback (visible browser)
twitter-export --profile PATH sync bookmarks  # custom browser profile
```

## Dependencies (Planned)

```
httpx              # async HTTP client
lancedb            # vector DB (or duckdb)
typer              # CLI framework
rich               # progress bars, formatted output
cryptography       # Chrome cookie decryption
secretstorage      # GNOME Keyring (Chrome cookies on Linux)
playwright         # optional, fallback browser automation
sentence-transformers  # or similar, for embeddings (phase 3)
```

## Open Questions

1. **LanceDB vs DuckDB** — Need to prototype both. LanceDB is more natural for our embeddings use case but DuckDB has better SQL story.
2. **Embedding model** — Which model for tweet text? sentence-transformers (local), or API-based (Claude/OpenAI)? Local preferred for privacy.
3. **Media storage layout** — Flat by hash? Nested by date? By collection?
4. **attention-export schema conventions** — Need to align with other exporters in the ecosystem.
5. **HTML export** — Worth building in v1 or defer? TweetHoarder's is impressive but significant effort.
