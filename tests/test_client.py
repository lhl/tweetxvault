from __future__ import annotations

from collections import deque

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
    fetch_page,
    parse_timeline_response,
)
from tweetxvault.config import SyncConfig


def test_build_timeline_urls() -> None:
    bookmarks_url = build_bookmarks_url("bookmark-qid", cursor="abc")
    likes_url = build_likes_url("likes-qid", "42", cursor="def")
    operation, variables = request_details(bookmarks_url)
    assert operation == "Bookmarks"
    assert variables["cursor"] == "abc"
    operation, variables = request_details(likes_url)
    assert operation == "Likes"
    assert variables["userId"] == "42"
    assert variables["cursor"] == "def"


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
