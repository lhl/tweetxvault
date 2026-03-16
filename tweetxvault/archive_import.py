"""Official X archive import helpers."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from rich.console import Console

from tweetxvault.auth import ResolvedAuthBundle, resolve_auth_bundle
from tweetxvault.client.base import build_async_client
from tweetxvault.client.timelines import (
    TimelineTweet,
    build_tweet_detail_url,
    fetch_page,
    parse_tweet_detail_response,
)
from tweetxvault.config import AppConfig, XDGPaths
from tweetxvault.exceptions import APIResponseError, ConfigError
from tweetxvault.extractor import ExtractedTweetGraph, extract_secondary_objects
from tweetxvault.jobs import locked_archive_job, resolve_job_context
from tweetxvault.query_ids import QueryIdStore, refresh_query_ids
from tweetxvault.storage import ArchiveStore, open_archive_store
from tweetxvault.storage.backend import ARCHIVE_SOURCE, LIVE_SOURCE, _PageBuffer
from tweetxvault.sync import ProcessLock, sync_collection
from tweetxvault.utils import resolve_query_ids, utc_now

_YTD_ASSIGNMENT_RE = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_.]+\s*=\s*", re.DOTALL)
_MANIFEST_ASSIGNMENT_RE = re.compile(r"^\s*window\.__THAR_CONFIG\s*=\s*", re.DOTALL)
_IMPORT_BATCH_SIZE = 500


@dataclass(slots=True)
class _ArchiveIdentity:
    account_id: str
    username: str
    display_name: str


@dataclass(slots=True)
class _PlaceholderTweetObject:
    tweet_id: str
    text: str
    author_id: str | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    created_at: str | None = None
    conversation_id: str | None = None
    lang: str | None = None
    note_tweet_text: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArchiveImportResult:
    skipped: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reconciled_collections: list[str] = field(default_factory=list)
    detail_lookups: int = 0
    detail_terminal_unavailable: int = 0
    detail_transient_failures: int = 0
    pending_enrichment: int = 0


class _ArchiveInput:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._data_dir: Path | None = None
        if path.is_dir():
            if (path / "data" / "manifest.js").exists():
                self._data_dir = path / "data"
            elif (path / "manifest.js").exists():
                self._data_dir = path
            else:
                raise ConfigError("Archive directory is missing manifest.js.")
        elif path.is_file() and zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
        else:
            raise ConfigError(
                "Archive input must be a .zip file or an extracted archive directory."
            )
        self.manifest = self._load_manifest()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def _normalize_name(self, relative_path: str) -> str:
        relative = relative_path.replace("\\", "/").lstrip("/")
        return relative if relative.startswith("data/") else f"data/{relative}"

    def _directory_path(self, relative_path: str) -> Path:
        assert self._data_dir is not None
        relative = self._normalize_name(relative_path)
        return self._data_dir / relative.removeprefix("data/")

    def read_text(self, relative_path: str) -> str:
        if self._zip is not None:
            return self._zip.read(self._normalize_name(relative_path)).decode("utf-8")
        return self._directory_path(relative_path).read_text(encoding="utf-8")

    @contextmanager
    def open_binary(self, relative_path: str):
        if self._zip is not None:
            handle = self._zip.open(self._normalize_name(relative_path), "r")
            try:
                yield handle
            finally:
                handle.close()
            return
        with self._directory_path(relative_path).open("rb") as handle:
            yield handle

    def iter_files(self, relative_dir: str) -> list[str]:
        prefix = self._normalize_name(relative_dir).rstrip("/") + "/"
        if self._zip is not None:
            return [
                name
                for name in sorted(self._zip.namelist())
                if name.startswith(prefix) and name != prefix
            ]
        directory = self._directory_path(relative_dir)
        if not directory.exists():
            return []
        return [f"{prefix}{path.name}" for path in sorted(directory.iterdir()) if path.is_file()]

    def _load_manifest(self) -> dict[str, Any]:
        raw = self.read_text("data/manifest.js")
        return _parse_assigned_json(raw, _MANIFEST_ASSIGNMENT_RE, "manifest.js")

    def dataset_files(self, key: str) -> list[str]:
        dataset = ((self.manifest.get("dataTypes") or {}).get(key)) or {}
        files = dataset.get("files") or []
        return [
            item["fileName"]
            for item in files
            if isinstance(item, dict) and isinstance(item.get("fileName"), str)
        ]

    def load_dataset(self, key: str) -> tuple[list[Any], list[tuple[str, Any]]]:
        items: list[Any] = []
        parts: list[tuple[str, Any]] = []
        for filename in self.dataset_files(key):
            parsed = parse_ytd_js(self.read_text(filename))
            parts.append((filename, parsed))
            if isinstance(parsed, list):
                items.extend(parsed)
        return items, parts

    def digest(self) -> str:
        digest = hashlib.sha256()
        relevant: set[str] = {"data/manifest.js"}
        for dataset_key in (
            "account",
            "profile",
            "tweets",
            "tweetHeaders",
            "deletedTweets",
            "deletedTweetHeaders",
            "like",
        ):
            relevant.update(self.dataset_files(dataset_key))
        tweets_info = ((self.manifest.get("dataTypes") or {}).get("tweets")) or {}
        media_directory = tweets_info.get("mediaDirectory")
        if isinstance(media_directory, str):
            relevant.update(self.iter_files(media_directory))
        for relative_path in sorted(relevant):
            digest.update(relative_path.encode("utf-8"))
            with self.open_binary(relative_path) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()


def _parse_assigned_json(raw: str, pattern: re.Pattern[str], label: str) -> Any:
    stripped = pattern.sub("", raw, count=1).strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Failed to parse {label}.") from exc


def parse_ytd_js(raw: str) -> Any:
    return _parse_assigned_json(raw, _YTD_ASSIGNMENT_RE, "YTD data")


def _archive_identity(manifest: dict[str, Any], account_items: list[Any]) -> _ArchiveIdentity:
    account_entry = account_items[0] if account_items and isinstance(account_items[0], dict) else {}
    account_block = account_entry.get("account") or {}
    user_info = manifest.get("userInfo") or {}
    account_id = str(user_info.get("accountId") or account_block.get("accountId") or "")
    if not account_id:
        raise ConfigError("Archive account.js/manifest.js does not include an account id.")
    username = str(user_info.get("userName") or account_block.get("username") or "").strip()
    if not username:
        raise ConfigError("Archive account.js/manifest.js does not include a username.")
    display_name = str(
        user_info.get("displayName") or account_block.get("accountDisplayName") or username
    ).strip()
    return _ArchiveIdentity(
        account_id=account_id,
        username=username,
        display_name=display_name or username,
    )


def _media_key_for_archive_item(item: dict[str, Any], *, position: int) -> str:
    media_id = item.get("id_str") or item.get("id")
    media_type = item.get("type")
    prefix = "3" if media_type == "photo" else "7"
    if media_id:
        return f"{prefix}_{media_id}"
    return f"{prefix}_archive_{position}"


def _deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _adapt_archive_tweet_payload(
    tweet_payload: dict[str, Any], identity: _ArchiveIdentity
) -> dict[str, Any]:
    legacy = _deepcopy_json(tweet_payload)
    tweet_id = str(legacy.get("id_str") or legacy.get("id") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive tweet payload is missing id_str.")
    legacy["id_str"] = tweet_id
    legacy["conversation_id_str"] = str(legacy.get("conversation_id_str") or tweet_id)
    for key in ("entities", "extended_entities"):
        block = legacy.get(key) or {}
        media_items = block.get("media")
        if not isinstance(media_items, list):
            continue
        for position, item in enumerate(media_items):
            if isinstance(item, dict) and not item.get("media_key"):
                item["media_key"] = _media_key_for_archive_item(item, position=position)
    return {
        "__typename": "Tweet",
        "rest_id": tweet_id,
        "legacy": legacy,
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "rest_id": identity.account_id,
                    "legacy": {
                        "screen_name": identity.username,
                        "name": identity.display_name,
                    },
                }
            }
        },
    }


def _timeline_tweet_from_archive(
    tweet_payload: dict[str, Any],
    identity: _ArchiveIdentity,
    *,
    sort_index: str | None,
) -> TimelineTweet:
    raw_json = _adapt_archive_tweet_payload(tweet_payload, identity)
    legacy = raw_json.get("legacy") or {}
    return TimelineTweet(
        tweet_id=raw_json["rest_id"],
        text=str(legacy.get("full_text") or ""),
        author_id=identity.account_id,
        author_username=identity.username,
        author_display_name=identity.display_name,
        created_at=legacy.get("created_at"),
        sort_index=sort_index,
        raw_json=raw_json,
    )


def _placeholder_tweet_object(like_payload: dict[str, Any]) -> _PlaceholderTweetObject:
    tweet_id = str(like_payload.get("tweetId") or "").strip()
    if not tweet_id:
        raise ConfigError("Archive like row is missing tweetId.")
    return _PlaceholderTweetObject(
        tweet_id=tweet_id,
        text=str(like_payload.get("fullText") or ""),
        raw_json={"like": like_payload},
    )


def _queue_secondary_graph(
    store: ArchiveStore,
    buffer: _PageBuffer,
    graph: ExtractedTweetGraph,
    *,
    source: str,
    deleted_at_by_tweet_id: dict[str, str] | None = None,
) -> None:
    deleted_at_by_tweet_id = deleted_at_by_tweet_id or {}
    for item in graph.tweet_objects.values():
        store._queue_record(
            store._tweet_object_record(
                item,
                source=source,
                deleted_at=deleted_at_by_tweet_id.get(item.tweet_id),
                cursor=buffer,
            ),
            cursor=buffer,
        )
    for item in graph.relations.values():
        store._queue_record(
            store._tweet_relation_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.media.values():
        store._queue_record(
            store._media_record(item, provenance_source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.urls.values():
        store._queue_record(store._url_record(item, source=source, cursor=buffer), cursor=buffer)
    for item in graph.url_refs.values():
        store._queue_record(
            store._url_ref_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )
    for item in graph.articles.values():
        store._queue_record(
            store._article_record(item, source=source, cursor=buffer),
            cursor=buffer,
        )


def _flush_buffer(store: ArchiveStore, buffer: _PageBuffer) -> None:
    if not buffer.records:
        return
    store.merge_rows(list(buffer.records.values()))
    buffer.records.clear()
    buffer.pending_tweets.clear()
    buffer.existing_rows.clear()


def _deleted_headers_map(items: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        tweet = item.get("tweet") if isinstance(item, dict) else {}
        tweet = tweet or {}
        tweet_id = tweet.get("tweet_id")
        deleted_at = tweet.get("deleted_at")
        if isinstance(tweet_id, str) and tweet_id and isinstance(deleted_at, str) and deleted_at:
            result[tweet_id] = deleted_at
    return result


def _import_authored_tweets(
    store: ArchiveStore,
    tweets: list[Any],
    identity: _ArchiveIdentity,
    *,
    deleted_headers: dict[str, str],
    counts: dict[str, int],
) -> None:
    buffer = _PageBuffer()
    for item in tweets:
        payload = item.get("tweet") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        timeline_tweet = _timeline_tweet_from_archive(
            payload,
            identity,
            sort_index=str(payload.get("id_str") or payload.get("id") or ""),
        )
        deleted_at = deleted_headers.get(timeline_tweet.tweet_id) or payload.get("deleted_at")
        store.upsert_tweet(timeline_tweet, cursor=buffer)
        store.upsert_membership(
            timeline_tweet.tweet_id,
            "tweet",
            source=ARCHIVE_SOURCE,
            deleted_at=deleted_at if isinstance(deleted_at, str) else None,
            sort_index=timeline_tweet.sort_index,
            cursor=buffer,
        )
        _queue_secondary_graph(
            store,
            buffer,
            extract_secondary_objects(timeline_tweet.raw_json),
            source=ARCHIVE_SOURCE,
            deleted_at_by_tweet_id=(
                {timeline_tweet.tweet_id: deleted_at}
                if isinstance(deleted_at, str) and deleted_at
                else {}
            ),
        )
        if isinstance(deleted_at, str) and deleted_at:
            counts["deleted_authored_tweets"] += 1
        else:
            counts["authored_tweets"] += 1
        if len(buffer.records) >= _IMPORT_BATCH_SIZE:
            _flush_buffer(store, buffer)
    _flush_buffer(store, buffer)


def _should_seed_like_placeholder(store: ArchiveStore, buffer: _PageBuffer, tweet_id: str) -> bool:
    row = store._lookup_row(store._row_key_for_tweet_object(tweet_id), cursor=buffer)
    if row is None:
        return True
    if row.get("source") == LIVE_SOURCE:
        return False
    return not any(
        row.get(field_name)
        for field_name in ("author_id", "author_username", "created_at", "conversation_id", "lang")
    )


def _import_likes(store: ArchiveStore, likes: list[Any], *, counts: dict[str, int]) -> None:
    buffer = _PageBuffer()
    for index, item in enumerate(likes, start=1):
        payload = item.get("like") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            continue
        tweet_id = str(payload.get("tweetId") or "").strip()
        if not tweet_id:
            continue
        raw_json = {"like": payload}
        timeline_tweet = TimelineTweet(
            tweet_id=tweet_id,
            text=str(payload.get("fullText") or ""),
            author_id=None,
            author_username=None,
            author_display_name=None,
            created_at=None,
            sort_index=str(-index),
            raw_json=raw_json,
        )
        store.upsert_tweet(timeline_tweet, cursor=buffer)
        store.upsert_membership(
            tweet_id,
            "like",
            source=ARCHIVE_SOURCE,
            sort_index=timeline_tweet.sort_index,
            cursor=buffer,
        )
        if _should_seed_like_placeholder(store, buffer, tweet_id):
            store._queue_record(
                store._tweet_object_record(
                    _placeholder_tweet_object(payload),
                    source=ARCHIVE_SOURCE,
                    enrichment_state="pending",
                    cursor=buffer,
                ),
                cursor=buffer,
            )
        counts["likes"] += 1
        if len(buffer.records) >= _IMPORT_BATCH_SIZE:
            _flush_buffer(store, buffer)
    _flush_buffer(store, buffer)


def _url_basename(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = Path(urlsplit(value).path).name
    return name or None


def _copy_file_from_archive(
    source: _ArchiveInput, relative_path: str, destination: Path
) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    byte_size = 0
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=destination.parent,
        delete=False,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    ) as handle:
        temp_path = Path(handle.name)
        try:
            with source.open_binary(relative_path) as input_handle:
                for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                    handle.write(chunk)
                    sha.update(chunk)
                    byte_size += len(chunk)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    temp_path.replace(destination)
    return sha.hexdigest(), byte_size


def _copy_exported_media(
    source: _ArchiveInput,
    store: ArchiveStore,
    paths: XDGPaths,
    media_directory: str | None,
    *,
    counts: dict[str, int],
    warnings: list[str],
) -> None:
    if not media_directory:
        return
    files = source.iter_files(media_directory)
    if not files:
        return

    rows = store.list_media_rows()
    row_by_asset: dict[tuple[str, str], tuple[dict[str, Any], bool]] = {}
    for row in rows:
        tweet_id = row.get("tweet_id")
        if not isinstance(tweet_id, str) or not tweet_id:
            continue
        media_name = _url_basename(row.get("media_url"))
        if media_name:
            row_by_asset[(tweet_id, media_name)] = (row, False)
        thumbnail_name = _url_basename(row.get("thumbnail_url"))
        if thumbnail_name and thumbnail_name != media_name:
            row_by_asset[(tweet_id, thumbnail_name)] = (row, True)

    pending_updates: list[dict[str, Any]] = []
    unmatched = 0
    for relative_path in files:
        filename = Path(relative_path).name
        if "-" not in filename:
            unmatched += 1
            continue
        tweet_id, asset_name = filename.split("-", 1)
        target = row_by_asset.get((tweet_id, asset_name))
        if target is None:
            unmatched += 1
            continue
        row, is_thumbnail = target
        suffix = "-poster" if is_thumbnail else ""
        media_key = str(row.get("media_key") or "media")
        extension = Path(asset_name).suffix.lower() or ".bin"
        relative_dest = Path("media") / tweet_id / f"{media_key}{suffix}{extension}"
        destination = paths.data_dir / relative_dest
        if not destination.exists():
            sha256, byte_size = _copy_file_from_archive(source, relative_path, destination)
            counts["media_files_copied"] += 1
        else:
            sha256 = str(row.get("thumbnail_sha256") if is_thumbnail else row.get("sha256") or "")
            byte_size = int(
                row.get("thumbnail_byte_size") if is_thumbnail else row.get("byte_size") or 0
            )
        content_type = mimetypes.guess_type(destination.name)[0]
        pending_updates.append(
            store.build_media_download_update(
                row,
                download_state="done",
                local_path=(row.get("local_path") if is_thumbnail else relative_dest.as_posix()),
                sha256=(row.get("sha256") if is_thumbnail else sha256 or None),
                byte_size=(row.get("byte_size") if is_thumbnail else byte_size or None),
                content_type=(row.get("content_type") if is_thumbnail else content_type),
                thumbnail_local_path=(
                    relative_dest.as_posix() if is_thumbnail else row.get("thumbnail_local_path")
                ),
                thumbnail_sha256=(sha256 or None) if is_thumbnail else row.get("thumbnail_sha256"),
                thumbnail_byte_size=(byte_size or None)
                if is_thumbnail
                else row.get("thumbnail_byte_size"),
                thumbnail_content_type=content_type
                if is_thumbnail
                else row.get("thumbnail_content_type"),
                downloaded_at=utc_now(),
                download_error=None,
            )
        )
        if len(pending_updates) >= 100:
            store.merge_rows(pending_updates.copy())
            pending_updates.clear()
    if pending_updates:
        store.merge_rows(pending_updates)
    if unmatched:
        warnings.append(f"{unmatched} archive media files did not match normalized media rows.")


async def _run_live_reconciliation(
    *,
    counts: dict[str, int],
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle | None,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
) -> tuple[list[str], list[str], ResolvedAuthBundle | None]:
    warnings: list[str] = []
    try:
        resolved_auth = auth_bundle or resolve_auth_bundle(config)
    except ConfigError as exc:
        warnings.append(f"live reconciliation skipped: {exc}")
        return [], warnings, None

    collections: list[str] = []
    if counts.get("authored_tweets") or counts.get("deleted_authored_tweets"):
        collections.append("tweets")
    if counts.get("likes"):
        collections.append("likes")
    completed: list[str] = []
    for collection in collections:
        try:
            await sync_collection(
                collection,
                full=False,
                limit=None,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
            )
            completed.append(collection)
        except Exception as exc:
            warnings.append(f"{collection} reconciliation failed: {exc}")
    return completed, warnings, resolved_auth


async def _enrich_pending_rows(
    *,
    limit: int,
    config: AppConfig,
    paths: XDGPaths,
    auth_bundle: ResolvedAuthBundle,
    transport: httpx.AsyncBaseTransport | None,
    console: Console,
) -> tuple[int, int, int, int]:
    if limit <= 0:
        async with locked_archive_job(config=config, paths=paths) as job:
            return 0, 0, 0, len(job.store.list_tweet_objects_for_enrichment())

    async with locked_archive_job(config=config, paths=paths) as job:
        store = job.store
        rows = store.list_tweet_objects_for_enrichment(limit=limit)
        if not rows:
            return 0, 0, 0, 0
        query_store = QueryIdStore(paths)
        query_ids = await resolve_query_ids(
            query_store,
            ["TweetDetail"],
            force_refresh=not query_store.is_fresh(),
            transport=transport,
        )
        client = build_async_client(auth_bundle, timeout=config.sync.timeout, transport=transport)
        succeeded = 0
        terminal = 0
        transient = 0
        try:
            for row in rows:
                tweet_id = row["tweet_id"]

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
                        refresh_once=refresh_once,
                    )
                    payload = response.json()
                    tweet = parse_tweet_detail_response(payload, tweet_id)
                    if tweet is None:
                        raise ValueError(f"TweetDetail did not include focal tweet {tweet_id}.")
                    store.persist_tweet_detail(
                        tweet=tweet,
                        raw_json=payload,
                        http_status=response.status_code,
                    )
                    succeeded += 1
                    job.mark_dirty()
                except APIResponseError as exc:
                    if exc.status_code in {404, 410}:
                        store.update_tweet_object_enrichment(
                            tweet_id,
                            enrichment_state="terminal_unavailable",
                            enrichment_checked_at=utc_now(),
                            enrichment_http_status=exc.status_code,
                            enrichment_reason="not_found",
                        )
                        terminal += 1
                        job.mark_dirty()
                        continue
                    raise
                except Exception as exc:
                    store.update_tweet_object_enrichment(
                        tweet_id,
                        enrichment_state="transient_failure",
                        enrichment_checked_at=utc_now(),
                        enrichment_http_status=None,
                        enrichment_reason=exc.__class__.__name__,
                    )
                    transient += 1
                    job.mark_dirty()
        finally:
            await client.aclose()
        remaining = len(store.list_tweet_objects_for_enrichment())
    return succeeded, terminal, transient, remaining


def _manifest_generation_date(manifest: dict[str, Any]) -> str | None:
    archive_info = manifest.get("archiveInfo") or {}
    generation = archive_info.get("generationDate")
    return generation if isinstance(generation, str) and generation else None


def _record_archive_capture(
    store: ArchiveStore, operation: str, filename: str, payload: Any
) -> None:
    store.append_raw_capture(
        operation,
        filename,
        None,
        200,
        payload,
        source=ARCHIVE_SOURCE,
    )


def _initial_counts() -> dict[str, int]:
    return {
        "authored_tweets": 0,
        "deleted_authored_tweets": 0,
        "likes": 0,
        "media_files_copied": 0,
    }


async def import_x_archive(
    archive_path: Path,
    *,
    detail_lookups: int = 0,
    config: AppConfig | None = None,
    paths: XDGPaths | None = None,
    auth_bundle: ResolvedAuthBundle | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    console: Console | None = None,
) -> ArchiveImportResult:
    config, paths = resolve_job_context(config=config, paths=paths)
    console = console or Console(stderr=True)
    source = _ArchiveInput(archive_path)
    try:
        digest = source.digest()
        generation_date = _manifest_generation_date(source.manifest)
        counts = _initial_counts()
        warnings: list[str] = []
        account_items, account_parts = source.load_dataset("account")
        identity = _archive_identity(source.manifest, account_items)
        existing_manifest: dict[str, Any] | None = None
        store: ArchiveStore | None = None

        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=True)
            assert store is not None
            existing_manifest = store.get_import_manifest(digest)
            if existing_manifest and existing_manifest.get("status") == "completed":
                store.close()
                return ArchiveImportResult(
                    skipped=True,
                    counts=counts,
                    warnings=["archive already imported; skipping duplicate import"],
                )

            store.ensure_archive_owner_id(identity.account_id)
            store.set_import_manifest(
                digest,
                archive_generation_date=generation_date,
                status="in_progress",
                import_started_at=utc_now(),
                warnings=warnings,
                counts=counts,
            )
            _record_archive_capture(store, "XArchiveManifest", "data/manifest.js", source.manifest)
            for filename, payload in account_parts:
                _record_archive_capture(store, "XArchiveAccount", filename, payload)

            tweets_info = ((source.manifest.get("dataTypes") or {}).get("tweets")) or {}
            if "bookmark" not in {key.lower() for key in (source.manifest.get("dataTypes") or {})}:
                warnings.append("archive does not contain a bookmark dataset")

            _, tweet_header_parts = source.load_dataset("tweetHeaders")
            deleted_tweets, deleted_tweet_parts = source.load_dataset("deletedTweets")
            deleted_headers_items, deleted_header_parts = source.load_dataset("deletedTweetHeaders")
            tweets, tweet_parts = source.load_dataset("tweets")
            likes, like_parts = source.load_dataset("like")

            for filename, payload in tweet_header_parts:
                _record_archive_capture(store, "XArchiveTweetHeaders", filename, payload)
            for filename, payload in deleted_header_parts:
                _record_archive_capture(store, "XArchiveDeletedTweetHeaders", filename, payload)
            for filename, payload in tweet_parts:
                _record_archive_capture(store, "XArchiveTweets", filename, payload)
            for filename, payload in deleted_tweet_parts:
                _record_archive_capture(store, "XArchiveDeletedTweets", filename, payload)
            for filename, payload in like_parts:
                _record_archive_capture(store, "XArchiveLikes", filename, payload)

            deleted_headers = _deleted_headers_map(deleted_headers_items)
            _import_authored_tweets(
                store, tweets, identity, deleted_headers=deleted_headers, counts=counts
            )
            _import_authored_tweets(
                store,
                deleted_tweets,
                identity,
                deleted_headers=deleted_headers,
                counts=counts,
            )
            _import_likes(store, likes, counts=counts)
            _copy_exported_media(
                source,
                store,
                paths,
                tweets_info.get("mediaDirectory") if isinstance(tweets_info, dict) else None,
                counts=counts,
                warnings=warnings,
            )
            store.set_import_manifest(
                digest,
                archive_generation_date=generation_date,
                status="completed",
                import_started_at=existing_manifest.get("import_started_at")
                if existing_manifest
                else utc_now(),
                import_completed_at=utc_now(),
                warnings=warnings,
                counts=counts,
            )
            store.optimize()
            store.close()
        except Exception:
            try:
                if store is not None:
                    store.set_import_manifest(
                        digest,
                        archive_generation_date=generation_date,
                        status="failed",
                        import_started_at=existing_manifest.get("import_started_at")
                        if existing_manifest
                        else utc_now(),
                        import_completed_at=utc_now(),
                        warnings=warnings,
                        counts=counts,
                    )
            finally:
                if store is not None:
                    store.close()
            raise
        finally:
            lock.release()

        reconciled_collections, reconcile_warnings, resolved_auth = await _run_live_reconciliation(
            counts=counts,
            config=config,
            paths=paths,
            auth_bundle=auth_bundle,
            transport=transport,
            console=console,
        )
        warnings.extend(reconcile_warnings)
        detail_succeeded = 0
        detail_terminal = 0
        detail_transient = 0
        pending = 0
        if resolved_auth is not None:
            (
                detail_succeeded,
                detail_terminal,
                detail_transient,
                pending,
            ) = await _enrich_pending_rows(
                limit=detail_lookups,
                config=config,
                paths=paths,
                auth_bundle=resolved_auth,
                transport=transport,
                console=console,
            )
        else:
            async with locked_archive_job(config=config, paths=paths) as job:
                pending = len(job.store.list_tweet_objects_for_enrichment())

        lock = ProcessLock(paths.lock_file)
        lock.acquire()
        try:
            store = open_archive_store(paths, create=False)
            assert store is not None
            final_counts = dict(counts)
            final_counts["detail_lookups"] = detail_succeeded
            final_counts["detail_terminal_unavailable"] = detail_terminal
            final_counts["detail_transient_failures"] = detail_transient
            final_counts["pending_enrichment"] = pending
            store.set_import_manifest(
                digest,
                archive_generation_date=generation_date,
                status="completed",
                import_started_at=existing_manifest.get("import_started_at")
                if existing_manifest
                else utc_now(),
                import_completed_at=utc_now(),
                warnings=warnings,
                counts=final_counts,
            )
            store.close()
        finally:
            lock.release()

        return ArchiveImportResult(
            skipped=False,
            counts=final_counts,
            warnings=warnings,
            reconciled_collections=reconciled_collections,
            detail_lookups=detail_succeeded,
            detail_terminal_unavailable=detail_terminal,
            detail_transient_failures=detail_transient,
            pending_enrichment=pending,
        )
    finally:
        source.close()
