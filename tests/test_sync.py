from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from rich.console import Console

from tests.conftest import make_bookmarks_response, make_likes_response
from tweetxvault.config import AppConfig
from tweetxvault.exceptions import ProcessLockError, TweetXVaultError
from tweetxvault.query_ids import QueryIdStore
from tweetxvault.storage import open_archive_store
from tweetxvault.sync import ProcessLock, RemotePreflightError, sync_all, sync_collection


def _op_and_variables(request: httpx.Request) -> tuple[str, dict[str, object]]:
    parsed = urlparse(str(request.url))
    operation = parsed.path.split("/")[-1]
    variables = json.loads(parse_qs(parsed.query)["variables"][0])
    return operation, variables


def _save_query_ids(paths) -> None:
    QueryIdStore(paths).save({"Bookmarks": "qid-bookmarks", "Likes": "qid-likes"})


def _console() -> Console:
    return Console(file=StringIO(), force_terminal=False, color_system=None)


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
    store.connection.execute(
        """
        INSERT INTO tweets (
            tweet_id, text, author_id, author_username, author_display_name,
            created_at, raw_json, first_seen_at, last_seen_at
        ) VALUES ('1', 'preexisting', '1', 'user1', 'User 1', 'date', '{}', 'now', 'now')
        """
    )
    store.connection.commit()
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

    assert not paths.database_file.exists()


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
