"""Base HTTP client helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from tweetxvault.auth import ResolvedAuthBundle
from tweetxvault.config import DEFAULT_USER_AGENT, PUBLIC_BEARER_TOKEN, SyncConfig
from tweetxvault.exceptions import (
    APIResponseError,
    AuthExpiredError,
    FeatureFlagDriftError,
    RateLimitExhaustedError,
    StaleQueryIdError,
)


def build_async_client(
    auth: ResolvedAuthBundle,
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    cookies = httpx.Cookies()
    cookies.set("auth_token", auth.auth_token, domain=".x.com", path="/")
    cookies.set("ct0", auth.ct0, domain=".x.com", path="/")
    headers = {
        "authorization": f"Bearer {PUBLIC_BEARER_TOKEN}",
        "origin": "https://x.com",
        "referer": "https://x.com/",
        "user-agent": DEFAULT_USER_AGENT,
        "x-csrf-token": auth.ct0,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
    }
    return httpx.AsyncClient(
        cookies=cookies,
        follow_redirects=True,
        headers=headers,
        timeout=timeout,
        transport=transport,
    )


def is_rate_limit(response: httpx.Response) -> bool:
    return response.status_code == 429


def is_auth_error(response: httpx.Response) -> bool:
    return response.status_code in {401, 403}


def is_stale_query_id(response: httpx.Response) -> bool:
    return response.status_code == 404


def is_feature_flag_error(response: httpx.Response) -> bool:
    return response.status_code == 400


def _classify_response(response: httpx.Response) -> APIResponseError:
    if is_auth_error(response):
        return AuthExpiredError(
            "X rejected the current session cookies.", status_code=response.status_code
        )
    if is_feature_flag_error(response):
        return FeatureFlagDriftError(
            "X returned 400 for the timeline request. The committed feature flags likely drifted.",
            status_code=response.status_code,
        )
    if is_stale_query_id(response):
        return StaleQueryIdError(
            "X returned 404 for the GraphQL operation. The query ID is stale.", status_code=404
        )
    return APIResponseError(
        f"Unexpected X API response: HTTP {response.status_code}.", status_code=response.status_code
    )


async def request_with_backoff(
    client: httpx.AsyncClient,
    url: str,
    sync_config: SyncConfig,
    *,
    refresh_once: Callable[[], Awaitable[str]] | None = None,
    status: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    retries = 0
    consecutive_429 = 0
    used_cooldown = False
    used_refresh = False

    while True:
        response = await client.get(url)
        if response.is_success:
            return response

        if is_rate_limit(response):
            consecutive_429 += 1
            if retries < sync_config.max_retries:
                delay = sync_config.backoff_base * (2**retries)
                if status:
                    status(
                        "rate limited (HTTP 429), "
                        f"retry {retries + 1}/{sync_config.max_retries} in {delay:.1f}s"
                    )
                retries += 1
                await sleep(delay)
                continue
            if consecutive_429 >= sync_config.cooldown_threshold and not used_cooldown:
                used_cooldown = True
                retries = 0
                if status:
                    status(
                        "rate limited repeatedly, "
                        f"cooling down for {sync_config.cooldown_duration:.1f}s"
                    )
                await sleep(sync_config.cooldown_duration)
                continue
            raise RateLimitExhaustedError(
                "Rate limit persisted after retries and cooldown.", status_code=response.status_code
            )

        if is_stale_query_id(response) and refresh_once and not used_refresh:
            used_refresh = True
            if status:
                status("query ID stale (HTTP 404), refreshing once")
            url = await refresh_once()
            continue

        raise _classify_response(response)
