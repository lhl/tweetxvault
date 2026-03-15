from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_article_result, make_tweet_result
from tweetxvault.articles import normalize_article_target, refresh_articles
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.query_ids import QueryIdStore
from tweetxvault.storage import open_archive_store

TWEET_ID = "2026531440414925307"


def _fixture_payload() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "dimitris_article_tweet_detail.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _seed_preview_article(paths) -> None:
    preview_tweet = TimelineTweet(
        tweet_id=TWEET_ID,
        text="preview tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=make_tweet_result(
            TWEET_ID,
            "preview tweet",
            user_id="1000",
            article=make_article_result(
                "preview-article",
                title="Preview title",
                preview_text="Preview only",
                plain_text=None,
            ),
        ),
    )
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[preview_tweet],
        last_head_tweet_id=TWEET_ID,
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


def test_normalize_article_target_accepts_ids_and_urls() -> None:
    assert normalize_article_target(TWEET_ID) == TWEET_ID
    assert normalize_article_target(f"https://x.com/dimitrispapail/status/{TWEET_ID}") == TWEET_ID


@pytest.mark.asyncio
async def test_refresh_articles_updates_preview_rows_from_tweet_detail(
    paths,
    config,
    auth_bundle,
) -> None:
    _seed_preview_article(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    payload = _fixture_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/TweetDetail" in str(request.url)
        return httpx.Response(200, json=payload)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 1
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = store.list_article_rows(preview_only=False)
        assert len(article_rows) == 1
        article = article_rows[0]
        assert article["status"] == "body_present"
        assert article["title"] == "You Don't Need to Run Every Eval"
        assert len(article["content_text"]) == 17308
        media_rows = store.list_media_rows()
        assert len(media_rows) == 10
        assert any(row["source"] == "article_cover" for row in media_rows)
    finally:
        store.close()
