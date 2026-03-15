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
from datetime import UTC, datetime, timedelta
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
    extract_thread_objects,
)


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _folder_key(folder_id: str | None) -> str:
    return folder_id or ""


def _expr_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
class RehydrateResult:
    tweets_updated: int = 0
    secondary_records: int = 0


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
        pa.field("conversation_id", pa.string()),
        pa.field("lang", pa.string()),
        pa.field("note_tweet_text", pa.large_string()),
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

    def _row_key_for_tweet(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> str:
        return f"tweet:{collection_type}:{_folder_key(folder_id)}:{tweet_id}"

    def _row_key_for_sync_state(self, collection_type: str, folder_id: str | None = None) -> str:
        return f"sync_state:{collection_type}:{_folder_key(folder_id)}"

    def _row_key_for_metadata(self, key: str) -> str:
        return f"metadata:{key}"

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

    def _capture_record(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        capture_id = str(uuid.uuid4())
        return capture_id, self._record(
            row_key=f"raw_capture:{capture_id}",
            record_type="raw_capture",
            operation=operation,
            cursor_in=cursor_in,
            cursor_out=cursor_out,
            captured_at=utc_now(),
            http_status=http_status,
            source="api",
            raw_json=json.dumps(raw_json, sort_keys=True),
        )

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
        *,
        cursor: _PageBuffer | None = None,
    ) -> str:
        capture_id, record = self._capture_record(
            operation, cursor_in, cursor_out, http_status, raw_json
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
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        legacy = tweet.raw_json.get("legacy") or {}
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor)
        now = utc_now()
        return self._record(
            row_key=row_key,
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            sort_index=sort_index,
            text=self._coalesce_value(tweet.text, existing["text"] if existing else None),
            author_id=self._coalesce_value(
                tweet.author_id, existing["author_id"] if existing else None
            ),
            author_username=self._coalesce_value(
                tweet.author_username, existing["author_username"] if existing else None
            ),
            author_display_name=self._coalesce_value(
                tweet.author_display_name, existing["author_display_name"] if existing else None
            ),
            created_at=self._coalesce_value(
                tweet.created_at, existing["created_at"] if existing else None
            ),
            conversation_id=self._coalesce_value(
                legacy.get("conversation_id_str"),
                existing["conversation_id"] if existing else None,
            ),
            lang=self._coalesce_value(legacy.get("lang"), existing["lang"] if existing else None),
            note_tweet_text=self._coalesce_value(
                extract_note_tweet_text(tweet.raw_json),
                existing["note_tweet_text"] if existing else None,
            ),
            raw_json=self._coalesce_value(
                self._json_value(tweet.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def upsert_membership(
        self,
        tweet_id: str,
        collection_type: str,
        *,
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

    def _tweet_object_record(
        self,
        tweet: Any,
        *,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_object(tweet.tweet_id)
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return self._record(
            row_key=row_key,
            record_type="tweet_object",
            tweet_id=tweet.tweet_id,
            text=self._coalesce_value(tweet.text, existing["text"] if existing else None) or "",
            author_id=self._coalesce_value(
                tweet.author_id, existing["author_id"] if existing else None
            ),
            author_username=self._coalesce_value(
                tweet.author_username, existing["author_username"] if existing else None
            ),
            author_display_name=self._coalesce_value(
                tweet.author_display_name, existing["author_display_name"] if existing else None
            ),
            created_at=self._coalesce_value(
                tweet.created_at, existing["created_at"] if existing else None
            ),
            conversation_id=self._coalesce_value(
                tweet.conversation_id, existing["conversation_id"] if existing else None
            ),
            lang=self._coalesce_value(tweet.lang, existing["lang"] if existing else None),
            note_tweet_text=self._coalesce_value(
                tweet.note_tweet_text, existing["note_tweet_text"] if existing else None
            ),
            raw_json=self._coalesce_value(
                self._json_value(tweet.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _tweet_relation_record(
        self,
        relation: Any,
        *,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet_relation(
            relation.source_tweet_id,
            relation.relation_type,
            relation.target_tweet_id,
        )
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return self._record(
            row_key=row_key,
            record_type="tweet_relation",
            tweet_id=relation.source_tweet_id,
            relation_type=relation.relation_type,
            target_tweet_id=relation.target_tweet_id,
            raw_json=self._coalesce_value(
                self._json_value(relation.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _media_record(self, media: Any, *, cursor: _PageBuffer | None = None) -> dict[str, Any]:
        row_key = self._row_key_for_media(media.tweet_id, media.media_key)
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return self._record(
            row_key=row_key,
            record_type="media",
            tweet_id=media.tweet_id,
            source=self._coalesce_value(media.source, existing["source"] if existing else None),
            article_id=self._coalesce_value(
                media.article_id, existing["article_id"] if existing else None
            ),
            position=media.position,
            media_key=media.media_key,
            media_type=self._coalesce_value(
                media.media_type, existing["media_type"] if existing else None
            ),
            media_url=self._coalesce_value(
                media.media_url, existing["media_url"] if existing else None
            ),
            thumbnail_url=self._coalesce_value(
                media.thumbnail_url, existing["thumbnail_url"] if existing else None
            ),
            width=self._coalesce_value(media.width, existing["width"] if existing else None),
            height=self._coalesce_value(media.height, existing["height"] if existing else None),
            duration_millis=self._coalesce_value(
                media.duration_millis, existing["duration_millis"] if existing else None
            ),
            variants_json=self._coalesce_value(
                self._json_value(media.variants) if media.variants else None,
                existing["variants_json"] if existing else None,
            ),
            download_state=self._coalesce_value(
                existing["download_state"] if existing else None,
                "pending",
            ),
            local_path=existing["local_path"] if existing else None,
            sha256=existing["sha256"] if existing else None,
            byte_size=existing["byte_size"] if existing else None,
            content_type=existing["content_type"] if existing else None,
            thumbnail_local_path=existing["thumbnail_local_path"] if existing else None,
            thumbnail_sha256=existing["thumbnail_sha256"] if existing else None,
            thumbnail_byte_size=existing["thumbnail_byte_size"] if existing else None,
            thumbnail_content_type=existing["thumbnail_content_type"] if existing else None,
            downloaded_at=existing["downloaded_at"] if existing else None,
            download_error=existing["download_error"] if existing else None,
            raw_json=self._coalesce_value(
                self._json_value(media.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _url_record(self, url: Any, *, cursor: _PageBuffer | None = None) -> dict[str, Any]:
        row_key = self._row_key_for_url(url.url_hash)
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return self._record(
            row_key=row_key,
            record_type="url",
            url_hash=url.url_hash,
            url=url.canonical_url,
            expanded_url=self._coalesce_value(
                url.expanded_url, existing["expanded_url"] if existing else None
            ),
            final_url=self._coalesce_value(
                url.final_url, existing["final_url"] if existing else None
            ),
            canonical_url=url.canonical_url,
            url_host=self._coalesce_value(url.host, existing["url_host"] if existing else None),
            title=self._coalesce_value(url.title, existing["title"] if existing else None),
            description=self._coalesce_value(
                url.description, existing["description"] if existing else None
            ),
            site_name=self._coalesce_value(
                url.site_name, existing["site_name"] if existing else None
            ),
            unfurl_state=self._coalesce_value(
                existing["unfurl_state"] if existing else None,
                "pending",
            ),
            last_fetched_at=existing["last_fetched_at"] if existing else None,
            raw_json=self._coalesce_value(
                self._json_value(url.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _url_ref_record(self, url_ref: Any, *, cursor: _PageBuffer | None = None) -> dict[str, Any]:
        row_key = self._row_key_for_url_ref(url_ref.tweet_id, url_ref.position)
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        return self._record(
            row_key=row_key,
            record_type="url_ref",
            tweet_id=url_ref.tweet_id,
            position=url_ref.position,
            url_hash=self._coalesce_value(
                url_ref.url_hash, existing["url_hash"] if existing else None
            ),
            url=self._coalesce_value(url_ref.short_url, existing["url"] if existing else None),
            expanded_url=self._coalesce_value(
                url_ref.expanded_url, existing["expanded_url"] if existing else None
            ),
            canonical_url=self._coalesce_value(
                url_ref.canonical_url, existing["canonical_url"] if existing else None
            ),
            display_url=self._coalesce_value(
                url_ref.display_url, existing["display_url"] if existing else None
            ),
            raw_json=self._coalesce_value(
                self._json_value(url_ref.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _article_record(
        self,
        article: Any,
        *,
        cursor: _PageBuffer | None = None,
    ) -> dict[str, Any]:
        row_key = self._row_key_for_article(article.tweet_id)
        now = utc_now()
        existing, first_seen_at, added_at = self._row_timestamps(row_key, cursor=cursor, now=now)
        content_text = self._coalesce_value(
            article.content_text, existing["content_text"] if existing else None
        )
        status = (
            "body_present"
            if content_text
            else self._coalesce_value(
                article.status,
                existing["status"] if existing else None,
                "preview_only",
            )
        )
        return self._record(
            row_key=row_key,
            record_type="article",
            tweet_id=article.tweet_id,
            article_id=self._coalesce_value(
                article.article_id, existing["article_id"] if existing else None
            ),
            title=self._coalesce_value(article.title, existing["title"] if existing else None),
            summary_text=self._coalesce_value(
                article.summary_text, existing["summary_text"] if existing else None
            ),
            content_text=content_text,
            canonical_url=self._coalesce_value(
                article.canonical_url, existing["canonical_url"] if existing else None
            ),
            published_at=self._coalesce_value(
                article.published_at, existing["published_at"] if existing else None
            ),
            status=status,
            raw_json=self._coalesce_value(
                self._json_value(article.raw_json), existing["raw_json"] if existing else None
            ),
            first_seen_at=first_seen_at,
            last_seen_at=now,
            added_at=added_at,
            synced_at=now,
        )

    def _buffer_secondary_graph(
        self,
        graph: ExtractedTweetGraph,
        *,
        cursor: _PageBuffer,
    ) -> None:
        for item in graph.tweet_objects.values():
            self._queue_record(self._tweet_object_record(item, cursor=cursor), cursor=cursor)
        for item in graph.relations.values():
            self._queue_record(self._tweet_relation_record(item, cursor=cursor), cursor=cursor)
        for item in graph.media.values():
            self._queue_record(self._media_record(item, cursor=cursor), cursor=cursor)
        for item in graph.urls.values():
            self._queue_record(self._url_record(item, cursor=cursor), cursor=cursor)
        for item in graph.url_refs.values():
            self._queue_record(self._url_ref_record(item, cursor=cursor), cursor=cursor)
        for item in graph.articles.values():
            self._queue_record(self._article_record(item, cursor=cursor), cursor=cursor)

    def _buffer_secondary_objects(
        self,
        tweets: list[TimelineTweet],
        *,
        cursor: _PageBuffer,
    ) -> None:
        graph = ExtractedTweetGraph()
        for tweet in tweets:
            graph.merge(extract_secondary_objects(tweet.raw_json))
        self._buffer_secondary_graph(graph, cursor=cursor)

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
            cursor=buffer,
        )
        for tweet in tweets:
            self.upsert_tweet(tweet, cursor=buffer)
            self.upsert_membership(
                tweet.tweet_id,
                collection_type,
                sort_index=tweet.sort_index,
                cursor=buffer,
            )
        self._buffer_secondary_objects(tweets, cursor=buffer)
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
        rows = self.table.search().where("record_type = 'media'").to_list()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            state = row.get("download_state") or "pending"
            media_type = row.get("media_type")
            if states is not None and state not in states:
                continue
            if media_types is not None and media_type not in media_types:
                continue
            filtered.append(row)
        filtered.sort(
            key=lambda row: (
                row.get("tweet_id") or "",
                row.get("position") if row.get("position") is not None else 1_000_000,
            )
        )
        return filtered[:limit] if limit is not None else filtered

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
        self._merge_records([updated])

    def list_url_rows(
        self,
        *,
        states: set[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.table.search().where("record_type = 'url'").to_list()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            state = row.get("unfurl_state") or "pending"
            if states is not None and state not in states:
                continue
            filtered.append(row)
        filtered.sort(key=lambda row: row.get("canonical_url") or row.get("url") or "")
        return filtered[:limit] if limit is not None else filtered

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
        self._merge_records([updated])

    def list_article_rows(
        self,
        *,
        preview_only: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.table.search().where("record_type = 'article'").to_list()
        if preview_only:
            rows = [row for row in rows if row.get("status") != "body_present"]
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
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details([tweet], cursor=buffer)
        self._buffer_secondary_graph(extract_secondary_objects(tweet.raw_json), cursor=buffer)
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
            cursor=buffer,
        )
        self._refresh_tweet_records_for_details(tweets, cursor=buffer)
        self._buffer_secondary_graph(
            extract_thread_objects([tweet.raw_json for tweet in tweets]),
            cursor=buffer,
        )
        self._merge_records(list(buffer.records.values()))

    def list_membership_tweet_ids(self, *, limit: int | None = None) -> list[str]:
        rows = self.table.search().where("record_type = 'tweet'").to_list()
        rows.sort(key=lambda row: (row.get("added_at") or "", row.get("tweet_id") or ""))
        tweet_ids = [row["tweet_id"] for row in rows if row.get("tweet_id")]
        unique = list(dict.fromkeys(tweet_ids))
        return unique[:limit] if limit is not None else unique

    def list_known_tweet_ids(self) -> set[str]:
        tweet_rows = self.table.search().where("record_type = 'tweet'").to_list()
        tweet_object_rows = self.table.search().where("record_type = 'tweet_object'").to_list()
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
        rows = self.table.search().where(expr).to_list()
        rows.sort(key=lambda row: (row.get("captured_at") or "", row.get("cursor_in") or ""))
        targets = [row["cursor_in"] for row in rows if isinstance(row.get("cursor_in"), str)]
        unique = list(dict.fromkeys(targets))
        return unique[:limit] if limit is not None else unique

    def list_url_ref_rows(self) -> list[dict[str, Any]]:
        rows = self.table.search().where("record_type = 'url_ref'").to_list()
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

    def export_rows(self, collection: str, *, sort: str = "newest") -> list[dict[str, Any]]:
        filter_expr = "record_type = 'tweet'"
        if collection != "all":
            filter_expr += f" AND collection_type = {_expr_quote(collection)}"
        tweet_rows = self.table.search().where(filter_expr).to_list()

        def sort_key(row: dict[str, Any]) -> int:
            return int(row["sort_index"]) if row["sort_index"] else -1

        tweet_ids = [row["tweet_id"] for row in tweet_rows if row.get("tweet_id")]
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
        for row in sorted(tweet_rows, key=sort_key, reverse=(sort != "oldest")):
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
                    "raw_json": json.loads(row["raw_json"]),
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
            "sync_state": self.table.count_rows("record_type = 'sync_state'"),
        }

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
            self._buffer_secondary_graph(extract_secondary_objects(raw), cursor=buffer)
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
        """Create FTS index on text column if it doesn't exist."""
        indices = self.table.list_indices()
        fts_exists = any(
            getattr(idx, "index_type", None) == "FTS" or getattr(idx, "columns", None) == ["text"]
            for idx in indices
        )
        if not fts_exists:
            self.table.create_fts_index("text", replace=True)

    def search_fts(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search over tweet text."""
        self.ensure_fts_index()
        return (
            self.table.search(query, query_type="fts")
            .where("record_type = 'tweet'")
            .limit(limit)
            .to_list()
        )

    def search_vector(self, vector: list[float], *, limit: int = 20) -> list[dict[str, Any]]:
        """Vector similarity search over tweet embeddings."""
        return (
            self.table.search(vector, query_type="vector")
            .where("record_type = 'tweet' AND embedding IS NOT NULL")
            .limit(limit)
            .to_list()
        )

    def search_hybrid(
        self, query: str, vector: list[float], *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Hybrid FTS + vector search with reranking."""
        self.ensure_fts_index()
        return (
            self.table.search(query_type="hybrid")
            .vector(vector)
            .text(query)
            .where("record_type = 'tweet'")
            .limit(limit)
            .to_list()
        )

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
