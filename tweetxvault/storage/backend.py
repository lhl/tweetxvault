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
    records: list[dict[str, Any]] = field(default_factory=list)
    pending_tweets: dict[str, TimelineTweet] = field(default_factory=dict)


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
        pa.field("raw_json", pa.large_string()),
        pa.field("first_seen_at", pa.string()),
        pa.field("last_seen_at", pa.string()),
        pa.field("added_at", pa.string()),
        pa.field("synced_at", pa.string()),
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
        existing_names = {f.name for f in self.table.schema}
        if "embedding" not in existing_names:
            arrow_table = self.table.to_arrow()
            null_embeddings = pa.nulls(len(arrow_table), type=pa.list_(pa.float32(), EMBEDDING_DIM))
            new_table = arrow_table.append_column("embedding", null_embeddings)
            self.table = self.db.create_table(self.TABLE_NAME, new_table, mode="overwrite")

    def close(self) -> None:
        return None

    def _record(self, **overrides: Any) -> dict[str, Any]:
        record = {field.name: None for field in ARCHIVE_SCHEMA}
        record.update(overrides)
        return record

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

    def _get_row(self, row_key: str) -> dict[str, Any] | None:
        rows = self.table.search().where(f"row_key = {_expr_quote(row_key)}").limit(1).to_list()
        return rows[0] if rows else None

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
        if cursor is None:
            self._merge_records([record])
        else:
            cursor.records.append(record)
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
    ) -> dict[str, Any]:
        row_key = self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        existing = self._get_row(row_key)
        now = utc_now()
        return self._record(
            row_key=row_key,
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            sort_index=sort_index,
            text=tweet.text,
            author_id=tweet.author_id,
            author_username=tweet.author_username,
            author_display_name=tweet.author_display_name,
            created_at=tweet.created_at,
            raw_json=json.dumps(tweet.raw_json, sort_keys=True),
            first_seen_at=existing["first_seen_at"] if existing else now,
            last_seen_at=now,
            added_at=existing["added_at"] if existing else now,
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
        cursor.records.append(
            self._tweet_record(
                tweet,
                collection_type,
                sort_index=sort_index,
                folder_id=folder_id,
            )
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
        if cursor is None:
            self._merge_records([record])
        else:
            cursor.records.append(record)

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
        if cursor is None:
            self._merge_records([record])
        else:
            cursor.records.append(record)

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
        self.set_sync_state(
            collection_type,
            last_head_tweet_id=last_head_tweet_id,
            backfill_cursor=backfill_cursor,
            backfill_incomplete=backfill_incomplete,
            cursor=buffer,
        )
        self._merge_records(buffer.records)

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
            "sync_state": self.table.count_rows("record_type = 'sync_state'"),
        }

    def rehydrate_authors(self, *, progress: Callable[[int], None] | None = None) -> int:
        """Re-extract author fields from raw_json for tweets missing usernames."""
        rows = (
            self.table.search().where("record_type = 'tweet' AND author_username IS NULL").to_list()
        )
        if not rows:
            return 0
        batch: list[dict[str, Any]] = []
        batch_size = 500
        updated = 0
        for row in rows:
            raw = json.loads(row["raw_json"])
            core = raw.get("core") or {}
            user_result = core.get("user_results", {}).get("result", {})
            user_legacy = user_result.get("legacy") or {}
            user_core = user_result.get("core") or {}
            username = user_legacy.get("screen_name") or user_core.get("screen_name")
            display_name = user_legacy.get("name") or user_core.get("name")
            author_id = user_result.get("rest_id")
            if not username and not display_name:
                if progress:
                    progress(1)
                continue
            row["author_username"] = username
            row["author_display_name"] = display_name
            if author_id:
                row["author_id"] = author_id
            batch.append(row)
            updated += 1
            if progress:
                progress(1)
            if len(batch) >= batch_size:
                self._merge_records(batch)
                batch = []
        if batch:
            self._merge_records(batch)
        return updated

    def count_unembedded(self) -> int:
        return self.table.count_rows("record_type = 'tweet' AND embedding IS NULL")

    def get_unembedded_tweets(self, *, batch_size: int = 100) -> list[list[dict[str, Any]]]:
        """Return unembedded tweet rows in batches."""
        rows = (
            self.table.search()
            .where("record_type = 'tweet' AND embedding IS NULL")
            .select(["row_key", "text", "author_username"])
            .to_list()
        )
        batches = []
        for i in range(0, len(rows), batch_size):
            batches.append(rows[i : i + batch_size])
        return batches

    def write_embeddings(self, row_keys: list[str], embeddings: np.ndarray) -> None:
        """Write embedding vectors for the given row_keys."""
        updates = pa.table(
            {
                "row_key": row_keys,
                "embedding": [emb.tolist() for emb in embeddings],
            },
            schema=pa.schema(
                [
                    pa.field("row_key", pa.string()),
                    pa.field("embedding", pa.list_(pa.float32(), EMBEDDING_DIM)),
                ]
            ),
        )
        self.table.merge_insert("row_key").when_matched_update_all(
            "target.embedding IS NULL"
        ).execute(updates)

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
            idx.get("index_type") == "FTS" or idx.get("columns") == ["text"] for idx in indices
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
            self.table.search(query, query_type="hybrid")
            .vector(vector)
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
