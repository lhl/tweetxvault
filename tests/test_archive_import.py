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
from tweetxvault.auth import ResolvedAuthBundle
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import APIResponseError, ArchiveOwnerMismatchError, ConfigError
from tweetxvault.storage import open_archive_store


def _wrap_ytd(name: str, payload: object) -> str:
    return f"window.{name} = {json.dumps(payload, indent=2)}\n"


def _write_archive_dir(
    base: Path,
    *,
    like_tweet_id: str = "300",
    media_kind: str = "photo",
    include_video_main_asset: bool = False,
) -> Path:
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

    if media_kind == "video":
        media_item = {
            "id": "500",
            "id_str": "500",
            "media_url": "http://pbs.twimg.com/ext_tw_video_thumb/archive-poster.jpg",
            "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/archive-poster.jpg",
            "expanded_url": "https://x.com/archiveuser/status/100/video/1",
            "url": "https://t.co/archive-video",
            "display_url": "pic.x.com/archive-video",
            "type": "video",
            "sizes": {"large": {"w": "1200", "h": "675", "resize": "fit"}},
            "video_info": {
                "duration_millis": 1000,
                "variants": [
                    {
                        "content_type": "application/x-mpegURL",
                        "url": "https://video.twimg.com/ext_tw_video/archive-video.m3u8",
                    },
                    {
                        "bitrate": 832000,
                        "content_type": "video/mp4",
                        "url": "https://video.twimg.com/ext_tw_video/archive-video.mp4",
                    },
                ],
            },
        }
        exported_media_names = ["100-archive-poster.jpg"]
        if include_video_main_asset:
            exported_media_names.append("100-archive-video.mp4")
    else:
        media_item = {
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
        exported_media_names = ["100-archive-photo.jpg"]

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
                "media": [media_item],
            },
            "extended_entities": {"media": [media_item]},
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
    for exported_media_name in exported_media_names:
        payload = b"archive-video" if exported_media_name.endswith(".mp4") else b"archive-photo"
        (media_dir / exported_media_name).write_bytes(payload)
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


def _auth_bundle() -> ResolvedAuthBundle:
    return ResolvedAuthBundle(
        auth_token="auth",
        ct0="ct0",
        user_id="42",
        auth_token_source="test",
        ct0_source="test",
        user_id_source="test",
    )


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


def test_import_x_archive_rejects_missing_manifest(paths, tmp_path: Path) -> None:
    broken_dir = tmp_path / "broken-archive"
    broken_dir.mkdir()

    with pytest.raises(ConfigError, match="missing manifest.js"):
        asyncio.run(
            import_x_archive(
                broken_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_archive_input_closes_zip_when_manifest_load_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_zip = tmp_path / "broken-archive.zip"
    with zipfile.ZipFile(archive_zip, "w") as handle:
        handle.writestr("data/manifest.js", "window.__THAR_CONFIG = {}\n")

    closed: list[str] = []
    original_close = zipfile.ZipFile.close

    def tracking_close(self: zipfile.ZipFile) -> None:
        if self.fp is not None:
            closed.append(str(self.filename))
        original_close(self)

    def raise_bad_manifest(_self: archive_import._ArchiveInput) -> dict[str, object]:
        raise ConfigError("bad manifest")

    monkeypatch.setattr(zipfile.ZipFile, "close", tracking_close)
    monkeypatch.setattr(archive_import._ArchiveInput, "_load_manifest", raise_bad_manifest)

    with pytest.raises(ConfigError, match="bad manifest"):
        archive_import._ArchiveInput(archive_zip)

    assert closed == [str(archive_zip)]


def test_import_x_archive_rejects_owner_mismatch(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.ensure_archive_owner_id("84")
    store.close()
    _disable_live_reconciliation(monkeypatch)

    with pytest.raises(ArchiveOwnerMismatchError):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_rejects_parent_segments_in_manifest_paths(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    manifest_path = archive_dir / "data" / "manifest.js"
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8").removeprefix("window.__THAR_CONFIG = ")
    )
    manifest["dataTypes"]["tweets"]["files"][0]["fileName"] = "data/../../../etc/passwd"
    manifest_path.write_text(
        f"window.__THAR_CONFIG = {json.dumps(manifest, indent=2)}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="must stay within the archive data/ directory"):
        asyncio.run(
            import_x_archive(
                archive_dir,
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_import_x_archive_reuses_existing_thumbnail_destination(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path, media_kind="video")
    (paths.data_dir / "media" / "100").mkdir(parents=True, exist_ok=True)
    (paths.data_dir / "media" / "100" / "7_500-poster.jpg").write_bytes(b"poster")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 0
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store.table.search().where("record_type = 'media'").limit(1).to_list()[0]
    assert media_row["download_state"] == "done"
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["thumbnail_sha256"] is None
    assert media_row["thumbnail_byte_size"] is None
    store.close()


def test_import_x_archive_preserves_main_and_thumbnail_updates_for_video_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(
        tmp_path,
        media_kind="video",
        include_video_main_asset=True,
    )
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.counts["media_files_copied"] == 2
    store = open_archive_store(paths, create=False)
    assert store is not None
    media_row = store.table.search().where("record_type = 'media'").limit(1).to_list()[0]
    assert media_row["local_path"] == "media/100/7_500.mp4"
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert (paths.data_dir / "media" / "100" / "7_500.mp4").exists()
    assert (paths.data_dir / "media" / "100" / "7_500-poster.jpg").exists()
    store.close()


def test_import_x_archive_detail_api_errors_become_transient_failures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise APIResponseError("server error", status_code=500)

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            detail_lookups=1,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.detail_lookups == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store.table.search().where("row_key = 'tweet_object:300'").limit(1).to_list()[0]
    assert tweet_object["enrichment_state"] == "transient_failure"
    assert tweet_object["enrichment_http_status"] == 500
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    manifest_counts = json.loads(manifest_row["counts_json"])
    assert manifest_counts["detail_transient_failures"] == 1
    store.close()


def test_import_x_archive_preserves_attempt_start_time(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    timestamps = iter(
        [
            "2026-03-17T00:00:00Z",
            "2026-03-17T00:00:01Z",
            "2026-03-17T00:00:02Z",
            "2026-03-17T00:00:03Z",
        ]
    )
    monkeypatch.setattr(archive_import, "utc_now", lambda: next(timestamps))

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
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    assert manifest_row["import_started_at"] == "2026-03-17T00:00:00Z"
    assert manifest_row["import_completed_at"] == "2026-03-17T00:00:03Z"
    store.close()
