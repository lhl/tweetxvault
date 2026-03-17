"""Sync orchestration."""

from __future__ import annotations

import asyncio
import fcntl
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from tweetxvault.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetxvault.client.base import build_async_client
from tweetxvault.client.timelines import (
    TimelineTweet,
    build_bookmarks_url,
    build_likes_url,
    build_user_tweets_url,
    fetch_page,
    parse_timeline_response,
)
from tweetxvault.config import AppConfig, XDGPaths, ensure_paths, load_config
from tweetxvault.exceptions import (
    APIResponseError,
    ArchiveOwnerMismatchError,
    AuthResolutionError,
    ConfigError,
    ProcessLockError,
    TweetXVaultError,
)
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import ArchiveStore, SyncState, open_archive_store
from tweetxvault.utils import resolve_query_ids

COLLECTION_TO_OPERATION = {
    "bookmarks": "Bookmarks",
    "likes": "Likes",
    "tweets": "UserTweets",
}
COLLECTION_TO_STORAGE = {
    "bookmarks": "bookmark",
    "likes": "like",
    "tweets": "tweet",
}


class LocalPreflightError(ConfigError):
    """Raised for local preflight failures."""


class RemotePreflightError(TweetXVaultError):
    """Raised for remote/API preflight failures."""


@dataclass(slots=True)
class ProbeResult:
    collection: str
    ready: bool
    detail: str
    local_error: bool = False


@dataclass(slots=True)
class PreflightResult:
    auth: ResolvedAuthBundle
    query_ids: dict[str, str]
    probes: dict[str, ProbeResult]

    @property
    def has_local_error(self) -> bool:
        return any(probe.local_error for probe in self.probes.values())

    @property
    def has_remote_error(self) -> bool:
        return any(not probe.ready and not probe.local_error for probe in self.probes.values())

    def is_ready_for(self, collections: Sequence[str]) -> bool:
        return all(self.probes[collection].ready for collection in collections)


@dataclass(slots=True)
class SyncResult:
    collection: str
    pages_fetched: int
    tweets_seen: int
    stop_reason: str


@dataclass(slots=True)
class SyncAllResult:
    exit_code: int
    results: list[SyncResult]
    errors: dict[str, str]


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ProcessLockError("Another tweetxvault archive job is already running.") from exc
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def _build_url(
    collection: str, query_id: str, auth: ResolvedAuthBundle, cursor: str | None, count: int
) -> str:
    if collection == "bookmarks":
        return build_bookmarks_url(query_id, cursor=cursor, count=count)
    if collection == "likes":
        return build_likes_url(query_id, auth.user_id or "", cursor=cursor, count=count)
    return build_user_tweets_url(query_id, auth.user_id or "", cursor=cursor, count=count)


async def run_preflight(
    *,
    config: AppConfig,
    paths: XDGPaths,
    collections: Sequence[str],
    auth_bundle: ResolvedAuthBundle | None = None,
    query_ids: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PreflightResult:
    auth_bundle = auth_bundle or resolve_auth_bundle(config)
    existing_store = open_archive_store(paths, create=False)
    if existing_store is not None:
        try:
            existing_owner = existing_store.get_archive_owner_id()
            if existing_owner and auth_bundle.user_id and existing_owner != auth_bundle.user_id:
                raise LocalPreflightError(
                    f"Local archive belongs to X user {existing_owner}, but current auth resolved "
                    f"{auth_bundle.user_id}."
                )
        finally:
            existing_store.close()
    operation_names = [COLLECTION_TO_OPERATION[collection] for collection in collections]
    query_store = QueryIdStore(paths)
    query_ids = query_ids or await resolve_query_ids(
        query_store,
        operation_names,
        force_refresh=not query_store.is_fresh(),
        transport=transport,
    )
    probes: dict[str, ProbeResult] = {}
    client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
    try:
        for collection in collections:
            operation = COLLECTION_TO_OPERATION[collection]
            try:
                auth_bundle.validate_for_collection(collection)
            except AuthResolutionError as exc:
                probes[collection] = ProbeResult(
                    collection=collection,
                    ready=False,
                    detail=str(exc),
                    local_error=True,
                )
                continue

            async def refresh_once(
                operation_name: str = operation,
                collection_name: str = collection,
            ) -> str:
                refreshed = await refresh_query_ids(
                    query_store,
                    operations=[operation_name],
                    client=client,
                )
                query_ids.update(refreshed)
                return _build_url(
                    collection_name,
                    query_ids[operation_name],
                    auth_bundle,
                    None,
                    1,
                )

            try:
                response = await fetch_page(
                    client,
                    _build_url(collection, query_ids[operation], auth_bundle, None, 1),
                    config.sync,
                    refresh_once=refresh_once,
                )
            except APIResponseError as exc:
                probes[collection] = ProbeResult(
                    collection=collection, ready=False, detail=str(exc)
                )
                continue

            if response.status_code != 200:
                probes[collection] = ProbeResult(
                    collection=collection,
                    ready=False,
                    detail=f"Probe returned HTTP {response.status_code}.",
                )
                continue
            probes[collection] = ProbeResult(
                collection=collection, ready=True, detail="Remote probe succeeded."
            )
    finally:
        await client.aclose()
    return PreflightResult(auth=auth_bundle, query_ids=query_ids, probes=probes)


async def _fetch_and_parse_page(
    *,
    collection: str,
    cursor: str | None,
    count: int,
    config: AppConfig,
    auth: ResolvedAuthBundle,
    query_store: QueryIdStore,
    query_ids: dict[str, str],
    client: httpx.AsyncClient,
) -> tuple[httpx.Response, dict[str, Any], list[TimelineTweet], str | None]:
    operation = COLLECTION_TO_OPERATION[collection]

    async def refresh_once() -> str:
        refreshed = await refresh_query_ids(query_store, operations=[operation], client=client)
        query_ids.update(refreshed)
        return _build_url(collection, query_ids[operation], auth, cursor, count)

    url = _build_url(collection, query_ids[operation], auth, cursor, count)
    response = await fetch_page(client, url, config.sync, refresh_once=refresh_once)
    payload = response.json()
    tweets, next_cursor = parse_timeline_response(payload, operation)
    return response, payload, tweets, next_cursor


def _store_state_for_page(
    *,
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    next_cursor: str | None,
    stop_reason: str,
    is_head_pass: bool,
) -> tuple[str | None, bool]:
    if not is_head_pass:
        return next_cursor, bool(next_cursor)
    if prior_backfill_incomplete:
        return prior_backfill_cursor, True
    if stop_reason in {"duplicate", "head-complete"}:
        return None, False
    return next_cursor, bool(next_cursor)


def _pass_label(*, is_head_pass: bool) -> str:
    return "head" if is_head_pass else "backfill"


async def _run_pass(
    *,
    collection: str,
    start_cursor: str | None,
    config: AppConfig,
    auth: ResolvedAuthBundle,
    query_store: QueryIdStore,
    query_ids: dict[str, str],
    store: ArchiveStore,
    count_limit: int | None,
    stop_on_duplicate: bool,
    previous_state: SyncState,
    prior_backfill_cursor: str | None,
    prior_backfill_incomplete: bool,
    initial_seen_ids: set[str],
    existing_tweet_ids: set[str],
    is_head_pass: bool,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
    client: httpx.AsyncClient,
) -> tuple[int, int, str, str | None, str | None]:
    pages_fetched = 0
    tweets_seen = 0
    cursor = start_cursor
    latest_head_id = previous_state.last_head_tweet_id
    stop_reason = "empty"
    seen_ids = initial_seen_ids

    while True:
        if count_limit is not None and pages_fetched >= count_limit:
            stop_reason = "limit"
            break

        response, payload, tweets, next_cursor = await _fetch_and_parse_page(
            collection=collection,
            cursor=cursor,
            count=20,
            config=config,
            auth=auth,
            query_store=query_store,
            query_ids=query_ids,
            client=client,
        )
        duplicate_seen = False
        if is_head_pass and stop_on_duplicate:
            duplicate_seen = any(
                tweet.tweet_id not in seen_ids and tweet.tweet_id in existing_tweet_ids
                for tweet in tweets
            )

        if is_head_pass and tweets and pages_fetched == 0:
            latest_head_id = tweets[0].tweet_id

        if not tweets:
            stop_reason = "empty"
        elif duplicate_seen:
            stop_reason = "duplicate"
        elif next_cursor is None:
            stop_reason = "head-complete" if is_head_pass else "backfill-complete"
        elif count_limit is not None and pages_fetched + 1 >= count_limit:
            stop_reason = "limit"
        else:
            stop_reason = "continue"

        backfill_cursor, backfill_incomplete = _store_state_for_page(
            prior_backfill_cursor=prior_backfill_cursor,
            prior_backfill_incomplete=prior_backfill_incomplete,
            next_cursor=next_cursor,
            stop_reason=stop_reason,
            is_head_pass=is_head_pass,
        )
        store.persist_page(
            operation=COLLECTION_TO_OPERATION[collection],
            collection_type=COLLECTION_TO_STORAGE[collection],
            cursor_in=cursor,
            cursor_out=next_cursor,
            http_status=response.status_code,
            raw_json=payload,
            tweets=tweets,
            last_head_tweet_id=latest_head_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
        )
        for tweet in tweets:
            seen_ids.add(tweet.tweet_id)

        pages_fetched += 1
        tweets_seen += len(tweets)
        console.print(
            f"{collection} {_pass_label(is_head_pass=is_head_pass)}: "
            f"page {pages_fetched}, "
            f"page_tweets {len(tweets)}, "
            f"total_tweets {tweets_seen}, "
            f"stop={stop_reason}",
            highlight=False,
        )

        if stop_reason != "continue":
            return pages_fetched, tweets_seen, stop_reason, latest_head_id, next_cursor

        cursor = next_cursor
        await sleep(config.sync.page_delay)

    return pages_fetched, tweets_seen, stop_reason, latest_head_id, cursor


async def sync_collection(
    collection: str,
    *,
    full: bool,
    backfill: bool = False,
    article_backfill: bool = False,
    resume_backfill: bool = True,
    limit: int | None = None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    query_ids: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SyncResult:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    paths = ensure_paths(paths)
    console = console or Console(stderr=True)

    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=[collection],
        auth_bundle=auth_bundle,
        query_ids=query_ids,
        transport=transport,
    )
    return await _sync_collection_ready(
        collection=collection,
        full=full,
        backfill=backfill,
        article_backfill=article_backfill,
        resume_backfill=resume_backfill,
        limit=limit,
        config=config,
        paths=paths,
        preflight=preflight,
        transport=transport,
        console=console,
        sleep=sleep,
    )


def _embed_new_tweets(store: Any, console: Console | None) -> None:
    """Embed any unembedded tweets if embedding deps are available."""
    from tweetxvault.embed import is_available

    if not is_available():
        return
    remaining = store.count_unembedded()
    if remaining == 0:
        return

    from tweetxvault.embed import get_engine

    if console:
        console.print(f"embedding {remaining} new tweets...")
    engine = get_engine()
    for batch in store.get_unembedded_tweets(batch_size=100):
        texts = [f"@{row['author_username'] or ''}: {row['text'] or ''}" for row in batch]
        vectors = engine.embed_batch(texts)
        store.write_embeddings(batch, vectors)
    if console:
        console.print(f"embedded {remaining} tweets")


def _log_embedding_warning(console: Console | None, message: str) -> None:
    if console is not None:
        console.print(f"[yellow]{message}[/yellow]")


async def _sync_collection_ready(
    *,
    collection: str,
    full: bool,
    backfill: bool = False,
    article_backfill: bool = False,
    resume_backfill: bool = True,
    limit: int | None,
    config: AppConfig,
    paths: XDGPaths,
    preflight: PreflightResult,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
    sleep: Callable[[float], Awaitable[None]],
) -> SyncResult:
    probe = preflight.probes[collection]
    if not probe.ready:
        if probe.local_error:
            raise LocalPreflightError(probe.detail)
        raise RemotePreflightError(probe.detail)

    lock = ProcessLock(paths.lock_file)
    lock.acquire()
    try:
        store = open_archive_store(paths, create=True)
        assert store is not None
        try:
            store.ensure_archive_owner_id(preflight.auth.user_id)
        except ArchiveOwnerMismatchError:
            store.close()
            raise

        if full:
            store.reset_sync_state(COLLECTION_TO_STORAGE[collection])

        previous_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
        prior_backfill_cursor = (
            previous_state.backfill_cursor if previous_state.backfill_incomplete else None
        )
        prior_backfill_incomplete = previous_state.backfill_incomplete
        seen_ids: set[str] = set()
        effective_backfill = backfill or article_backfill
        stop_on_dup = not full and not effective_backfill
        existing_tweet_ids: set[str] = set()
        if stop_on_dup:
            existing_tweet_ids = store.get_collection_tweet_ids(COLLECTION_TO_STORAGE[collection])
        pages_total = 0
        client = build_async_client(
            preflight.auth, timeout=config.sync.timeout, transport=transport
        )
        try:
            console.print(f"{collection}: starting head pass", highlight=False)
            head_pages, head_tweets, head_reason, _latest_head_id, _ = await _run_pass(
                collection=collection,
                start_cursor=None,
                config=config,
                auth=preflight.auth,
                query_store=QueryIdStore(paths),
                query_ids=dict(preflight.query_ids),
                store=store,
                count_limit=limit,
                stop_on_duplicate=stop_on_dup,
                previous_state=previous_state,
                prior_backfill_cursor=prior_backfill_cursor,
                prior_backfill_incomplete=prior_backfill_incomplete,
                initial_seen_ids=seen_ids,
                existing_tweet_ids=existing_tweet_ids,
                is_head_pass=True,
                console=console,
                sleep=sleep,
                client=client,
            )
            pages_total = head_pages
            tweets_total = head_tweets
            stop_reason = head_reason
            remaining = None if limit is None else max(limit - head_pages, 0)

            if resume_backfill and prior_backfill_incomplete and remaining != 0:
                console.print(f"{collection}: resuming saved backfill pass", highlight=False)
                refreshed_state = store.get_sync_state(COLLECTION_TO_STORAGE[collection])
                backfill_pages, backfill_tweets, backfill_reason, _, _ = await _run_pass(
                    collection=collection,
                    start_cursor=prior_backfill_cursor,
                    config=config,
                    auth=preflight.auth,
                    query_store=QueryIdStore(paths),
                    query_ids=dict(preflight.query_ids),
                    store=store,
                    count_limit=remaining,
                    stop_on_duplicate=False,
                    previous_state=refreshed_state,
                    prior_backfill_cursor=prior_backfill_cursor,
                    prior_backfill_incomplete=prior_backfill_incomplete,
                    initial_seen_ids=seen_ids,
                    existing_tweet_ids=set(),
                    is_head_pass=False,
                    console=console,
                    sleep=sleep,
                    client=client,
                )
                pages_total += backfill_pages
                tweets_total += backfill_tweets
                stop_reason = backfill_reason
        finally:
            await client.aclose()
            if pages_total > 0:
                try:
                    _embed_new_tweets(store, console)
                except Exception as exc:
                    _log_embedding_warning(
                        console,
                        "sync completed, but auto-embedding was skipped; "
                        f"run 'tweetxvault embed' later ({exc})",
                    )
                store.optimize()
            store.close()
    finally:
        lock.release()

    return SyncResult(
        collection=collection,
        pages_fetched=pages_total,
        tweets_seen=tweets_total,
        stop_reason=stop_reason,
    )


async def sync_all(
    *,
    full: bool,
    backfill: bool = False,
    article_backfill: bool = False,
    limit: int | None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> SyncAllResult:
    if config is None or paths is None:
        loaded_config, loaded_paths = load_config()
        config = config or loaded_config
        paths = paths or loaded_paths
    paths = ensure_paths(paths)
    console = console or Console(stderr=True)
    preflight = await run_preflight(
        config=config,
        paths=paths,
        collections=["bookmarks", "likes"],
        auth_bundle=auth_bundle,
        transport=transport,
    )
    if not preflight.is_ready_for(["bookmarks", "likes"]):
        if preflight.has_local_error:
            raise LocalPreflightError("sync all preflight failed on local auth/config.")
        raise RemotePreflightError("sync all preflight failed on a remote probe.")

    results: list[SyncResult] = []
    errors: dict[str, str] = {}
    exit_code = 0
    for collection in ("bookmarks", "likes"):
        try:
            result = await _sync_collection_ready(
                collection=collection,
                full=full,
                backfill=backfill,
                article_backfill=article_backfill,
                limit=limit,
                config=config,
                paths=paths,
                preflight=preflight,
                transport=transport,
                console=console,
                sleep=sleep,
            )
            results.append(result)
        except TweetXVaultError as exc:
            exit_code = 2
            errors[collection] = str(exc)
            console.print(f"{collection}: failed ({exc})")
            break
    return SyncAllResult(exit_code=exit_code, results=results, errors=errors)
