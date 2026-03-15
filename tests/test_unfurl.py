from __future__ import annotations

import httpx
import pytest

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.storage import open_archive_store
from tweetxvault.unfurl import unfurl_urls


def _complex_tweet(tweet_id: str = "100") -> TimelineTweet:
    quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        urls=[
            make_url_entity(
                "https://t.co/quoted",
                "https://quoted.example.com/post",
                display_url="quoted.example.com/post",
            )
        ],
        media=[
            make_video_media(
                "7_quoted",
                "https://pbs.twimg.com/ext_tw_video_thumb/quoted.jpg",
                bitrate_url="https://video.twimg.com/ext_tw_video/quoted.mp4",
            )
        ],
    )
    raw = make_tweet_result(
        tweet_id,
        "root short",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story?utm_source=x&keep=1",
                display_url="example.com/story",
            )
        ],
        media=[make_photo_media("3_root", "https://pbs.twimg.com/media/root.jpg")],
        quoted_tweet=quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text="Article body",
            url="https://x.com/i/article/123",
        ),
    )
    return TimelineTweet(
        tweet_id=tweet_id,
        text="root short",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )


def _seed_archive(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_complex_tweet()],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


@pytest.mark.asyncio
async def test_unfurl_urls_persists_html_metadata(paths, config) -> None:
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        if "quoted.example.com" in str(request.url):
            html = """
            <html><head>
            <title>Quoted Story</title>
            <meta property="og:description" content="Quoted description">
            </head><body></body></html>
            """
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        html = """
        <html><head>
        <title>Root Story</title>
        <link rel="canonical" href="https://example.com/story?keep=1">
        <meta name="description" content="Root description">
        <meta property="og:site_name" content="Example">
        </head><body></body></html>
        """
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    result = await unfurl_urls(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 2
    assert result.updated == 2
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_url_rows(states={"done"})
        assert len(rows) == 2
        root = next(
            row for row in rows if row["canonical_url"] == "https://example.com/story?keep=1"
        )
        assert root["title"] == "Root Story"
        assert root["description"] == "Root description"
        assert root["site_name"] == "Example"
        quoted = next(
            row for row in rows if row["canonical_url"] == "https://quoted.example.com/post"
        )
        assert quoted["title"] == "Quoted Story"
        assert quoted["description"] == "Quoted description"
    finally:
        store.close()
