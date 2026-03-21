"""Base HTTP client helpers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

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


@dataclass(slots=True, frozen=True)
class RateLimitInfo:
    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    retry_after: float | None = None


@dataclass(slots=True)
class AdaptiveRequestPacer:
    base_delay: float
    dynamic_delay: float = 0.0
    _last_logged_delay: float | None = None

    async def wait(
        self,
        *,
        attempted: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if attempted <= 0:
            return
        delay = max(self.base_delay, self.dynamic_delay)
        if delay > 0:
            await sleep(delay)

    def observe(
        self,
        response: httpx.Response,
        *,
        status: Callable[[str], None] | None = None,
    ) -> None:
        info = get_rate_limit_info(response)
        if info is None:
            return
        suggested_delay = compute_adaptive_pacing_delay(info)
        if suggested_delay is None or suggested_delay <= 0:
            return
        self.dynamic_delay = suggested_delay
        effective_delay = max(self.base_delay, suggested_delay)
        if status is None or effective_delay <= self.base_delay + 0.5:
            return
        if not self._should_log(effective_delay):
            return
        summary = format_rate_limit_info(info)
        message = f"rate-limit pacing active, using {effective_delay:.1f}s/request"
        if summary:
            message += f" ({summary})"
        status(message)
        self._last_logged_delay = effective_delay

    def _should_log(self, effective_delay: float) -> bool:
        if self._last_logged_delay is None:
            return True
        prior = max(self._last_logged_delay, 0.1)
        return (
            abs(effective_delay - self._last_logged_delay) >= 0.5
            and abs(effective_delay - self._last_logged_delay) / prior >= 0.2
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


def _parse_int_header(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed > 10_000_000_000:
        parsed //= 1000
    return parsed


def _parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        reference_now = time.time() if now is None else now
        return max(parsed.timestamp() - reference_now, 0.0)


def get_rate_limit_info(
    response: httpx.Response,
    *,
    now: float | None = None,
) -> RateLimitInfo | None:
    info = RateLimitInfo(
        limit=_parse_int_header(response.headers.get("x-rate-limit-limit")),
        remaining=_parse_int_header(response.headers.get("x-rate-limit-remaining")),
        reset_at=_parse_int_header(response.headers.get("x-rate-limit-reset")),
        retry_after=_parse_retry_after(response.headers.get("retry-after"), now=now),
    )
    if (
        info.limit is None
        and info.remaining is None
        and info.reset_at is None
        and info.retry_after is None
    ):
        return None
    return info


def compute_rate_limit_wait(
    info: RateLimitInfo,
    *,
    now: float | None = None,
    padding: float = 1.0,
) -> float | None:
    reference_now = time.time() if now is None else now
    candidates: list[float] = []
    if info.retry_after is not None:
        candidates.append(max(info.retry_after, 0.0))
    if info.reset_at is not None:
        candidates.append(max(info.reset_at - reference_now + padding, 0.0))
    if not candidates:
        return None
    delay = max(candidates)
    return delay if delay > 0 else None


def compute_adaptive_pacing_delay(
    info: RateLimitInfo,
    *,
    now: float | None = None,
    padding: float = 1.0,
) -> float | None:
    reference_now = time.time() if now is None else now
    if info.reset_at is None or info.remaining is None:
        return None
    seconds_until_reset = info.reset_at - reference_now + padding
    if seconds_until_reset <= 0:
        return None
    if info.remaining <= 0:
        return seconds_until_reset
    return seconds_until_reset / info.remaining


def format_rate_limit_info(info: RateLimitInfo, *, now: float | None = None) -> str | None:
    reference_now = time.time() if now is None else now
    parts: list[str] = []
    if info.remaining is not None and info.limit is not None:
        parts.append(f"remaining {info.remaining}/{info.limit}")
    elif info.remaining is not None:
        parts.append(f"remaining {info.remaining}")
    if info.reset_at is not None:
        reset_time = datetime.fromtimestamp(info.reset_at, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        reset_delay = max(info.reset_at - reference_now, 0.0)
        parts.append(f"reset in {reset_delay:.1f}s")
        parts.append(f"at {reset_time}")
    elif info.retry_after is not None:
        parts.append(f"retry-after {info.retry_after:.1f}s")
    return ", ".join(parts) if parts else None


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
    max_retries: int | None = None,
    backoff_base: float | None = None,
    refresh_once: Callable[[], Awaitable[str]] | None = None,
    status: Callable[[str], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    effective_max_retries = sync_config.max_retries if max_retries is None else max_retries
    effective_backoff_base = sync_config.backoff_base if backoff_base is None else backoff_base
    retries = 0
    consecutive_429 = 0
    used_cooldown = False
    used_refresh = False

    while True:
        response = await client.get(url)
        if response.is_success:
            return response

        if is_rate_limit(response):
            info = get_rate_limit_info(response)
            header_delay = compute_rate_limit_wait(info) if info is not None else None
            if header_delay is not None:
                if status:
                    message = f"rate limited (HTTP 429), waiting {header_delay:.1f}s before retry"
                    summary = format_rate_limit_info(info)
                    if summary:
                        message += f" ({summary})"
                    status(message)
                retries = 0
                consecutive_429 = 0
                used_cooldown = False
                await sleep(header_delay)
                continue
            consecutive_429 += 1
            if retries < effective_max_retries:
                delay = effective_backoff_base * (2**retries)
                if status:
                    status(
                        "rate limited (HTTP 429), "
                        f"retry {retries + 1}/{effective_max_retries} in {delay:.1f}s"
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
