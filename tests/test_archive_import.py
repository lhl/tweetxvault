from __future__ import annotations

import asyncio
import json
import zipfile
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import tweetxvault.archive_import as archive_import
from tweetxvault.archive_import import import_x_archive
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import ConfigError
from tweetxvault.storage import open_archive_store


def _wrap_ytd(name: str, payload: object) -> str:
    return f"window.{name} = {json.dumps(payload, indent=2)}\n"


def _write_archive_dir(base: Path, *, like_tweet_id: str = "300") -> Path:
    root = base / "archive"
    data_dir = root / "data"
    media_dir = data_dir / "tweets_media"
    media_dir.mkdir(parents=True)

    manifest = {
        "userInfo": {
            "accountId": "42",
            "userName": "archiveuser",
            "displayName": "Archive User",
        },
        "archiveInfo": {
            "generationDate": "2026-03-16T08:57:45.244Z",
            "isPartialArchive": False,
        },
        "dataTypes": {
            "account": {
                "files": [{"fileName": "data/account.js", "globalName": "YTD.account.part0"}]
            },
            "tweets": {
                "files": [{"fileName": "data/tweets.js", "globalName": "YTD.tweets.part0"}],
                "mediaDirectory": "data/tweets_media",
            },
            "tweetHeaders": {
                "files": [
                    {"fileName": "data/tweet-headers.js", "globalName": "YTD.tweet_headers.part0"}
                ]
            },
            "deletedTweets": {
                "files": [
                    {
                        "fileName": "data/deleted-tweets.js",
                        "globalName": "YTD.deleted_tweets.part0",
                    }
                ]
            },
            "deletedTweetHeaders": {
                "files": [
                    {
                        "fileName": "data/deleted-tweet-headers.js",
                        "globalName": "YTD.deleted_tweet_headers.part0",
                    }
                ]
            },
            "like": {"files": [{"fileName": "data/like.js", "globalName": "YTD.like.part0"}]},
        },
    }
    (data_dir / "manifest.js").write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest, indent=2)}\n",
        encoding="utf-8",
    )
    (data_dir / "account.js").write_text(
        _wrap_ytd(
            "YTD.account.part0",
            [
                {
                    "account": {
                        "username": "archiveuser",
                        "accountId": "42",
                        "accountDisplayName": "Archive User",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )

    authored_tweet = {
        "tweet": {
            "id": "100",
            "id_str": "100",
            "full_text": "archive authored tweet",
            "created_at": "Sat Mar 14 00:00:00 +0000 2026",
            "lang": "en",
            "entities": {
                "urls": [],
                "hashtags": [],
                "user_mentions": [],
                "media": [
                    {
                        "id": "500",
                        "id_str": "500",
                        "media_url": "http://pbs.twimg.com/media/archive-photo.jpg",
                        "media_url_https": "https://pbs.twimg.com/media/archive-photo.jpg",
                        "expanded_url": "https://x.com/archiveuser/status/100/photo/1",
                        "url": "https://t.co/archive-photo",
                        "display_url": "pic.x.com/archive-photo",
                        "type": "photo",
                        "sizes": {"large": {"w": "1200", "h": "675", "resize": "fit"}},
                    }
                ],
            },
            "extended_entities": {
                "media": [
                    {
                        "id": "500",
                        "id_str": "500",
                        "media_url": "http://pbs.twimg.com/media/archive-photo.jpg",
                        "media_url_https": "https://pbs.twimg.com/media/archive-photo.jpg",
                        "expanded_url": "https://x.com/archiveuser/status/100/photo/1",
                        "url": "https://t.co/archive-photo",
                        "display_url": "pic.x.com/archive-photo",
                        "type": "photo",
                        "sizes": {"large": {"w": "1200", "h": "675", "resize": "fit"}},
                    }
                ]
            },
            "favorite_count": "0",
            "retweet_count": "0",
            "retweeted": False,
            "favorited": False,
            "source": '<a href="https://x.com" rel="nofollow">Twitter Web App</a>',
        }
    }
    deleted_tweet = {
        "tweet": {
            "id": "200",
            "id_str": "200",
            "full_text": "deleted archive tweet",
            "created_at": "Sun Mar 15 00:00:00 +0000 2026",
            "lang": "en",
            "entities": {"urls": [], "hashtags": [], "user_mentions": []},
            "favorite_count": "0",
            "retweet_count": "0",
            "retweeted": False,
            "favorited": False,
            "source": '<a href="https://x.com" rel="nofollow">Twitter Web App</a>',
        }
    }
    (data_dir / "tweets.js").write_text(
        _wrap_ytd("YTD.tweets.part0", [authored_tweet]),
        encoding="utf-8",
    )
    (data_dir / "tweet-headers.js").write_text(
        _wrap_ytd(
            "YTD.tweet_headers.part0",
            [
                {
                    "tweet": {
                        "tweet_id": "100",
                        "user_id": "42",
                        "created_at": authored_tweet["tweet"]["created_at"],
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    (data_dir / "deleted-tweets.js").write_text(
        _wrap_ytd("YTD.deleted_tweets.part0", [deleted_tweet]),
        encoding="utf-8",
    )
    (data_dir / "deleted-tweet-headers.js").write_text(
        _wrap_ytd(
            "YTD.deleted_tweet_headers.part0",
            [
                {
                    "tweet": {
                        "tweet_id": "200",
                        "user_id": "42",
                        "created_at": deleted_tweet["tweet"]["created_at"],
                        "deleted_at": "Mon Mar 16 00:00:00 +0000 2026",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    (data_dir / "like.js").write_text(
        _wrap_ytd(
            "YTD.like.part0",
            [
                {
                    "like": {
                        "tweetId": like_tweet_id,
                        "fullText": "archive liked tweet",
                        "expandedUrl": f"https://twitter.com/i/web/status/{like_tweet_id}",
                    }
                }
            ],
        ),
        encoding="utf-8",
    )
    (media_dir / "100-archive-photo.jpg").write_bytes(b"archive-photo")
    return root


def _write_archive_zip(archive_dir: Path, destination: Path) -> Path:
    with zipfile.ZipFile(destination, "w") as handle:
        for path in sorted(archive_dir.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(archive_dir).as_posix())
    return destination


def _live_tweet(tweet_id: str, *, text: str) -> TimelineTweet:
    raw_json = {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": {
            "full_text": text,
            "created_at": "Sat Mar 14 00:00:00 +0000 2026",
            "conversation_id_str": tweet_id,
            "lang": "en",
            "entities": {"urls": []},
        },
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "rest_id": "999",
                    "legacy": {"screen_name": "liveuser", "name": "Live User"},
                }
            }
        },
    }
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id="999",
        author_username="liveuser",
        author_display_name="Live User",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="999",
        raw_json=raw_json,
    )


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _disable_live_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_no_auth(_config):
        raise ConfigError("no auth configured")

    monkeypatch.setattr(archive_import, "resolve_auth_bundle", raise_no_auth)


def test_import_x_archive_directory_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.counts["authored_tweets"] == 1
    assert result.counts["deleted_authored_tweets"] == 1
    assert result.counts["likes"] == 1
    assert result.counts["media_files_copied"] == 1
    assert result.pending_enrichment == 1
    assert any("bookmark dataset" in warning for warning in result.warnings)
    assert any("live reconciliation skipped" in warning for warning in result.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.get_archive_owner_id() == "42"
    assert store.counts()["import_manifests"] == 1

    tweet_rows = store.table.search().where("record_type = 'tweet'").to_list()
    row_by_key = {(row["collection_type"], row["tweet_id"]): row for row in tweet_rows}
    assert row_by_key[("tweet", "100")]["source"] == "x_archive"
    assert row_by_key[("tweet", "200")]["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    assert row_by_key[("like", "300")]["sort_index"] == "-1"

    tweet_objects = {
        row["tweet_id"]: row
        for row in store.table.search().where("record_type = 'tweet_object'").to_list()
    }
    assert tweet_objects["100"]["source"] == "x_archive"
    assert tweet_objects["200"]["enrichment_state"] == "terminal_unavailable"
    assert tweet_objects["200"]["enrichment_reason"] == "deleted"
    assert tweet_objects["300"]["enrichment_state"] == "pending"

    media_rows = store.table.search().where("record_type = 'media'").to_list()
    assert len(media_rows) == 1
    assert media_rows[0]["download_state"] == "done"
    assert media_rows[0]["local_path"] == "media/100/3_500.jpg"
    assert (paths.data_dir / media_rows[0]["local_path"]).exists()

    manifest_rows = store.table.search().where("record_type = 'import_manifest'").to_list()
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_counts["pending_enrichment"] == 1
    store.close()


def test_repeated_import_short_circuits_across_directory_and_zip_inputs(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )
    second = asyncio.run(
        import_x_archive(
            archive_zip,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert first.skipped is False
    assert second.skipped is True
    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["import_manifests"] == 1
    store.close()


def test_archive_import_does_not_downgrade_existing_live_tweet_object(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("300", text="live bookmark tweet")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store.table.search().where("row_key = 'tweet_object:300'").limit(1).to_list()[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live bookmark tweet"
    assert tweet_object["enrichment_state"] == "done"
    like_row = store.table.search().where("row_key = 'tweet:like::300'").limit(1).to_list()[0]
    assert like_row["source"] == "x_archive"
    store.close()


def test_live_sync_can_upgrade_archive_like_placeholder_after_import(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, like_tweet_id="300")
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    store = open_archive_store(paths, create=False)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_live_tweet("300", text="live liked tweet")],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    tweet_object = store.table.search().where("row_key = 'tweet_object:300'").limit(1).to_list()[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live liked tweet"
    assert tweet_object["enrichment_state"] == "done"
    store.close()
