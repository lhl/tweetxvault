# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from archive_store import LanceArchiveStore

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.exceptions import ArchiveOwnerMismatchError


def tweet(tweet_id: str, *, text: str, sort_index: str) -> TimelineTweet:
    return TimelineTweet(
        tweet_id=tweet_id,
        text=text,
        author_id="1",
        author_username="user1",
        author_display_name="User 1",
        created_at="Sat Mar 14 00:00:00 +0000 2026",
        sort_index=sort_index,
        raw_json={"tweet": tweet_id, "text": text},
    )


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="tweetxvault-lancedb-store-"))
    store = LanceArchiveStore(base / "archive.lancedb", create=True)

    initial_versions = store.version_count()
    initial_counts = store.counts()
    assert initial_counts == {"raw_captures": 0, "tweets": 0, "collections": 0, "sync_state": 0}

    original_merge = store._merge_records

    def fail_once(records):
        raise RuntimeError("synthetic merge failure")

    store._merge_records = fail_once
    try:
        try:
            store.persist_page(
                operation="Bookmarks",
                collection_type="bookmark",
                cursor_in=None,
                cursor_out="cursor-fail",
                http_status=200,
                raw_json={"ok": False},
                tweets=[tweet("0", text="should not persist", sort_index="999")],
                last_head_tweet_id="0",
                backfill_cursor="cursor-fail",
                backfill_incomplete=True,
            )
        except RuntimeError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("expected the synthetic merge failure")
    finally:
        store._merge_records = original_merge

    assert store.counts() == initial_counts
    assert store.version_count() == initial_versions

    store.ensure_archive_owner_id("42")
    try:
        store.ensure_archive_owner_id("84")
    except ArchiveOwnerMismatchError:
        owner_guardrail = True
    else:  # pragma: no cover - defensive
        raise AssertionError("owner guardrail did not trigger")

    before_page_1 = store.version_count()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="cursor-1",
        http_status=200,
        raw_json={"page": 1},
        tweets=[
            tweet("1", text="hello bookmarks", sort_index="500"),
            tweet("2", text="machine learning note", sort_index="499"),
        ],
        last_head_tweet_id="1",
        backfill_cursor="cursor-1",
        backfill_incomplete=True,
    )
    after_page_1 = store.version_count()

    before_page_2 = store.version_count()
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in="cursor-1",
        cursor_out=None,
        http_status=200,
        raw_json={"page": 2},
        tweets=[
            tweet("2", text="machine learning note updated", sort_index="499"),
            tweet("3", text="archive design", sort_index="498"),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    after_page_2 = store.version_count()

    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"page": "likes"},
        tweets=[tweet("2", text="liked machine learning note", sort_index="400")],
        last_head_tweet_id="2",
        backfill_cursor=None,
        backfill_incomplete=False,
    )

    counts = store.counts()
    bookmark_state = store.get_sync_state("bookmark")
    like_state = store.get_sync_state("like")
    exported_bookmarks = store.export_rows("bookmark")
    exported_all = store.export_rows("all")

    assert counts == {"raw_captures": 3, "tweets": 4, "collections": 4, "sync_state": 2}
    assert after_page_1 == before_page_1 + 1
    assert after_page_2 == before_page_2 + 1
    assert bookmark_state.backfill_incomplete is False
    assert bookmark_state.backfill_cursor is None
    assert bookmark_state.last_head_tweet_id == "1"
    assert like_state.last_head_tweet_id == "2"
    assert store.has_membership("2", "bookmark") is True
    assert store.has_membership("2", "like") is True
    assert store.has_membership("1", "like") is False
    assert [row["tweet_id"] for row in exported_bookmarks] == ["3", "2", "1"]
    assert exported_bookmarks[1]["text"] == "machine learning note updated"
    assert len(exported_all) == 4

    store.reset_sync_state("bookmark")
    reset_state = store.get_sync_state("bookmark")
    assert reset_state.backfill_cursor is None
    assert reset_state.last_head_tweet_id is None
    assert reset_state.backfill_incomplete is False

    print(
        json.dumps(
            {
                "base_dir": str(base),
                "owner_guardrail": owner_guardrail,
                "counts_after_spike": counts,
                "page_version_deltas": [after_page_1 - before_page_1, after_page_2 - before_page_2],
                "bookmark_export_order": [row["tweet_id"] for row in exported_bookmarks],
                "all_collections": [
                    (row["tweet_id"], row["collection"]["type"]) for row in exported_all
                ],
                "supports_duplicate_scoped_membership": (
                    store.has_membership("2", "bookmark") and store.has_membership("2", "like")
                ),
                "bookmark_state_before_reset": {
                    "last_head_tweet_id": bookmark_state.last_head_tweet_id,
                    "backfill_cursor": bookmark_state.backfill_cursor,
                    "backfill_incomplete": bookmark_state.backfill_incomplete,
                },
                "bookmark_state_after_reset": {
                    "last_head_tweet_id": reset_state.last_head_tweet_id,
                    "backfill_cursor": reset_state.backfill_cursor,
                    "backfill_incomplete": reset_state.backfill_incomplete,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
