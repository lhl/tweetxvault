# twitter-export — Implementation Plan

## Goal

Build a Python tool for regular, unattended export of Twitter/X bookmarks and likes into a local embedded database with semantic search capabilities and full media archival. Part of the broader [attention-export](~/github/lhl/attention-export) system.

## Decision: Build Fresh, Cite TweetHoarder

We are **building our own tool from scratch**, not forking [TweetHoarder](https://github.com/tfriedel/tweethoarder) (MIT licensed). Rationale:

- Our differentiators (vector search/embeddings, media archival, articles export, attention-export integration) touch every layer — storage, client, CLI, export. A fork would require rewriting most of it anyway.
- We want a different storage architecture (SeekDB/LanceDB with native vector search, not SQLite).
- We can build leaner (~2,000-3,000 lines vs their ~5,600) by making our own dependency and design choices.
- The hard problems (query hash discovery, feature flags, cursor extraction, Chrome cookie decryption) are well-documented in tweethoarder's codebase — reimplementing independently with that reference is straightforward.

TweetHoarder is cited as prior art and its `reference/tweethoarder/` checkout is used for studying patterns, not as a code source.

## Prior Art: TweetHoarder

[TweetHoarder](https://github.com/tfriedel/tweethoarder) — the closest existing tool.

- **Origin**: Started Jan 2, 2026. ~100 commits (65 on day one). Built with Claude Code (Opus 4.5), using beads for coordination. Architecture ported from [bird](https://github.com/steipete/bird) (TypeScript). MIT licensed.
- **Stack**: Python 3.13+, async (httpx), SQLite, Typer CLI, cookie-based auth

### What It Does Well

| Area | Details |
|------|---------|
| **Auth extraction** | Firefox + Chrome cookie auto-detection with fallback chain (env vars → config file → Firefox → Chrome w/ keyring decryption). Extracts `auth_token`, `ct0`, `twid`. |
| **Query hash discovery** | Dynamic: fetches Twitter's JS bundles, regex-extracts operation→hash mappings. Falls back to hardcoded list. 24h TTL cache. Auto-refreshes on 404. |
| **Feature flags** | ~60 flags ported from bird reference implementation. Per-endpoint flag sets. |
| **Checkpoint/resume** | Tracks sync progress (cursor, last_tweet_id, sort_index) per collection type. |
| **Rate limiting** | Adaptive exponential backoff: 0.2s initial → 60s max, 2x multiplier, resets after 5 successes. 5-minute cooldown after 3 consecutive 429s. |
| **Raw data preservation** | Full GraphQL JSON stored in `raw_json` column alongside parsed fields. |
| **Export formats** | JSON, Markdown (thread-aware), CSV, HTML (single-file viewer with virtual scrolling, themes, faceted search). |
| **Collection types** | likes, bookmarks (with folder support), tweets, reposts, replies, feed |

### Our Differentiators

| Gap in TweetHoarder | Our Approach |
|---------------------|-------------|
| **No media downloads** | First-class media archival: images, videos (MP4 + m3u8/ffmpeg), GIFs. Content-hash dedup. |
| **No search/embeddings** | Native vector search via SeekDB or LanceDB. Semantic "find similar" queries. Hybrid search (vector + text + metadata filters). |
| **No articles export** | `UserArticlesTweets` GraphQL endpoint — nobody has implemented this yet. Novel feature. |
| **No scheduling** | Designed for cron from day one. Incremental by default, clear exit codes. |
| **SQLite only** | Embedded DB with native vector + full-text + hybrid search. |
| **No Playwright fallback** | Direct API primary, Playwright for debugging/CAPTCHA fallback. |
| **No attention-export integration** | Module in the attention-export ecosystem. |

### Patterns to Study Independently

These are non-obvious problems TweetHoarder solved. We study the approach and write our own:

1. **Query hash auto-discovery** (`query_ids/scraper.py`): 4 regex patterns against JS bundles from multiple discovery pages. Fallback list + 24h TTL cache.
2. **Feature flag sets** (`client/features.py`): ~60 flags, different per endpoint. Ported from bird.
3. **Cursor extraction**: Timeline response walking for pagination cursors across different GraphQL response shapes.
4. **Chrome cookie decryption** (`auth/chrome.py`): AES-128-CBC with PBKDF2, key from GNOME Keyring via secretstorage.
5. **Sort index tracking** (`sync/sort_index.py`): Preserving Twitter's timeline ordering for resume.

## Architecture

### Approach: Direct API (primary) + Playwright (fallback)

TweetHoarder's query hash auto-discovery proves the direct API approach is viable without constant maintenance. With reliable hash discovery, direct API keeps its advantages (fast, lightweight, precise rate control, cronnable) without its main disadvantage (hash staleness).

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
│   ├── NEW  db.py               # DB layer (SeekDB or LanceDB)
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
├── docs/                         # Plans, research, analysis
├── reference/                    # Third-party repo snapshots (read-only)
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

### DB Choice

See [ANALYSIS-db.md](ANALYSIS-db.md) for the full comparison of SQLite, DuckDB, LanceDB, and SeekDB.

**Top contenders**: SeekDB and LanceDB. Both support native vector search in embedded mode.

| | SeekDB | LanceDB |
|--|--------|---------|
| **Best for** | Hybrid search (vector + full-text + SQL in one query) | Pure vector similarity |
| **SQL** | MySQL-compatible | None (Python API) |
| **Full-text search** | Native | Basic |
| **Built-in embeddings** | Yes (all-MiniLM-L6-v2 default, 14 providers) | No |
| **Hybrid search** | Single `hybrid_search()` call with RRF ranking | Manual stitching |
| **Maturity** | Engine: OceanBase (15+ years); SDK: ~4 months | ~2 years |
| **Distance metrics** | L2, cosine, inner product | L2, cosine, dot |

**Decision**: Prototype both. SeekDB's hybrid search is exactly what we want ("bookmarks about AI from @user"), but the SDK is young. LanceDB is more proven but requires a second system for structured queries. See evaluation plan in ANALYSIS-db.md.

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

- [ ] Embedding generation for tweet text (local model, 384d default)
- [ ] Vector storage + HNSW indexing
- [ ] Semantic search CLI: `search "topic"`
- [ ] Hybrid search: vector + text + metadata filters in one query
- [ ] Similar tweet lookup: `similar <tweet_id>`

### Phase 4: Extended Collections + Polish

- [ ] User's own tweets sync
- [ ] Reposts sync
- [ ] Thread expansion (on-demand via TweetDetail)
- [ ] Articles export (`UserArticlesTweets` — novel, no existing tool does this)
- [ ] Following/followers lists
- [ ] Bookmark folders
- [ ] HTML export with search/filter
- [ ] Systemd timer template
- [ ] attention-export integration
- [ ] Multimodal embeddings for media (Jina CLIP or local CLIP model)

## Articles Export (Novel Feature)

Twitter/X Articles (long-form content) are accessible via the `UserArticlesTweets` GraphQL operation. TweetHoarder has the query ID (`8zBy9h4L90aDL02RsBcCFg`) and feature flags reserved but **no existing tool has implemented article export**.

### Open questions (need to probe the endpoint):
- Does `UserArticlesTweets` return full article body or just a tweet stub with a link?
- Are articles you've bookmarked/liked discoverable, or only articles by a specific user?
- If the endpoint returns stubs, can we fetch full content via `TweetDetail` or do we need Playwright page scraping?
- What metadata is available (title, cover image, word count)?

### Plan:
Once auth is working (phase 1), probe the endpoint with a test request and inspect the response shape. If full content is available via GraphQL, add as a collection type. If not, consider Playwright-based scraping as a targeted fallback for article body extraction.

## CLI Design

```
twitter-export sync bookmarks          # incremental bookmark sync
twitter-export sync likes              # incremental likes sync
twitter-export sync all                # sync everything
twitter-export sync all --full         # full re-sync from scratch
twitter-export sync articles           # sync articles (phase 4)

twitter-export export json             # export all to JSON
twitter-export export csv --collection likes
twitter-export export md --collection bookmarks

twitter-export search "machine learning"    # semantic search (phase 3)
twitter-export similar 1234567890           # find similar tweets (phase 3)

twitter-export auth check              # verify cookies are valid
twitter-export auth refresh-ids        # force query hash refresh

twitter-export --headful sync likes    # Playwright fallback (visible browser)
twitter-export --profile PATH sync bookmarks  # custom browser profile
```

## Dependencies (Planned)

```
httpx              # async HTTP client
typer              # CLI framework
rich               # progress bars, formatted output
cryptography       # Chrome cookie decryption
secretstorage      # GNOME Keyring (Chrome cookies on Linux)

# DB (one of — pending prototype evaluation):
pyseekdb           # SeekDB embedded + vector + hybrid search
# or: lancedb     # LanceDB embedded vector DB

# Optional:
playwright         # fallback browser automation
ffmpeg-python      # m3u8 video download (or subprocess ffmpeg)
```

## Open Questions

1. **SeekDB vs LanceDB** — Need to prototype both. SeekDB's hybrid search is ideal but SDK is young. See ANALYSIS-db.md evaluation plan.
2. **Embedding model** — Default: all-MiniLM-L6-v2 (384d, local, free). SeekDB bundles this. Consider upgrading to larger model if recall is insufficient.
3. **Media storage layout** — Flat by content hash (`data/media/{sha256[:2]}/{sha256}.{ext}`). Simple, dedup-native. See ANALYSIS-db.md.
4. **Articles endpoint shape** — Need to probe `UserArticlesTweets` once auth works. Determines whether articles are a GraphQL collection type or need Playwright scraping.
5. **attention-export schema conventions** — Need to align with other exporters in the ecosystem.
6. **HTML export** — Defer to phase 4. TweetHoarder's is impressive but significant effort; our search/embedding features may provide a better browsing experience anyway.
