"""Query ID scraping from X web bundles."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import httpx

from tweetxvault.query_ids.constants import (
    BUNDLE_URL_PATTERN,
    CLIENT_WEB_BUNDLE_URL,
    DISCOVERY_PAGE_URLS,
    FALLBACK_QUERY_IDS,
    TARGET_OPERATIONS,
)
from tweetxvault.query_ids.store import QueryIdStore

QUERY_ID_PATTERNS = (
    re.compile(r'queryId:"([A-Za-z0-9_-]{20,})",operationName:"([^"]+)"'),
    re.compile(r'"queryId":"([A-Za-z0-9_-]{20,})","operationName":"([^"]+)"'),
    re.compile(r'operationName:"([^"]+)",queryId:"([A-Za-z0-9_-]{20,})"'),
)
CHUNK_MANIFEST_PATTERN = re.compile(r'"([^"]+)":"([a-f0-9]{7,8})"')
BUNDLE_URL_RE = re.compile(BUNDLE_URL_PATTERN)


def extract_bundle_urls(html: str) -> set[str]:
    return set(BUNDLE_URL_RE.findall(html))


def extract_query_ids(script: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for pattern in QUERY_ID_PATTERNS:
        for match in pattern.finditer(script):
            if pattern.pattern.startswith("operationName"):
                operation_name, query_id = match.groups()
            else:
                query_id, operation_name = match.groups()
            found[operation_name] = query_id
    return found


def _chunk_keywords(operations: Sequence[str]) -> set[str]:
    keywords: set[str] = set()
    for operation in operations:
        if "Bookmark" in operation:
            keywords.add("Bookmark")
        if "Like" in operation:
            keywords.add("Like")
        if "Article" in operation:
            keywords.add("Article")
        keywords.add(operation)
    return keywords


def extract_candidate_chunk_urls(script: str, operations: Sequence[str]) -> set[str]:
    urls: set[str] = set()
    keywords = _chunk_keywords(operations)
    for chunk_name, chunk_hash in CHUNK_MANIFEST_PATTERN.findall(script):
        if any(keyword in chunk_name for keyword in keywords):
            urls.add(f"{CLIENT_WEB_BUNDLE_URL}/{chunk_name}.{chunk_hash}a.js")
    return urls


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.text


async def discover_query_ids(
    client: httpx.AsyncClient,
    *,
    operations: Sequence[str] = TARGET_OPERATIONS,
) -> dict[str, str]:
    found: dict[str, str] = {}
    queue: list[str] = []
    seen: set[str] = set()

    for url in DISCOVERY_PAGE_URLS:
        html = await _fetch_text(client, url)
        queue.extend(sorted(extract_bundle_urls(html)))

    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        text = await _fetch_text(client, url)
        found.update(extract_query_ids(text))
        queue.extend(sorted(extract_candidate_chunk_urls(text, operations) - seen))
        if all(operation in found for operation in operations):
            break

    return {operation: found[operation] for operation in operations if operation in found}


async def refresh_query_ids(
    store: QueryIdStore,
    *,
    operations: Iterable[str] = TARGET_OPERATIONS,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    operation_list = list(operations)
    close_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=20.0)
    try:
        discovered = await discover_query_ids(client, operations=operation_list)
    finally:
        if close_client:
            await client.aclose()

    merged = {**FALLBACK_QUERY_IDS, **discovered}
    store.save(merged)
    return {operation: merged[operation] for operation in operation_list if operation in merged}
