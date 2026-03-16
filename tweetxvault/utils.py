"""Small shared utilities."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from tweetxvault.exceptions import QueryIdRefreshError
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


async def resolve_query_ids(
    store: QueryIdStore,
    operations: Sequence[str],
    *,
    force_refresh: bool,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, str]:
    ids = {operation: store.get(operation) for operation in operations}
    if force_refresh or any(value is None for value in ids.values()):
        client = httpx.AsyncClient(follow_redirects=True, timeout=20.0, transport=transport)
        try:
            await refresh_query_ids(store, operations=operations, client=client)
        finally:
            await client.aclose()
        ids = {operation: store.get(operation) for operation in operations}
    missing = [operation for operation, value in ids.items() if value is None]
    if missing:
        raise QueryIdRefreshError(f"Missing query IDs for operations: {', '.join(missing)}")
    return {operation: value for operation, value in ids.items() if value is not None}
