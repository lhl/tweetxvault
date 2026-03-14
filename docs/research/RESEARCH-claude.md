https://claude.ai/chat/5aa18c09-e9f9-4b23-96af-4f80ad5f1f38

# Exporting your Twitter/X bookmarks and likes in 2026

**The official Twitter/X API is prohibitively expensive for personal bookmark export, but a thriving ecosystem of browser extensions, userscripts, and open-source scrapers fills the gap effectively.** The most reliable methods bypass the official API entirely, instead intercepting Twitter's internal GraphQL API through authenticated browser sessions. The official X data archive does not include bookmarks at all, making third-party tools essential. This guide covers every working approach as of early 2026, organized from simplest to most technical.

## The official API is essentially off-limits for personal use

Twitter/X's API pricing makes personal data export impractical through official channels. The **Free tier ($0)** cannot access bookmarks or likes endpoints—X removed like and follow endpoints from the free tier in 2025. The **Basic tier ($200/month)** grants access to likes but not bookmarks. The **Pro tier ($5,000/month)** is the minimum for bookmark API access, and even then imposes an **800-bookmark hard cap**—users with larger collections simply cannot retrieve their full history through the official API.

A **pay-per-use beta** launched in November 2025 may eventually offer granular per-endpoint pricing at lower cost, but it remains in limited availability as of early 2026. The practical reality: nobody should pay $200–5,000/month just to export their own data. Every method below works around these restrictions.

| Tier | Monthly cost | Bookmarks access | Likes access | Bookmark limit |
|------|-------------|-----------------|-------------|----------------|
| Free | $0 | ❌ | ❌ (removed 2025) | — |
| Basic | $200 | ❌ | ✅ | — |
| Pro | $5,000 | ✅ | ✅ | 800 max |
| Enterprise | $42,000+ | ✅ | ✅ | Custom |

## Browser userscripts and extensions are the easiest path

The single best free tool for most users is **Twitter Web Exporter** by prinsss, an open-source userscript (installed via Tampermonkey or Violentmonkey) last updated **February 25, 2026**. It intercepts GraphQL API responses directly within your logged-in browser session—no API keys, no developer account, no authentication setup required. It exports bookmarks, likes, tweets, followers, and more to JSON, CSV, or HTML, and supports bulk media download as ZIP files. Crucially, it **bypasses the 800-bookmark API limit** since it captures data as the web app loads it. The main tradeoff: you must manually scroll through your bookmarks/likes page to load the data (though community auto-scroll workarounds exist).

For users wanting a simpler install-and-click experience, several Chrome extensions are actively maintained:

- **X Bookmarks Exporter** (by ExtensionsBox, updated January 2026): Exports to CSV, JSON, or XLSX. Free for up to 20 items; **$9.90 one-time** for unlimited exports with media downloads. Has companion extensions for likes, posts, and followers.
- **Bookmark Save** (bookmarksave.com): Completely free with no artificial limits. Exports to PDF, CSV, Markdown, and TXT. Includes a "Smart Feed Cleanup" feature that auto-removes bookmarks after export.
- **Twillot** (twillot.com, open-source on GitHub): Exports both bookmarks and likes to CSV/JSON with AI-powered categorization. Freemium model with basic and pro tiers.
- **ArchivlyX** (archivlyx.com): Handles bookmarks, likes, tweets, and followers. Exports to CSV, Markdown, JSON, with Notion integration. Free core features; **$9.90 one-time** for pro.
- **Dewey** (getdewey.co, updated December 2025): The most established bookmark manager, now supporting X, Bluesky, LinkedIn, and other platforms. Exports to CSV, PDF, Google Sheets. **$10/month** or **$225 lifetime**.

Firefox users have solid options too: **Xporter** (free, CSV export of bookmarks), **booksave** (free, bookmarks + likes + media), and the comprehensive **Twitter Exporter** add-on which handles bookmarks, likes, followers, DMs, and more with automatic rate-limit handling.

## Open-source GitHub repos for programmatic export

For developers who want scriptable, automated export, several actively maintained repositories stand out. All use the reverse-engineered Twitter GraphQL API rather than the official (expensive) v2 API.

**prinsss/twitter-web-exporter** (TypeScript, updated February 2026) remains the gold standard even as a GitHub project—it works as both a userscript and has been adapted into Firefox extensions. It captures everything visible in the Twitter web app without sending its own API requests.

**vladkens/twscrape** (~2,200 stars, v0.17.0 released April 2025) is the most popular Python library for Twitter scraping. It supports bookmark export via its `bookmarks()` method, handles multiple account rotation for rate limits, and outputs SNScrape-compatible JSON. One important caveat: the **likes endpoint was deprecated** in v0.13 because X restricted the underlying API. Issues were still being filed through January 2026, suggesting active community use.

**trevorhobenshield/twitter-api-client** (~1,900 stars) offers the most comprehensive API coverage in Python, wrapping v1, v2, and GraphQL APIs with explicit `likes()` and `bookmarks` methods. However, its last commit was May 2024, and maintenance has visibly slowed—use with caution as GraphQL endpoints rotate.

**imperatrona/twitter-scraper** (Go, updated April 2025) provides `GetBookmarks()` and `FetchBookmarks()` methods with pagination support, making it the best option for Go developers. It's a well-maintained fork of the original n0madic/twitter-scraper.

For the simplest possible approach, a **browser console gist by gd3kr** lets you paste JavaScript directly into your browser's developer console on the bookmarks page. It auto-scrolls, collects tweet data, and downloads a JSON file—zero installation required.

| Repository | Language | Bookmarks | Likes | Stars | Last active |
|-----------|----------|-----------|-------|-------|-------------|
| prinsss/twitter-web-exporter | TypeScript | ✅ | ✅ | High | Feb 2026 |
| vladkens/twscrape | Python | ✅ | ❌ (deprecated) | 2,200+ | Jan 2026 |
| trevorhobenshield/twitter-api-client | Python | ✅ | ✅ | 1,900 | May 2024 |
| imperatrona/twitter-scraper | Go | ✅ | ❌ | Active | Apr 2025 |
| Altimis/Scweet | Python | ❌ | ✅ | 898 | Feb 2026 |

Notable dead projects: **twint** (16,300 stars, archived March 2023) and **snscrape** (public data only, no bookmark/like support) are no longer viable options.

## Authentication relies on browser cookies, not API keys

The standard authentication method across virtually all working tools is **cookie-based auth** using two values extracted from your browser: the `auth_token` cookie and the `ct0` cookie (CSRF token). The extraction process is straightforward: log into x.com, open Developer Tools (F12), navigate to Application → Cookies → x.com, and copy both values.

Tools that operate as browser extensions or userscripts (like Twitter Web Exporter, Dewey, Twillot) skip this entirely—they piggyback on your existing logged-in session automatically. This is the simplest path for non-technical users.

For programmatic tools, several utilities automate cookie extraction: **twitter-cli** and **bird** scan Chrome, Firefox, Arc, and Brave browser profiles to decrypt and extract cookies automatically. **tweepy-authlib** (v1.7.0, October 2025) can authenticate with username/password by simulating the web login flow with TLS fingerprint spoofing, though it's now in maintenance-end status.

A significant challenge emerged in **October 2025** when Twitter introduced **castle.io bot detection** on the web login form, making programmatic login harder. Cookie extraction from an existing browser session remains unaffected and is now the strongly recommended approach. GraphQL query IDs also rotate periodically, causing 404 errors in tools that cache them—actively maintained tools handle this automatically.

## The official archive skips bookmarks entirely

Twitter's "Download an archive of your data" feature (Settings → Your Account → Download an archive) produces a ZIP file containing JS/JSON data files and an interactive HTML viewer. It **does not include bookmarks**—this is confirmed by multiple technical sources and tool developers. It does include **likes as tweet IDs**, but without timestamps indicating when tweets were liked, and with all engagement metrics zeroed out.

The archive contains tweets, retweets, replies, DMs, follower/following lists (as opaque user IDs, not usernames), profile data, muted/blocked accounts, and advertising personalization data. Media files are included but may be compressed. The archive is free to request, processes in 24–48 hours, and can be requested once per day. For converting archive data into something usable, **Tweetback** (open-source by Zach Leatherman) builds a searchable static website from your `tweets.js` file.

## Paid services worth considering for ongoing sync

For users who want a set-and-forget solution rather than one-time exports, two paid services stand out. **Readwise** ($9.99/month annual) automatically syncs new Twitter bookmarks once daily into its highlight system, which then flows to Obsidian, Notion, or other note-taking apps via official plugins. **Dewey** ($10/month or $225 lifetime) provides the most mature bookmark management experience across multiple platforms with AI tagging, Notion integration, and Google Sheets sync.

**IFTTT** and **Zapier** both support Twitter likes as triggers (enabling automatic saves to Google Sheets, Raindrop.io, or Notion) but **neither supports bookmarks as a trigger**—a critical limitation. For Notion-specific workflows, both Dewey and ArchivlyX offer direct integration.

## Conclusion

The landscape for exporting Twitter/X bookmarks and likes has bifurcated sharply: the official API is priced for businesses, not individuals, while the unofficial ecosystem has matured into reliable, well-maintained tools. For most users, **installing Twitter Web Exporter as a userscript is the single best move**—it's free, open-source, handles both bookmarks and likes, updated as recently as February 2026, and requires zero technical setup beyond installing Tampermonkey. Users wanting managed bookmark organization should look at Dewey or Readwise. Developers needing programmatic access should start with twscrape (Python) or imperatrona/twitter-scraper (Go), authenticating via browser cookies. The key risk across all unofficial tools is that Twitter/X periodically rotates its internal API structure, so sticking with actively maintained projects is essential—check commit dates before committing to any tool.
