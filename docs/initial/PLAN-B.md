# Plan B: Direct GraphQL API Client (requests)

## Overview

Use Python `requests` to hit Twitter's GraphQL API endpoints directly — the same endpoints the web app calls (`/graphql/{hash}/Bookmarks`, `/graphql/{hash}/Likes`, etc.). Credentials are extracted automatically from the active Firefox profile's `cookies.sqlite`. Handle cursor-based pagination in a loop. Store raw JSON responses in an embedded DB.

## Approach

No browser needed. We construct the exact same HTTP requests that the Twitter web app makes: Bearer token + cookies + CSRF header. We paginate by extracting cursor values from each response and passing them to the next request. This is essentially what `twitter-likes-export` does, generalized to multiple endpoints.

## File Tree

```
~/github/lhl/attention-export/
 ├── twitter/
 │    ├── NEW  __init__.py
 │    ├── NEW  config.py          # Endpoint configs, query hashes, feature flags
 │    ├── NEW  firefox_creds.py   # Extract cookies/csrf from Firefox cookies.sqlite
 │    ├── NEW  client.py          # GraphQL API client with pagination
 │    ├── NEW  db.py              # DB storage layer (lancedb or duckdb)
 │    ├── NEW  models.py          # Data models / schema definitions
 │    └── NEW  cli.py             # CLI entry point
 └── UPDATE README.md
```

## Architecture

```
Firefox cookies.sqlite ──> firefox_creds.py ──> auth_token + ct0
                                                      │
                                                      v
                                              client.py (requests)
                                              ┌─────────────────────┐
                                              │  1. Build headers    │
                                              │     (Bearer + CSRF) │
                                              │  2. Construct query  │
                                              │     variables       │
                                              │  3. GET endpoint     │
                                              │  4. Extract cursor   │
                                              │  5. Loop until done  │
                                              └────────┬────────────┘
                                                       │ raw JSON
                                                       v
                                                    db.py ──> embedded DB
```

## Detailed Design

### 1. Firefox Credential Extraction (`firefox_creds.py`)

**Identical to Plan A** — same module, same approach. Extracts cookies from `~/.mozilla/firefox/profile.default/cookies.sqlite` (plaintext on Linux, copy to temp file to avoid WAL lock).

Critical cookies needed:
- `auth_token` — session authentication
- `ct0` — CSRF token (sent as both cookie and `x-csrf-token` header)

### 2. Endpoint Configuration (`config.py`)

Twitter's GraphQL endpoints have operation-specific query hashes that change when Twitter redeploys. We need to track these.

```python
# Well-known public Bearer token — embedded in Twitter's web app JS bundle.
# Same token used by twitter-web-exporter, twitter-advanced-scraper, and
# every browser session. It identifies the "Twitter Web App" client, not the user.
BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# GraphQL operation hashes — these change when Twitter redeploys.
# Last verified: 2026-03-14
# Source: browser DevTools network tab or twitter-web-exporter source
ENDPOINTS = {
    "Bookmarks": {
        "hash": "yzqS_xCIaJPMb0v5B9dMmA",  # changes periodically
        "path": "/i/api/graphql/{hash}/Bookmarks",
    },
    "Likes": {
        "hash": "QK8AVO3RpcnbLPKXLAiVog",  # from twitter-likes-export
        "path": "/i/api/graphql/{hash}/Likes",
    },
    "UserTweets": {
        "hash": "CdG2Vuc1v6F5JyEngGpxVw",
        "path": "/i/api/graphql/{hash}/UserTweets",
    },
    "Following": {
        "hash": "PAnE9toEuR1Y0HzMCAvjYA",
        "path": "/i/api/graphql/{hash}/Following",
    },
}

# Feature flags — sent with every request. Twitter changes these over time.
# If a request 400s, these likely need updating from a fresh browser capture.
DEFAULT_FEATURES = {
    "responsive_web_twitter_blue_verified_badge_is_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "view_counts_public_visibility_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_uc_gql_enabled": True,
    "vibe_api_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": False,
    "interactive_text_enabled": True,
    "responsive_web_text_conversations_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}
```

**Hash discovery strategy** (when hashes go stale):
1. Open x.com in browser, go to bookmarks/likes
2. DevTools > Network > filter `graphql` > find the operation
3. Copy the hash from the URL
4. Or: scrape the main.js bundle for the operation-to-hash mapping (automatable but fragile)

### 3. GraphQL API Client (`client.py`)

```python
import requests
import json
import time
import urllib.parse

class TwitterGraphQLClient:
    BASE_URL = "https://x.com"

    def __init__(self, cookies: dict, db):
        self.db = db
        self.session = requests.Session()

        # Build cookie header string
        self.session.cookies.update(cookies)

        # Required headers for all requests
        self.session.headers.update({
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "x-csrf-token": cookies["ct0"],
            "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-client-language": "en",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:137.0) Gecko/20100101 Firefox/137.0",
            "Referer": "https://x.com/",
            "Origin": "https://x.com",
        })

    def fetch_all(self, operation: str, user_id: str = None, count: int = 100,
                  delay: float = 2.0):
        """
        Paginate through a GraphQL endpoint until exhausted.
        Stores each page's raw response in the DB.
        """
        endpoint = ENDPOINTS[operation]
        url = f"{self.BASE_URL}{endpoint['path'].format(hash=endpoint['hash'])}"

        cursor = None
        page_num = 0

        while True:
            variables = self._build_variables(operation, user_id, count, cursor)
            params = {
                "variables": json.dumps(variables),
                "features": json.dumps(DEFAULT_FEATURES),
            }

            resp = self.session.get(url, params=params)

            if resp.status_code == 429:
                # Rate limited — back off
                retry_after = int(resp.headers.get("retry-after", 60))
                time.sleep(retry_after)
                continue

            if resp.status_code != 200:
                raise Exception(f"{operation} returned {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            self.db.store_raw(operation, data)
            page_num += 1

            # Extract next cursor
            new_cursor = self._extract_cursor(data)
            if not new_cursor or new_cursor == cursor:
                break  # no more pages
            cursor = new_cursor

            time.sleep(delay)  # be polite

    def _build_variables(self, operation, user_id, count, cursor):
        """Build the variables dict for each operation type."""
        variables = {"count": count, "includePromotedContent": False}

        if operation in ("Likes", "UserTweets", "Following"):
            variables["userId"] = user_id

        if cursor:
            variables["cursor"] = cursor

        return variables

    def _extract_cursor(self, response_json: dict) -> str | None:
        """
        Walk the response to find the bottom pagination cursor.
        Twitter timeline responses have entries where the last entry
        (or entries with cursorType="Bottom") contain the next cursor.
        """
        try:
            instructions = (
                response_json.get("data", {})
                .get("user", response_json.get("data", {}))  # some ops nest under "user"
                .get("result", {})
                .get("timeline_v2", response_json.get("data", {}).get("bookmark_timeline_v2", {}))
                .get("timeline", {})
                .get("instructions", [])
            )
            for instruction in instructions:
                entries = instruction.get("entries", [])
                for entry in reversed(entries):
                    content = entry.get("content", {})
                    if content.get("cursorType") == "Bottom":
                        return content.get("value")
                    # Alternative: last entry is cursor
                    if "value" in content and entry.get("entryId", "").startswith("cursor-bottom"):
                        return content["value"]
        except (KeyError, AttributeError):
            pass
        return None
```

**Key details:**
- **GET requests** with URL-encoded JSON params (same as browser)
- **Rate limit handling**: respect `429` + `retry-after` header, back off
- **Cursor pagination**: extract `cursor-bottom-*` entry from each response, pass as `cursor` variable
- **Polite delay**: configurable `delay` between pages (default 2s)
- **Raw JSON storage**: full response saved, parsing is a separate concern

### 4. User ID Discovery

The `Likes` and `UserTweets` endpoints require a numeric `userId`. We can get this from:

```python
def get_user_id(self, screen_name: str) -> str:
    """Resolve @handle to numeric user_id via UserByScreenName."""
    url = f"{self.BASE_URL}/i/api/graphql/xmU6X_CKVnQ5lSrCbAmJsg/UserByScreenName"
    params = {
        "variables": json.dumps({"screen_name": screen_name}),
        "features": json.dumps(DEFAULT_FEATURES),
    }
    resp = self.session.get(url, params=params)
    data = resp.json()
    return data["data"]["user"]["result"]["rest_id"]
```

Or extract from the `twid` cookie: `twid=u%3D1234567890` -> user_id = `1234567890`.

### 5. DB Storage (`db.py`)

Same interface as Plan A:

```python
class TwitterDB:
    def store_raw(self, operation: str, response_json: dict):
        """Store raw GraphQL response with metadata.
        Fields: id, operation, captured_at, page_num, cursor, raw_json, source="api"
        """

    def get_tweets(self, operation: str = None) -> list:
        """Query stored data."""

    def deduplicate(self):
        """Dedupe by tweet rest_id."""
```

### 6. CLI (`cli.py`)

```
python -m twitter.cli bookmarks              # export bookmarks
python -m twitter.cli likes --user lhl       # export likes (resolves user_id)
python -m twitter.cli all --user lhl         # export everything
python -m twitter.cli --delay 5 bookmarks    # slower pagination
python -m twitter.cli --profile PATH likes   # custom Firefox profile
```

## Handling Stale Query Hashes

This is the main maintenance burden. Options (from least to most effort):

1. **Manual update**: When a request 400s, open DevTools, grab new hash, update `config.py`. Takes ~30 seconds.
2. **Auto-discover from main.js**: Twitter's JS bundle contains a mapping of operation names to hashes. We could fetch and parse the bundle at startup. Fragile but automatable.
3. **Hybrid approach (recommended)**: Try the stored hash; on 400, attempt auto-discovery from the JS bundle; if that fails, error with instructions to manually update.

```python
def discover_query_hash(self, operation_name: str) -> str:
    """Attempt to discover current query hash from Twitter's JS bundle."""
    # 1. Fetch x.com, find main.*.js URL in HTML
    # 2. Fetch the JS bundle
    # 3. Regex for the operation name near a hash string
    # This is fragile but works as a fallback
    ...
```

## Pros

- **Fast** — pure HTTP, no browser overhead; full export in 30-60 seconds for typical volumes
- **Lightweight** — only needs `requests` (already installed everywhere); ~5MB vs Playwright's ~150MB
- **Precise control** — exact rate limiting, retry logic, page size, delays; easy to tune
- **Easy to debug** — HTTP status codes, response bodies, curl-reproducible; no browser black box
- **Cronnable** — minimal resource usage; can run every hour without concern
- **Predictable pagination** — cursor-based with clear termination condition (cursor == previous or empty)
- **Portable** — works anywhere Python + requests works; no browser binary management

## Cons

- **Query hashes go stale** — Twitter changes GraphQL operation hashes on every deploy (roughly weekly). When they change, requests 400 until updated. This is the **#1 maintenance burden**.
- **Feature flags drift** — the `features` JSON blob changes over time; stale flags can cause 400 errors or missing data
- **More brittle to API changes** — if Twitter restructures their GraphQL response schema (nesting, field names), our cursor extraction and any parsing breaks
- **No JS execution** — can't handle CAPTCHAs, login walls, or consent dialogs; if Twitter gates bookmarks behind a JS challenge, this approach is dead
- **Auth header complexity** — must correctly construct Bearer + CSRF + cookies + all required headers; one wrong header = 403
- **Bookmarks endpoint is newer and less documented** — the Likes endpoint hash (`QK8AVO3RpcnbLPKXLAiVog`) from twitter-likes-export may already be stale; Bookmarks hash needs fresh capture
- **No fallback** — if the API approach fails, there's no graceful degradation; Plan A can at least show you the browser

## Cookie Lifetime & Refresh

Same as Plan A:
- `auth_token` lasts ~1 year
- `ct0` rotates but is refreshed when you use Firefox normally
- For cron: re-extract from Firefox before each run
- If expired: log in to x.com in Firefox; no programmatic refresh possible without storing credentials

## Estimated Complexity

- **firefox_creds.py**: ~60 lines (shared with Plan A)
- **config.py**: ~60 lines (endpoint configs, feature flags — the part that needs periodic updates)
- **client.py**: ~150 lines (HTTP client, pagination, cursor extraction, hash discovery)
- **db.py**: ~80 lines (shared with Plan A)
- **cli.py**: ~50 lines
- **Total**: ~400 lines of Python

## Dependencies

```
requests
# DB: one of:
# lancedb
# duckdb
```

## Maintenance Checklist (for when things break)

When requests start returning 400/403:

1. **Check query hashes**: Open x.com > DevTools > Network > filter `graphql` > find the operation > copy new hash from URL > update `config.py`
2. **Check feature flags**: Compare `features` param in browser request vs `config.py` > update any differences
3. **Check Bearer token**: Extremely rarely changes (has been the same for years), but verify if all else fails
4. **Check cookies**: Run `python -m twitter.cli check-auth` to verify cookies are still valid
