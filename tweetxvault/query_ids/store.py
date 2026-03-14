"""On-disk query ID cache."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from tweetxvault.config import XDGPaths
from tweetxvault.query_ids.constants import FALLBACK_QUERY_IDS

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class QueryIdCache(BaseModel):
    fetched_at: datetime | None = None
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    ids: dict[str, str] = Field(default_factory=dict)


class QueryIdStore:
    def __init__(self, paths: XDGPaths):
        self.path = paths.query_id_cache_file
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> QueryIdCache:
        if not self.path.exists():
            return QueryIdCache()
        raw = json.loads(self.path.read_text())
        return QueryIdCache.model_validate(raw)

    def save(self, ids: dict[str, str], *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> QueryIdCache:
        cache = QueryIdCache(fetched_at=datetime.now(tz=UTC), ttl_seconds=ttl_seconds, ids=ids)
        payload = cache.model_dump_json(indent=2)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        temp_path.replace(self.path)
        return cache

    def is_fresh(self, cache: QueryIdCache | None = None, *, now: datetime | None = None) -> bool:
        cache = cache or self.load()
        if cache.fetched_at is None:
            return False
        now = now or datetime.now(tz=UTC)
        return cache.fetched_at + timedelta(seconds=cache.ttl_seconds) > now

    def get(self, operation: str) -> str | None:
        cache = self.load()
        if self.is_fresh(cache) and operation in cache.ids:
            return cache.ids[operation]
        return FALLBACK_QUERY_IDS.get(operation) or cache.ids.get(operation)
