from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import make_article_result, make_tweet_detail_response, make_tweet_result
from tweetxvault.articles import normalize_article_target, refresh_articles
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig, AuthConfig, SyncConfig
from tweetxvault.query_ids import QueryIdStore
from tweetxvault.storage import open_archive_store

TWEET_ID = "2026531440414925307"


def _fixture_payload() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "dimitris_article_tweet_detail.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _preview_tweet(tweet_id: str, *, plain_text: str | None = None) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text="preview tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=make_tweet_result(
            tweet_id,
            "preview tweet",
            user_id="1000",
            article=make_article_result(
                f"preview-article-{tweet_id}",
                title="Preview title",
                preview_text="Preview only",
                plain_text=plain_text,
            ),
        ),
    )


def _seed_preview_article(paths, *tweet_ids: str) -> None:
    tweets = [_preview_tweet(tweet_id) for tweet_id in (tweet_ids or (TWEET_ID,))]
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=tweets,
        last_head_tweet_id=tweets[-1].tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


def _article_detail_payload(tweet_id: str, *, plain_text: str = "Body") -> dict[str, object]:
    return make_tweet_detail_response(
        [
            make_tweet_result(
                tweet_id,
                "detail tweet",
                user_id="1000",
                article=make_article_result(
                    f"detail-article-{tweet_id}",
                    title=f"Title {tweet_id}",
                    preview_text="Preview only",
                    plain_text=plain_text,
                    url=f"https://x.com/i/article/{tweet_id}",
                ),
            )
        ]
    )


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


@pytest.mark.asyncio
async def test_refresh_articles_marks_missing_focal_tweet_as_failure(
    paths,
    config,
    auth_bundle,
) -> None:
    _seed_preview_article(paths)
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_article_detail_payload("999", plain_text="Wrong tweet"),
            request=request,
        )

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 0
    assert result.failed == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = store.list_article_rows(preview_only=True)
        assert len(article_rows) == 1
        assert article_rows[0]["status"] == "preview_only"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_respects_limit(paths, config, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = request.url.params["variables"]
        if '"focalTweetId":"100"' in variables:
            requests.append("100")
            return httpx.Response(
                200,
                json=_article_detail_payload("100", plain_text="Body 100"),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        limit=1,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 1
    assert result.updated == 1
    assert result.failed == 0
    assert requests == ["100"]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        preview_rows = store.list_article_rows(preview_only=True)
        all_rows = store.list_article_rows(preview_only=False)
        assert [row["tweet_id"] for row in preview_rows] == ["101"]
        assert {row["tweet_id"]: row["status"] for row in all_rows} == {
            "100": "body_present",
            "101": "preview_only",
        }
    finally:
        store.close()


@pytest.mark.asyncio
async def test_refresh_articles_respects_detail_sleep(paths, auth_bundle) -> None:
    _seed_preview_article(paths, "100", "101")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    sleep_calls: list[float] = []
    config = AppConfig(
        auth=AuthConfig(auth_token="token", ct0="ct0", user_id="42"),
        sync=SyncConfig(detail_delay=1.0),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        variables = request.url.params["variables"]
        if '"focalTweetId":"100"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("100", plain_text="Body 100"),
                request=request,
            )
        if '"focalTweetId":"101"' in variables:
            return httpx.Response(
                200,
                json=_article_detail_payload("101", plain_text="Body 101"),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    result = await refresh_articles(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )

    assert result.processed == 2
    assert result.updated == 2
    assert sleep_calls == [1.0]
