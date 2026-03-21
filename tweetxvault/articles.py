"""Article refresh helpers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from rich.console import Console

from tweetxvault.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetxvault.client.base import AdaptiveRequestPacer, build_async_client
from tweetxvault.client.timelines import (
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
)
from tweetxvault.config import AppConfig, XDGPaths
from tweetxvault.exceptions import ConfigError
from tweetxvault.jobs import locked_archive_job, resolve_job_context
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.utils import resolve_query_ids

_STATUS_URL_RE = re.compile(r"/status/(\d+)")


@dataclass(slots=True)
class ArticleRefreshResult:
    processed: int = 0
    updated: int = 0
    failed: int = 0


def normalize_article_target(value: str) -> str:
    candidate = value.strip()
    if candidate.isdigit():
        return candidate
    match = _STATUS_URL_RE.search(candidate)
    if match:
        return match.group(1)
    raise ConfigError(f"Unsupported article target '{value}'. Use a tweet ID or x.com status URL.")


async def refresh_articles(
    *,
    targets: list[str] | None = None,
    preview_only: bool = True,
    limit: int | None = None,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ArticleRefreshResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    auth_bundle = auth_bundle or resolve_auth_bundle(config)
    console = console or Console(stderr=True)

    async with locked_archive_job(config=config, paths=paths) as job:
        store = job.store
        if targets:
            tweet_ids = [normalize_article_target(target) for target in targets]
        else:
            tweet_ids = store.get_article_tweet_ids(preview_only=preview_only, limit=limit)
        result = ArticleRefreshResult()
        if not tweet_ids:
            return result

        query_store = QueryIdStore(paths)
        query_ids = await resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        try:
            attempted = 0
            pacer = AdaptiveRequestPacer(config.sync.detail_delay)
            for tweet_id in tweet_ids:
                result.processed += 1
                await pacer.wait(attempted=attempted, sleep=sleep)
                attempted += 1

                async def refresh_once(tweet_id: str = tweet_id) -> str:
                    refreshed = await refresh_query_ids(
                        query_store,
                        operations=["TweetDetail"],
                        client=client,
                    )
                    query_ids.update(refreshed)
                    return build_tweet_detail_url(query_ids["TweetDetail"], tweet_id)

                try:
                    response = await fetch_page(
                        client,
                        build_tweet_detail_url(query_ids["TweetDetail"], tweet_id),
                        config.sync,
                        max_retries=config.sync.detail_max_retries,
                        backoff_base=config.sync.detail_backoff_base,
                        refresh_once=refresh_once,
                    )
                    pacer.observe(response)
                    payload = response.json()
                    tweet = parse_tweet_detail_response(payload, tweet_id)
                    if tweet is None:
                        raise ValueError(f"TweetDetail did not include focal tweet {tweet_id}.")
                    store.persist_tweet_detail(
                        tweet=tweet,
                        raw_json=payload,
                        http_status=response.status_code,
                    )
                    result.updated += 1
                except Exception as exc:
                    result.failed += 1
                    if console:
                        console.print(f"article {tweet_id}: failed ({exc})", highlight=False)
        finally:
            await client.aclose()

        if result.updated > 0:
            job.mark_dirty()
        return result
