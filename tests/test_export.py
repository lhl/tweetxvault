from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.export import export_html_archive, export_json_archive
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
        note_text="root longform text",
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
        text="root longform text",
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


def test_export_json_includes_article_media_and_urls(paths, tmp_path: Path) -> None:
    _seed_archive(paths)
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        out_path = tmp_path / "archive.json"
        export_json_archive(store, collection="bookmark", out_path=out_path)
    finally:
        store.close()

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    row = payload[0]
    assert row["article"]["title"] == "Article title"
    assert row["article"]["content_text"] == "Article body"
    assert row["article"]["media"][0]["source"] == "article_cover"
    assert row["urls"][0]["resolved"]["canonical_url"] == "https://example.com/story?keep=1"


def test_export_html_renders_article_body_and_media(paths, tmp_path: Path) -> None:
    _seed_archive(paths)
    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        out_path = tmp_path / "archive.html"
        export_html_archive(store, collection="bookmark", out_path=out_path)
    finally:
        store.close()

    html = out_path.read_text(encoding="utf-8")
    assert "Article title" in html
    assert "Article body" in html
    assert "example.com/story" in html
    assert "media-grid" in html
