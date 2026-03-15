from __future__ import annotations

import httpx
import pytest

from tests.conftest import make_tweet_detail_response, make_tweet_result, make_url_entity
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.query_ids import QueryIdStore
from tweetxvault.storage import open_archive_store
from tweetxvault.threads import expand_threads, normalize_thread_target


def _seed_thread_archive(paths) -> dict[str, object]:
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
    store = open_archive_store(paths, create=True)
    assert store is not None
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
    store.close()
    return root_raw


def test_normalize_thread_target_accepts_ids_and_urls() -> None:
    assert normalize_thread_target("2026531440414925307") == "2026531440414925307"
    assert (
        normalize_thread_target("https://x.com/dimitrispapail/status/2026531440414925307")
        == "2026531440414925307"
    )


@pytest.mark.asyncio
async def test_expand_threads_fetches_membership_and_linked_status(
    paths,
    config,
    auth_bundle,
) -> None:
    root_raw = _seed_thread_archive(paths)
    parent_raw = make_tweet_result("200", "parent tweet", user_id="2000")
    linked_raw = make_tweet_result("300", "linked tweet", user_id="3000")
    QueryIdStore(paths).save({"TweetDetail": "detail-qid"})
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        focal = request.url.params["variables"]
        if '"focalTweetId":"100"' in focal:
            requests.append("100")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([root_raw, parent_raw], module=True),
                request=request,
            )
        if '"focalTweetId":"300"' in focal:
            requests.append("300")
            return httpx.Response(
                200,
                json=make_tweet_detail_response([linked_raw]),
                request=request,
            )
        raise AssertionError(f"unexpected request {request.url}")

    result = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(handler),
    )

    assert result.processed == 2
    assert result.expanded == 2
    assert result.failed == 0
    assert requests == ["100", "300"]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        membership_rows = store.table.search().where("record_type = 'tweet'").to_list()
        assert [row["tweet_id"] for row in membership_rows] == ["100"]

        tweet_object_rows = store.table.search().where("record_type = 'tweet_object'").to_list()
        assert {row["tweet_id"] for row in tweet_object_rows} == {"100", "200", "300"}

        relation_rows = store.table.search().where("record_type = 'tweet_relation'").to_list()
        relations = {
            (row["tweet_id"], row["relation_type"], row["target_tweet_id"]) for row in relation_rows
        }
        assert ("100", "reply_to", "200") in relations
        assert ("100", "thread_parent", "200") in relations
        assert ("200", "thread_child", "100") in relations
        assert ("100", "links_to_status", "300") in relations
        assert store.list_raw_capture_target_ids("ThreadExpandDetail") == ["100", "300"]
    finally:
        store.close()

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"should not refetch thread targets: {request.url}")

    second = await expand_threads(
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=httpx.MockTransport(unexpected_request),
    )

    assert second.processed == 0
    assert second.expanded == 0
    assert second.failed == 0
    assert second.skipped >= 1
