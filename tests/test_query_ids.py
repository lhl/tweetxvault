from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tweetxvault.query_ids.scraper import (
    extract_bundle_urls,
    extract_candidate_chunk_urls,
    extract_query_ids,
)
from tweetxvault.query_ids.store import QueryIdCache, QueryIdStore


def test_extract_bundle_urls() -> None:
    html = """
    <script src="https://abs.twimg.com/responsive-web/client-web/main.1234abcd.js"></script>
    <link rel="preload" href="https://abs.twimg.com/responsive-web/client-web/vendor.5678efgh.js">
    """
    urls = extract_bundle_urls(html)
    assert "https://abs.twimg.com/responsive-web/client-web/main.1234abcd.js" in urls
    assert "https://abs.twimg.com/responsive-web/client-web/vendor.5678efgh.js" in urls


def test_extract_query_ids_supports_multiple_patterns() -> None:
    snippet = """
    queryId:"abc1234567890123456789",operationName:"Bookmarks"
    {"queryId":"def1234567890123456789","operationName":"Likes"}
    operationName:"TweetDetail",queryId:"ghi1234567890123456789"
    """
    extracted = extract_query_ids(snippet)
    assert extracted["Bookmarks"] == "abc1234567890123456789"
    assert extracted["Likes"] == "def1234567890123456789"
    assert extracted["TweetDetail"] == "ghi1234567890123456789"


def test_extract_candidate_chunk_urls() -> None:
    manifest = (
        '"bundle.Bookmarks":"a060a2c",'
        '"shared~bundle.BookmarkFolders~bundle.Bookmarks":"877e5cb",'
        '"main":"1234abc"'
    )
    urls = extract_candidate_chunk_urls(manifest, ["Bookmarks", "Likes"])
    assert "https://abs.twimg.com/responsive-web/client-web/bundle.Bookmarks.a060a2ca.js" in urls
    assert (
        "https://abs.twimg.com/responsive-web/client-web/shared~bundle.BookmarkFolders~bundle.Bookmarks.877e5cba.js"
        in urls
    )


def test_query_id_store_freshness_and_fallback(paths) -> None:
    store = QueryIdStore(paths)
    saved = store.save({"Bookmarks": "fresh-bookmarks"})
    assert store.is_fresh(saved)
    assert store.get("Bookmarks") == "fresh-bookmarks"

    stale = QueryIdCache(
        fetched_at=datetime.now(tz=UTC) - timedelta(days=2),
        ttl_seconds=60,
        ids={"Bookmarks": "stale-bookmarks"},
    )
    assert not store.is_fresh(stale)
    assert store.get("TweetDetail") is not None
