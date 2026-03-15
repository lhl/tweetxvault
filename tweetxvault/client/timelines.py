"""Timeline request builders and parsers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from tweetxvault.client.base import request_with_backoff
from tweetxvault.client.features import (
    build_bookmarks_features,
    build_field_toggles,
    build_likes_features,
    build_tweet_detail_features,
    build_user_tweets_features,
)
from tweetxvault.config import API_BASE_URL, SyncConfig
from tweetxvault.extractor import extract_author_fields, extract_canonical_text, unwrap_tweet_result


@dataclass(slots=True)
class TimelineTweet:
    tweet_id: str
    text: str
    author_id: str | None
    author_username: str | None
    author_display_name: str | None
    created_at: str | None
    sort_index: str | None
    raw_json: dict[str, Any]


def _encode_param(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _timeline_params(variables: dict[str, Any], *, features: dict[str, bool]) -> str:
    params = {
        "variables": _encode_param(variables),
        "features": _encode_param(features),
        "fieldToggles": _encode_param(build_field_toggles()),
    }
    return urlencode(params)


def build_bookmarks_url(query_id: str, cursor: str | None = None, *, count: int = 20) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": False,
        "withBirdwatchNotes": False,
        "withClientEventToken": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_bookmarks_features())
    return f"{API_BASE_URL}/{query_id}/Bookmarks?{params}"


def build_likes_url(
    query_id: str, user_id: str, cursor: str | None = None, *, count: int = 20
) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": False,
        "userId": user_id,
        "withBirdwatchNotes": False,
        "withClientEventToken": False,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_likes_features())
    return f"{API_BASE_URL}/{query_id}/Likes?{params}"


def build_user_tweets_url(
    query_id: str, user_id: str, cursor: str | None = None, *, count: int = 20
) -> str:
    variables: dict[str, Any] = {
        "count": count,
        "includePromotedContent": True,
        "userId": user_id,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True,
        "withV2Timeline": True,
    }
    if cursor:
        variables["cursor"] = cursor
    params = _timeline_params(variables, features=build_user_tweets_features())
    return f"{API_BASE_URL}/{query_id}/UserTweets?{params}"


def build_tweet_detail_url(query_id: str, tweet_id: str) -> str:
    variables: dict[str, Any] = {
        "focalTweetId": tweet_id,
        "withCommunity": True,
        "withVoice": True,
        "withBirdwatchNotes": True,
        "includePromotedContent": True,
    }
    params = _timeline_params(variables, features=build_tweet_detail_features())
    return f"{API_BASE_URL}/{query_id}/TweetDetail?{params}"


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    sync_config: SyncConfig,
    *,
    refresh_once: Callable[[], Awaitable[str]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    return await request_with_backoff(
        client,
        url,
        sync_config,
        refresh_once=refresh_once,
        sleep=sleep,
    )


def _extract_tweet_results_from_content(content: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    item_content = content.get("itemContent") or content.get("content", {}).get("itemContent")
    if isinstance(item_content, dict):
        result = unwrap_tweet_result(item_content.get("tweet_results", {}).get("result"))
        if result:
            results.append(result)

    for item in content.get("items", []):
        if not isinstance(item, dict):
            continue
        nested_item = item.get("item", {})
        nested_content = nested_item.get("itemContent")
        if isinstance(nested_content, dict):
            result = unwrap_tweet_result(nested_content.get("tweet_results", {}).get("result"))
            if result:
                results.append(result)
    return results


def _iter_entries(node: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "entryId" in node and "content" in node:
            entries.append(node)
        for value in node.values():
            entries.extend(_iter_entries(value))
    elif isinstance(node, list):
        for item in node:
            entries.extend(_iter_entries(item))
    return entries


def _extract_cursor(entry: dict[str, Any]) -> str | None:
    entry_id = entry.get("entryId", "")
    content = entry.get("content", {})
    if entry_id.startswith("cursor-bottom-"):
        return content.get("value")
    if content.get("cursorType") == "Bottom":
        return content.get("value")
    return None


def _tweet_from_result(result: dict[str, Any], *, sort_index: str | None) -> TimelineTweet | None:
    legacy = result.get("legacy") or {}
    tweet_id = result.get("rest_id")
    if not tweet_id:
        return None
    author_id, author_username, author_display_name = extract_author_fields(result)
    return TimelineTweet(
        tweet_id=tweet_id,
        text=extract_canonical_text(result),
        author_id=author_id,
        author_username=author_username,
        author_display_name=author_display_name,
        created_at=legacy.get("created_at"),
        sort_index=sort_index,
        raw_json=result,
    )


def parse_timeline_response(
    data: dict[str, Any], operation: str
) -> tuple[list[TimelineTweet], str | None]:
    tweets: list[TimelineTweet] = []
    seen_ids: set[str] = set()
    bottom_cursor: str | None = None

    for entry in _iter_entries(data):
        bottom_cursor = bottom_cursor or _extract_cursor(entry)
        sort_index = entry.get("sortIndex")
        for result in _extract_tweet_results_from_content(entry.get("content", {})):
            tweet = _tweet_from_result(result, sort_index=sort_index)
            if tweet and tweet.tweet_id not in seen_ids:
                seen_ids.add(tweet.tweet_id)
                tweets.append(tweet)

    if operation not in {"Bookmarks", "Likes", "UserTweets"}:
        raise ValueError(f"Unsupported timeline operation: {operation}")
    return tweets, bottom_cursor


def parse_tweet_detail_response(
    data: dict[str, Any],
    focal_tweet_id: str,
) -> TimelineTweet | None:
    for entry in _iter_entries(data):
        sort_index = entry.get("sortIndex")
        for result in _extract_tweet_results_from_content(entry.get("content", {})):
            tweet = _tweet_from_result(result, sort_index=sort_index)
            if tweet and tweet.tweet_id == focal_tweet_id:
                return tweet
    return None
