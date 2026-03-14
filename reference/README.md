# Reference Materials

Third-party repos, gists, and saved pages collected during research for the twitter-export project. These are reference copies — not our code.

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
