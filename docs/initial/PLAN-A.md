# Plan A: Playwright Browser Automation + GraphQL Interception

## Overview

Use Playwright (Python) to drive a real browser session on x.com, intercept the same GraphQL API responses that `twitter-web-exporter` captures, auto-scroll to paginate, and dump raw JSON into an embedded DB. Credentials are extracted automatically from the active Firefox profile's `cookies.sqlite`.

## Approach

Playwright loads x.com with injected Firefox cookies, navigates to the bookmarks/likes pages, and registers a `page.on("response")` handler that captures GraphQL responses matching target operation names. A scroll loop triggers Twitter's lazy-loading pagination. Raw response JSON is stored directly in the DB.

## File Tree

```
~/github/lhl/attention-export/
 ├── twitter/
 │    ├── NEW  __init__.py
 │    ├── NEW  config.py          # Constants, endpoint patterns, profile paths
 │    ├── NEW  firefox_creds.py   # Extract cookies/csrf from Firefox cookies.sqlite
 │    ├── NEW  scraper.py         # Playwright browser automation + GraphQL capture
 │    ├── NEW  db.py              # DB storage layer (lancedb or duckdb)
 │    ├── NEW  models.py          # Data models / schema definitions
 │    └── NEW  cli.py             # CLI entry point (bookmarks, likes, etc.)
 └── UPDATE README.md
```

## Architecture

```
Firefox cookies.sqlite ──> firefox_creds.py ──> auth cookies + ct0
                                                      │
                                                      v
                                              scraper.py (Playwright)
                                              ┌─────────────────────┐
                                              │  1. Launch browser   │
                                              │  2. Inject cookies   │
                                              │  3. Navigate to page │
                                              │  4. Register response│
                                              │     interceptor     │
                                              │  5. Auto-scroll loop │
                                              │  6. Collect GraphQL  │
                                              │     responses       │
                                              └────────┬────────────┘
                                                       │ raw JSON
                                                       v
                                                    db.py ──> embedded DB
```

## Detailed Design

### 1. Firefox Credential Extraction (`firefox_creds.py`)

**Source**: `~/.mozilla/firefox/profile.default/cookies.sqlite`

Firefox on Linux stores cookies as **plaintext** in SQLite — no decryption needed.

```python
import sqlite3
from pathlib import Path
import configparser

def get_default_firefox_profile() -> Path:
    """Parse profiles.ini to find the default profile path."""
    profiles_ini = Path.home() / ".mozilla/firefox/profiles.ini"
    config = configparser.ConfigParser()
    config.read(profiles_ini)
    # Find the Install* section with Default= or fall back to Profile0
    for section in config.sections():
        if section.startswith("Install") and "Default" in config[section]:
            rel_path = config[section]["Default"]
            return Path.home() / ".mozilla/firefox" / rel_path
    raise FileNotFoundError("No default Firefox profile found")

def extract_twitter_cookies(profile_path: Path = None) -> dict:
    """Extract x.com/twitter.com cookies from Firefox cookies.sqlite.

    Returns dict with cookie name -> value for all Twitter cookies.
    Critical cookies: auth_token, ct0 (CSRF), twid, personalization_id
    """
    if profile_path is None:
        profile_path = get_default_firefox_profile()
    db_path = profile_path / "cookies.sqlite"

    # Copy to temp file to avoid locking issues with running Firefox
    import shutil, tempfile
    tmp = Path(tempfile.mkdtemp()) / "cookies.sqlite"
    shutil.copy2(db_path, tmp)

    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT name, value FROM moz_cookies "
        "WHERE host LIKE '%.x.com' OR host LIKE '%.twitter.com' "
        "ORDER BY name"
    ).fetchall()
    conn.close()
    tmp.unlink()

    return {name: value for name, value in rows}

def get_auth_headers(cookies: dict) -> dict:
    """Build the required auth headers from extracted cookies."""
    return {
        "ct0": cookies.get("ct0", ""),       # used as x-csrf-token header
        "auth_token": cookies.get("auth_token", ""),
    }
```

**Key details:**
- Copy cookies.sqlite to a temp file first — Firefox holds a WAL lock on the live DB
- Firefox profile auto-detected from `profiles.ini` (default: `profile.default`)
- No encryption, no master password needed for cookies (only for saved logins)
- Critical cookies: `auth_token`, `ct0` (doubles as x-csrf-token)

### 2. Playwright Scraper (`scraper.py`)

**GraphQL endpoints to intercept** (from twitter-web-exporter analysis):

| Operation | URL Pattern | Data |
|-----------|-------------|------|
| `Bookmarks` | `*/graphql/*/Bookmarks*` | All bookmarked tweets |
| `Likes` | `*/graphql/*/Likes*` | All liked tweets |
| `UserTweets` | `*/graphql/*/UserTweets*` | User's own tweets |
| `Following` | `*/graphql/*/Following*` | Following list |
| `Followers` | `*/graphql/*/Followers*` | Followers list |

```python
import asyncio
from playwright.async_api import async_playwright, Page, Response
import json, re

# Well-known public Bearer token (same one used by twitter-web-exporter,
# twitter-advanced-scraper, and the official web app)
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

GRAPHQL_PATTERN = re.compile(r"/graphql/[^/]+/(Bookmarks|Likes|UserTweets|Following|Followers)")

class TwitterScraper:
    def __init__(self, cookies: dict, db):
        self.cookies = cookies
        self.db = db
        self.captured = []

    async def handle_response(self, response: Response):
        """Intercept GraphQL responses matching our target operations."""
        url = response.url
        match = GRAPHQL_PATTERN.search(url)
        if match and response.status == 200:
            operation = match.group(1)
            try:
                body = await response.json()
                self.db.store_raw(operation, body)
                self.captured.append(operation)
            except Exception:
                pass  # non-JSON response, skip

    async def scrape(self, target: str = "bookmarks", max_scrolls: int = 200):
        """
        Launch browser, inject cookies, navigate to target page,
        scroll to load all content, capturing GraphQL responses.
        """
        urls = {
            "bookmarks": "https://x.com/i/bookmarks",
            "likes": "https://x.com/{user_id}/likes",  # needs screen_name
            "following": "https://x.com/{user_id}/following",
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0"
            )

            # Inject cookies
            cookie_list = []
            for name, value in self.cookies.items():
                cookie_list.append({
                    "name": name, "value": value,
                    "domain": ".x.com", "path": "/"
                })
            await context.add_cookies(cookie_list)

            page = await context.new_page()
            page.on("response", self.handle_response)

            await page.goto(urls[target])
            await page.wait_for_load_state("networkidle")

            # Scroll loop — keep scrolling until no new content
            prev_count = 0
            stale_rounds = 0
            for i in range(max_scrolls):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)  # wait for lazy load

                if len(self.captured) == prev_count:
                    stale_rounds += 1
                    if stale_rounds >= 5:
                        break  # no new data after 5 scrolls
                else:
                    stale_rounds = 0
                    prev_count = len(self.captured)

            await browser.close()
```

**Key design decisions:**
- **Headless Chromium** (not Firefox) — Playwright's Chromium is more reliable for interception; Firefox cookies work fine cross-browser since we inject them via `context.add_cookies()`
- **Response interception** via `page.on("response")` — captures the full GraphQL JSON including all nested tweet/user data and pagination cursors
- **Scroll-based pagination** — mirrors how the web app loads data; no need to construct GraphQL queries ourselves
- **Stale detection** — stops after 5 consecutive scrolls with no new captured responses
- **Raw JSON storage** — we store the full API response, not parsed/flattened data, so nothing is lost

### 3. DB Storage (`db.py`)

Raw JSON goes into the embedded DB. Schema TBD (lancedb vs duckdb), but the interface is:

```python
class TwitterDB:
    def store_raw(self, operation: str, response_json: dict):
        """Store a raw GraphQL response with metadata."""
        # Fields: id (auto), operation, captured_at, raw_json, source="playwright"

    def get_tweets(self, operation: str = None) -> list:
        """Query stored tweets, optionally filtered by operation type."""

    def deduplicate(self):
        """Remove duplicate tweet entries by tweet rest_id."""
```

### 4. CLI (`cli.py`)

```
python -m twitter.cli bookmarks       # export bookmarks
python -m twitter.cli likes           # export likes
python -m twitter.cli all             # export everything
python -m twitter.cli --headful likes # visible browser for debugging
python -m twitter.cli --profile PATH  # custom Firefox profile path
```

## Pros

- **Captures everything the web app sees** — full tweet objects with all metadata, media URLs, engagement counts, quoted tweets, threads
- **No GraphQL query construction** — we don't need to know or maintain the query hashes, variables, or feature flags; the browser does it for us
- **Resilient to API changes** — if Twitter changes GraphQL schema, the browser still works; we just store whatever comes back
- **Cookie-only auth** — no Bearer token management; the browser handles all auth headers natively
- **Can handle JS-rendered content** — login walls, CAPTCHAs (with `--headful`), consent dialogs
- **Reuses twitter-web-exporter's proven interception approach** in a scriptable/cronnable form

## Cons

- **Heavy runtime** — Playwright downloads ~150MB Chromium; each run launches a full browser process (~300-500MB RAM)
- **Slower** — browser startup + rendering + scroll waits = 2-10 min per export depending on volume
- **Scroll-based pagination is fragile** — depends on Twitter's scroll-triggered lazy loading behavior; if they change the UX (e.g., "load more" button), the scroll loop breaks
- **Headless detection risk** — Twitter may detect headless Chromium (though Playwright has good stealth; `--headful` as fallback)
- **Harder to debug** — failures are browser-level (timeouts, DOM changes, network issues) rather than clean HTTP errors
- **No granular rate control** — we scroll at whatever pace; can't easily throttle API calls independently
- **Playwright dependency** — adds ~150MB to the project; browser binaries need periodic updates (`playwright install`)

## Cookie Lifetime & Refresh

- `auth_token` typically lasts **1 year** (long-lived session cookie)
- `ct0` (CSRF) rotates more frequently but is refreshed automatically by the browser during the session
- For cron: if `auth_token` is still valid in Firefox, it's valid for Playwright — just re-extract before each run
- If cookies expire: user must log in to x.com in Firefox once; no way to automate this without storing credentials

## Estimated Complexity

- **firefox_creds.py**: ~60 lines, straightforward SQLite reads
- **scraper.py**: ~150 lines, core complexity is the scroll loop + response handler
- **db.py**: ~80 lines, depends on DB choice
- **cli.py**: ~50 lines, argparse wrapper
- **Total**: ~340 lines of Python

## Dependencies

```
playwright>=1.49
# DB: one of:
# lancedb
# duckdb
```
