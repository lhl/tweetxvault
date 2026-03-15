"""Shared export/view helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

COLLECTION_ALIASES = {
    "all": "all",
    "bookmark": "bookmark",
    "bookmarks": "bookmark",
    "like": "like",
    "likes": "like",
    "tweet": "tweet",
    "tweets": "tweet",
}

COLLECTION_LABELS = {
    "all": "all",
    "bookmark": "bookmarks",
    "like": "likes",
    "tweet": "tweets",
}


def normalize_collection_name(collection: str) -> str:
    normalized = COLLECTION_ALIASES.get(collection.strip().lower())
    if normalized is None:
        allowed = ", ".join(sorted(COLLECTION_ALIASES))
        raise ValueError(f"Unsupported collection '{collection}'. Expected one of: {allowed}.")
    return normalized


def display_collection_name(collection: str) -> str:
    return COLLECTION_LABELS[normalize_collection_name(collection)]


def default_export_path(base_dir: Path, collection: str, *, extension: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    label = display_collection_name(collection)
    return base_dir / f"export-{label}-{stamp}.{extension}"


def tweet_url(row: dict[str, object]) -> str:
    author = row.get("author")
    username = None
    if isinstance(author, dict):
        username = author.get("username")
    tweet_id = row.get("tweet_id")
    if isinstance(username, str) and username and isinstance(tweet_id, str) and tweet_id:
        return f"https://x.com/{username}/status/{tweet_id}"
    return f"https://x.com/i/web/status/{tweet_id or ''}"
