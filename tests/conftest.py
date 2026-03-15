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


def make_url_entity(
    short_url: str,
    expanded_url: str,
    *,
    display_url: str | None = None,
    unwound_url: str | dict[str, object] | None = None,
) -> dict[str, object]:
    entity: dict[str, object] = {
        "url": short_url,
        "expanded_url": expanded_url,
        "display_url": display_url or expanded_url,
    }
    if unwound_url is not None:
        entity["unwound_url"] = unwound_url
    return entity


def make_photo_media(
    media_key: str,
    media_url: str,
    *,
    short_url: str | None = None,
) -> dict[str, object]:
    return {
        "media_key": media_key,
        "type": "photo",
        "media_url_https": media_url,
        "url": short_url or f"https://t.co/{media_key}",
        "sizes": {"large": {"w": 1200, "h": 675}},
    }


def make_video_media(
    media_key: str,
    poster_url: str,
    *,
    bitrate_url: str,
    duration_millis: int = 12345,
) -> dict[str, object]:
    return {
        "media_key": media_key,
        "type": "video",
        "media_url_https": poster_url,
        "sizes": {"large": {"w": 1280, "h": 720}},
        "video_info": {
            "duration_millis": duration_millis,
            "variants": [
                {
                    "content_type": "application/x-mpegURL",
                    "url": bitrate_url.replace(".mp4", ".m3u8"),
                },
                {"bitrate": 256000, "content_type": "video/mp4", "url": bitrate_url},
                {
                    "bitrate": 832000,
                    "content_type": "video/mp4",
                    "url": bitrate_url.replace(".mp4", "-hd.mp4"),
                },
            ],
        },
    }


def make_article_result(
    article_id: str,
    *,
    title: str,
    preview_text: str,
    plain_text: str | None = None,
    url: str | None = None,
) -> dict[str, object]:
    article: dict[str, object] = {
        "id": article_id,
        "rest_id": article_id,
        "title": title,
        "preview_text": preview_text,
        "metadata": {"first_published_at_secs": 1_742_003_200},
        "cover_media": {
            "media_info": {"original_img_url": "https://pbs.twimg.com/article-cover.jpg"}
        },
    }
    if plain_text is not None:
        article["plain_text"] = plain_text
    if url is not None:
        article["permalink"] = url
    return article


def make_tweet_result(
    tweet_id: str,
    text: str,
    *,
    user_id: str = "100",
    note_text: str | None = None,
    urls: list[dict[str, object]] | None = None,
    media: list[dict[str, object]] | None = None,
    quoted_tweet: dict[str, object] | None = None,
    retweeted_tweet: dict[str, object] | None = None,
    article: dict[str, object] | None = None,
    conversation_id: str | None = None,
    lang: str = "en",
) -> dict[str, object]:
    legacy: dict[str, object] = {
        "created_at": "Sat Mar 14 00:00:00 +0000 2026",
        "full_text": text,
        "conversation_id_str": conversation_id or tweet_id,
        "lang": lang,
    }
    if urls:
        legacy["entities"] = {"urls": urls}
    if media:
        legacy.setdefault("entities", {})
        legacy["extended_entities"] = {"media": media}
    if retweeted_tweet is not None:
        legacy["retweeted_status_result"] = {"result": retweeted_tweet}
    payload: dict[str, object] = {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": legacy,
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
    if note_text is not None:
        payload["note_tweet"] = {
            "is_expandable": True,
            "note_tweet_results": {
                "result": {
                    "text": note_text,
                    "entity_set": {"urls": urls or []},
                }
            },
        }
    if quoted_tweet is not None:
        payload["quoted_status_result"] = {"result": quoted_tweet}
    if article is not None:
        payload["article"] = {"article_results": {"result": article}}
    return payload


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
