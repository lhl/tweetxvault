from __future__ import annotations

import asyncio
import json
import zipfile
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

import tweetxvault.archive_import as archive_import
from tests.conftest import make_tweet_detail_response, make_tweet_result, request_details
from tweetxvault.archive_import import enrich_imported_archive, import_x_archive
from tweetxvault.auth import ResolvedAuthBundle
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import (
    APIResponseError,
    ArchiveOwnerMismatchError,
    ConfigError,
    StaleQueryIdError,
)
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


def _write_root_layout_archive_dir(base: Path, **kwargs: object) -> Path:
    root = _write_archive_dir(base, **kwargs)
    data_dir = root / "data"
    for path in sorted(data_dir.iterdir()):
        path.rename(root / path.name)
    data_dir.rmdir()
    return root


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


def test_import_x_archive_logs_progress_on_tty(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, color_system=None)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=console,
            debug=True,
        )
    )

    output = buffer.getvalue()
    assert "archive import: opening" in output
    assert "archive import: hashing archive contents for idempotence check..." in output
    assert "archive import: loading archive datasets..." in output
    assert "archive import hash" in output
    assert "archive import likes" in output
    assert "archive import media" in output
    assert "archive import: running follow-up reconciliation and enrichment..." in output
    assert "archive import: debug summary:" in output


def test_import_x_archive_zip_populates_archive_and_copies_media(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    archive_zip = _write_archive_zip(archive_dir, tmp_path / "archive.zip")
    _disable_live_reconciliation(monkeypatch)

    result = asyncio.run(
        import_x_archive(
            archive_zip,
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
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


def test_import_x_archive_supports_root_manifest_layout(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_root_layout_archive_dir(tmp_path)
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
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()


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


def test_repeated_import_can_reuse_existing_archive_for_enrich_followup(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_reconciliation(**_kwargs):
        return ["likes"], [], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        assert kwargs["limit"] is None
        return 3, 1, 2, 4

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert first.skipped is False
    assert second.skipped is True
    assert second.followup_performed is True
    assert second.reconciled_collections == ["likes"]
    assert second.counts["authored_tweets"] == 1
    assert second.counts["deleted_authored_tweets"] == 1
    assert second.counts["likes"] == 1
    assert second.detail_lookups == 3
    assert second.detail_terminal_unavailable == 1
    assert second.detail_transient_failures == 2
    assert second.pending_enrichment == 4

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_rows = store.table.search().where("record_type = 'import_manifest'").to_list()
    manifest_counts = json.loads(manifest_rows[0]["counts_json"])
    assert manifest_counts["detail_lookups"] == 3
    assert manifest_counts["pending_enrichment"] == 4
    store.close()


def test_repeated_import_enrich_preserves_existing_manifest_warnings(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    first = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    async def fake_followup(**_kwargs):
        return archive_import.ArchiveEnrichResult(
            warnings=["detail enrichment failed: upstream 429"],
            pending_enrichment=first.pending_enrichment,
        )

    monkeypatch.setattr(archive_import, "_run_archive_followup", fake_followup)

    second = asyncio.run(
        import_x_archive(
            archive_dir,
            enrich=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    expected_warning = (
        "archive does not contain a bookmark dataset (expected for current official X archives)"
    )
    assert expected_warning in second.warnings
    assert "detail enrichment failed: upstream 429" in second.warnings

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    manifest_warnings = json.loads(manifest_row["warnings_json"])
    assert expected_warning in manifest_warnings
    assert "detail enrichment failed: upstream 429" in manifest_warnings
    store.close()


def test_enrich_imported_archive_requires_completed_import(paths) -> None:
    with pytest.raises(ConfigError, match="No completed X archive import found"):
        asyncio.run(
            enrich_imported_archive(
                config=AppConfig(),
                paths=paths,
                console=_console(),
            )
        )


def test_enrich_imported_archive_reuses_existing_import_state(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    captured: dict[str, object] = {}

    async def fake_reconciliation(**kwargs):
        captured["collections"] = kwargs["collections"]
        return ["tweets", "likes"], ["bulk reconciliation warning"], _auth_bundle()

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        return 4, 1, 2, 3

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fake_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        enrich_imported_archive(
            limit=25,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert captured == {"collections": ["tweets", "likes"], "limit": 25}
    assert result.reconciled_collections == ["tweets", "likes"]
    assert result.warnings == ["bulk reconciliation warning"]
    assert result.detail_lookups == 4
    assert result.detail_terminal_unavailable == 1
    assert result.detail_transient_failures == 2
    assert result.pending_enrichment == 3


def test_enrich_imported_archive_can_skip_live_reconciliation(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    provided_auth = _auth_bundle()
    captured: dict[str, object] = {}

    async def fail_reconciliation(**_kwargs):
        raise AssertionError("live reconciliation should be skipped")

    async def fake_enrich_pending_rows(**kwargs):
        captured["limit"] = kwargs["limit"]
        captured["auth_token"] = kwargs["auth_bundle"].auth_token
        captured["user_id"] = kwargs["auth_bundle"].user_id
        return 5, 0, 1, 2

    monkeypatch.setattr(archive_import, "_run_live_reconciliation", fail_reconciliation)
    monkeypatch.setattr(archive_import, "_enrich_pending_rows", fake_enrich_pending_rows)

    result = asyncio.run(
        enrich_imported_archive(
            limit=7,
            reconcile_live=False,
            config=AppConfig(),
            paths=paths,
            auth_bundle=provided_auth,
            console=_console(),
        )
    )

    assert captured == {"limit": 7, "auth_token": "auth", "user_id": "42"}
    assert result.reconciled_collections == []
    assert result.warnings == []
    assert result.detail_lookups == 5
    assert result.detail_terminal_unavailable == 0
    assert result.detail_transient_failures == 1
    assert result.pending_enrichment == 2


def test_enrich_pending_rows_batches_detail_writes(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    buffer = archive_import._PageBuffer()
    for index in range(12):
        tweet_id = str(1000 + index)
        store._queue_record(
            store._tweet_object_record(
                archive_import._PlaceholderTweetObject(
                    tweet_id=tweet_id,
                    text=f"placeholder {tweet_id}",
                ),
                source=archive_import.ARCHIVE_SOURCE,
                enrichment_state="pending",
                cursor=buffer,
            ),
            cursor=buffer,
        )
    archive_import._flush_buffer(store, buffer)
    before = store.version_count()
    store.close()

    @asynccontextmanager
    async def fake_locked_archive_job(*, config=None, paths=None, console=None):
        store = open_archive_store(paths, create=False)
        assert store is not None

        class _Job:
            def __init__(self, store):
                self.store = store

            def mark_dirty(self, rows: int = 1, batches: int = 1) -> None:
                return None

        try:
            yield _Job(store)
        finally:
            store.close()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*args, **_kwargs):
        detail_url = args[1]
        _operation, variables = request_details(detail_url)
        tweet_id = variables["focalTweetId"]
        payload = make_tweet_detail_response(
            [make_tweet_result(tweet_id, f"enriched {tweet_id}", user_id="777")]
        )
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", detail_url),
        )

    class DummyClient:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(archive_import, "_DETAIL_ENRICH_WRITE_BATCH", 5)
    monkeypatch.setattr(archive_import, "locked_archive_job", fake_locked_archive_job)
    monkeypatch.setattr(archive_import, "resolve_query_ids", fake_resolve_query_ids)
    monkeypatch.setattr(
        archive_import, "build_async_client", lambda *_args, **_kwargs: DummyClient()
    )
    monkeypatch.setattr(archive_import, "fetch_page", fake_fetch_page)

    refreshed, terminal, transient, pending = asyncio.run(
        archive_import._enrich_pending_rows(
            limit=None,
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert (refreshed, terminal, transient, pending) == (12, 0, 0, 0)

    store = open_archive_store(paths, create=False)
    assert store is not None
    after = store.version_count()
    tweet_object_rows = store.list_tweet_objects_for_enrichment()
    store.close()

    assert after - before == 3
    assert tweet_object_rows == []


def test_archive_live_reconciliation_skips_resuming_saved_backfills(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, object]] = []

    async def fake_sync_collection(*args, **kwargs):
        captured.append({"collection": args[0], **kwargs})
        return SimpleNamespace(pages_fetched=1, tweets_seen=1, stop_reason="duplicate")

    monkeypatch.setattr(archive_import, "sync_collection", fake_sync_collection)

    reconciled, warnings, resolved_auth = asyncio.run(
        archive_import._run_live_reconciliation(
            collections=["likes", "tweets"],
            config=AppConfig(),
            paths=paths,
            auth_bundle=_auth_bundle(),
            transport=None,
            console=_console(),
        )
    )

    assert reconciled == ["likes", "tweets"]
    assert warnings == []
    assert resolved_auth is not None
    assert [kwargs["collection"] for kwargs in captured] == ["likes", "tweets"]
    assert all(kwargs["resume_backfill"] is False for kwargs in captured)


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


def test_import_x_archive_parse_errors_include_filename(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    (archive_dir / "data" / "like.js").write_text(
        "window.YTD.like.part0 = not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"like\.js"):
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
    assert media_row["download_state"] == "pending"
    assert media_row["local_path"] is None
    assert media_row["thumbnail_local_path"] == "media/100/7_500-poster.jpg"
    assert media_row["thumbnail_sha256"] is None
    assert media_row["thumbnail_byte_size"] is None
    assert store.list_media_rows(states={"pending"})[0]["row_key"] == media_row["row_key"]
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
    assert media_row["download_state"] == "done"
    assert (paths.data_dir / "media" / "100" / "7_500.mp4").exists()
    assert (paths.data_dir / "media" / "100" / "7_500-poster.jpg").exists()
    store.close()


def test_archive_deleted_tweet_preserves_existing_live_fields(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("200", text="live deleted tweet")
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="200",
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
    tweet_row = store.table.search().where("row_key = 'tweet:tweet::200'").limit(1).to_list()[0]
    assert tweet_row["source"] == "live_graphql"
    assert tweet_row["text"] == "live deleted tweet"
    assert tweet_row["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
    tweet_object = store.table.search().where("row_key = 'tweet_object:200'").limit(1).to_list()[0]
    assert tweet_object["source"] == "live_graphql"
    assert tweet_object["text"] == "live deleted tweet"
    assert tweet_object["deleted_at"] == "Mon Mar 16 00:00:00 +0000 2026"
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


def test_import_x_archive_detail_stale_query_id_leaves_rows_retryable(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    async def fake_reconciliation(**_kwargs):
        return [], [], _auth_bundle()

    async def fake_resolve_query_ids(*_args, **_kwargs):
        return {"TweetDetail": "detail-query-id"}

    async def fake_fetch_page(*_args, **_kwargs):
        raise StaleQueryIdError("stale query id", status_code=404)

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
    assert result.detail_terminal_unavailable == 0
    assert result.detail_transient_failures == 0
    assert result.pending_enrichment == 1
    assert any("detail enrichment failed: stale query id" in warning for warning in result.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    tweet_object = store.table.search().where("row_key = 'tweet_object:300'").limit(1).to_list()[0]
    assert tweet_object["enrichment_state"] == "pending"
    assert tweet_object["enrichment_http_status"] is None
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    manifest_counts = json.loads(manifest_row["counts_json"])
    assert manifest_counts["pending_enrichment"] == 1
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


def test_import_x_archive_sample_limit_does_not_require_debug(paths, tmp_path: Path) -> None:
    archive_dir = _write_archive_dir(tmp_path)

    result = asyncio.run(
        import_x_archive(
            archive_dir,
            sample_limit=1,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert result.skipped is False
    assert result.followup_performed is False
    assert any("sampled import" in warning for warning in result.warnings)


def test_sampled_debug_import_stays_non_completed_and_full_import_can_rerun(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    sampled = asyncio.run(
        import_x_archive(
            archive_dir,
            sample_limit=1,
            debug=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert sampled.skipped is False
    assert sampled.followup_performed is False
    assert any("sampled import" in warning for warning in sampled.warnings)

    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    assert manifest_row["status"] == "sampled"
    store.close()

    full = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert full.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    assert manifest_row["status"] == "completed"
    store.close()


def test_interrupted_import_marks_manifest_failed_and_rerun_reuses_archive_captures(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)
    original_import_authored_tweets = archive_import._import_authored_tweets
    optimize_calls = {"count": 0}

    def abort_import(*_args, **_kwargs) -> None:
        raise KeyboardInterrupt()

    def fake_optimize(self) -> None:
        optimize_calls["count"] += 1

    monkeypatch.setattr(archive_import, "_import_authored_tweets", abort_import)
    monkeypatch.setattr(archive_import.ArchiveStore, "optimize", fake_optimize)

    with pytest.raises(KeyboardInterrupt):
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
    assert manifest_row["status"] == "failed"
    raw_capture_count = store.counts()["raw_captures"]
    store.close()

    assert optimize_calls["count"] == 1

    monkeypatch.setattr(archive_import, "_import_authored_tweets", original_import_authored_tweets)

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    store = open_archive_store(paths, create=False)
    assert store is not None
    manifest_row = (
        store.table.search().where("record_type = 'import_manifest'").limit(1).to_list()[0]
    )
    assert manifest_row["status"] == "completed"
    assert store.counts()["raw_captures"] == raw_capture_count
    store.close()


def test_import_x_archive_regen_clears_archive_rows_but_keeps_live_rows(
    paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_dir = _write_archive_dir(tmp_path)
    _disable_live_reconciliation(monkeypatch)

    store = open_archive_store(paths, create=True)
    assert store is not None
    live_tweet = _live_tweet("900", text="existing live bookmark")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[live_tweet],
        last_head_tweet_id="900",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

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
    first_raw_capture_count = store.counts()["raw_captures"]
    media_row = store.table.search().where("record_type = 'media'").limit(1).to_list()[0]
    original_media_path = paths.data_dir / str(media_row["local_path"])
    stale_media_path = paths.data_dir / "media" / "100" / "stale.jpg"
    stale_media_path.parent.mkdir(parents=True, exist_ok=True)
    original_media_path.rename(stale_media_path)
    updated_media_row = dict(media_row)
    updated_media_row["local_path"] = "media/100/stale.jpg"
    store.merge_rows([updated_media_row])
    store.close()

    rerun = asyncio.run(
        import_x_archive(
            archive_dir,
            regen=True,
            config=AppConfig(),
            paths=paths,
            console=_console(),
        )
    )

    assert rerun.skipped is False
    assert not stale_media_path.exists()
    assert (paths.data_dir / "media" / "100" / "3_500.jpg").exists()

    store = open_archive_store(paths, create=False)
    assert store is not None
    assert store.counts()["raw_captures"] == first_raw_capture_count
    live_row = store.table.search().where("row_key = 'tweet:bookmark::900'").limit(1).to_list()[0]
    assert live_row["source"] == "live_graphql"
    assert store.counts()["import_manifests"] == 1
    store.close()


def test_remove_archive_owned_files_only_removes_media_subtree(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    media_path = data_dir / "media" / "100" / "asset.jpg"
    notes_path = data_dir / "notes" / "asset.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"media")
    notes_path.write_bytes(b"notes")

    removed = archive_import._remove_archive_owned_files(
        data_dir,
        ["media/100/asset.jpg", "notes/asset.jpg", "../escape.jpg", "/tmp/escape.jpg"],
    )

    assert removed == 1
    assert not media_path.exists()
    assert notes_path.exists()
