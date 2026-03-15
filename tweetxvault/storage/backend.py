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

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import XDGPaths
from tweetxvault.exceptions import ArchiveOwnerMismatchError
from tweetxvault.extractor import (
    ExtractedTweetGraph,
    extract_author_fields,
    extract_canonical_text,
    extract_note_tweet_text,
    extract_secondary_objects,
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
        pa.field("url", pa.string()),
        pa.field("expanded_url", pa.string()),
        pa.field("canonical_url", pa.string()),
        pa.field("display_url", pa.string()),
        pa.field("url_host", pa.string()),
        pa.field("article_id", pa.string()),
        pa.field("title", pa.large_string()),
        pa.field("summary_text", pa.large_string()),
        pa.field("content_text", pa.large_string()),
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
            url=url.canonical_url,
            expanded_url=self._coalesce_value(
                url.expanded_url, existing["expanded_url"] if existing else None
            ),
            canonical_url=url.canonical_url,
            url_host=self._coalesce_value(url.host, existing["url_host"] if existing else None),
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

    def export_rows(self, collection: str, *, sort: str = "newest") -> list[dict[str, Any]]:
        filter_expr = "record_type = 'tweet'"
        if collection != "all":
            filter_expr += f" AND collection_type = {_expr_quote(collection)}"
        tweet_rows = self.table.search().where(filter_expr).to_list()

        def sort_key(row: dict[str, Any]) -> int:
            return int(row["sort_index"]) if row["sort_index"] else -1

        exported = []
        for row in sorted(tweet_rows, key=sort_key, reverse=(sort != "oldest")):
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
