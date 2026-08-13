from __future__ import annotations

from io import StringIO

import httpx
from rich.console import Console

from tweetxvault.bluesky import _post_id, sync_bluesky_likes
from tweetxvault.config import AppConfig, BlueskyConfig, SyncConfig, load_config
from tweetxvault.storage import open_archive_store


def test_post_id_uses_did_and_record_key() -> None:
    assert _post_id("at://did:plc:author/app.bsky.feed.post/3abc") == "bsky:did:plc:author:3abc"


def test_bluesky_handle_can_come_from_environment(tmp_path) -> None:
    config, _ = load_config(
        {
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "TWEETXVAULT_BLUESKY_HANDLE": "buruthebull.com",
        }
    )

    assert config.bluesky == BlueskyConfig(handle="buruthebull.com")


async def test_sync_bluesky_likes_archives_posts_and_media(paths) -> None:
    did = "did:plc:liker"
    uri = "at://did:plc:author/app.bsky.feed.post/3abc"
    unavailable_uri = "at://did:plc:gone/app.bsky.feed.post/3gone"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/com.atproto.identity.resolveHandle"):
            assert request.url.params["handle"] == "buruthebull.com"
            return httpx.Response(200, json={"did": did})
        if request.url.host == "plc.directory":
            return httpx.Response(
                200,
                json={
                    "service": [
                        {
                            "id": "#atproto_pds",
                            "type": "AtprotoPersonalDataServer",
                            "serviceEndpoint": "https://pds.example",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/com.atproto.repo.listRecords"):
            assert request.url.params["repo"] == did
            assert request.url.params["collection"] == "app.bsky.feed.like"
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "uri": f"at://{did}/app.bsky.feed.like/like1",
                            "value": {
                                "subject": {"uri": uri, "cid": "post-cid"},
                                "createdAt": "2026-08-13T12:00:00.000Z",
                            },
                        },
                        {
                            "uri": f"at://{did}/app.bsky.feed.like/like2",
                            "value": {
                                "subject": {"uri": unavailable_uri, "cid": "gone-cid"},
                                "createdAt": "2026-08-13T11:00:00.000Z",
                            },
                        },
                    ]
                },
            )
        if request.url.path.endswith("/app.bsky.feed.getPosts"):
            assert request.url.params.get_list("uris") == [uri, unavailable_uri]
            return httpx.Response(
                200,
                json={
                    "posts": [
                        {
                            "uri": uri,
                            "cid": "post-cid",
                            "author": {
                                "did": "did:plc:author",
                                "handle": "artist.example",
                                "displayName": "Artist",
                            },
                            "record": {
                                "text": "A Bluesky post",
                                "createdAt": "2026-08-12T11:00:00.000Z",
                                "langs": ["en"],
                            },
                            "embed": {
                                "$type": "app.bsky.embed.images#view",
                                "images": [
                                    {
                                        "thumb": "https://cdn.example/thumb.jpg",
                                        "fullsize": "https://cdn.example/full.jpg",
                                        "alt": "Artwork",
                                        "aspectRatio": {"width": 1200, "height": 800},
                                    }
                                ],
                            },
                            "indexedAt": "2026-08-12T11:00:01.000Z",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    result = await sync_bluesky_likes(
        "@BuruTheBull.com",
        full=False,
        limit=None,
        config=AppConfig(sync=SyncConfig(page_delay=0)),
        paths=paths,
        console=Console(file=StringIO(), force_terminal=False),
        transport=httpx.MockTransport(handler),
    )

    assert result.handle == "buruthebull.com"
    assert result.posts_archived == 2
    assert result.posts_hydrated == 1
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.export_rows("like")
        assert len(rows) == 2
        assert rows[0]["tweet_id"] == "bsky:did:plc:author:3abc"
        assert rows[0]["source"] == "bluesky_api"
        assert rows[0]["text"] == "A Bluesky post"
        assert rows[0]["media"][0]["url"] == "https://cdn.example/full.jpg"
        assert rows[0]["urls"][0]["expanded_url"] == (
            "https://bsky.app/profile/artist.example/post/3abc"
        )
        assert rows[1]["tweet_id"] == "bsky:did:plc:gone:3gone"
        assert rows[1]["text"] in (None, "")
    finally:
        store.close()
