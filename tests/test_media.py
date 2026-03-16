from __future__ import annotations

import httpx
import pytest

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.media import download_media
from tweetxvault.storage import open_archive_store


def _complex_tweet(tweet_id: str = "100") -> TimelineTweet:
    quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        urls=[
            make_url_entity(
                "https://t.co/quoted",
                "https://quoted.example.com/post",
                display_url="quoted.example.com/post",
            )
        ],
        media=[
            make_video_media(
                "7_quoted",
                "https://pbs.twimg.com/ext_tw_video_thumb/quoted.jpg",
                bitrate_url="https://video.twimg.com/ext_tw_video/quoted.mp4",
            )
        ],
    )
    raw = make_tweet_result(
        tweet_id,
        "root short",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story?utm_source=x&keep=1",
                display_url="example.com/story",
            )
        ],
        media=[make_photo_media("3_root", "https://pbs.twimg.com/media/root.jpg")],
        quoted_tweet=quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text="Article body",
            url="https://x.com/i/article/123",
        ),
    )
    return TimelineTweet(
        tweet_id=tweet_id,
        text="root short",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=raw,
    )


def _seed_archive(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_complex_tweet()],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()


@pytest.mark.asyncio
async def test_download_media_updates_rows_and_files(paths, config) -> None:
    _seed_archive(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(".mp4"):
            return httpx.Response(
                200, content=b"video-bytes", headers={"content-type": "video/mp4"}
            )
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})

    result = await download_media(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 3
    assert result.downloaded == 3
    assert result.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        rows = store.list_media_rows()
        assert all(row["download_state"] == "done" for row in rows)
        assert all(
            (paths.data_dir / row["local_path"]).exists() for row in rows if row["local_path"]
        )
        video_row = next(row for row in rows if row["media_type"] == "video")
        assert video_row["thumbnail_local_path"] is not None
        assert (paths.data_dir / video_row["thumbnail_local_path"]).exists()
    finally:
        store.close()

    repeat = await download_media(
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
    )
    assert repeat.processed == 0
    assert repeat.downloaded == 0
    assert repeat.skipped == 0


@pytest.mark.asyncio
async def test_download_media_retries_failed_rows_and_respects_limit(paths, config) -> None:
    _seed_archive(paths)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="nope", request=request)

    failed = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        transport=httpx.MockTransport(failing_handler),
    )

    assert failed.processed == 2
    assert failed.downloaded == 0
    assert failed.failed == 2

    skipped = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        transport=httpx.MockTransport(failing_handler),
    )
    assert skipped.processed == 0
    assert skipped.downloaded == 0
    assert skipped.failed == 0

    def success_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    retried = await download_media(
        config=config,
        paths=paths,
        photos_only=True,
        retry_failed=True,
        limit=1,
        transport=httpx.MockTransport(success_handler),
    )

    assert retried.processed == 1
    assert retried.downloaded == 1
    assert retried.failed == 0

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        done_photos = store.list_media_rows(states={"done"}, media_types={"photo"})
        failed_photos = store.list_media_rows(states={"failed"}, media_types={"photo"})
        pending_video = store.list_media_rows(states={"pending"}, media_types={"video"})
        assert len(done_photos) == 1
        assert len(failed_photos) == 1
        assert len(pending_video) == 1
    finally:
        store.close()
