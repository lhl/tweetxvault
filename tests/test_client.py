from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.conftest import make_bookmarks_response, make_likes_response, request_details
from tweetxvault.client.base import (
    RateLimitExhaustedError,
    is_auth_error,
    is_feature_flag_error,
    is_rate_limit,
    is_stale_query_id,
)
from tweetxvault.client.timelines import (
    build_bookmarks_url,
    build_likes_url,
    build_tweet_detail_url,
    fetch_page,
    parse_timeline_response,
    parse_tweet_detail_response,
)
from tweetxvault.config import SyncConfig


def test_build_timeline_urls() -> None:
    bookmarks_url = build_bookmarks_url("bookmark-qid", cursor="abc")
    likes_url = build_likes_url("likes-qid", "42", cursor="def")
    detail_url = build_tweet_detail_url("detail-qid", "2026531440414925307")
    operation, variables = request_details(bookmarks_url)
    assert operation == "Bookmarks"
    assert variables["cursor"] == "abc"
    bookmarks_query = parse_qs(urlparse(bookmarks_url).query)
    assert '"withArticlePlainText":true' in bookmarks_query["fieldToggles"][0]
    assert '"withArticleSummaryText":true' in bookmarks_query["fieldToggles"][0]
    operation, variables = request_details(likes_url)
    assert operation == "Likes"
    assert variables["userId"] == "42"
    assert variables["cursor"] == "def"
    operation, variables = request_details(detail_url)
    assert operation == "TweetDetail"
    assert variables["focalTweetId"] == "2026531440414925307"


def test_parse_tweet_detail_response_real_article_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "dimitris_article_tweet_detail.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    tweet = parse_tweet_detail_response(payload, "2026531440414925307")

    assert tweet is not None
    assert tweet.tweet_id == "2026531440414925307"
    article = ((tweet.raw_json.get("article") or {}).get("article_results") or {}).get(
        "result"
    ) or {}
    assert article["title"] == "You Don't Need to Run Every Eval"
    assert len(article["plain_text"]) == 17308


def test_parse_timeline_response_bookmarks_shape() -> None:
    tweets, cursor = parse_timeline_response(
        make_bookmarks_response(["1", "2"], cursor="next"), "Bookmarks"
    )
    assert [tweet.tweet_id for tweet in tweets] == ["1", "2"]
    assert cursor == "next"


def test_parse_timeline_response_likes_module_shape() -> None:
    tweets, cursor = parse_timeline_response(
        make_likes_response(["10", "11"], cursor="older", module=True),
        "Likes",
    )
    assert [tweet.tweet_id for tweet in tweets] == ["10", "11"]
    assert cursor == "older"


def test_response_classification_helpers() -> None:
    request = httpx.Request("GET", "https://example.com")
    assert is_rate_limit(httpx.Response(429, request=request))
    assert is_auth_error(httpx.Response(401, request=request))
    assert is_feature_flag_error(httpx.Response(400, request=request))
    assert is_stale_query_id(httpx.Response(404, request=request))


@pytest.mark.asyncio
async def test_fetch_page_refreshes_once_on_404() -> None:
    responses = deque(
        [
            httpx.Response(404, request=httpx.Request("GET", "https://example.com/one")),
            httpx.Response(
                200, json={"ok": True}, request=httpx.Request("GET", "https://example.com/two")
            ),
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.popleft()

    refreshed = {"count": 0}

    async def refresh_once() -> str:
        refreshed["count"] += 1
        return "https://example.com/two"

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await fetch_page(
            client, "https://example.com/one", SyncConfig(), refresh_once=refresh_once
        )
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert refreshed["count"] == 1


@pytest.mark.asyncio
async def test_fetch_page_raises_after_repeated_429() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    try:
        with pytest.raises(RateLimitExhaustedError):
            await fetch_page(
                client,
                "https://example.com",
                SyncConfig(
                    max_retries=1, backoff_base=0.1, cooldown_threshold=1, cooldown_duration=0.2
                ),
                sleep=fake_sleep,
            )
    finally:
        await client.aclose()

    assert delays == [0.1, 0.2, 0.1]
