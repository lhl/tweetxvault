"""Static query ID configuration."""

from __future__ import annotations

from tweetxvault.config import BUNDLE_URL_REGEX, CLIENT_WEB_BUNDLE_BASE, DISCOVERY_PAGE_URL

DISCOVERY_PAGE_URLS = (DISCOVERY_PAGE_URL,)
CLIENT_WEB_BUNDLE_URL = CLIENT_WEB_BUNDLE_BASE
BUNDLE_URL_PATTERN = BUNDLE_URL_REGEX
TARGET_OPERATIONS = (
    "Bookmarks",
    "Likes",
    "BookmarkFolderTimeline",
    "TweetDetail",
    "UserArticlesTweets",
)

# Sourced from X web bundles fetched on 2026-03-14.
FALLBACK_QUERY_IDS = {
    "Bookmarks": "Fy0QMy4q_aZCpkO0PnyLYw",
    "Likes": "a2vYKkx2AtoCmEIRO8Gfbw",
    "BookmarkFolderTimeline": "hNY7X2xE2N7HVF6Qb_mu6w",
    "TweetDetail": "9rs110LSoPARDs61WOBZ7A",
    "UserArticlesTweets": "Z_dacytwC8WEeV3U7XnW5A",
}
