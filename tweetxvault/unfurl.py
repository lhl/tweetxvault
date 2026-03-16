"""URL unfurl helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

import httpx
from rich.console import Console

from tweetxvault.config import DEFAULT_USER_AGENT, AppConfig, XDGPaths
from tweetxvault.extractor import canonicalize_url
from tweetxvault.jobs import locked_archive_job
from tweetxvault.utils import utc_now

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)
_UPDATE_BATCH_SIZE = 100


@dataclass(slots=True)
class UrlUnfurlResult:
    processed: int = 0
    updated: int = 0
    failed: int = 0


def _clean_html_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(unescape(value).split())
    return cleaned or None


def _extract_html_metadata(html: str) -> tuple[str | None, str | None, str | None, str | None]:
    title_match = _TITLE_RE.search(html)
    canonical_match = _CANONICAL_RE.search(html)
    title = _clean_html_text(title_match.group(1) if title_match else None)
    canonical_url = _clean_html_text(canonical_match.group(1) if canonical_match else None)
    meta: dict[str, str] = {}
    for key, value in _META_RE.findall(html):
        lowered = key.strip().lower()
        if lowered not in meta:
            meta[lowered] = value
    description = _clean_html_text(
        meta.get("description") or meta.get("og:description") or meta.get("twitter:description")
    )
    site_name = _clean_html_text(
        meta.get("og:site_name") or meta.get("application-name") or meta.get("twitter:site")
    )
    return title, description, site_name, canonical_url


async def unfurl_urls(
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    console: Console | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UrlUnfurlResult:
    async with locked_archive_job(config=config, paths=paths) as job:
        config = job.config
        store = job.store
        states = {"pending"}
        if retry_failed:
            states.add("failed")
        rows = store.list_url_rows(states=states, limit=limit)
        result = UrlUnfurlResult()
        if not rows:
            return result

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=max(config.sync.timeout, 30.0),
            transport=transport,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            },
        ) as client:
            pending_updates: list[dict[str, object]] = []

            def flush_updates() -> None:
                if not pending_updates:
                    return
                store.merge_rows(pending_updates.copy())
                pending_updates.clear()

            for row in rows:
                result.processed += 1
                request_url = (
                    row.get("final_url")
                    or row.get("expanded_url")
                    or row.get("canonical_url")
                    or row.get("url")
                )
                if not isinstance(request_url, str) or not request_url:
                    pending_updates.append(
                        store.build_url_unfurl_update(
                            row,
                            http_status=row.get("http_status"),
                            final_url=row.get("final_url"),
                            canonical_url=row.get("canonical_url"),
                            title=row.get("title"),
                            description=row.get("description"),
                            site_name=row.get("site_name"),
                            content_type=row.get("content_type"),
                            unfurl_state="failed",
                            last_fetched_at=utc_now(),
                            download_error="missing URL to unfurl",
                        )
                    )
                    result.failed += 1
                    if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                        flush_updates()
                    continue

                try:
                    response = await client.get(request_url)
                    response.raise_for_status()
                    final_url = str(response.url)
                    content_type = response.headers.get("content-type")
                    title = row.get("title")
                    description = row.get("description")
                    site_name = row.get("site_name")
                    canonical_url = row.get("canonical_url")
                    if content_type and "html" in content_type.lower():
                        html = response.text[:500000]
                        (
                            parsed_title,
                            parsed_description,
                            parsed_site_name,
                            parsed_canonical_url,
                        ) = _extract_html_metadata(html)
                        title = parsed_title or title
                        description = parsed_description or description
                        site_name = parsed_site_name or site_name
                        canonical_url = (
                            canonicalize_url(parsed_canonical_url)
                            or canonicalize_url(final_url)
                            or canonical_url
                        )
                    else:
                        canonical_url = canonical_url or canonicalize_url(final_url)
                    pending_updates.append(
                        store.build_url_unfurl_update(
                            row,
                            http_status=response.status_code,
                            final_url=final_url,
                            canonical_url=canonical_url,
                            title=title,
                            description=description,
                            site_name=site_name,
                            content_type=content_type,
                            unfurl_state="done",
                            last_fetched_at=utc_now(),
                            download_error=None,
                        )
                    )
                    result.updated += 1
                except Exception as exc:
                    pending_updates.append(
                        store.build_url_unfurl_update(
                            row,
                            http_status=getattr(
                                getattr(exc, "response", None),
                                "status_code",
                                None,
                            ),
                            final_url=row.get("final_url"),
                            canonical_url=row.get("canonical_url"),
                            title=row.get("title"),
                            description=row.get("description"),
                            site_name=row.get("site_name"),
                            content_type=row.get("content_type"),
                            unfurl_state="failed",
                            last_fetched_at=utc_now(),
                            download_error=str(exc),
                        )
                    )
                    result.failed += 1
                    if console:
                        console.print(f"url {row['row_key']}: failed ({exc})", highlight=False)
                if len(pending_updates) >= _UPDATE_BATCH_SIZE:
                    flush_updates()

            flush_updates()

        if result.processed > 0:
            job.mark_dirty()
        return result
