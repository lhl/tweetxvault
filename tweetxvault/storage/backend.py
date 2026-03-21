"""Archive storage backend.

The shipped MVP initially used SQLite, but the current backend is LanceDB with a
single-table archive model. The public ArchiveStore API stays focused on the sync
semantics the rest of the application needs.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from tweetxvault.client.timelines import TimelineTweet, parse_tweet_detail_tweets
from tweetxvault.config import XDGPaths
from tweetxvault.exceptions import ArchiveOwnerMismatchError
from tweetxvault.extractor import (
    ExtractedTweetGraph,
    extract_author_fields,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
    extract_status_id_from_url,
    extract_thread_objects,
)
from tweetxvault.utils import utc_now


def _folder_key(folder_id: str | None) -> str:
    return folder_id or ""


def _expr_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _expr_in(field: str, values: set[str]) -> str:
    return f"{field} IN ({', '.join(_expr_quote(value) for value in sorted(values))})"


def _pending_state_expr(field: str) -> str:
    return f"({field} IS NULL OR {field} = '' OR {field} = 'pending')"


def _state_filter_expr(field: str, states: set[str]) -> str:
    clauses: list[str] = []
    explicit_states = {state for state in states if state != "pending"}
    if explicit_states:
        clauses.append(_expr_in(field, explicit_states))
    if "pending" in states:
        clauses.append(_pending_state_expr(field))
    if not clauses:
        raise ValueError("state filter requires at least one state")
    return clauses[0] if len(clauses) == 1 else f"({' OR '.join(clauses)})"


def _and_expr(*clauses: str) -> str:
    return " AND ".join(clause for clause in clauses if clause)


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class SyncState:
    collection_type: str
    last_head_tweet_id: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False
    updated_at: str | None = None


@dataclass(slots=True)
class _PageBuffer:
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_tweets: dict[str, TimelineTweet] = field(default_factory=dict)
    existing_rows: dict[str, dict[str, Any] | None] = field(default_factory=dict)


@dataclass(slots=True)
class _RecordContext:
    existing: dict[str, Any] | None
    now: str
    first_seen_at: str
    added_at: str


@dataclass(slots=True)
class RehydrateResult:
    tweets_updated: int = 0
    secondary_records: int = 0


@dataclass(slots=True)
class ArchiveCollectionStats:
    collection_type: str
    post_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    last_synced_at: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False


@dataclass(slots=True)
class ArchiveStats:
    owner_user_id: str | None = None
    unique_post_count: int = 0
    collection_membership_count: int = 0
    article_count: int = 0
    raw_capture_count: int = 0
    media_count: int = 0
    url_count: int = 0
    oldest_created_at: str | None = None
    newest_created_at: str | None = None
    latest_capture_at: str | None = None
    latest_sync_at: str | None = None
    version_count: int = 0
    collections: list[ArchiveCollectionStats] = field(default_factory=list)
    pending_enrichment_count: int = 0
    transient_enrichment_failure_count: int = 0
    terminal_enrichment_count: int = 0
    done_enrichment_count: int = 0
    preview_article_count: int = 0
    missing_tweet_object_count: int = 0
    expanded_thread_target_count: int = 0
    pending_thread_membership_count: int = 0
    pending_thread_linked_status_count: int = 0


ARCHIVE_SCHEMA = pa.schema(
    [
        pa.field("row_key", pa.string(), nullable=False),
        pa.field("record_type", pa.string(), nullable=False),
        pa.field("tweet_id", pa.string()),
        pa.field("collection_type", pa.string()),
        pa.field("folder_id", pa.string()),
        pa.field("sort_index", pa.string()),
        pa.field("operation", pa.string()),
        pa.field("cursor_in", pa.string()),
        pa.field("cursor_out", pa.string()),
        pa.field("captured_at", pa.string()),
        pa.field("http_status", pa.int32()),
        pa.field("source", pa.string()),
        pa.field("text", pa.large_string()),
        pa.field("author_id", pa.string()),
        pa.field("author_username", pa.string()),
        pa.field("author_display_name", pa.large_string()),
        pa.field("created_at", pa.string()),
        pa.field("deleted_at", pa.string()),
        pa.field("conversation_id", pa.string()),
        pa.field("lang", pa.string()),
        pa.field("note_tweet_text", pa.large_string()),
        pa.field("enrichment_state", pa.string()),
        pa.field("enrichment_checked_at", pa.string()),
        pa.field("enrichment_http_status", pa.int32()),
        pa.field("enrichment_reason", pa.string()),
        pa.field("raw_json", pa.large_string()),
        pa.field("first_seen_at", pa.string()),
        pa.field("last_seen_at", pa.string()),
        pa.field("added_at", pa.string()),
        pa.field("synced_at", pa.string()),
        pa.field("relation_type", pa.string()),
        pa.field("target_tweet_id", pa.string()),
        pa.field("position", pa.int32()),
        pa.field("media_key", pa.string()),
        pa.field("media_type", pa.string()),
        pa.field("media_url", pa.string()),
        pa.field("thumbnail_url", pa.string()),
        pa.field("width", pa.int32()),
        pa.field("height", pa.int32()),
        pa.field("duration_millis", pa.int64()),
        pa.field("variants_json", pa.large_string()),
        pa.field("download_state", pa.string()),
        pa.field("local_path", pa.string()),
        pa.field("provenance_source", pa.string()),
        pa.field("sha256", pa.string()),
        pa.field("byte_size", pa.int64()),
        pa.field("content_type", pa.string()),
        pa.field("thumbnail_local_path", pa.string()),
        pa.field("thumbnail_sha256", pa.string()),
        pa.field("thumbnail_byte_size", pa.int64()),
        pa.field("thumbnail_content_type", pa.string()),
        pa.field("downloaded_at", pa.string()),
        pa.field("download_error", pa.large_string()),
        pa.field("url_hash", pa.string()),
        pa.field("url", pa.string()),
        pa.field("expanded_url", pa.string()),
        pa.field("final_url", pa.string()),
        pa.field("canonical_url", pa.string()),
        pa.field("display_url", pa.string()),
        pa.field("url_host", pa.string()),
        pa.field("description", pa.large_string()),
        pa.field("site_name", pa.string()),
        pa.field("unfurl_state", pa.string()),
        pa.field("last_fetched_at", pa.string()),
        pa.field("article_id", pa.string()),
        pa.field("title", pa.large_string()),
        pa.field("summary_text", pa.large_string()),
        pa.field("content_text", pa.large_string()),
        pa.field("published_at", pa.string()),
        pa.field("status", pa.string()),
        pa.field("archive_digest", pa.string()),
        pa.field("archive_generation_date", pa.string()),
        pa.field("import_started_at", pa.string()),
        pa.field("import_completed_at", pa.string()),
        pa.field("warnings_json", pa.large_string()),
        pa.field("counts_json", pa.large_string()),
        pa.field("last_head_tweet_id", pa.string()),
        pa.field("backfill_cursor", pa.string()),
        pa.field("backfill_incomplete", pa.bool_()),
        pa.field("updated_at", pa.string()),
        pa.field("key", pa.string()),
        pa.field("value", pa.large_string()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]
)

EMBEDDING_DIM = 384
SECONDARY_RECORD_TYPES = ("tweet_object", "tweet_relation", "media", "url", "url_ref", "article")
LIVE_SOURCE = "live_graphql"
ARCHIVE_SOURCE = "x_archive"
SEARCH_KIND_POST = "post"
SEARCH_KIND_ARTICLE = "article"
SEARCH_COLLECTION_ORDER = ("bookmark", "like", "tweet")
SEARCH_TEXT_FIELD = "text"


class ArchiveStore:
    TABLE_NAME = "archive"

    def __init__(self, db_path: Path, *, create: bool) -> None:
        self.db_path = db_path
        if create:
            db_path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(db_path)
        table_names = set(self.db.list_tables().tables)
        if self.TABLE_NAME in table_names:
            self.table = self.db.open_table(self.TABLE_NAME)
            self._migrate_schema()
        elif create:
            self.table = self.db.create_table(
                self.TABLE_NAME,
                schema=ARCHIVE_SCHEMA,
                mode="overwrite",
            )
        else:
            raise FileNotFoundError(f"LanceDB archive table not found at {db_path}")

    def _migrate_schema(self) -> None:
        if self.table.schema.equals(ARCHIVE_SCHEMA):
            return
        arrow_table = self.table.to_arrow()
        existing_names = {schema_field.name for schema_field in arrow_table.schema}
        for schema_field in ARCHIVE_SCHEMA:
            if schema_field.name not in existing_names:
                arrow_table = arrow_table.append_column(
                    schema_field.name,
                    pa.nulls(len(arrow_table), type=schema_field.type),
                )
        arrays = [
            arrow_table[schema_field.name].cast(schema_field.type)
            for schema_field in ARCHIVE_SCHEMA
        ]
        new_table = pa.Table.from_arrays(arrays, schema=ARCHIVE_SCHEMA)
        self.table = self.db.create_table(self.TABLE_NAME, new_table, mode="overwrite")

    def close(self) -> None:
        return None

    def _record(self, **overrides: Any) -> dict[str, Any]:
        record = {field.name: None for field in ARCHIVE_SCHEMA}
        record.update(overrides)
        return record

    def _coalesce_value(self, *values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            return value
        return None

    def _json_value(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, sort_keys=True)

    def _merge_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        payload = pa.Table.from_pylist(records, schema=ARCHIVE_SCHEMA)
        (
            self.table.merge_insert("row_key")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(payload)
        )

    def merge_rows(self, rows: list[dict[str, Any]]) -> None:
        self._merge_records(rows)

    def _row_key_for_tweet(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> str:
        return f"tweet:{collection_type}:{_folder_key(folder_id)}:{tweet_id}"

    def _row_key_for_sync_state(self, collection_type: str, folder_id: str | None = None) -> str:
        return f"sync_state:{collection_type}:{_folder_key(folder_id)}"

    def _row_key_for_metadata(self, key: str) -> str:
        return f"metadata:{key}"

    def _row_key_for_import_manifest(self, archive_digest: str) -> str:
        return f"import_manifest:{archive_digest}"

    def _row_key_for_tweet_object(self, tweet_id: str) -> str:
        return f"tweet_object:{tweet_id}"

    def _row_key_for_tweet_relation(
        self, source_tweet_id: str, relation_type: str, target_tweet_id: str
    ) -> str:
        return f"tweet_relation:{source_tweet_id}:{relation_type}:{target_tweet_id}"

    def _row_key_for_media(self, tweet_id: str, media_key: str) -> str:
        return f"media:{tweet_id}:{media_key}"

    def _row_key_for_url(self, url_hash: str) -> str:
        return f"url:{url_hash}"

    def _row_key_for_url_ref(self, tweet_id: str, position: int) -> str:
        return f"url_ref:{tweet_id}:{position}"

    def _row_key_for_article(self, tweet_id: str) -> str:
        return f"article:{tweet_id}"

    def _get_row(self, row_key: str) -> dict[str, Any] | None:
        rows = self.table.search().where(f"row_key = {_expr_quote(row_key)}").limit(1).to_list()
        return rows[0] if rows else None

    def _rows_for_values(
        self,
        record_type: str,
        field_name: str,
        values: set[str] | list[str] | tuple[str, ...],
    ) -> list[dict[str, Any]]:
        unique_values = [value for value in dict.fromkeys(values) if value]
        if not unique_values:
            return []
        rows: list[dict[str, Any]] = []
        chunk_size = 100
        for start in range(0, len(unique_values), chunk_size):
            chunk = unique_values[start : start + chunk_size]
            joined = " OR ".join(f"{field_name} = {_expr_quote(value)}" for value in chunk)
            expr = f"record_type = {_expr_quote(record_type)} AND ({joined})"
            rows.extend(self.table.search().where(expr).to_list())
        return rows

    def _lookup_row(
        self, row_key: str, *, cursor: _PageBuffer | None = None
    ) -> dict[str, Any] | None:
        if cursor is None:
            return self._get_row(row_key)
        if row_key in cursor.records:
            return cursor.records[row_key]
        if row_key not in cursor.existing_rows:
            cursor.existing_rows[row_key] = self._get_row(row_key)
        return cursor.existing_rows[row_key]

    def prefetch_rows(self, row_keys: list[str], *, cursor: _PageBuffer) -> None:
        missing = [
            row_key
            for row_key in dict.fromkeys(row_keys)
            if row_key and row_key not in cursor.records and row_key not in cursor.existing_rows
        ]
        if not missing:
            return
        found: dict[str, dict[str, Any]] = {}
        chunk_size = 200
        for start in range(0, len(missing), chunk_size):
            chunk = missing[start : start + chunk_size]
            expr = _expr_in("row_key", set(chunk))
            for row in self.table.search().where(expr).to_list():
                row_key = row.get("row_key")
                if isinstance(row_key, str) and row_key:
                    found[row_key] = row
        for row_key in missing:
            cursor.existing_rows[row_key] = found.get(row_key)

    def _queue_record(self, record: dict[str, Any], *, cursor: _PageBuffer | None = None) -> None:
        if cursor is None:
            self._merge_records([record])
            return
        cursor.records[record["row_key"]] = record

    def _row_timestamps(
        self, row_key: str, *, cursor: _PageBuffer | None = None, now: str | None = None
    ) -> tuple[dict[str, Any] | None, str, str]:
        existing = self._lookup_row(row_key, cursor=cursor)
        stamp = now or utc_now()
        first_seen_at = existing["first_seen_at"] if existing else stamp
        added_at = existing["added_at"] if existing else stamp
        return existing, first_seen_at, added_at

    def _record_context(
        self,
        row_key: str,
        *,
        cursor: _PageBuffer | None = None,
    ) -> _RecordContext:
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return _RecordContext(
            existing=existing,
            now=now,
            first_seen_at=first_seen_at,
            added_at=added_at,
        )

    @staticmethod
    def _existing_value(context: _RecordContext, field_name: str) -> Any:
        if context.existing is None:
            return None
        return context.existing.get(field_name)

    def _coalesce_existing(
        self,
        context: _RecordContext,
        field_name: str,
        *values: Any,
    ) -> Any:
        return self._coalesce_value(*values, self._existing_value(context, field_name))

    def _record_with_context(
        self,
        context: _RecordContext,
        **overrides: Any,
    ) -> dict[str, Any]:
        return self._record(
            **overrides,
            first_seen_at=context.first_seen_at,
            last_seen_at=context.now,
            added_at=context.added_at,
            synced_at=context.now,
        )

    def _normalized_source(self, value: str | None) -> str:
        return value or LIVE_SOURCE

    def _prefer_incoming_source(
        self,
        context: _RecordContext,
        incoming_source: str,
        *,
        source_field: str = "source",
        deleted_at: str | None = None,
    ) -> bool:
        if context.existing is None:
            return True
        existing_source = self._normalized_source(self._existing_value(context, source_field))
        if incoming_source == existing_source:
            return True
        return incoming_source == LIVE_SOURCE and existing_source != LIVE_SOURCE

    def _merge_by_source_precedence(
        self,
        context: _RecordContext,
        field_name: str,
        incoming: Any,
        *,
        prefer_incoming: bool,
    ) -> Any:
        existing = self._existing_value(context, field_name)
        if prefer_incoming:
            return self._coalesce_value(incoming, existing)
        return self._coalesce_value(existing, incoming)

    def _merged_source_value(
        self,
        context: _RecordContext,
        incoming_source: str,
        *,
        prefer_incoming: bool,
        field_name: str = "source",
    ) -> str:
        if prefer_incoming:
            return incoming_source
        return self._normalized_source(self._existing_value(context, field_name))

    def _merged_deleted_at(
        self,
        context: _RecordContext,
        incoming_deleted_at: str | None,
        *,
        prefer_incoming: bool,
        incoming_source: str,
    ) -> str | None:
        if prefer_incoming and incoming_source == LIVE_SOURCE:
            return incoming_deleted_at
        return self._coalesce_value(
            incoming_deleted_at, self._existing_value(context, "deleted_at")
        )

    def _capture_record(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str,
        capture_key: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        capture_id = capture_key or str(uuid.uuid4())
        return capture_id, self._record(
            row_key=f"raw_capture:{capture_id}",
            record_type="raw_capture",
            operation=operation,
            cursor_in=cursor_in,
            cursor_out=cursor_out,
            captured_at=utc_now(),
            http_status=http_status,
            source=source,
            raw_json=json.dumps(raw_json, sort_keys=True),
        )

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: Any,
        *,
        source: str = LIVE_SOURCE,
        capture_key: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> str:
        capture_id, record = self._capture_record(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=source,
            capture_key=capture_key,
        )
        self._queue_record(record, cursor=cursor)
        return capture_id

    def upsert_tweet(self, tweet: TimelineTweet, *, cursor: _PageBuffer | None = None) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_tweet() is only supported inside page buffering "
                "for the LanceDB backend."
            )
        cursor.pending_tweets[tweet.tweet_id] = tweet

    def _tweet_record(
        self,
        tweet: TimelineTweet,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        legacy = tweet.raw_json.get("legacy") or {}
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            sort_index=sort_index,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            ),
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=self._merge_by_source_precedence(
                context,
                "created_at",
                tweet.created_at,
                prefer_incoming=prefer_incoming,
            ),
            deleted_at=self._merged_deleted_at(
                context,
                deleted_at,
                prefer_incoming=prefer_incoming,
                incoming_source=source,
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                legacy.get("conversation_id_str"),
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                legacy.get("lang"),
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                extract_note_tweet_text(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def upsert_membership(
        self,
        tweet_id: str,
        collection_type: str,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> None:
        if cursor is None:
            raise RuntimeError(
                "ArchiveStore.upsert_membership() is only supported inside page buffering "
                "for the LanceDB backend."
            )
        try:
            tweet = cursor.pending_tweets[tweet_id]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing pending tweet {tweet_id} for collection {collection_type}."
            ) from exc
        self._queue_record(
            self._tweet_record(
                tweet,
                collection_type,
                source=source,
                deleted_at=deleted_at,
                sort_index=sort_index,
                folder_id=folder_id,
                cursor=cursor,
            ),
            cursor=cursor,
        )

    def get_sync_state(self, collection_type: str, folder_id: str | None = None) -> SyncState:
        row = self._get_row(self._row_key_for_sync_state(collection_type, folder_id))
        if row is None:
            return SyncState(collection_type=collection_type)
        return SyncState(
            collection_type=collection_type,
            last_head_tweet_id=row["last_head_tweet_id"],
            backfill_cursor=row["backfill_cursor"],
            backfill_incomplete=bool(row["backfill_incomplete"]),
            updated_at=row["updated_at"],
        )

    def _sync_state_record(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        return self._record(
            row_key=self._row_key_for_sync_state(collection_type, folder_id),
            record_type="sync_state",
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            updated_at=utc_now(),
        )

    def set_sync_state(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> None:
        record = self._sync_state_record(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            folder_id=folder_id,
        )
        self._queue_record(record, cursor=cursor)

    def reset_sync_state(self, collection_type: str, folder_id: str | None = None) -> None:
        row_key = self._row_key_for_sync_state(collection_type, folder_id)
        self.table.delete(f"row_key = {_expr_quote(row_key)}")

    def has_membership(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> bool:
        row_key = self._row_key_for_tweet(tweet_id, collection_type, folder_id)
        return self._get_row(row_key) is not None

    def get_collection_tweet_ids(self, collection_type: str) -> set[str]:
        filter_expr = f"record_type = 'tweet' AND collection_type = {_expr_quote(collection_type)}"
        rows = self.table.search().where(filter_expr).select(["tweet_id"]).to_list()
        return {row["tweet_id"] for row in rows}

    def get_archive_owner_id(self) -> str | None:
        row = self._get_row(self._row_key_for_metadata("owner_user_id"))
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str, *, cursor: _PageBuffer | None = None) -> None:
        record = self._record(
            row_key=self._row_key_for_metadata("owner_user_id"),
            record_type="metadata",
            key="owner_user_id",
            value=user_id,
            updated_at=utc_now(),
        )
        self._queue_record(record, cursor=cursor)

    def ensure_archive_owner_id(self, user_id: str | None) -> None:
        if not user_id:
            return
        existing = self.get_archive_owner_id()
        if existing and existing != user_id:
            raise ArchiveOwnerMismatchError(
                f"Local archive belongs to X user {existing}, but current auth resolved {user_id}."
            )
        if existing is None:
            self.set_archive_owner_id(user_id)

    def get_import_manifest(self, archive_digest: str) -> dict[str, Any] | None:
        return self._get_row(self._row_key_for_import_manifest(archive_digest))

    def set_import_manifest(
        self,
        archive_digest: str,
        *,
        archive_generation_date: str | None,
        status: str,
        import_started_at: str | None = None,
        import_completed_at: str | None = None,
        warnings: list[str] | None = None,
        counts: dict[str, Any] | None = None,
    ) -> None:
        existing = self.get_import_manifest(archive_digest)
        self.merge_rows(
            [
                self._record(
                    row_key=self._row_key_for_import_manifest(archive_digest),
                    record_type="import_manifest",
                    archive_digest=archive_digest,
                    archive_generation_date=archive_generation_date
                    or (existing.get("archive_generation_date") if existing else None),
                    import_started_at=import_started_at
                    or (existing.get("import_started_at") if existing else None)
                    or utc_now(),
                    import_completed_at=import_completed_at,
                    status=status,
                    warnings_json=self._json_value(warnings or []),
                    counts_json=self._json_value(counts or {}),
                    updated_at=utc_now(),
                )
            ]
        )

    def _tweet_object_record(
        self,
        tweet: Any,
        *,
        source: str = LIVE_SOURCE,
        deleted_at: str | None = None,
        enrichment_state: str | None = None,
        enrichment_checked_at: str | None = None,
        enrichment_http_status: int | None = None,
        enrichment_reason: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_object(tweet.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            source,
            deleted_at=deleted_at,
        )
        if source == LIVE_SOURCE:
            enrichment_state = enrichment_state or "done"
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_http_status = (
                200 if enrichment_http_status is None else enrichment_http_status
            )
            enrichment_reason = None
        elif deleted_at and enrichment_state is None:
            enrichment_state = "terminal_unavailable"
            enrichment_checked_at = enrichment_checked_at or context.now
            enrichment_reason = enrichment_reason or "deleted"
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_object",
            tweet_id=tweet.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            text=self._merge_by_source_precedence(
                context,
                "text",
                tweet.text,
                prefer_incoming=prefer_incoming,
            )
            or "",
            author_id=self._merge_by_source_precedence(
                context,
                "author_id",
                tweet.author_id,
                prefer_incoming=prefer_incoming,
            ),
            author_username=self._merge_by_source_precedence(
                context,
                "author_username",
                tweet.author_username,
                prefer_incoming=prefer_incoming,
            ),
            author_display_name=self._merge_by_source_precedence(
                context,
                "author_display_name",
                tweet.author_display_name,
                prefer_incoming=prefer_incoming,
            ),
            created_at=self._merge_by_source_precedence(
                context,
                "created_at",
                tweet.created_at,
                prefer_incoming=prefer_incoming,
            ),
            deleted_at=self._merged_deleted_at(
                context,
                deleted_at,
                prefer_incoming=prefer_incoming,
                incoming_source=source,
            ),
            conversation_id=self._merge_by_source_precedence(
                context,
                "conversation_id",
                tweet.conversation_id,
                prefer_incoming=prefer_incoming,
            ),
            lang=self._merge_by_source_precedence(
                context,
                "lang",
                tweet.lang,
                prefer_incoming=prefer_incoming,
            ),
            note_tweet_text=self._merge_by_source_precedence(
                context,
                "note_tweet_text",
                tweet.note_tweet_text,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_state=self._merge_by_source_precedence(
                context,
                "enrichment_state",
                enrichment_state,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_checked_at=self._merge_by_source_precedence(
                context,
                "enrichment_checked_at",
                enrichment_checked_at,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_http_status=self._merge_by_source_precedence(
                context,
                "enrichment_http_status",
                enrichment_http_status,
                prefer_incoming=prefer_incoming,
            ),
            enrichment_reason=self._merge_by_source_precedence(
                context,
                "enrichment_reason",
                enrichment_reason,
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(tweet.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _tweet_relation_record(
        self,
        relation: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_relation(
            relation.source_tweet_id,
            relation.relation_type,
            relation.target_tweet_id,
        )
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="tweet_relation",
            tweet_id=relation.source_tweet_id,
            relation_type=relation.relation_type,
            target_tweet_id=relation.target_tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(relation.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _media_record(
        self,
        media: Any,
        *,
        provenance_source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_media(media.tweet_id, media.media_key)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(
            context,
            provenance_source,
            source_field="provenance_source",
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="media",
            tweet_id=media.tweet_id,
            source=self._merge_by_source_precedence(
                context,
                "source",
                media.source,
                prefer_incoming=prefer_incoming,
            ),
            provenance_source=self._merged_source_value(
                context,
                provenance_source,
                prefer_incoming=prefer_incoming,
                field_name="provenance_source",
            ),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                media.article_id,
                prefer_incoming=prefer_incoming,
            ),
            position=media.position,
            media_key=media.media_key,
            media_type=self._merge_by_source_precedence(
                context,
                "media_type",
                media.media_type,
                prefer_incoming=prefer_incoming,
            ),
            media_url=self._merge_by_source_precedence(
                context,
                "media_url",
                media.media_url,
                prefer_incoming=prefer_incoming,
            ),
            thumbnail_url=self._merge_by_source_precedence(
                context,
                "thumbnail_url",
                media.thumbnail_url,
                prefer_incoming=prefer_incoming,
            ),
            width=self._merge_by_source_precedence(
                context,
                "width",
                media.width,
                prefer_incoming=prefer_incoming,
            ),
            height=self._merge_by_source_precedence(
                context,
                "height",
                media.height,
                prefer_incoming=prefer_incoming,
            ),
            duration_millis=self._merge_by_source_precedence(
                context,
                "duration_millis",
                media.duration_millis,
                prefer_incoming=prefer_incoming,
            ),
            variants_json=self._merge_by_source_precedence(
                context,
                "variants_json",
                self._json_value(media.variants) if media.variants else None,
                prefer_incoming=prefer_incoming,
            ),
            download_state=self._coalesce_value(
                self._existing_value(context, "download_state"),
                "pending",
            ),
            local_path=self._existing_value(context, "local_path"),
            sha256=self._existing_value(context, "sha256"),
            byte_size=self._existing_value(context, "byte_size"),
            content_type=self._existing_value(context, "content_type"),
            thumbnail_local_path=self._existing_value(context, "thumbnail_local_path"),
            thumbnail_sha256=self._existing_value(context, "thumbnail_sha256"),
            thumbnail_byte_size=self._existing_value(context, "thumbnail_byte_size"),
            thumbnail_content_type=self._existing_value(context, "thumbnail_content_type"),
            downloaded_at=self._existing_value(context, "downloaded_at"),
            download_error=self._existing_value(context, "download_error"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(media.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_record(
        self,
        url: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url(url.url_hash)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url",
            url_hash=url.url_hash,
            url=url.canonical_url,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            final_url=self._merge_by_source_precedence(
                context,
                "final_url",
                url.final_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=url.canonical_url,
            url_host=self._merge_by_source_precedence(
                context,
                "url_host",
                url.host,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                url.title,
                prefer_incoming=prefer_incoming,
            ),
            description=self._merge_by_source_precedence(
                context,
                "description",
                url.description,
                prefer_incoming=prefer_incoming,
            ),
            site_name=self._merge_by_source_precedence(
                context,
                "site_name",
                url.site_name,
                prefer_incoming=prefer_incoming,
            ),
            unfurl_state=self._coalesce_value(
                self._existing_value(context, "unfurl_state"),
                "pending",
            ),
            last_fetched_at=self._existing_value(context, "last_fetched_at"),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _url_ref_record(
        self,
        url_ref: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_url_ref(url_ref.tweet_id, url_ref.position)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="url_ref",
            tweet_id=url_ref.tweet_id,
            position=url_ref.position,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            url_hash=self._merge_by_source_precedence(
                context,
                "url_hash",
                url_ref.url_hash,
                prefer_incoming=prefer_incoming,
            ),
            url=self._merge_by_source_precedence(
                context,
                "url",
                url_ref.short_url,
                prefer_incoming=prefer_incoming,
            ),
            expanded_url=self._merge_by_source_precedence(
                context,
                "expanded_url",
                url_ref.expanded_url,
                prefer_incoming=prefer_incoming,
            ),
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                url_ref.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            display_url=self._merge_by_source_precedence(
                context,
                "display_url",
                url_ref.display_url,
                prefer_incoming=prefer_incoming,
            ),
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(url_ref.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _article_record(
        self,
        article: Any,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_article(article.tweet_id)
        context = self._record_context(row_key, cursor=cursor)
        prefer_incoming = self._prefer_incoming_source(context, source)
        content_text = self._merge_by_source_precedence(
            context,
            "content_text",
            article.content_text,
            prefer_incoming=prefer_incoming,
        )
        status = (
            "body_present"
            if content_text
            else self._coalesce_value(
                article.status,
                self._existing_value(context, "status"),
                "preview_only",
            )
        )
        return self._record_with_context(
            context,
            row_key=row_key,
            record_type="article",
            tweet_id=article.tweet_id,
            source=self._merged_source_value(context, source, prefer_incoming=prefer_incoming),
            article_id=self._merge_by_source_precedence(
                context,
                "article_id",
                article.article_id,
                prefer_incoming=prefer_incoming,
            ),
            title=self._merge_by_source_precedence(
                context,
                "title",
                article.title,
                prefer_incoming=prefer_incoming,
            ),
            summary_text=self._merge_by_source_precedence(
                context,
                "summary_text",
                article.summary_text,
                prefer_incoming=prefer_incoming,
            ),
            content_text=content_text,
            canonical_url=self._merge_by_source_precedence(
                context,
                "canonical_url",
                article.canonical_url,
                prefer_incoming=prefer_incoming,
            ),
            published_at=self._merge_by_source_precedence(
                context,
                "published_at",
                article.published_at,
                prefer_incoming=prefer_incoming,
            ),
            status=status,
            raw_json=self._merge_by_source_precedence(
                context,
                "raw_json",
                self._json_value(article.raw_json),
                prefer_incoming=prefer_incoming,
            ),
        )

    def _buffer_secondary_graph(
        self,
        graph: ExtractedTweetGraph,
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer,
    ) -> None:
        for item in graph.tweet_objects.values():
            self._queue_record(
                self._tweet_object_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.relations.values():
            self._queue_record(
                self._tweet_relation_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.media.values():
            self._queue_record(
                self._media_record(item, provenance_source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.urls.values():
            self._queue_record(self._url_record(item, source=source, cursor=cursor), cursor=cursor)
        for item in graph.url_refs.values():
            self._queue_record(
                self._url_ref_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )
        for item in graph.articles.values():
            self._queue_record(
                self._article_record(item, source=source, cursor=cursor),
                cursor=cursor,
            )

    def _buffer_secondary_objects(
        self,
        tweets: list[TimelineTweet],
        *,
        source: str = LIVE_SOURCE,
        cursor: _PageBuffer,
    ) -> None:
        graph = ExtractedTweetGraph()
        for tweet in tweets:
            graph.merge(extract_secondary_objects(tweet.raw_json))
        self._buffer_secondary_graph(graph, source=source, cursor=cursor)

    def persist_page(
        self,
        *,
        operation: str,
        collection_type: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
        tweets: list[TimelineTweet],
        last_head_tweet_id: str | None,
        backfill_cursor: str | None,
        backfill_incomplete: bool,
    ) -> None:
        buffer = _PageBuffer()
        self.append_raw_capture(
            operation,
            cursor_in,
            cursor_out,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        for tweet in tweets:
            self.upsert_tweet(tweet, cursor=buffer)
            self.upsert_membership(
                tweet.tweet_id,
                collection_type,
                source=LIVE_SOURCE,
                sort_index=tweet.sort_index,
                cursor=buffer,
            )
        self._buffer_secondary_objects(tweets, source=LIVE_SOURCE, cursor=buffer)
        self.set_sync_state(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))

    def list_media_rows(
        self,
        *,
        states: set[str] | None = None,
        media_types: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        if media_types is not None and not media_types:
            return []
        where_expr = _and_expr(
            "record_type = 'media'",
            _state_filter_expr("download_state", states) if states is not None else "",
            _expr_in("media_type", media_types) if media_types is not None else "",
        )
        rows = self.table.search().where(where_expr).to_list()
        rows.sort(
            key=lambda row: (
                row.get("tweet_id") or "",
                row.get("position") if row.get("position") is not None else 1_000_000,
            )
        )
        return rows[:limit] if limit is not None else rows

    def update_media_download(
        self,
        row_key: str,
        *,
        download_state: str,
        local_path: str | None,
        sha256: str | None,
        byte_size: int | None,
        content_type: str | None,
        thumbnail_local_path: str | None,
        thumbnail_sha256: str | None,
        thumbnail_byte_size: int | None,
        thumbnail_content_type: str | None,
        downloaded_at: str | None,
        download_error: str | None,
    ) -> None:
        row = self._get_row(row_key)
        if row is None:
            raise KeyError(f"Media row not found: {row_key}")
        self.merge_rows(
            [
                self.build_media_download_update(
                    row,
                    download_state=download_state,
                    local_path=local_path,
                    sha256=sha256,
                    byte_size=byte_size,
                    content_type=content_type,
                    thumbnail_local_path=thumbnail_local_path,
                    thumbnail_sha256=thumbnail_sha256,
                    thumbnail_byte_size=thumbnail_byte_size,
                    thumbnail_content_type=thumbnail_content_type,
                    downloaded_at=downloaded_at,
                    download_error=download_error,
                )
            ]
        )

    def build_media_download_update(
        self,
        row: dict[str, Any],
        *,
        download_state: str,
        local_path: str | None,
        sha256: str | None,
        byte_size: int | None,
        content_type: str | None,
        thumbnail_local_path: str | None,
        thumbnail_sha256: str | None,
        thumbnail_byte_size: int | None,
        thumbnail_content_type: str | None,
        downloaded_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        updated = dict(row)
        updated.update(
            {
                "download_state": download_state,
                "local_path": local_path,
                "sha256": sha256,
                "byte_size": byte_size,
                "content_type": content_type,
                "thumbnail_local_path": thumbnail_local_path,
                "thumbnail_sha256": thumbnail_sha256,
                "thumbnail_byte_size": thumbnail_byte_size,
                "thumbnail_content_type": thumbnail_content_type,
                "downloaded_at": downloaded_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def list_url_rows(
        self,
        *,
        states: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if states is not None and not states:
            return []
        where_expr = _and_expr(
            "record_type = 'url'",
            _state_filter_expr("unfurl_state", states) if states is not None else "",
        )
        rows = self.table.search().where(where_expr).to_list()
        rows.sort(key=lambda row: row.get("canonical_url") or row.get("url") or "")
        return rows[:limit] if limit is not None else rows

    def update_url_unfurl(
        self,
        row_key: str,
        *,
        http_status: int | None,
        final_url: str | None,
        canonical_url: str | None,
        title: str | None,
        description: str | None,
        site_name: str | None,
        content_type: str | None,
        unfurl_state: str,
        last_fetched_at: str | None,
        download_error: str | None,
    ) -> None:
        row = self._get_row(row_key)
        if row is None:
            raise KeyError(f"URL row not found: {row_key}")
        self.merge_rows(
            [
                self.build_url_unfurl_update(
                    row,
                    http_status=http_status,
                    final_url=final_url,
                    canonical_url=canonical_url,
                    title=title,
                    description=description,
                    site_name=site_name,
                    content_type=content_type,
                    unfurl_state=unfurl_state,
                    last_fetched_at=last_fetched_at,
                    download_error=download_error,
                )
            ]
        )

    def build_url_unfurl_update(
        self,
        row: dict[str, Any],
        *,
        http_status: int | None,
        final_url: str | None,
        canonical_url: str | None,
        title: str | None,
        description: str | None,
        site_name: str | None,
        content_type: str | None,
        unfurl_state: str,
        last_fetched_at: str | None,
        download_error: str | None,
    ) -> dict[str, Any]:
        updated = dict(row)
        updated.update(
            {
                "http_status": http_status,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "url": canonical_url or updated.get("url"),
                "title": title,
                "description": description,
                "site_name": site_name,
                "content_type": content_type,
                "unfurl_state": unfurl_state,
                "last_fetched_at": last_fetched_at,
                "download_error": download_error,
                "updated_at": utc_now(),
            }
        )
        return updated

    def list_article_rows(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr(
            "record_type = 'article'",
            "(status IS NULL OR status != 'body_present')" if preview_only else "",
        )
        rows = self.table.search().where(where_expr).to_list()
        rows.sort(key=lambda row: row.get("tweet_id") or "")
        return rows[:limit] if limit is not None else rows

    def get_article_tweet_ids(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[str]:
        return [
            row["tweet_id"]
            for row in self.list_article_rows(preview_only=preview_only, limit=limit)
        ]

    def list_tweet_objects_for_enrichment(
        self, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        rows = (
            self.table.search()
            .where(
                "record_type = 'tweet_object' "
                "AND (enrichment_state = 'pending' OR enrichment_state = 'transient_failure')"
            )
            .to_list()
        )
        rows.sort(
            key=lambda row: (row.get("enrichment_checked_at") or "", row.get("tweet_id") or "")
        )
        return rows[:limit] if limit is not None else rows

    def update_tweet_object_enrichment(
        self,
        tweet_id: str,
        *,
        enrichment_state: str,
        enrichment_checked_at: str | None,
        enrichment_http_status: int | None,
        enrichment_reason: str | None,
    ) -> None:
        row = self._get_row(self._row_key_for_tweet_object(tweet_id))
        if row is None:
            raise KeyError(f"Tweet object row not found: {tweet_id}")
        updated = dict(row)
        updated.update(
            {
                "enrichment_state": enrichment_state,
                "enrichment_checked_at": enrichment_checked_at,
                "enrichment_http_status": enrichment_http_status,
                "enrichment_reason": enrichment_reason,
                "updated_at": utc_now(),
            }
        )
        self.merge_rows([updated])

    def _refresh_tweet_records_for_detail(
        self,
        tweet: TimelineTweet,
        *,
        cursor: _PageBuffer,
    ) -> None:
        legacy = tweet.raw_json.get("legacy") or {}
        rows = self._rows_for_values("tweet", "tweet_id", [tweet.tweet_id])
        now = utc_now()
        for row in rows:
            updated = dict(row)
            updated["text"] = self._coalesce_value(tweet.text, row.get("text"))
            updated["author_id"] = self._coalesce_value(tweet.author_id, row.get("author_id"))
            updated["author_username"] = self._coalesce_value(
                tweet.author_username,
                row.get("author_username"),
            )
            updated["author_display_name"] = self._coalesce_value(
                tweet.author_display_name,
                row.get("author_display_name"),
            )
            updated["created_at"] = self._coalesce_value(tweet.created_at, row.get("created_at"))
            updated["conversation_id"] = self._coalesce_value(
                legacy.get("conversation_id_str"),
                row.get("conversation_id"),
            )
            updated["lang"] = self._coalesce_value(legacy.get("lang"), row.get("lang"))
            updated["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(tweet.raw_json),
                row.get("note_tweet_text"),
            )
            updated["source"] = LIVE_SOURCE
            updated["deleted_at"] = None
            updated["raw_json"] = self._json_value(tweet.raw_json)
            updated["last_seen_at"] = now
            updated["synced_at"] = now
            cursor.records[updated["row_key"]] = updated

    def _refresh_tweet_records_for_details(
        self,
        tweets: list[TimelineTweet],
        *,
        cursor: _PageBuffer,
    ) -> None:
        for tweet in tweets:
            self._refresh_tweet_records_for_detail(tweet, cursor=cursor)

    def persist_tweet_detail(
        self,
        *,
        tweet: TimelineTweet,
        raw_json: dict[str, Any],
        http_status: int = 200,
    ) -> None:
        buffer = _PageBuffer()
        self.append_raw_capture(
            "TweetDetail",
            tweet.tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details([tweet], cursor=buffer)
        self._buffer_secondary_graph(
            extract_secondary_objects(tweet.raw_json),
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))

    def persist_thread_detail(
        self,
        *,
        focal_tweet_id: str,
        tweets: list[TimelineTweet],
        raw_json: dict[str, Any],
        http_status: int = 200,
    ) -> None:
        buffer = _PageBuffer()
        self.append_raw_capture(
            "ThreadExpandDetail",
            focal_tweet_id,
            None,
            http_status,
            raw_json,
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details(tweets, cursor=buffer)
        self._buffer_secondary_graph(
            extract_thread_objects([tweet.raw_json for tweet in tweets]),
            source=LIVE_SOURCE,
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))

    def list_membership_tweet_ids(self, *, limit: int | None = None) -> list[str]:
        rows = (
            self.table.search()
            .where("record_type = 'tweet'")
            .select(["tweet_id", "added_at"])
            .to_list()
        )
        rows.sort(key=lambda row: (row.get("added_at") or "", row.get("tweet_id") or ""))
        tweet_ids = [row["tweet_id"] for row in rows if row.get("tweet_id")]
        unique = list(dict.fromkeys(tweet_ids))
        return unique[:limit] if limit is not None else unique

    def list_known_tweet_ids(self) -> set[str]:
        tweet_rows = (
            self.table.search().where("record_type = 'tweet'").select(["tweet_id"]).to_list()
        )
        tweet_object_rows = (
            self.table.search().where("record_type = 'tweet_object'").select(["tweet_id"]).to_list()
        )
        tweet_ids = {
            row["tweet_id"]
            for row in tweet_rows + tweet_object_rows
            if isinstance(row.get("tweet_id"), str) and row["tweet_id"]
        }
        return tweet_ids

    def list_raw_capture_target_ids(
        self,
        operation: str,
        *,
        limit: int | None = None,
    ) -> list[str]:
        expr = (
            "record_type = 'raw_capture' "
            f"AND operation = {_expr_quote(operation)} "
            "AND cursor_in IS NOT NULL"
        )
        rows = self.table.search().where(expr).select(["captured_at", "cursor_in"]).to_list()
        rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        targets = [row["cursor_in"] for row in rows if isinstance(row.get("cursor_in"), str)]
        unique = list(dict.fromkeys(targets))
        return unique[:limit] if limit is not None else unique

    def list_url_ref_rows(self) -> list[dict[str, Any]]:
        rows = (
            self.table.search()
            .where("record_type = 'url_ref'")
            .select(["tweet_id", "position", "canonical_url", "expanded_url", "url"])
            .to_list()
        )
        rows.sort(
            key=lambda row: (
                row.get("tweet_id") or "",
                row.get("position") if row.get("position") is not None else 1_000_000,
            )
        )
        return rows

    def _serialize_media_row(self, row: dict[str, Any]) -> dict[str, Any]:
        variants = json.loads(row["variants_json"]) if row.get("variants_json") else []
        return {
            "media_key": row.get("media_key"),
            "type": row.get("media_type"),
            "source": row.get("source"),
            "article_id": row.get("article_id"),
            "position": row.get("position"),
            "url": row.get("media_url"),
            "thumbnail_url": row.get("thumbnail_url"),
            "width": row.get("width"),
            "height": row.get("height"),
            "duration_millis": row.get("duration_millis"),
            "variants": variants,
            "download": {
                "state": row.get("download_state") or "pending",
                "local_path": row.get("local_path"),
                "sha256": row.get("sha256"),
                "byte_size": row.get("byte_size"),
                "content_type": row.get("content_type"),
                "thumbnail_local_path": row.get("thumbnail_local_path"),
                "thumbnail_sha256": row.get("thumbnail_sha256"),
                "thumbnail_byte_size": row.get("thumbnail_byte_size"),
                "thumbnail_content_type": row.get("thumbnail_content_type"),
                "downloaded_at": row.get("downloaded_at"),
                "error": row.get("download_error"),
            },
        }

    def _serialize_article_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "article_id": row.get("article_id"),
            "title": row.get("title"),
            "summary_text": row.get("summary_text"),
            "content_text": row.get("content_text"),
            "canonical_url": row.get("canonical_url"),
            "published_at": row.get("published_at"),
            "status": row.get("status"),
        }

    def _serialize_url_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "url_hash": row.get("url_hash"),
            "canonical_url": row.get("canonical_url"),
            "expanded_url": row.get("expanded_url"),
            "final_url": row.get("final_url"),
            "host": row.get("url_host"),
            "title": row.get("title"),
            "description": row.get("description"),
            "site_name": row.get("site_name"),
            "content_type": row.get("content_type"),
            "http_status": row.get("http_status"),
            "unfurl_state": row.get("unfurl_state") or "pending",
            "last_fetched_at": row.get("last_fetched_at"),
            "error": row.get("download_error"),
        }

    def count_export_rows(self, collection: str) -> int:
        filter_expr = "record_type = 'tweet'"
        if collection != "all":
            filter_expr += f" AND collection_type = {_expr_quote(collection)}"
        return self.table.count_rows(filter_expr)

    def export_rows(
        self,
        collection: str,
        *,
        sort: str = "newest",
        limit: int | None = None,
        include_raw_json: bool = True,
    ) -> list[dict[str, Any]]:
        filter_expr = "record_type = 'tweet'"
        if collection != "all":
            filter_expr += f" AND collection_type = {_expr_quote(collection)}"
        tweet_columns = [
            "tweet_id",
            "text",
            "author_id",
            "author_username",
            "author_display_name",
            "created_at",
            "collection_type",
            "folder_id",
            "sort_index",
            "added_at",
            "synced_at",
        ]
        if include_raw_json:
            tweet_columns.append("raw_json")
        tweet_rows = self.table.search().where(filter_expr).select(tweet_columns).to_list()

        def sort_index_value(row: dict[str, Any]) -> int:
            raw = row.get("sort_index")
            if not raw:
                return 0
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        def oldest_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            created_at = _parse_created_at(row.get("created_at"))
            if created_at is not None:
                return (0, created_at, sort_index_value(row), row.get("tweet_id") or "")
            return (1, datetime.max, sort_index_value(row), row.get("tweet_id") or "")

        def newest_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
            created_at = _parse_created_at(row.get("created_at"))
            if created_at is not None:
                return (
                    0,
                    -created_at.timestamp(),
                    -sort_index_value(row),
                    row.get("tweet_id") or "",
                )
            return (1, 0.0, -sort_index_value(row), row.get("tweet_id") or "")

        sort_key = oldest_sort_key if sort == "oldest" else newest_sort_key
        sorted_rows = sorted(tweet_rows, key=sort_key)
        if limit is not None:
            sorted_rows = sorted_rows[:limit]

        tweet_ids = [row["tweet_id"] for row in sorted_rows if row.get("tweet_id")]
        media_rows = self._rows_for_values("media", "tweet_id", tweet_ids)
        article_rows = self._rows_for_values("article", "tweet_id", tweet_ids)
        url_ref_rows = self._rows_for_values("url_ref", "tweet_id", tweet_ids)
        url_hashes = [row["url_hash"] for row in url_ref_rows if row.get("url_hash")]
        url_rows = self._rows_for_values("url", "url_hash", url_hashes)

        media_by_tweet: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(
            media_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            media_by_tweet.setdefault(row["tweet_id"], []).append(self._serialize_media_row(row))

        articles_by_tweet = {
            row["tweet_id"]: self._serialize_article_row(row)
            for row in article_rows
            if row.get("tweet_id")
        }
        urls_by_hash = {
            row["url_hash"]: self._serialize_url_row(row) for row in url_rows if row.get("url_hash")
        }
        url_refs_by_tweet: dict[str, list[dict[str, Any]]] = {}
        for row in sorted(
            url_ref_rows,
            key=lambda item: (
                item.get("tweet_id") or "",
                item.get("position") if item.get("position") is not None else 1_000_000,
            ),
        ):
            resolved = urls_by_hash.get(row.get("url_hash"))
            url_refs_by_tweet.setdefault(row["tweet_id"], []).append(
                {
                    "position": row.get("position"),
                    "url_hash": row.get("url_hash"),
                    "short_url": row.get("url"),
                    "expanded_url": row.get("expanded_url"),
                    "display_url": row.get("display_url"),
                    "canonical_url": row.get("canonical_url"),
                    "resolved": resolved,
                }
            )

        exported = []
        for row in sorted_rows:
            tweet_media = media_by_tweet.get(row["tweet_id"], [])
            article = articles_by_tweet.get(row["tweet_id"])
            if article is not None:
                article["media"] = [
                    item
                    for item in tweet_media
                    if item.get("article_id") == article.get("article_id")
                    or str(item.get("source") or "").startswith("article_")
                ]
            exported.append(
                {
                    "tweet_id": row["tweet_id"],
                    "text": row["text"],
                    "author": {
                        "id": row["author_id"],
                        "username": row["author_username"],
                        "display_name": row["author_display_name"],
                    },
                    "created_at": row["created_at"],
                    "collection": {
                        "type": row["collection_type"],
                        "folder_id": row["folder_id"] or None,
                        "sort_index": row["sort_index"],
                        "added_at": row["added_at"],
                        "synced_at": row["synced_at"],
                    },
                    "media": tweet_media,
                    "urls": url_refs_by_tweet.get(row["tweet_id"], []),
                    "article": article,
                    "raw_json": json.loads(row["raw_json"]) if include_raw_json else None,
                }
            )
        return exported

    def counts(self) -> dict[str, int]:
        tweet_rows = self.table.count_rows("record_type = 'tweet'")
        return {
            "raw_captures": self.table.count_rows("record_type = 'raw_capture'"),
            "tweets": tweet_rows,
            "collections": tweet_rows,
            "tweet_objects": self.table.count_rows("record_type = 'tweet_object'"),
            "tweet_relations": self.table.count_rows("record_type = 'tweet_relation'"),
            "media": self.table.count_rows("record_type = 'media'"),
            "urls": self.table.count_rows("record_type = 'url'"),
            "url_refs": self.table.count_rows("record_type = 'url_ref'"),
            "articles": self.table.count_rows("record_type = 'article'"),
            "import_manifests": self.table.count_rows("record_type = 'import_manifest'"),
            "sync_state": self.table.count_rows("record_type = 'sync_state'"),
        }

    def archive_stats(self) -> ArchiveStats:
        counts = self.counts()
        tweet_rows = (
            self.table.search()
            .where("record_type = 'tweet'")
            .select(["tweet_id", "collection_type", "created_at"])
            .to_list()
        )
        capture_rows = (
            self.table.search()
            .where("record_type = 'raw_capture'")
            .select(["captured_at", "operation", "cursor_in"])
            .to_list()
        )
        sync_rows = (
            self.table.search()
            .where("record_type = 'sync_state'")
            .select(
                [
                    "collection_type",
                    "updated_at",
                    "backfill_cursor",
                    "backfill_incomplete",
                ]
            )
            .to_list()
        )
        tweet_object_rows = (
            self.table.search()
            .where("record_type = 'tweet_object'")
            .select(["tweet_id", "enrichment_state"])
            .to_list()
        )
        article_rows = (
            self.table.search().where("record_type = 'article'").select(["status"]).to_list()
        )
        url_ref_rows = (
            self.table.search()
            .where("record_type = 'url_ref'")
            .select(["tweet_id", "canonical_url", "expanded_url", "url"])
            .to_list()
        )

        oldest_created_dt: datetime | None = None
        newest_created_dt: datetime | None = None
        oldest_created_at: str | None = None
        newest_created_at: str | None = None
        unique_post_ids: set[str] = set()
        tweet_object_ids: set[str] = set()
        expanded_thread_targets: set[str] = set()
        collection_stats = {
            collection: ArchiveCollectionStats(collection_type=collection)
            for collection in SEARCH_COLLECTION_ORDER
        }

        def update_created_bounds(
            raw: str | None,
            *,
            collection: ArchiveCollectionStats | None = None,
        ) -> None:
            nonlocal oldest_created_dt, newest_created_dt, oldest_created_at, newest_created_at

            created_at = _parse_created_at(raw)
            if created_at is None:
                return
            if oldest_created_dt is None or created_at < oldest_created_dt:
                oldest_created_dt = created_at
                oldest_created_at = raw
            if newest_created_dt is None or created_at > newest_created_dt:
                newest_created_dt = created_at
                newest_created_at = raw
            if collection is None:
                return

            collection_oldest_dt = _parse_created_at(collection.oldest_created_at)
            if collection_oldest_dt is None or created_at < collection_oldest_dt:
                collection.oldest_created_at = raw
            collection_newest_dt = _parse_created_at(collection.newest_created_at)
            if collection_newest_dt is None or created_at > collection_newest_dt:
                collection.newest_created_at = raw

        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            if isinstance(tweet_id, str) and tweet_id:
                unique_post_ids.add(tweet_id)
            collection_type = row.get("collection_type")
            collection = None
            if isinstance(collection_type, str) and collection_type:
                collection = collection_stats.setdefault(
                    collection_type,
                    ArchiveCollectionStats(collection_type=collection_type),
                )
                collection.post_count += 1
            update_created_bounds(row.get("created_at"), collection=collection)

        pending_enrichment_count = 0
        transient_enrichment_failure_count = 0
        terminal_enrichment_count = 0
        done_enrichment_count = 0
        for row in tweet_object_rows:
            tweet_id = row.get("tweet_id")
            if isinstance(tweet_id, str) and tweet_id:
                tweet_object_ids.add(tweet_id)
            enrichment_state = row.get("enrichment_state")
            if enrichment_state == "pending":
                pending_enrichment_count += 1
            elif enrichment_state == "transient_failure":
                transient_enrichment_failure_count += 1
            elif enrichment_state == "terminal_unavailable":
                terminal_enrichment_count += 1
            elif enrichment_state == "done":
                done_enrichment_count += 1

        preview_article_count = sum(
            1
            for row in article_rows
            if not isinstance(row.get("status"), str) or row.get("status") != "body_present"
        )
        missing_tweet_object_count = len(unique_post_ids - tweet_object_ids)

        latest_capture_at: str | None = None
        for row in capture_rows:
            captured_at = row.get("captured_at")
            if (
                isinstance(captured_at, str)
                and captured_at
                and (latest_capture_at is None or captured_at > latest_capture_at)
            ):
                latest_capture_at = captured_at
            if (
                row.get("operation") == "ThreadExpandDetail"
                and isinstance(row.get("cursor_in"), str)
                and row["cursor_in"]
            ):
                expanded_thread_targets.add(row["cursor_in"])

        latest_sync_at: str | None = None
        for row in sync_rows:
            collection_type = row.get("collection_type")
            if not isinstance(collection_type, str) or not collection_type:
                continue
            collection = collection_stats.setdefault(
                collection_type,
                ArchiveCollectionStats(collection_type=collection_type),
            )
            updated_at = row.get("updated_at")
            if (
                isinstance(updated_at, str)
                and updated_at
                and (collection.last_synced_at is None or updated_at > collection.last_synced_at)
            ):
                collection.last_synced_at = updated_at
                backfill_cursor = row.get("backfill_cursor")
                collection.backfill_cursor = (
                    backfill_cursor
                    if isinstance(backfill_cursor, str) and backfill_cursor
                    else None
                )
                collection.backfill_incomplete = bool(row.get("backfill_incomplete"))
            if (
                isinstance(updated_at, str)
                and updated_at
                and (latest_sync_at is None or updated_at > latest_sync_at)
            ):
                latest_sync_at = updated_at

        ordered_collections = [
            collection_stats[name] for name in SEARCH_COLLECTION_ORDER if name in collection_stats
        ]
        ordered_collections.extend(
            collection_stats[name]
            for name in sorted(collection_stats)
            if name not in SEARCH_COLLECTION_ORDER
        )
        pending_thread_membership_count = len(unique_post_ids - expanded_thread_targets)
        known_tweet_ids = unique_post_ids | tweet_object_ids
        pending_linked_status_targets: set[str] = set()
        for row in url_ref_rows:
            target_id = None
            for field_name in ("canonical_url", "expanded_url", "url"):
                candidate = row.get(field_name)
                if isinstance(candidate, str):
                    target_id = extract_status_id_from_url(candidate)
                    if target_id:
                        break
            source_tweet_id = row.get("tweet_id")
            if (
                not target_id
                or target_id == source_tweet_id
                or target_id in expanded_thread_targets
                or target_id in known_tweet_ids
            ):
                continue
            pending_linked_status_targets.add(target_id)
        return ArchiveStats(
            owner_user_id=self.get_archive_owner_id(),
            unique_post_count=len(unique_post_ids),
            collection_membership_count=counts["tweets"],
            article_count=counts["articles"],
            raw_capture_count=counts["raw_captures"],
            media_count=counts["media"],
            url_count=counts["urls"],
            oldest_created_at=oldest_created_at,
            newest_created_at=newest_created_at,
            latest_capture_at=latest_capture_at,
            latest_sync_at=latest_sync_at,
            version_count=self.version_count(),
            collections=ordered_collections,
            pending_enrichment_count=pending_enrichment_count,
            transient_enrichment_failure_count=transient_enrichment_failure_count,
            terminal_enrichment_count=terminal_enrichment_count,
            done_enrichment_count=done_enrichment_count,
            preview_article_count=preview_article_count,
            missing_tweet_object_count=missing_tweet_object_count,
            expanded_thread_target_count=len(expanded_thread_targets),
            pending_thread_membership_count=pending_thread_membership_count,
            pending_thread_linked_status_count=len(pending_linked_status_targets),
        )

    def list_archive_import_media_paths(self) -> list[str]:
        rows = (
            self.table.search()
            .where("record_type = 'media' AND provenance_source = 'x_archive'")
            .select(["local_path", "thumbnail_local_path"])
            .to_list()
        )
        relative_paths = {
            path
            for row in rows
            for path in (row.get("local_path"), row.get("thumbnail_local_path"))
            if isinstance(path, str) and path
        }
        return sorted(relative_paths)

    def clear_archive_import_data(self) -> dict[str, int]:
        deletions: dict[str, int] = {}
        filters = {
            "raw_captures": "record_type = 'raw_capture' AND source = 'x_archive'",
            "tweets": "record_type = 'tweet' AND source = 'x_archive'",
            "tweet_objects": "record_type = 'tweet_object' AND source = 'x_archive'",
            "tweet_relations": "record_type = 'tweet_relation' AND source = 'x_archive'",
            "media": "record_type = 'media' AND provenance_source = 'x_archive'",
            "urls": "record_type = 'url' AND source = 'x_archive'",
            "url_refs": "record_type = 'url_ref' AND source = 'x_archive'",
            "articles": "record_type = 'article' AND source = 'x_archive'",
            "import_manifests": "record_type = 'import_manifest'",
        }
        for key, expr in filters.items():
            count = self.table.count_rows(expr)
            if count:
                self.table.delete(expr)
            deletions[key] = count
        return deletions

    def _flush_rehydrate_buffer(self, buffer: _PageBuffer) -> int:
        if not buffer.records:
            return 0
        secondary_records = sum(
            1
            for record in buffer.records.values()
            if record["record_type"] in SECONDARY_RECORD_TYPES
        )
        self._merge_records(list(buffer.records.values()))
        buffer.records.clear()
        buffer.existing_rows.clear()
        return secondary_records

    def rehydrate_from_raw_json(
        self, *, progress: Callable[[int], None] | None = None
    ) -> RehydrateResult:
        """Rebuild normalized tweet fields and secondary rows from stored raw_json."""
        rows = self.table.search().where("record_type = 'tweet'").to_list()
        if not rows:
            return RehydrateResult()
        buffer = _PageBuffer()
        batch_size = 500
        result = RehydrateResult()
        for row in rows:
            raw = json.loads(row["raw_json"])
            legacy = raw.get("legacy") or {}
            author_id, username, display_name = extract_author_fields(raw)
            canonical_text = extract_canonical_text(raw)
            updated_row = dict(row)
            changed = False
            desired_updates = {
                "text": self._coalesce_value(canonical_text, updated_row.get("text")),
                "author_id": self._coalesce_value(author_id, updated_row.get("author_id")),
                "author_username": self._coalesce_value(
                    username, updated_row.get("author_username")
                ),
                "author_display_name": self._coalesce_value(
                    display_name,
                    updated_row.get("author_display_name"),
                ),
                "created_at": self._coalesce_value(
                    legacy.get("created_at"), updated_row.get("created_at")
                ),
                "conversation_id": self._coalesce_value(
                    legacy.get("conversation_id_str"),
                    updated_row.get("conversation_id"),
                ),
                "lang": self._coalesce_value(legacy.get("lang"), updated_row.get("lang")),
                "source": updated_row.get("source") or LIVE_SOURCE,
            }
            desired_updates["note_tweet_text"] = self._coalesce_value(
                extract_note_tweet_text(raw),
                updated_row.get("note_tweet_text"),
            )
            for key, value in desired_updates.items():
                if updated_row.get(key) != value:
                    updated_row[key] = value
                    changed = True
            if changed:
                buffer.records[updated_row["row_key"]] = updated_row
                result.tweets_updated += 1
            self._buffer_secondary_graph(
                extract_secondary_objects(raw),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if progress:
                progress(1)
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        detail_rows = (
            self.table.search()
            .where(
                "record_type = 'raw_capture' "
                "AND (operation = 'TweetDetail' OR operation = 'ThreadExpandDetail')"
            )
            .to_list()
        )
        detail_rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        for row in detail_rows:
            raw_json = row.get("raw_json")
            if not isinstance(raw_json, str) or not raw_json:
                continue
            detail_payload = json.loads(raw_json)
            detail_tweets = parse_tweet_detail_tweets(detail_payload)
            if not detail_tweets:
                continue
            self._buffer_secondary_graph(
                extract_thread_objects([tweet.raw_json for tweet in detail_tweets]),
                source=self._normalized_source(row.get("source")),
                cursor=buffer,
            )
            if len(buffer.records) >= batch_size:
                result.secondary_records += self._flush_rehydrate_buffer(buffer)
        result.secondary_records += self._flush_rehydrate_buffer(buffer)
        return result

    def rehydrate_authors(self, *, progress: Callable[[int], None] | None = None) -> int:
        return self.rehydrate_from_raw_json(progress=progress).tweets_updated

    def count_unembedded(self) -> int:
        return self.table.count_rows("record_type = 'tweet' AND embedding IS NULL")

    def get_unembedded_tweets(self, *, batch_size: int = 100) -> list[list[dict[str, Any]]]:
        """Return unembedded tweet rows (full rows) in batches."""
        rows = self.table.search().where("record_type = 'tweet' AND embedding IS NULL").to_list()
        batches = []
        for i in range(0, len(rows), batch_size):
            batches.append(rows[i : i + batch_size])
        return batches

    def write_embeddings(self, rows: list[dict[str, Any]], embeddings: np.ndarray) -> None:
        """Write embedding vectors back into full rows via merge_insert."""
        for row, emb in zip(rows, embeddings, strict=True):
            row["embedding"] = emb.tolist()
        self._merge_records(rows)

    def clear_embeddings(self) -> None:
        """Clear all embeddings so they can be regenerated."""
        count = self.table.count_rows("record_type = 'tweet' AND embedding IS NOT NULL")
        if count == 0:
            return
        arrow_table = self.table.to_arrow()
        embedding_col_idx = arrow_table.schema.get_field_index("embedding")
        null_embeddings = pa.nulls(len(arrow_table), type=pa.list_(pa.float32(), EMBEDDING_DIM))
        new_table = arrow_table.set_column(embedding_col_idx, "embedding", null_embeddings)
        self.table = self.db.create_table(self.TABLE_NAME, new_table, mode="overwrite")

    def ensure_fts_index(self) -> None:
        """Create the tweet-text FTS index if it doesn't exist."""
        indices = self.table.list_indices()
        fts_exists = any(
            getattr(idx, "index_type", None) == "FTS"
            or getattr(idx, "columns", None) == [SEARCH_TEXT_FIELD]
            for idx in indices
        )
        if not fts_exists:
            self.table.create_fts_index(SEARCH_TEXT_FIELD, replace=True)

    def _search_score(self, row: dict[str, Any]) -> float | None:
        score = row.get("match_score")
        if score is None:
            score = row.get("_relevance_score")
        if score is None:
            score = row.get("_distance")
        if score is None:
            score = row.get("_score")
        if score is None:
            return None
        try:
            return float(score)
        except (TypeError, ValueError):
            return None

    def _query_tokens(self, query: str) -> list[str]:
        return [token.casefold() for token in query.split() if token]

    def _ordered_search_collections(self, collections: set[str]) -> list[str]:
        return sorted(
            collections,
            key=lambda value: (
                SEARCH_COLLECTION_ORDER.index(value)
                if value in SEARCH_COLLECTION_ORDER
                else len(SEARCH_COLLECTION_ORDER),
                value,
            ),
        )

    def _search_collection_expr(self, collections: set[str] | None) -> str:
        return _expr_in("collection_type", collections) if collections else ""

    def _dedupe_search_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_tweet_ids: set[str] = set()
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id or tweet_id in seen_tweet_ids:
                continue
            seen_tweet_ids.add(tweet_id)
            deduped.append(row)
        return deduped

    def _collect_search_context(
        self, tweet_ids: list[str]
    ) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
        tweet_rows = self._rows_for_values("tweet", "tweet_id", tweet_ids)
        tweet_object_rows = self._rows_for_values("tweet_object", "tweet_id", tweet_ids)
        collections_by_tweet_id: dict[str, set[str]] = {}
        metadata_by_tweet_id: dict[str, dict[str, Any]] = {}

        def merge_metadata(row: dict[str, Any]) -> None:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                return
            metadata = metadata_by_tweet_id.setdefault(tweet_id, {})
            for metadata_field in (
                "author_id",
                "author_username",
                "author_display_name",
                "created_at",
                "text",
                "note_tweet_text",
            ):
                if metadata.get(metadata_field) not in (None, ""):
                    continue
                value = row.get(metadata_field)
                if value not in (None, ""):
                    metadata[metadata_field] = value

        for row in tweet_object_rows:
            merge_metadata(row)
        for row in tweet_rows:
            tweet_id = row.get("tweet_id")
            collection_type = row.get("collection_type")
            if isinstance(tweet_id, str) and tweet_id and isinstance(collection_type, str):
                collections_by_tweet_id.setdefault(tweet_id, set()).add(collection_type)
            merge_metadata(row)

        ordered_collections = {
            tweet_id: self._ordered_search_collections(values)
            for tweet_id, values in collections_by_tweet_id.items()
        }
        return ordered_collections, metadata_by_tweet_id

    def _search_post_rows_fts(
        self, query: str, *, limit: int, collections: set[str] | None = None
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr("record_type = 'tweet'", self._search_collection_expr(collections))
        return self.table.search(query, query_type="fts").where(where_expr).limit(limit).to_list()

    def _search_article_rows_fts(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        rows = self.table.search().where("record_type = 'article'").to_list()
        matches: list[dict[str, Any]] = []
        for row in rows:
            haystack = " ".join(
                part
                for part in (
                    row.get("title"),
                    row.get("summary_text"),
                    row.get("content_text"),
                )
                if isinstance(part, str) and part
            )
            folded = haystack.casefold()
            if not all(token in folded for token in tokens):
                continue
            matched = dict(row)
            matched["match_score"] = float(sum(folded.count(token) for token in tokens))
            matches.append(matched)
        matches.sort(
            key=lambda row: (
                row.get("match_score") if row.get("match_score") is not None else float("-inf"),
                len(row.get("content_text") or row.get("summary_text") or row.get("title") or ""),
            ),
            reverse=True,
        )
        return matches[:limit]

    def _search_post_rows_vector(
        self, vector: list[float], *, limit: int, collections: set[str] | None = None
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr(
            "record_type = 'tweet' AND embedding IS NOT NULL",
            self._search_collection_expr(collections),
        )
        return (
            self.table.search(vector, vector_column_name="embedding", query_type="vector")
            .metric("cosine")
            .where(where_expr)
            .limit(limit)
            .to_list()
        )

    def _search_post_rows_hybrid(
        self,
        query: str,
        vector: list[float],
        *,
        limit: int,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_expr = _and_expr("record_type = 'tweet'", self._search_collection_expr(collections))
        return (
            self.table.search(vector_column_name="embedding", query_type="hybrid")
            .vector(vector)
            .text(query)
            .metric("cosine")
            .where(where_expr)
            .limit(limit)
            .to_list()
        )

    def _project_post_search_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            collections = collections_by_tweet_id.get(tweet_id)
            if not collections:
                fallback = row.get("collection_type")
                collections = [fallback] if isinstance(fallback, str) and fallback else []
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_POST,
                    "collections": collections,
                    "author_id": row.get("author_id") or metadata.get("author_id"),
                    "author_username": row.get("author_username")
                    or metadata.get("author_username"),
                    "created_at": row.get("created_at") or metadata.get("created_at"),
                    "text": self._coalesce_value(
                        row.get("note_tweet_text"),
                        row.get("text"),
                        metadata.get("note_tweet_text"),
                        metadata.get("text"),
                    ),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def _compose_article_search_text(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        summary_or_body = self._coalesce_value(row.get("summary_text"), row.get("content_text"))
        title = row.get("title")
        if title and summary_or_body:
            return f"{title}\n\n{summary_or_body}"
        return self._coalesce_value(
            title,
            summary_or_body,
            metadata.get("note_tweet_text"),
            metadata.get("text"),
        )

    def _project_article_search_results(
        self,
        rows: list[dict[str, Any]],
        *,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        tweet_ids = [
            tweet_id
            for row in rows
            if isinstance((tweet_id := row.get("tweet_id")), str) and tweet_id
        ]
        collections_by_tweet_id, metadata_by_tweet_id = self._collect_search_context(tweet_ids)
        results: list[dict[str, Any]] = []
        for row in rows:
            tweet_id = row.get("tweet_id")
            if not isinstance(tweet_id, str) or not tweet_id:
                continue
            result_collections = collections_by_tweet_id.get(tweet_id, [])
            if collections is not None and not set(result_collections).intersection(collections):
                continue
            metadata = metadata_by_tweet_id.get(tweet_id, {})
            results.append(
                {
                    "tweet_id": tweet_id,
                    "type": SEARCH_KIND_ARTICLE,
                    "collections": result_collections,
                    "author_id": metadata.get("author_id"),
                    "author_username": metadata.get("author_username"),
                    "created_at": metadata.get("created_at"),
                    "text": self._compose_article_search_text(row, metadata),
                    "match_score": self._search_score(row),
                }
            )
        return results

    def search_fts(
        self,
        query: str,
        *,
        limit: int = 20,
        types: set[str] | None = None,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over exposed search result types."""
        self.ensure_fts_index()
        fetch_limit = max(limit, 1)
        max_fetch_limit = max(limit * 8, 50)
        results: list[dict[str, Any]] = []
        while True:
            post_raw_rows: list[dict[str, Any]] = []
            article_raw_rows: list[dict[str, Any]] = []
            post_rows: list[dict[str, Any]] = []
            article_rows: list[dict[str, Any]] = []
            if types is None or SEARCH_KIND_POST in types:
                post_raw_rows = self._search_post_rows_fts(
                    query,
                    limit=fetch_limit,
                    collections=collections,
                )
                post_rows = self._dedupe_search_rows(post_raw_rows)
            if types is None or SEARCH_KIND_ARTICLE in types:
                article_raw_rows = self._search_article_rows_fts(query, limit=fetch_limit)
                article_rows = self._dedupe_search_rows(article_raw_rows)
            results = self._project_post_search_results(post_rows)
            results.extend(
                self._project_article_search_results(article_rows, collections=collections)
            )
            results.sort(
                key=lambda row: (
                    row["match_score"] if row.get("match_score") is not None else float("-inf")
                ),
                reverse=True,
            )
            exhausted = True
            if types is None or SEARCH_KIND_POST in types:
                exhausted = exhausted and len(post_raw_rows) < fetch_limit
            if types is None or SEARCH_KIND_ARTICLE in types:
                exhausted = exhausted and len(article_raw_rows) < fetch_limit
            if len(results) >= limit or fetch_limit >= max_fetch_limit or exhausted:
                return results[:limit]
            fetch_limit = min(fetch_limit * 2, max_fetch_limit)

    def search_vector(
        self,
        vector: list[float],
        *,
        limit: int = 20,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector similarity search over post embeddings."""
        fetch_limit = max(limit, 1)
        max_fetch_limit = max(limit * 8, 50)
        results: list[dict[str, Any]] = []
        while True:
            raw_rows = self._search_post_rows_vector(
                vector,
                limit=fetch_limit,
                collections=collections,
            )
            rows = self._dedupe_search_rows(raw_rows)
            results = self._project_post_search_results(rows)
            if (
                len(results) >= limit
                or fetch_limit >= max_fetch_limit
                or len(raw_rows) < fetch_limit
            ):
                return results[:limit]
            fetch_limit = min(fetch_limit * 2, max_fetch_limit)

    def search_hybrid(
        self,
        query: str,
        vector: list[float],
        *,
        limit: int = 20,
        collections: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid FTS + vector search over posts."""
        self.ensure_fts_index()
        fetch_limit = max(limit, 1)
        max_fetch_limit = max(limit * 8, 50)
        results: list[dict[str, Any]] = []
        while True:
            raw_rows = self._search_post_rows_hybrid(
                query,
                vector,
                limit=fetch_limit,
                collections=collections,
            )
            rows = self._dedupe_search_rows(raw_rows)
            results = self._project_post_search_results(rows)
            if (
                len(results) >= limit
                or fetch_limit >= max_fetch_limit
                or len(raw_rows) < fetch_limit
            ):
                return results[:limit]
            fetch_limit = min(fetch_limit * 2, max_fetch_limit)

    def has_embeddings(self) -> bool:
        return self.table.count_rows("record_type = 'tweet' AND embedding IS NOT NULL") > 0

    def version_count(self) -> int:
        return len(self.table.list_versions())

    def optimize(self) -> None:
        self.table.optimize(cleanup_older_than=timedelta(seconds=0))


def open_archive_store(paths: XDGPaths, *, create: bool) -> ArchiveStore | None:
    if not create and not paths.database_path.exists():
        return None
    try:
        return ArchiveStore(paths.database_path, create=create)
    except FileNotFoundError:
        return None
