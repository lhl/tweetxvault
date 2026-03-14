# Comparison: Plan A (Playwright) vs Plan B (Direct API)

## The Key Insight: Query Hash Maintenance

This turned out to be the deciding factor. Twitter's GraphQL endpoints use URLs like:
```
https://x.com/i/api/graphql/{HASH}/Bookmarks
```
The hash changes every time Twitter redeploys (roughly weekly).

| | Plan A (Playwright) | Plan B (Direct API) |
|--|---------------------|---------------------|
| **Needs query hashes?** | **NO** | YES |
| **Why?** | Browser loads Twitter's JS bundle which contains current hashes. We just intercept responses by matching the operation name (`/graphql/.+/Bookmarks`). | Must construct full URLs with correct hashes. Stale hash = 400 error. |
| **Hash maintenance** | Zero | Weekly manual updates, or fragile auto-discovery from main.js bundle |

This is exactly how `twitter-web-exporter` works — it pattern-matches URLs like `!/\/graphql\/.+\/Likes/` and never hardcodes a hash. Both `twitter-likes-export` and `twitter-advanced-scraper` hardcode hashes and break when they rotate.

## Approach Summary

### Plan A: Playwright + GraphQL Response Interception
- Launch headless Chromium with Firefox cookies injected
- Navigate to bookmarks/likes page
- `page.on("response")` captures GraphQL JSON as the browser fetches it
- Auto-scroll triggers pagination (browser handles cursor passing natively)
- Store raw JSON responses in embedded DB

### Plan B: Direct HTTP API Client (requests)
- Extract cookies from Firefox, construct auth headers manually
- Build GraphQL GET requests with hardcoded hashes + feature flags + variables
- Handle cursor-based pagination ourselves
- Store raw JSON responses in embedded DB

### Also Considered: DOM Scraping (download_twitter_likes approach)
- Playwright navigates to likes page, scrolls, queries `<article>` DOM elements
- Extracts tweet URLs, images, videos from rendered HTML
- Downloads media directly (images via requests, videos via ffmpeg + m3u8)
- **Not viable for our use case**: only captures what's visible in DOM (lossy), breaks on any UI change, focused on media download not data export

## Side-by-Side Comparison

| Dimension | Plan A (Playwright) | Plan B (Direct API) |
|-----------|---------------------|---------------------|
| **Query hashes** | Not needed | Must track & update |
| **Feature flags** | Not needed (browser handles) | Must track & update |
| **Auth complexity** | Inject cookies, browser does the rest | Must construct exact header set (Bearer + CSRF + cookies + 7 other headers) |
| **Pagination** | Browser handles natively via scroll | Must extract cursors, build next request |
| **Response schema changes** | Transparent (we store raw JSON) | Cursor extraction may break |
| **Runtime** | Heavy (~300-500MB RAM, 2-10 min) | Light (~10MB RAM, 30-60 sec) |
| **Dependencies** | playwright (~150MB Chromium binary) | requests only |
| **Speed** | Slow (browser + render + scroll waits) | Fast (pure HTTP) |
| **Debuggability** | Medium (can use --headful, but browser is opaque) | High (HTTP status codes, curl-reproducible) |
| **CAPTCHA/JS challenges** | Handles via --headful fallback | Completely blocked |
| **Rate limiting** | Implicit (scroll pace) | Explicit control (delay param) |
| **Cron suitability** | OK but heavy | Excellent if hashes are current |
| **Maintenance burden** | **Low** (only breaks if Twitter changes page UX) | **High** (breaks on every deploy with new hashes) |

## Shared Code (~60%)

Both plans share these modules identically:

| Module | Purpose | Lines (est.) |
|--------|---------|-------------|
| `firefox_creds.py` | Extract cookies from Firefox cookies.sqlite | ~60 |
| `db.py` | Embedded DB storage layer | ~80 |
| `models.py` | Data models / schema definitions | ~40 |
| `cli.py` | CLI entry point, arg parsing | ~60 |

Only the **data fetching** layer differs:
- Plan A: `scraper.py` (~150 lines) — Playwright browser automation + response interception
- Plan B: `client.py` (~150 lines) — requests HTTP client + cursor pagination
- Plan B also needs: `config.py` (~60 lines) — endpoint hashes, feature flags (maintenance surface)

## Should We Implement Both as Adapters?

**Yes, but with Plan A as primary and Plan B as optional/secondary.**

The adapter pattern is natural here since 60% of code is shared:

```
cli.py
  └── fetcher interface: fetch_all(target, cookies) -> raw JSON pages
        ├── PlaywrightFetcher (scraper.py)  ← default
        └── APIFetcher (client.py)          ← opt-in via --direct flag
```

```
python -m twitter.cli bookmarks              # Plan A (default, zero maintenance)
python -m twitter.cli bookmarks --direct     # Plan B (fast, but needs current hashes)
```

### Why bother with Plan B at all?

- **Speed**: 30 seconds vs 5 minutes for a full export
- **Resource usage**: great for frequent cron (every hour)
- **Environments without display**: headless servers where Playwright's Chromium has issues
- **Parallel requests**: can fetch bookmarks + likes simultaneously (Playwright can too but needs multiple browser contexts)

### Why Plan A should be default?

- **Zero maintenance** — no hashes, no feature flags, no header construction
- **Resilient** — if Twitter changes their API contract, the browser adapts; we just capture what comes back
- **Proven pattern** — exactly how twitter-web-exporter works (actively maintained, ~800 stars)

## Query Hash Auto-Discovery: Can We Solve Plan B's Problem?

Theoretically yes, practically fragile:

1. **Fetch x.com** → parse HTML for `<script src="main.XXXXX.js">`
2. **Fetch the JS bundle** (~2-5MB minified)
3. **Regex for operation mappings** like `{queryId:"yzqS_xCIaJPMb0v5B9dMmA",operationName:"Bookmarks",...}`

Problems:
- Twitter's bundle is webpack-chunked; operation-hash mappings may be split across chunks
- Minification changes variable names unpredictably
- Multiple JS bundles loaded dynamically; the right one may require executing JS to discover
- No existing tool in our repos implements this — it's entirely theoretical
- **If we're going to launch a browser to parse JS bundles anyway, we might as well just use Plan A**

**None of the 5 repos we reviewed implement auto-discovery.** The tools that need hashes (twitter-likes-export, twitter-advanced-scraper) just hardcode them.

## Recommendation

```
                        ┌─────────────────────┐
                        │     Shared Core      │
                        │  firefox_creds.py    │
                        │  db.py / models.py   │
                        │  cli.py              │
                        └──────────┬──────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │               │
              ┌─────▼─────┐ ┌─────▼──────┐  ┌────▼────┐
              │ Playwright │ │ Direct API │  │ Future  │
              │ (default)  │ │ (--direct) │  │ adapters│
              └────────────┘ └────────────┘  └─────────┘
```

**Start with Plan A (Playwright) only.** It's lower maintenance, equally capable, and the `download_twitter_likes` repo proves the Playwright + Firefox cookies + headless approach works for Twitter specifically.

Add Plan B as a second adapter later **only if** the Playwright overhead becomes a real problem for cron frequency. The shared interface makes this a clean addition at any point.

## Implementation Priority

1. `firefox_creds.py` — shared, both plans need it
2. `db.py` + `models.py` — shared storage layer
3. `scraper.py` — Plan A Playwright fetcher (primary)
4. `cli.py` — wire it together
5. *(later, if needed)* `client.py` + `config.py` — Plan B direct API fetcher
