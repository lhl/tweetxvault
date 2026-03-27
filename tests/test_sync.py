from __future__ import annotations

import asyncio
import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from rich.console import Console

import tweetxvault.sync as sync_module
from tests.conftest import (
    make_article_result,
    make_bookmarks_response,
    make_likes_response,
    make_tweet_result,
    make_user_tweets_response,
)
from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import ConfigError, ProcessLockError, TweetXVaultError
from tweetxvault.query_ids import QueryIdStore
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import ProcessLock, RemotePreflightError, sync_all, sync_collection


def _op_and_variables(request: httpx.Request) -> tuple[str, dict[str, object]]:
    parsed = urlparse(str(request.url))
    operation = parsed.path.split("/")[-1]
    variables = json.loads(parse_qs(parsed.query)["variables"][0])
    return operation, variables


def _save_query_ids(paths) -> None:
    QueryIdStore(paths).save(
        {
            "Bookmarks": "qid-bookmarks",
            "Likes": "qid-likes",
            "UserTweets": "qid-user-tweets",
        }
    )


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


def _bookmarks_response_from_results(
    results: list[dict[str, object]],
    *,
    cursor: str | None = None,
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for index, result in enumerate(results):
        tweet_id = result["rest_id"]
        entries.append(
            {
                "entryId": f"tweet-{tweet_id}",
                "sortIndex": str(500 - index),
                "content": {
                    "entryType": "TimelineTimelineItem",
                    "itemContent": {
                        "itemType": "TimelineTweet",
                        "tweet_results": {"result": result},
                    },
                },
            }
        )
    if cursor is not None:
        entries.append(
            {
                "entryId": "cursor-bottom-1",
                "content": {"cursorType": "Bottom", "value": cursor},
            }
        )
    return {
        "data": {
            "bookmark_timeline_v2": {
                "timeline": {
                    "instructions": [{"type": "TimelineAddEntries", "entries": entries}],
                }
            }
        }
    }


@pytest.mark.asyncio
async def test_sync_collection_end_to_end_and_resume(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)
    seen: list[tuple[str, str | None, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))

        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    transport = httpx.MockTransport(handler)
    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=transport,
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    seen.clear()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    assert [item for item in seen if item[2] == 20][:2] == [
        ("Bookmarks", None, 20),
        ("Bookmarks", "c1", 20),
    ]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["raw_captures"] == 4
        assert counts["tweets"] == 4
        assert counts["collections"] == 4
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_collection_runs_followups_when_requested(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_query_ids(paths)
    calls: list[object] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["probe"], cursor="probe-cursor"),
                request=request,
            )
        return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)

    async def fake_followups(**kwargs) -> None:
        calls.append(kwargs["plan"])

    monkeypatch.setattr(sync_module, "_run_auto_followups", fake_followups)

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
        followups=sync_module.SyncFollowupPlan(),
    )

    assert result.pages_fetched == 1
    assert calls == [sync_module.SyncFollowupPlan()]


@pytest.mark.asyncio
async def test_run_auto_followups_continues_after_task_failure(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    calls: list[str] = []

    async def fail_enrich(**_kwargs):
        calls.append("enrich")
        raise RuntimeError("boom")

    async def ok_threads(**_kwargs):
        calls.append("threads")
        return SimpleNamespace(processed=1, expanded=1, skipped=0, failed=0)

    async def ok_articles(**_kwargs):
        calls.append("articles")
        return SimpleNamespace(processed=1, updated=1, failed=0)

    async def ok_media(**_kwargs):
        calls.append("media")
        return SimpleNamespace(processed=1, downloaded=1, skipped=0, failed=0)

    async def ok_unfurl(**_kwargs):
        calls.append("unfurl")
        return SimpleNamespace(processed=1, updated=1, failed=0)

    monkeypatch.setattr(sync_module, "_run_followup_archive_enrich", fail_enrich)
    monkeypatch.setattr(sync_module, "_run_followup_threads", ok_threads)
    monkeypatch.setattr(sync_module, "_run_followup_articles", ok_articles)
    monkeypatch.setattr(sync_module, "_run_followup_media", ok_media)
    monkeypatch.setattr(sync_module, "_run_followup_unfurl", ok_unfurl)

    await sync_module._run_auto_followups(
        plan=sync_module.SyncFollowupPlan(),
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        transport=None,
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert calls == ["enrich", "threads", "articles", "media", "unfurl"]
    output = buffer.getvalue()
    assert "sync follow-up: running archive enrich" in output
    assert "sync follow-up archive enrich failed" in output
    assert "sync follow-up: threads: 1 processed, 1 expanded, 0 skipped, 0 failed" in output


@pytest.mark.asyncio
async def test_sync_collection_interrupt_best_effort_optimizes_after_committed_pages(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_query_ids(paths)
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    optimize_calls = {"count": 0}
    sleep_calls = {"count": 0}

    def fake_optimize(self) -> None:
        optimize_calls["count"] += 1

    async def interrupt_after_four_pages(_delay: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 4:
            raise KeyboardInterrupt()

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["probe"], cursor="probe-cursor"),
                request=request,
            )
        if cursor is None:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["1"], cursor="c1"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=make_bookmarks_response(["2"], cursor="c2"),
                request=request,
            )
        if cursor == "c2":
            return httpx.Response(
                200,
                json=make_bookmarks_response(["3"], cursor="c3"),
                request=request,
            )
        if cursor == "c3":
            return httpx.Response(
                200,
                json=make_bookmarks_response(["4"], cursor="c4"),
                request=request,
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    monkeypatch.setattr(sync_module.ArchiveStore, "optimize", fake_optimize)

    with pytest.raises(KeyboardInterrupt):
        await sync_collection(
            "bookmarks",
            full=False,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(handler),
            console=console,
            sleep=interrupt_after_four_pages,
        )

    assert optimize_calls["count"] == 1
    assert "interrupt received, compacting archive before exit" in buffer.getvalue()


@pytest.mark.asyncio
async def test_sync_collection_logs_head_and_backfill_progress(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200, json=make_bookmarks_response(["2"], cursor="c2"), request=request
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    output = console_buffer.getvalue()
    assert "bookmarks: starting head pass" in output
    assert "bookmarks head: page 1, page_tweets 2, total_tweets 2, stop=duplicate" in output
    assert "bookmarks: resuming saved backfill pass" in output
    assert "bookmarks backfill: page 1, page_tweets 1, total_tweets 1, stop=continue" in output


@pytest.mark.asyncio
async def test_sync_collection_head_only_clears_saved_backfill_state(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        head_only=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 1
    output = console_buffer.getvalue()
    assert "bookmarks: starting head pass" in output
    assert "bookmarks head: page 1, page_tweets 2, total_tweets 2, stop=duplicate" in output
    assert "bookmarks: resuming saved backfill pass" not in output

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.backfill_cursor is None
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_collection_clears_saved_backfill_after_empty_backfill_page(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def second_handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200, json=make_bookmarks_response(["new"], cursor="probe-cursor"), request=request
            )
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["new", "1"], cursor="ignored"), request=request
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([], cursor="c1"),
                request=request,
            )
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 2
    output = console_buffer.getvalue()
    assert "bookmarks: resuming saved backfill pass" in output
    assert "bookmarks backfill: page 1, page_tweets 0, total_tweets 0, stop=empty" in output

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is False
        assert state.backfill_cursor is None
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_head_only_cannot_be_combined_with_backfill_modes(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    with pytest.raises(ConfigError, match="--head-only cannot be combined"):
        await sync_collection(
            "bookmarks",
            full=False,
            backfill=True,
            head_only=True,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json=make_bookmarks_response(["1"]), request=request
                )
            ),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )


@pytest.mark.asyncio
async def test_sync_collection_keeps_success_when_auto_embedding_fails(
    paths,
    config: AppConfig,
    auth_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _save_query_ids(paths)
    monkeypatch.setitem(
        sys.modules,
        "tweetxvault.embed",
        SimpleNamespace(
            is_available=lambda: True,
            get_engine=lambda: (_ for _ in ()).throw(RuntimeError("model download failed")),
        ),
    )
    console_buffer = StringIO()
    console = Console(file=console_buffer, force_terminal=False, color_system=None)

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        if int(variables["count"]) == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=console,
        sleep=lambda _: asyncio.sleep(0),
    )

    assert result.pages_fetched == 1
    output = console_buffer.getvalue()
    assert "sync completed, but auto-embedding was skipped" in output
    assert "model download failed" in output

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["raw_captures"] == 1
        assert counts["tweets"] == 1
        assert counts["collections"] == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_user_tweets_end_to_end_and_resume(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    seen: list[tuple[str, str | None, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))

        if count == 1:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10"], cursor="probe-cursor"),
                request=request,
            )
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10"], cursor="c1"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=make_user_tweets_response(["11"], cursor="c2"),
                request=request,
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_user_tweets_response(["12"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    first = await sync_collection(
        "tweets",
        full=False,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("tweet")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    seen.clear()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        seen.append((operation, cursor if isinstance(cursor, str) else None, count))
        if count == 1:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["new"], cursor="probe-cursor"),
                request=request,
            )
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["new", "10"], cursor="ignored"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=make_user_tweets_response(["11"], cursor="c2"),
                request=request,
            )
        if cursor == "c2":
            return httpx.Response(200, json=make_user_tweets_response(["12"]), request=request)
        raise AssertionError(f"unexpected request cursor {cursor}")

    second = await sync_collection(
        "tweets",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert second.pages_fetched == 3
    assert [item for item in seen if item[2] == 20][:2] == [
        ("UserTweets", None, 20),
        ("UserTweets", "c1", 20),
    ]

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["raw_captures"] == 4
        assert counts["tweets"] == 4
        assert counts["collections"] == 4
        state = store.get_sync_state("tweet")
        assert state.backfill_incomplete is False
        assert state.last_head_tweet_id == "new"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_full_sync_leaves_resumable_backfill_state(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)

    async def first_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["1"], cursor="probe-cursor"),
                request=request,
            )
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    first = await sync_collection(
        "bookmarks",
        full=True,
        limit=1,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(first_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert first.pages_fetched == 1

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        state = store.get_sync_state("bookmark")
        assert state.backfill_incomplete is True
        assert state.backfill_cursor == "c1"
    finally:
        store.close()

    async def second_handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["3"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["3", "1"], cursor="ignored"),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    second = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(second_handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert second.pages_fetched == 2

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        assert store.counts()["tweets"] == 3
    finally:
        store.close()


@pytest.mark.asyncio
async def test_collection_scoped_duplicate_detection(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Likes",
        collection_type="like",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="preexisting",
                author_id="1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            )
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected request {operation} {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.pages_fetched == 2


@pytest.mark.asyncio
async def test_duplicate_detection_stops_sync(paths, config: AppConfig, auth_bundle) -> None:
    """After a full sync, a normal re-sync should stop on the first page (all dupes)."""
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out="c1",
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="already stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            ),
            TimelineTweet(
                tweet_id="2",
                text="also stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="9",
                raw_json={"tweet": "2"},
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    pages_requested: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        pages_requested.append(cursor if isinstance(cursor, str) else None)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1", "2"], cursor="c1"), request=request
            )
        raise AssertionError(f"should have stopped before cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.stop_reason == "duplicate"
    assert result.pages_fetched == 1
    assert pages_requested == [None]


@pytest.mark.asyncio
async def test_duplicate_detection_stops_user_tweets_sync(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="UserTweets",
        collection_type="tweet",
        cursor_in=None,
        cursor_out="c1",
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="10",
                text="already stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "10"},
            ),
            TimelineTweet(
                tweet_id="11",
                text="also stored",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="9",
                raw_json={"tweet": "11"},
            ),
        ],
        last_head_tweet_id="10",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    pages_requested: list[str | None] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_user_tweets_response(["10"]), request=request)
        pages_requested.append(cursor if isinstance(cursor, str) else None)
        if cursor is None:
            return httpx.Response(
                200,
                json=make_user_tweets_response(["10", "11"], cursor="c1"),
                request=request,
            )
        raise AssertionError(f"should have stopped before cursor {cursor} for {operation}")

    result = await sync_collection(
        "tweets",
        full=False,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"UserTweets": "qid-user-tweets"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.stop_reason == "duplicate"
    assert result.pages_fetched == 1
    assert pages_requested == [None]


@pytest.mark.asyncio
async def test_backfill_flag_skips_duplicate_stop(paths, config: AppConfig, auth_bundle) -> None:
    """--backfill should continue past duplicates without resetting state."""
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="existing",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json={"tweet": "1"},
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if cursor is None:
            return httpx.Response(
                200, json=make_bookmarks_response(["1"], cursor="c1"), request=request
            )
        if cursor == "c1":
            return httpx.Response(200, json=make_bookmarks_response(["2"]), request=request)
        raise AssertionError(f"unexpected cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        backfill=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )
    assert result.pages_fetched == 2
    assert result.stop_reason != "duplicate"


@pytest.mark.asyncio
async def test_article_backfill_refreshes_older_duplicate_pages(
    paths, config: AppConfig, auth_bundle
) -> None:
    _save_query_ids(paths)
    store = open_archive_store(paths, create=True)
    assert store is not None
    store.persist_page(
        operation="Bookmarks",
        collection_type="bookmark",
        cursor_in=None,
        cursor_out=None,
        http_status=200,
        raw_json={"seed": True},
        tweets=[
            TimelineTweet(
                tweet_id="1",
                text="existing one",
                author_id="a1",
                author_username="user1",
                author_display_name="User 1",
                created_at="date",
                sort_index="10",
                raw_json=make_tweet_result("1", "existing one", user_id="101"),
            ),
            TimelineTweet(
                tweet_id="2",
                text="existing two",
                author_id="a2",
                author_username="user2",
                author_display_name="User 2",
                created_at="date",
                sort_index="9",
                raw_json=make_tweet_result("2", "existing two", user_id="102"),
            ),
        ],
        last_head_tweet_id="1",
        backfill_cursor=None,
        backfill_incomplete=False,
    )
    store.close()

    article_tweet = make_tweet_result(
        "2",
        "existing two",
        user_id="102",
        article=make_article_result(
            "article-2",
            title="Older article",
            preview_text="Older article preview",
            plain_text="Older article body",
            url="https://x.com/i/article/2",
        ),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        cursor = variables.get("cursor")
        count = int(variables["count"])
        if count == 1:
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([make_tweet_result("1", "existing one")]),
                request=request,
            )
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results(
                    [make_tweet_result("1", "existing one")],
                    cursor="c1",
                ),
                request=request,
            )
        if cursor == "c1":
            return httpx.Response(
                200,
                json=_bookmarks_response_from_results([article_tweet]),
                request=request,
            )
        raise AssertionError(f"unexpected cursor {cursor}")

    result = await sync_collection(
        "bookmarks",
        full=False,
        article_backfill=True,
        limit=None,
        config=config,
        paths=paths,
        auth_bundle=auth_bundle,
        query_ids={"Bookmarks": "qid-bookmarks"},
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert result.pages_fetched == 2
    assert result.stop_reason != "duplicate"

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        article_rows = store.table.search().where("record_type = 'article'").to_list()
        assert len(article_rows) == 1
        assert article_rows[0]["tweet_id"] == "2"
        assert article_rows[0]["title"] == "Older article"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_all_aborts_on_failed_preflight_without_writes(paths, config: AppConfig) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        if int(variables["count"]) != 1:
            raise AssertionError("sync all should not reach persisted sync after failed preflight")
        if operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(401, request=request)

    with pytest.raises(RemotePreflightError):
        await sync_all(
            full=False,
            limit=None,
            config=config,
            paths=paths,
            transport=httpx.MockTransport(handler),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )

    assert not paths.database_path.exists()


@pytest.mark.asyncio
async def test_sync_all_reports_partial_runtime_failure(paths, config: AppConfig) -> None:
    _save_query_ids(paths)
    seen_operations: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        seen_operations.append(f"{operation}:{count}:{cursor}")
        if count == 1 and operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if count == 1 and operation == "Likes":
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks":
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(500, request=request)

    outcome = await sync_all(
        full=False,
        limit=None,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 2
    assert [result.collection for result in outcome.results] == ["bookmarks"]
    assert "likes" in outcome.errors

    store = open_archive_store(paths, create=False)
    assert store is not None
    try:
        counts = store.counts()
        assert counts["collections"] == 1
        assert counts["tweets"] == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_sync_all_limit_applies_per_collection(paths, config: AppConfig) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        if count == 1:
            if operation == "Bookmarks":
                return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(
                200,
                json=make_bookmarks_response(["1"], cursor="bookmark-next"),
                request=request,
            )
        if operation == "Likes" and cursor is None:
            return httpx.Response(
                200,
                json=make_likes_response(["10"], cursor="likes-next"),
                request=request,
            )
        raise AssertionError(f"unexpected request {operation} {cursor}")

    outcome = await sync_all(
        full=False,
        limit=1,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 0
    assert [(result.collection, result.pages_fetched) for result in outcome.results] == [
        ("bookmarks", 1),
        ("likes", 1),
    ]


@pytest.mark.asyncio
async def test_sync_all_shared_preflight_probes_once_per_collection(
    paths, config: AppConfig
) -> None:
    _save_query_ids(paths)
    probe_counts = {"Bookmarks": 0, "Likes": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        operation, variables = _op_and_variables(request)
        count = int(variables["count"])
        cursor = variables.get("cursor")
        if count == 1:
            probe_counts[operation] += 1
            if operation == "Bookmarks":
                return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        if operation == "Bookmarks" and cursor is None:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        if operation == "Likes" and cursor is None:
            return httpx.Response(200, json=make_likes_response(["10"]), request=request)
        raise AssertionError(f"unexpected request {operation} {cursor}")

    outcome = await sync_all(
        full=False,
        limit=1,
        config=config,
        paths=paths,
        transport=httpx.MockTransport(handler),
        console=_console(),
        sleep=lambda _: asyncio.sleep(0),
    )

    assert outcome.exit_code == 0
    assert probe_counts == {"Bookmarks": 1, "Likes": 1}


def test_process_lock_prevents_second_holder(paths) -> None:
    first = ProcessLock(paths.lock_file)
    second = ProcessLock(paths.lock_file)
    first.acquire()
    try:
        with pytest.raises(ProcessLockError):
            second.acquire()
    finally:
        first.release()


@pytest.mark.asyncio
async def test_sync_failure_releases_lock(paths, config: AppConfig, auth_bundle) -> None:
    _save_query_ids(paths)

    async def handler(request: httpx.Request) -> httpx.Response:
        _operation, variables = _op_and_variables(request)
        if int(variables["count"]) == 1:
            return httpx.Response(200, json=make_bookmarks_response(["1"]), request=request)
        return httpx.Response(500, request=request)

    with pytest.raises(TweetXVaultError):
        await sync_collection(
            "bookmarks",
            full=False,
            limit=None,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            query_ids={"Bookmarks": "qid-bookmarks"},
            transport=httpx.MockTransport(handler),
            console=_console(),
            sleep=lambda _: asyncio.sleep(0),
        )

    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    lock.release()


def test_first_run_creates_dirs_and_missing_auth_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "config-root"),
        "XDG_DATA_HOME": str(tmp_path / "data-root"),
        "XDG_CACHE_HOME": str(tmp_path / "cache-root"),
        "TWEETXVAULT_FIREFOX_PROFILES_INI": str(tmp_path / "missing-profiles.ini"),
    }
    monkeypatch.delenv("TWEETXVAULT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWEETXVAULT_CT0", raising=False)
    monkeypatch.delenv("TWEETXVAULT_USER_ID", raising=False)

    from tweetxvault.auth import resolve_auth_bundle
    from tweetxvault.config import load_config

    config, paths = load_config(env)
    assert paths.config_dir.exists()
    assert paths.data_dir.exists()
    assert paths.cache_dir.exists()

    with pytest.raises(Exception) as exc_info:
        resolve_auth_bundle(config, env=env)
    assert "TWEETXVAULT_AUTH_TOKEN" in str(exc_info.value) or "Firefox" in str(exc_info.value)


def test_security_audit_no_logger_calls_with_cookie_values() -> None:
    repo = Path(__file__).resolve().parents[1] / "tweetxvault"
    for path in repo.rglob("*.py"):
        for line in path.read_text().splitlines():
            if "logger." in line:
                assert "auth_token" not in line
                assert "ct0" not in line
