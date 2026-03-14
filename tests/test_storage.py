from __future__ import annotations

import pytest

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.exceptions import ArchiveOwnerMismatchError
from tweetxvault.storage import open_archive_store


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

    assert store.counts() == {"raw_captures": 0, "tweets": 0, "collections": 0, "sync_state": 0}
    monkeypatch.setattr(store, "upsert_membership", original)
    store.close()


def test_owner_guardrail(paths) -> None:
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.set_archive_owner_id("42")
    store.connection.commit()
    with pytest.raises(ArchiveOwnerMismatchError):
        store.ensure_archive_owner_id("84")
    store.close()
