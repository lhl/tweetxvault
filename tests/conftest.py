from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tweetxvault.auth import ResolvedAuthBundle
from tweetxvault.config import AppConfig, AuthConfig, XDGPaths, ensure_paths


@pytest.fixture
def paths(tmp_path: Path) -> XDGPaths:
    resolved = XDGPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    return ensure_paths(resolved)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(auth=AuthConfig(auth_token="token", ct0="ct0", user_id="42"))


@pytest.fixture
def auth_bundle() -> ResolvedAuthBundle:
    return ResolvedAuthBundle(
        auth_token="token",
        ct0="ct0",
        user_id="42",
        auth_token_source="test",
        ct0_source="test",
        user_id_source="test",
    )


def make_tweet_result(tweet_id: str, text: str, *, user_id: str = "100") -> dict[str, object]:
    return {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": {
            "created_at": "Sat Mar 14 00:00:00 +0000 2026",
            "full_text": text,
        },
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "rest_id": user_id,
                    "legacy": {
                        "screen_name": f"user{user_id}",
                        "name": f"User {user_id}",
                    },
                }
            }
        },
    }


def make_bookmarks_response(
    tweet_ids: list[str], *, cursor: str | None = None
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for index, tweet_id in enumerate(tweet_ids):
        entries.append(
            {
                "entryId": f"tweet-{tweet_id}",
                "sortIndex": str(500 - index),
                "content": {
                    "entryType": "TimelineTimelineItem",
                    "itemContent": {
                        "itemType": "TimelineTweet",
                        "tweet_results": {
                            "result": make_tweet_result(tweet_id, f"bookmark {tweet_id}")
                        },
                    },
                },
            }
        )
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom-1",
                "content": {"cursorType": "Bottom", "value": cursor},
            }
        )
    return {
        "data": {
            "bookmark_timeline_v2": {
                "timeline": {
                    "instructions": [{"type": "TimelineAddEntries", "entries": entries}],
                }
            }
        }
    }


def make_likes_response(
    tweet_ids: list[str],
    *,
    cursor: str | None = None,
    module: bool = False,
) -> dict[str, object]:
    if module:
        items = []
        for index, tweet_id in enumerate(tweet_ids):
            items.append(
                {
                    "entryId": f"tweet-{tweet_id}",
                    "item": {
                        "itemContent": {
                            "itemType": "TimelineTweet",
                            "tweet_results": {
                                "result": make_tweet_result(tweet_id, f"like {tweet_id}")
                            },
                        }
                    },
                    "sortIndex": str(400 - index),
                }
            )
        entries = [
            {
                "entryId": "module-1",
                "sortIndex": "400",
                "content": {"entryType": "TimelineTimelineModule", "items": items},
            }
        ]
    else:
        entries = []
        for index, tweet_id in enumerate(tweet_ids):
            entries.append(
                {
                    "entryId": f"tweet-{tweet_id}",
                    "sortIndex": str(400 - index),
                    "content": {
                        "entryType": "TimelineTimelineItem",
                        "itemContent": {
                            "itemType": "TimelineTweet",
                            "tweet_results": {
                                "result": make_tweet_result(tweet_id, f"like {tweet_id}")
                            },
                        },
                    },
                }
            )
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom-1",
                "content": {"cursorType": "Bottom", "value": cursor},
            }
        )
    return {
        "data": {
            "user": {
                "result": {
                    "timeline_v2": {
                        "timeline": {
                            "instructions": [{"type": "TimelineAddEntries", "entries": entries}],
                        }
                    }
                }
            }
        }
    }


def request_details(url: str) -> tuple[str, dict[str, object]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    variables = json.loads(query["variables"][0])
    operation = parsed.path.split("/")[-1]
    return operation, variables
