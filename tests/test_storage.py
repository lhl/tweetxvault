from __future__ import annotations

import pytest

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.exceptions import ArchiveOwnerMismatchError
from tweetxvault.storage import open_archive_store


def _counts(**overrides: int) -> dict[str, int]:
    counts = {
        "raw_captures": 0,
        "tweets": 0,
        "collections": 0,
        "tweet_objects": 0,
        "tweet_relations": 0,
        "media": 0,
        "urls": 0,
        "url_refs": 0,
        "articles": 0,
        "sync_state": 0,
    }
    counts.update(overrides)
    return counts


def _tweet(tweet_id: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=f"tweet {tweet_id}",
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json={"tweet": tweet_id},
    )


def _complex_tweet(tweet_id: str = "100") -> TimelineTweet:
    quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        note_text="quoted longform",
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


def test_store_persist_page_is_atomic(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    original = store.upsert_membership

    def fail_once(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "upsert_membership", fail_once)

    with pytest.raises(RuntimeError):
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out="cursor-1",
            http_status=200,
            raw_json={"ok": True},
            tweets=[_tweet("1")],
            last_head_tweet_id="1",
            backfill_cursor="cursor-1",
            backfill_incomplete=True,
        )

    assert store.counts() == _counts()
    monkeypatch.setattr(store, "upsert_membership", original)
    store.close()


def test_store_persist_page_is_atomic_on_secondary_object_failure(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    def fail_once(*args, **kwargs):
        raise RuntimeError("secondary boom")

    monkeypatch.setattr(store, "_buffer_secondary_objects", fail_once)

    with pytest.raises(RuntimeError):
        store.persist_page(
            operation="Bookmarks",
            collection_type="bookmark",
            cursor_in=None,
            cursor_out="cursor-1",
            http_status=200,
            raw_json={"ok": True},
            tweets=[_complex_tweet()],
            last_head_tweet_id="100",
            backfill_cursor="cursor-1",
            backfill_incomplete=True,
        )

    assert store.counts() == _counts()
    store.close()


def test_owner_guardrail(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.set_archive_owner_id("42")
    with pytest.raises(ArchiveOwnerMismatchError):
        store.ensure_archive_owner_id("84")
    store.close()


def test_persist_page_creates_single_version(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    before = store.version_count()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1")],
        last_head_tweet_id="1",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    after = store.version_count()

    assert after == before + 1
    assert store.counts() == _counts(raw_captures=1, tweets=1, collections=1, sync_state=1)
    store.close()


def test_export_rows_only_returns_tweet_records(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.set_archive_owner_id("42")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1")],
        last_head_tweet_id="1",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )

    exported = store.export_rows("all")

    assert len(exported) == 1
    assert exported[0]["tweet_id"] == "1"
    assert exported[0]["collection"]["type"] == "bookmark"
    store.close()


def test_export_rows_filters_collection_without_table_scan(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("1")],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[_tweet("2")],
        last_head_tweet_id="2",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    def fail_to_arrow() -> None:
        raise AssertionError(
            "export_rows should filter through LanceDB search, not table.to_arrow()"
        )

    monkeypatch.setattr(store.table, "to_arrow", fail_to_arrow)

    exported = store.export_rows("bookmark")

    assert [row["tweet_id"] for row in exported] == ["1"]
    assert exported[0]["collection"]["type"] == "bookmark"
    store.close()


def test_persist_page_extracts_secondary_objects_once_across_collections(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    counts = store.counts()
    assert counts == _counts(
        raw_captures=2,
        tweets=2,
        collections=2,
        tweet_objects=2,
        tweet_relations=1,
        media=3,
        urls=2,
        url_refs=2,
        articles=1,
        sync_state=2,
    )

    article_rows = store.table.search().where("record_type = 'article'").to_list()
    assert article_rows[0]["title"] == "Article title"
    assert article_rows[0]["content_text"] == "Article body"
    media_rows = store.table.search().where("record_type = 'media'").to_list()
    assert any(row["source"] == "article_cover" for row in media_rows)
    relation_rows = store.table.search().where("record_type = 'tweet_relation'").to_list()
    assert relation_rows[0]["relation_type"] == "quote_of"
    assert relation_rows[0]["target_tweet_id"] == "200"
    store.close()


def test_rehydrate_from_raw_json_rebuilds_secondary_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    store.table.delete(
        "record_type IN ('tweet_object', 'tweet_relation', 'media', 'url', 'url_ref', 'article')"
    )
    tweet_row = store.table.search().where("record_type = 'tweet'").limit(1).to_list()[0]
    tweet_row["text"] = "root short"
    tweet_row["author_username"] = None
    tweet_row["author_display_name"] = None
    store._merge_records([tweet_row])

    result = store.rehydrate_from_raw_json()

    assert result.tweets_updated == 1
    assert result.secondary_records == 11
    counts = store.counts()
    assert counts["tweet_objects"] == 2
    assert counts["tweet_relations"] == 1
    assert counts["media"] == 3
    assert counts["urls"] == 2
    assert counts["url_refs"] == 2
    assert counts["articles"] == 1
    rebuilt_row = store.table.search().where("record_type = 'tweet'").limit(1).to_list()[0]
    assert rebuilt_row["text"] == "root longform text"
    assert rebuilt_row["author_username"] == "user1000"
    assert rebuilt_row["note_tweet_text"] == "root longform text"
    store.close()


def test_export_rows_include_secondary_objects(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id=tweet.tweet_id,
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    exported = store.export_rows("bookmark")

    assert len(exported) == 1
    row = exported[0]
    assert row["article"]["title"] == "Article title"
    assert row["article"]["media"][0]["source"] == "article_cover"
    assert any(item["source"] == "tweet_media" for item in row["media"])
    assert row["urls"][0]["resolved"]["canonical_url"] == "https://example.com/story?keep=1"
    store.close()
