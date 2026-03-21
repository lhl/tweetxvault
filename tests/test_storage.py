from __future__ import annotations

import pytest

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_detail_response,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.exceptions import ArchiveOwnerMismatchError
from tweetxvault.storage import open_archive_store
from tweetxvault.storage.backend import _PageBuffer


class _FakeQueryBuilder:
    def __init__(self) -> None:
        self.metric_name: str | None = None
        self.where_expr: str | None = None
        self.limit_value: int | None = None
        self.vector_value: list[float] | None = None
        self.text_value: str | None = None

    def metric(self, metric: str):
        self.metric_name = metric
        return self

    def where(self, expr: str, prefilter: bool | None = None):
        self.where_expr = expr
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def vector(self, vector: list[float]):
        self.vector_value = vector
        return self

    def text(self, text: str):
        self.text_value = text
        return self

    def to_list(self) -> list[dict[str, object]]:
        return [{"tweet_id": "1"}]


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
        "import_manifests": 0,
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


def _tweet_with_created_at(
    tweet_id: str,
    *,
    created_at: str | None,
    sort_index: str,
) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=f"tweet {tweet_id}",
        author_id="1",
        author_username=f"user{tweet_id}",
        author_display_name=f"User {tweet_id}",
        created_at=created_at,
        sort_index=sort_index,
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


def test_prefetch_rows_populates_buffer_cache(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
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

    buffer = _PageBuffer()
    store.prefetch_rows(
        [
            "tweet:bookmark::100",
            "tweet_object:100",
            "tweet_object:missing",
        ],
        cursor=buffer,
    )

    assert buffer.existing_rows["tweet:bookmark::100"]["tweet_id"] == "100"
    assert buffer.existing_rows["tweet_object:100"]["tweet_id"] == "100"
    assert buffer.existing_rows["tweet_object:missing"] is None
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


def test_archive_stats_summarizes_collections_and_bounds(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    store.set_archive_owner_id("42")
    newer = _complex_tweet("100")
    older = _tweet_with_created_at(
        "200",
        created_at="Tue Oct 09 21:39:26 +0000 2012",
        sort_index="20",
    )
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"ok": True},
        tweets=[newer],
        last_head_tweet_id="100",
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
        tweets=[newer],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[older],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    stats = store.archive_stats()
    collections = {row.collection_type: row for row in stats.collections}

    assert stats.owner_user_id == "42"
    assert stats.unique_post_count == 2
    assert stats.collection_membership_count == 3
    assert stats.article_count == 1
    assert stats.raw_capture_count == 3
    assert stats.media_count == 3
    assert stats.url_count == 2
    assert stats.oldest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    assert stats.newest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert stats.latest_capture_at is not None
    assert stats.latest_sync_at is not None
    assert stats.version_count == store.version_count()

    assert collections["bookmark"].post_count == 1
    assert collections["bookmark"].oldest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert collections["bookmark"].newest_created_at == "Sat Mar 14 00:00:00 +0000 2026"
    assert collections["bookmark"].last_synced_at is not None
    assert collections["bookmark"].backfill_cursor == "cursor-1"
    assert collections["bookmark"].backfill_incomplete is True

    assert collections["like"].post_count == 1
    assert collections["tweet"].post_count == 1
    assert collections["tweet"].oldest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    assert collections["tweet"].newest_created_at == "Tue Oct 09 21:39:26 +0000 2012"
    store.close()


def test_archive_stats_reports_followup_work(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    primary = _complex_tweet("100")
    linked_raw = make_tweet_result(
        "400",
        "linked status source",
        urls=[
            make_url_entity(
                "https://t.co/status",
                "https://x.com/other/status/999",
                display_url="x.com/other/status/999",
            )
        ],
    )
    linked = TimelineTweet(
        tweet_id="400",
        text="linked status source",
        author_id="100",
        author_username="user100",
        author_display_name="User 100",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="20",
        raw_json=linked_raw,
    )
    extra = _tweet("300")

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[primary, linked, extra],
        last_head_tweet_id="300",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[primary],
        raw_json={"ok": True},
    )
    store.update_tweet_object_enrichment(
        "100",
        enrichment_state="transient_failure",
        enrichment_checked_at="2026-03-21T00:00:00+00:00",
        enrichment_http_status=503,
        enrichment_reason="rate_limit",
    )
    store.update_tweet_object_enrichment(
        "400",
        enrichment_state="pending",
        enrichment_checked_at=None,
        enrichment_http_status=None,
        enrichment_reason=None,
    )
    store.table.delete("row_key = 'tweet_object:300'")

    stats = store.archive_stats()

    assert stats.pending_enrichment_count == 1
    assert stats.transient_enrichment_failure_count == 1
    assert stats.terminal_enrichment_count == 0
    assert stats.done_enrichment_count == 1
    assert stats.missing_tweet_object_count == 1
    assert stats.expanded_thread_target_count == 1
    assert stats.pending_thread_membership_count == 2
    assert stats.pending_thread_linked_status_count == 1
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


def test_export_rows_limit_only_fetches_secondary_rows_for_selected_tweets(
    paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    older = _complex_tweet("100")
    newer = _complex_tweet("200")
    newer.created_at = "Sun Mar 15 00:00:00 +0000 2026"
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[older, newer],
        last_head_tweet_id="200",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    seen: list[tuple[str, tuple[str, ...]]] = []
    original = store._rows_for_values

    def wrapped(record_type: str, field_name: str, values):
        if field_name == "tweet_id":
            seen.append((record_type, tuple(values)))
        return original(record_type, field_name, values)

    monkeypatch.setattr(store, "_rows_for_values", wrapped)

    exported = store.export_rows("bookmark", sort="newest", limit=1, include_raw_json=False)

    assert [row["tweet_id"] for row in exported] == ["200"]
    assert exported[0]["raw_json"] is None
    assert seen
    for record_type, values in seen:
        assert values == ("200",), f"{record_type} fetched secondary rows for unexpected tweets"
    store.close()


def test_export_rows_sorts_by_created_at_and_puts_unknown_dates_last(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweets = [
        _tweet_with_created_at(
            "older",
            created_at="Tue Oct 09 21:39:26 +0000 2012",
            sort_index="999",
        ),
        _tweet_with_created_at(
            "newer",
            created_at="Thu Apr 11 03:55:13 +0000 2024",
            sort_index="-999",
        ),
        _tweet_with_created_at(
            "unknown",
            created_at=None,
            sort_index="-1000",
        ),
    ]
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=tweets,
        last_head_tweet_id="newer",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    oldest = store.export_rows("like", sort="oldest")
    newest = store.export_rows("like", sort="newest")

    assert [row["tweet_id"] for row in oldest] == ["older", "newer", "unknown"]
    assert [row["tweet_id"] for row in newest] == ["newer", "older", "unknown"]
    store.close()


def test_search_fts_projects_posts_and_articles_with_aggregated_collections(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet("100")
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[tweet],
        last_head_tweet_id="100",
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
        tweets=[tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    post_results = store.search_fts("root", limit=5)
    article_results = store.search_fts("article", limit=5)
    article_only_posts = store.search_fts("article", limit=5, types={"post"})
    bookmark_articles = store.search_fts("article", limit=5, collections={"bookmark"})
    tweet_articles = store.search_fts("article", limit=5, collections={"tweet"})

    assert [(row["type"], row["tweet_id"], row["collections"]) for row in post_results] == [
        ("post", "100", ["bookmark", "like"])
    ]
    assert post_results[0]["author_username"] == "user1000"
    assert post_results[0]["text"] == "root longform text"

    assert [(row["type"], row["tweet_id"], row["collections"]) for row in article_results] == [
        ("article", "100", ["bookmark", "like"])
    ]
    assert str(article_results[0]["text"]).startswith("Article title")

    assert article_only_posts == []
    assert [(row["type"], row["tweet_id"]) for row in bookmark_articles] == [("article", "100")]
    assert tweet_articles == []
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


def test_persist_page_extracts_secondary_objects_once_for_authored_tweets(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    tweet = _complex_tweet()
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
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

    exported = store.export_rows("tweet")
    assert [row["tweet_id"] for row in exported] == [tweet.tweet_id]
    assert exported[0]["collection"]["type"] == "tweet"
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


def test_persist_page_preserves_richer_secondary_values_from_existing_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    rich_tweet = _complex_tweet()
    thin_quoted = make_tweet_result(
        "200",
        "quoted short",
        user_id="2000",
        media=[{"media_key": "7_quoted", "type": "video"}],
    )
    thin_raw = make_tweet_result(
        "100",
        "root short",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://example.com/story",
                display_url="example.com/story",
            )
        ],
        media=[{"media_key": "3_root", "type": "photo"}],
        quoted_tweet=thin_quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text=None,
            url=None,
        ),
    )
    thin_tweet = TimelineTweet(
        tweet_id="100",
        text="root short",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=thin_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[rich_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[thin_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    article_row = store.table.search().where("record_type = 'article'").limit(1).to_list()[0]
    assert article_row["content_text"] == "Article body"
    assert article_row["canonical_url"] == "https://x.com/i/article/123"
    assert article_row["status"] == "body_present"

    media_rows = store.table.search().where("record_type = 'media'").to_list()
    media_by_key = {row["media_key"]: row for row in media_rows}
    assert media_by_key["3_root"]["media_url"] == "https://pbs.twimg.com/media/root.jpg"
    assert (
        media_by_key["7_quoted"]["media_url"]
        == "https://video.twimg.com/ext_tw_video/quoted-hd.mp4"
    )
    assert media_by_key["7_quoted"]["variants_json"] is not None
    store.close()


def test_secondary_row_listings_filter_through_lancedb(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    body_tweet = _complex_tweet()
    preview_raw = make_tweet_result(
        "101",
        "preview short",
        user_id="1001",
        urls=[
            make_url_entity(
                "https://t.co/preview",
                "https://preview.example.com/post",
                display_url="preview.example.com/post",
            )
        ],
        article=make_article_result(
            "article-2",
            title="Preview article",
            preview_text="Preview only",
            plain_text=None,
            url="https://x.com/i/article/456",
        ),
    )
    preview_tweet = TimelineTweet(
        tweet_id="101",
        text="preview short",
        author_id="1001",
        author_username="user1001",
        author_display_name="User 1001",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="11",
        raw_json=preview_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[body_tweet, preview_tweet],
        last_head_tweet_id="101",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    root_photo = next(row for row in store.list_media_rows() if row["media_key"] == "3_root")
    store.update_media_download(
        root_photo["row_key"],
        download_state="done",
        local_path="media/root.jpg",
        sha256="root-sha",
        byte_size=123,
        content_type="image/jpeg",
        thumbnail_local_path=None,
        thumbnail_sha256=None,
        thumbnail_byte_size=None,
        thumbnail_content_type=None,
        downloaded_at="2026-03-16T00:00:00Z",
        download_error=None,
    )

    root_url = next(
        row
        for row in store.list_url_rows()
        if row["canonical_url"] == "https://example.com/story?keep=1"
    )
    store.update_url_unfurl(
        root_url["row_key"],
        http_status=200,
        final_url="https://example.com/story?keep=1",
        canonical_url="https://example.com/story?keep=1",
        title="Root title",
        description="Root description",
        site_name="Example",
        content_type="text/html",
        unfurl_state="done",
        last_fetched_at="2026-03-16T00:00:00Z",
        download_error=None,
    )

    pending_video_rows = store.list_media_rows(states={"pending"}, media_types={"video"})
    assert [(row["tweet_id"], row["media_key"]) for row in pending_video_rows] == [
        ("200", "7_quoted")
    ]

    done_photo_rows = store.list_media_rows(states={"done"}, media_types={"photo"})
    assert [(row["tweet_id"], row["media_key"]) for row in done_photo_rows] == [("100", "3_root")]

    pending_url_rows = store.list_url_rows(states={"pending"})
    assert [row["canonical_url"] for row in pending_url_rows] == [
        "https://preview.example.com/post",
        "https://quoted.example.com/post",
    ]

    done_url_rows = store.list_url_rows(states={"done"})
    assert [row["canonical_url"] for row in done_url_rows] == ["https://example.com/story?keep=1"]

    preview_rows = store.list_article_rows(preview_only=True)
    assert [row["tweet_id"] for row in preview_rows] == ["101"]
    assert preview_rows[0]["status"] == "preview_only"
    store.close()


def test_search_vector_uses_cosine_metric(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    query = _FakeQueryBuilder()
    captured: dict[str, object] = {}

    def fake_search(value, *, vector_column_name=None, query_type="auto"):
        captured["value"] = value
        captured["vector_column_name"] = vector_column_name
        captured["query_type"] = query_type
        return query

    monkeypatch.setattr(store.table, "search", fake_search)
    monkeypatch.setattr(
        store,
        "_collect_search_context",
        lambda tweet_ids: ({"1": ["bookmark"]}, {"1": {}}),
    )

    results = store.search_vector([0.1, 0.2, 0.3], limit=7)

    assert len(results) == 1
    assert results[0]["tweet_id"] == "1"
    assert results[0]["type"] == "post"
    assert captured == {
        "value": [0.1, 0.2, 0.3],
        "vector_column_name": "embedding",
        "query_type": "vector",
    }
    assert query.metric_name == "cosine"
    assert query.where_expr == "record_type = 'tweet' AND embedding IS NOT NULL"
    assert query.limit_value == 7
    store.close()


def test_search_hybrid_uses_cosine_metric(paths, monkeypatch: pytest.MonkeyPatch) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    query = _FakeQueryBuilder()
    captured: dict[str, object] = {}

    def fake_search(query_value=None, *, vector_column_name=None, query_type="auto"):
        captured["query_value"] = query_value
        captured["vector_column_name"] = vector_column_name
        captured["query_type"] = query_type
        return query

    monkeypatch.setattr(store.table, "search", fake_search)
    monkeypatch.setattr(store, "ensure_fts_index", lambda: None)
    monkeypatch.setattr(
        store,
        "_collect_search_context",
        lambda tweet_ids: ({"1": ["bookmark"]}, {"1": {}}),
    )

    results = store.search_hybrid("machine learning", [0.1, 0.2, 0.3], limit=9)

    assert len(results) == 1
    assert results[0]["tweet_id"] == "1"
    assert results[0]["type"] == "post"
    assert captured == {
        "query_value": None,
        "vector_column_name": "embedding",
        "query_type": "hybrid",
    }
    assert query.vector_value == [0.1, 0.2, 0.3]
    assert query.text_value == "machine learning"
    assert query.metric_name == "cosine"
    assert query.where_expr == "record_type = 'tweet'"
    assert query.limit_value == 9
    store.close()


def test_persist_thread_detail_keeps_context_tweets_out_of_memberships(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    root_raw = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        conversation_id="200",
        urls=[
            make_url_entity(
                "https://t.co/thread",
                "https://x.com/example/status/300?s=20",
                display_url="x.com/example/status/300",
            )
        ],
    )
    root_tweet = TimelineTweet(
        tweet_id="100",
        text="reply tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=root_raw,
    )
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    parent_tweet = TimelineTweet(
        tweet_id="200",
        text="parent tweet",
        author_id="2000",
        author_username="user2000",
        author_display_name="User 2000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=parent_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[root_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[root_tweet, parent_tweet],
        raw_json=make_tweet_detail_response([root_raw, parent_raw]),
    )

    membership_rows = store.table.search().where("record_type = 'tweet'").to_list()
    assert [row["tweet_id"] for row in membership_rows] == ["100"]

    tweet_object_rows = store.table.search().where("record_type = 'tweet_object'").to_list()
    assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200"}
    relation_rows = store.table.search().where("record_type = 'tweet_relation'").to_list()
    relations = {
        (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
    }
    assert ("100", "reply_to", "200") in relations
    assert ("100", "thread_parent", "200") in relations
    assert ("200", "thread_child", "100") in relations
    assert ("100", "links_to_status", "300") in relations

    raw_capture_rows = store.table.search().where("record_type = 'raw_capture'").to_list()
    assert any(row["operation"] == "ThreadExpandDetail" for row in raw_capture_rows)
    store.close()


def test_rehydrate_from_raw_json_rebuilds_thread_capture_secondary_rows(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None

    root_raw = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        conversation_id="200",
        urls=[
            make_url_entity(
                "https://t.co/thread",
                "https://x.com/example/status/300?s=20",
                display_url="x.com/example/status/300",
            )
        ],
    )
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    root_tweet = TimelineTweet(
        tweet_id="100",
        text="reply tweet",
        author_id="1000",
        author_username="user1000",
        author_display_name="User 1000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="10",
        raw_json=root_raw,
    )
    parent_tweet = TimelineTweet(
        tweet_id="200",
        text="parent tweet",
        author_id="2000",
        author_username="user2000",
        author_display_name="User 2000",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index="9",
        raw_json=parent_raw,
    )

    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"ok": True},
        tweets=[root_tweet],
        last_head_tweet_id="100",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.persist_thread_detail(
        focal_tweet_id="100",
        tweets=[root_tweet, parent_tweet],
        raw_json=make_tweet_detail_response([root_raw, parent_raw]),
    )
    store.table.delete(
        "record_type IN ('tweet_object', 'tweet_relation', 'media', 'url', 'url_ref', 'article')"
    )

    result = store.rehydrate_from_raw_json()

    assert result.secondary_records > 0
    tweet_object_rows = store.table.search().where("record_type = 'tweet_object'").to_list()
    assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200"}
    relation_rows = store.table.search().where("record_type = 'tweet_relation'").to_list()
    relations = {
        (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
    }
    assert ("100", "reply_to", "200") in relations
    assert ("100", "thread_parent", "200") in relations
    assert ("200", "thread_child", "100") in relations
    assert ("100", "links_to_status", "300") in relations
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
