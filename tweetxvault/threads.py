"""Thread/context expansion helpers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from rich.console import Console

from tweetxvault.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetxvault.client.base import build_async_client
from tweetxvault.client.timelines import (
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
    parse_tweet_detail_tweets,
)
from tweetxvault.config import AppConfig, XDGPaths
from tweetxvault.exceptions import ConfigError
from tweetxvault.extractor import extract_status_id_from_url
from tweetxvault.jobs import locked_archive_job, resolve_job_context
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import ArchiveStore
from tweetxvault.sync import _resolve_query_ids

_SCAN_PROGRESS_EVERY = 100


@dataclass(slots=True)
class ThreadExpandResult:
    processed: int = 0
    expanded: int = 0
    skipped: int = 0
    failed: int = 0


def normalize_thread_target(value: str) -> str:
    candidate = value.strip()
    if candidate.isdigit():
        return candidate
    tweet_id = extract_status_id_from_url(candidate)
    if tweet_id:
        return tweet_id
    raise ConfigError(f"Unsupported thread target '{value}'. Use a tweet ID or x.com status URL.")


def _dedupe_targets(values: list[str]) -> tuple[list[str], int]:
    unique = list(dict.fromkeys(values))
    return unique, len(values) - len(unique)


def _log_thread_status(console: Console, tweet_id: str, message: str) -> None:
    console.print(f"thread {tweet_id}: {message}", highlight=False)


def _log_threads(console: Console, message: str) -> None:
    console.print(f"threads: {message}", highlight=False)


def _log_scan_progress(
    console: Console,
    *,
    phase: str,
    scanned: int,
    total: int,
    result: ThreadExpandResult,
) -> None:
    if scanned % _SCAN_PROGRESS_EVERY != 0 and scanned != total:
        return
    _log_threads(
        console,
        f"{phase} {scanned}/{total} scanned, "
        f"{result.processed} processed, "
        f"{result.expanded} expanded, "
        f"{result.skipped} skipped, "
        f"{result.failed} failed",
    )


async def _fetch_detail(
    *,
    tweet_id: str,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    console: Console,
) -> tuple[dict[str, object], list]:
    async def refresh_once() -> str:
        refreshed = await refresh_query_ids(
            query_store,
            operations=["TweetDetail"],
            client=client,
        )
        query_ids.update(refreshed)
        return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

    response = await fetch_page(
        client,
        build_tweet_detail_url(query_ids["TweetDetail"], tweet_id),
        config.sync,
        refresh_once=refresh_once,
        status=lambda message: _log_thread_status(console, tweet_id, message),
    )
    payload = response.json()
    tweets = parse_tweet_detail_tweets(payload)
    focal = parse_tweet_detail_response(payload, tweet_id)
    if focal is None:
        raise ValueError(f"TweetDetail did not include focal tweet {tweet_id}.")
    return payload, tweets


async def _expand_target(
    *,
    tweet_id: str,
    store: ArchiveStore,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    console: Console,
) -> list[str]:
    payload, tweets = await _fetch_detail(
        tweet_id=tweet_id,
        query_ids=query_ids,
        query_store=query_store,
        client=client,
        config=config,
        console=console,
    )
    store.persist_thread_detail(
        focal_tweet_id=tweet_id,
        tweets=tweets,
        raw_json=payload,
    )
    return [tweet.tweet_id for tweet in tweets]


async def _try_expand_target(
    *,
    tweet_id: str,
    store: ArchiveStore,
    query_ids: dict[str, str],
    query_store: QueryIdStore,
    client: httpx.AsyncClient,
    config: AppConfig,
    expanded_targets: set[str],
    known_tweet_ids: set[str],
    result: ThreadExpandResult,
    console: Console,
) -> None:
    result.processed += 1
    try:
        discovered_ids = await _expand_target(
            tweet_id=tweet_id,
            store=store,
            query_ids=query_ids,
            query_store=query_store,
            client=client,
            config=config,
            console=console,
        )
    except Exception as exc:
        result.failed += 1
        _log_thread_status(console, tweet_id, f"failed ({exc})")
        return

    expanded_targets.add(tweet_id)
    known_tweet_ids.update(discovered_ids)
    result.expanded += 1


async def expand_threads(
    *,
    targets: list[str] | None = None,
    limit: int | None = None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ThreadExpandResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    _log_threads(console, "preparing archive expansion job")
    auth_bundle = auth_bundle or resolve_auth_bundle(config)

    async with locked_archive_job(config=config, paths=paths) as job:
        store = job.store
        _log_threads(console, "resolving TweetDetail query ID")
        query_store = QueryIdStore(paths)
        query_ids = await _resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        result = ThreadExpandResult()
        client = build_async_client(
            auth_bundle,
            timeout=config.sync.timeout,
            transport=transport,
        )
        try:
            _log_threads(console, "loading archived thread expansion state...")
            expanded_targets = set(store.list_raw_capture_target_ids("ThreadExpandDetail"))
            _log_threads(
                console,
                f"loaded {len(expanded_targets)} previously expanded thread targets",
            )
            known_tweet_ids: set[str] = set()

            if targets:
                requested, duplicate_count = _dedupe_targets(
                    [normalize_thread_target(target) for target in targets]
                )
                result.skipped += duplicate_count
                target_ids = requested[:limit] if limit is not None else requested
                _log_threads(console, f"explicit target pass over {len(target_ids)} targets")
                for scanned, tweet_id in enumerate(target_ids, start=1):
                    await _try_expand_target(
                        tweet_id=tweet_id,
                        store=store,
                        query_ids=query_ids,
                        query_store=query_store,
                        client=client,
                        config=config,
                        expanded_targets=expanded_targets,
                        known_tweet_ids=known_tweet_ids,
                        result=result,
                        console=console,
                    )
                    _log_scan_progress(
                        console,
                        phase="explicit",
                        scanned=scanned,
                        total=len(target_ids),
                        result=result,
                    )
            else:
                _log_threads(console, "loading archived membership tweets...")
                membership_ids = store.list_membership_tweet_ids()
                _log_threads(
                    console,
                    "membership pass over "
                    f"{len(membership_ids)} archived tweets "
                    f"({len(expanded_targets)} already expanded)",
                )
                for scanned, tweet_id in enumerate(membership_ids, start=1):
                    if limit is not None and result.processed >= limit:
                        break
                    if tweet_id in expanded_targets:
                        result.skipped += 1
                        _log_scan_progress(
                            console,
                            phase="membership",
                            scanned=scanned,
                            total=len(membership_ids),
                            result=result,
                        )
                        continue
                    await _try_expand_target(
                        tweet_id=tweet_id,
                        store=store,
                        query_ids=query_ids,
                        query_store=query_store,
                        client=client,
                        config=config,
                        expanded_targets=expanded_targets,
                        known_tweet_ids=known_tweet_ids,
                        result=result,
                        console=console,
                    )
                    _log_scan_progress(
                        console,
                        phase="membership",
                        scanned=scanned,
                        total=len(membership_ids),
                        result=result,
                    )

                if limit is None or result.processed < limit:
                    _log_threads(console, "loading known tweet ids for linked-status pass...")
                    known_tweet_ids = store.list_known_tweet_ids()
                    _log_threads(
                        console,
                        f"loaded {len(known_tweet_ids)} known tweet ids for linked-status dedupe",
                    )
                    _log_threads(console, "loading archived url refs...")
                    url_ref_rows = store.list_url_ref_rows()
                    _log_threads(
                        console,
                        f"linked-status pass over {len(url_ref_rows)} archived url refs",
                    )
                    for scanned, row in enumerate(url_ref_rows, start=1):
                        if limit is not None and result.processed >= limit:
                            break
                        target_id = None
                        for field_name in ("canonical_url", "expanded_url", "url"):
                            candidate = row.get(field_name)
                            if isinstance(candidate, str):
                                target_id = extract_status_id_from_url(candidate)
                                if target_id:
                                    break
                        if not target_id:
                            _log_scan_progress(
                                console,
                                phase="linked-status",
                                scanned=scanned,
                                total=len(url_ref_rows),
                                result=result,
                            )
                            continue
                        source_tweet_id = row.get("tweet_id")
                        if (
                            target_id == source_tweet_id
                            or target_id in expanded_targets
                            or target_id in known_tweet_ids
                        ):
                            result.skipped += 1
                            _log_scan_progress(
                                console,
                                phase="linked-status",
                                scanned=scanned,
                                total=len(url_ref_rows),
                                result=result,
                            )
                            continue
                        await _try_expand_target(
                            tweet_id=target_id,
                            store=store,
                            query_ids=query_ids,
                            query_store=query_store,
                            client=client,
                            config=config,
                            expanded_targets=expanded_targets,
                            known_tweet_ids=known_tweet_ids,
                            result=result,
                            console=console,
                        )
                        _log_scan_progress(
                            console,
                            phase="linked-status",
                            scanned=scanned,
                            total=len(url_ref_rows),
                            result=result,
                        )
        finally:
            await client.aclose()

        if result.expanded > 0:
            job.mark_dirty()
        return result
