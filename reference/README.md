# Reference Materials

Third-party repos, gists, and saved pages collected during research for the tweetxvault project. These are reference copies — not our code.

## Repos

### twitter-web-exporter
- **Source**: https://github.com/prinsss/twitter-web-exporter
- **What**: TypeScript UserScript (Tampermonkey/Violentmonkey) that intercepts Twitter's GraphQL API responses in-browser. Exports bookmarks, likes, tweets, followers, following, DMs to JSON/CSV/HTML. Bulk media download. No API keys needed.
- **Key insight**: Uses XHR hooking + regex pattern matching on operation names (`/graphql/.+/Bookmarks`), never hardcodes query hashes. This is the approach our Plan A is modeled after.
- **Status**: Actively maintained (last updated Mar 2026)

### twitter-likes-export
- **Source**: https://github.com/gasser707/twitter-likes-export
- **What**: Python scripts to export likes via Twitter's GraphQL API (`/graphql/{hash}/Likes`). Requires manual credential extraction from browser DevTools. Outputs JSON + optional HTML with downloaded media.
- **Key insight**: Shows the exact GraphQL request structure, variables, feature flags, and cursor pagination for the Likes endpoint. Hardcodes query hash (breaks on rotation).
- **Status**: Minimal (single commit, Feb 2025)

### twitter-advanced-scraper
- **Source**: https://github.com/Mahdi-hasan-shuvo/twitter-advanced-scraper
- **What**: Python library for searching tweets by keyword or user profile with engagement filtering (min likes/views/comments). Uses GraphQL API with cookie rotation.
- **Key insight**: Shows cookie rotation strategy for rate limit avoidance. Hardcodes query hashes. Includes Selenium-based cookie extraction from automated login.
- **Status**: Active (last updated Mar 2026)

### download_twitter_likes
- **Source**: https://github.com/raviddog/download_twitter_likes
- **What**: Playwright (Python, sync API) script that scrolls through your likes page and downloads media (images, GIFs, m3u8 videos via ffmpeg). Uses Firefox with injected session cookies. SQLite dedup tracking.
- **Key insight**: DOM scraping approach — parses `<article>` elements and `<img>`/`<video>` tags instead of intercepting GraphQL. More fragile but demonstrates Playwright + cookie injection pattern. Also shows m3u8 video download via ffmpeg.
- **Status**: Active (includes downloaded_clean.db seed)

### Siftly
- **Source**: https://github.com/lhl/Siftly (fork)
- **What**: Self-hosted AI-powered Twitter bookmark manager. Next.js + SQLite + Anthropic API. Imports bookmarks via bookmarklet or console script, then runs a 4-stage AI pipeline (entity extraction, vision analysis, semantic tagging, categorization). Interactive mindmap visualization.
- **Key insight**: Shows what's possible for bookmark enrichment/organization beyond raw export.
- **Status**: Active (last updated Mar 2026)

### xkit
- **Source**: https://github.com/rxliuli/xkit
- **What**: Web tool for visualizing your Twitter interaction network as a D3.js force-directed graph. Analyzes replies and likes to identify your core social circle. Exports as PNG.
- **Key insight**: Analysis/visualization tool, not an exporter. Shows interaction pattern analysis approach.
- **Status**: Active (last updated Oct 2025)

### tweethoarder
- **Source**: https://github.com/tfriedel/tweethoarder
- **What**: Python local archiver for likes, bookmarks, tweets, reposts, and home feed. Stores in SQLite, exports JSON/Markdown/CSV/searchable HTML. Cookie-based auth against internal GraphQL API. ~5,600 lines Python.
- **Origin**: Started Jan 2, 2026. 100 commits (65 on day one). Built with Claude Code (Opus 4.5) using beads for task coordination. Architecture ported from [bird](https://github.com/steipete/bird) (TypeScript). Essentially entirely AI-generated code — no copyright protection under current law.
- **Key insight**: Closest existing tool to our project — same stack (Python + SQLite + cookie auth + internal GraphQL). Solves query hash auto-discovery (JS bundle parsing with 4 regex patterns + fallback list + 24h TTL cache). Good reference for feature flags (~60 per endpoint), checkpoint/resume, adaptive rate limiting, and export formats. Does NOT do media downloads, search/embeddings, or scheduling.
- **Status**: Active (last updated Mar 2026)

### helioLJ-TweetVault
- **Source**: https://github.com/helioLJ/TweetVault
- **What**: Self-hosted bookmark archive (Go 1.23 backend + Next.js 15 frontend + PostgreSQL, Docker Compose). Imports via ZIP upload from twitter-web-exporter extension. Stores media as Postgres blobs, tag management with per-bookmark completion tracking. ~3,600 LOC.
- **Key insight**: Uses a PostgreSQL **materialized view** to pre-compute aggregated bookmark data (tags + media as JSON), refreshed every 5 min via goroutine — good pattern for read-heavy workloads with complex joins. Also tracks completion status per bookmark-tag pair (mark "To Read" as done without removing the tag).
- **Status**: Active (MIT license)

### TUNA-NOPE-TweetVault
- **Source**: https://github.com/TUNA-NOPE/TweetVault
- **What**: AI-powered post-processing tool — reads already-exported bookmark JSON, batch-classifies tweets via OpenRouter's free LLM API, generates organized Markdown output by category. Python CLI (~620 LOC) + Next.js web UI (~1,100 LOC).
- **Key insight**: LLM can **dynamically create new categories** if tweets don't fit existing ones. Smart rate limiting with per-minute tracking, daily API caps with auto-sleep until midnight, and resumable batch processing (progress saved after every batch of 10). The auto-classification concept could inspire a future feature using local embeddings or LLM to auto-tag bookmarks by topic.
- **Status**: Active (MIT license)

### UserScripts
- **Source**: https://github.com/ChinaGodMan/UserScripts
- **What**: Large collection (100+) of UserScripts. Twitter-relevant scripts include: `twitter-media-downloader` (one-click image/video download), `twitter-download-blocklist`, `twitter-hide-reposts`, `twitter-show-date-normally`.
- **Status**: Active community project (last updated Mar 2026)

## Gists

### gd3kr-twitter-bookmarks
- **Source**: https://gist.github.com/gd3kr/948296cf675469f5028911f8eb276dbc
- **What**: ~80 line JS console snippet. Paste into DevTools on bookmarks page — auto-scrolls, captures tweet text via `MutationObserver` on `[data-testid="tweetText"]`, downloads as JSON.
- **Key insight**: Minimal proof that DOM scraping + auto-scroll works. Text-only (no metadata, media, or author info). Useful as emergency manual fallback.

## Saved Pages

### How to export Twitter following list + likes, posts
- **Source**: https://folk.app/blog/how-to-export-twitter-followers (saved HTML + assets)
- **What**: Blog post from folk.app covering various Twitter export methods for non-technical users.

## Non-repo Data

### twitter/
- **What**: Personal Twitter archive — monthly tweet dumps (2006-2022) as markdown, parsed archive data, extracted following/followers lists (JSON), media files. Not a git repo.
