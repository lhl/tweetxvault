# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.exceptions import ArchiveOwnerMismatchError

try:
    import lancedb
    import pyarrow as pa
except ImportError as exc:  # pragma: no cover - exercised by manual spike commands
    raise RuntimeError("The LanceDB spike requires `uv run --with lancedb python ...`.") from exc


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
    ]
)


class LanceArchiveStore:
    """Single-table LanceDB spike for archive semantics.

    The spike intentionally denormalizes tweet + membership rows into one table row
    per `(tweet_id, collection_type, folder_id)` so one `merge_insert` call can
    represent a whole page commit.
    """

    TABLE_NAME = "archive"

    def __init__(self, db_path: Path, *, create: bool) -> None:
        self.db_path = db_path
        if create:
            db_path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(db_path)
        table_names = set(self.db.table_names())
        if self.TABLE_NAME in table_names:
            self.table = self.db.open_table(self.TABLE_NAME)
        elif create:
            self.table = self.db.create_table(
                self.TABLE_NAME,
                schema=ARCHIVE_SCHEMA,
                mode="overwrite",
            )
        else:
            raise FileNotFoundError(f"LanceDB archive table not found at {db_path}")

    def close(self) -> None:
        return None

    def _row_key_for_tweet(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> str:
        return f"tweet:{collection_type}:{_folder_key(folder_id)}:{tweet_id}"

    def _row_key_for_sync_state(self, collection_type: str, folder_id: str | None = None) -> str:
        return f"sync_state:{collection_type}:{_folder_key(folder_id)}"

    def _row_key_for_metadata(self, key: str) -> str:
        return f"metadata:{key}"

    def _record(self, **overrides: Any) -> dict[str, Any]:
        row = {field.name: None for field in ARCHIVE_SCHEMA}
        row.update(overrides)
        return row

    def _merge_records(self, records: list[dict[str, Any]]) -> None:
        payload = pa.Table.from_pylist(records, schema=ARCHIVE_SCHEMA)
        (
            self.table.merge_insert("row_key")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(payload)
        )

    def _all_rows(self) -> list[dict[str, Any]]:
        return self.table.to_arrow().to_pylist()

    def _get_row(self, row_key: str) -> dict[str, Any] | None:
        rows = self.table.search().where(f"row_key = {_expr_quote(row_key)}").limit(1).to_list()
        return rows[0] if rows else None

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
    ) -> dict[str, Any]:
        capture_id = str(uuid.uuid4())
        return self._record(
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

    def upsert_tweet_record(
        self,
        tweet: TimelineTweet,
        collection_type: str,
        *,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self._get_row(
            self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id)
        )
        now = utc_now()
        return self._record(
            row_key=self._row_key_for_tweet(tweet.tweet_id, collection_type, folder_id),
            record_type="tweet",
            tweet_id=tweet.tweet_id,
            collection_type=collection_type,
            folder_id=_folder_key(folder_id),
            sort_index=tweet.sort_index,
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

    def set_sync_state(
        self,
        collection_type: str,
        *,
        last_head_tweet_id: str | None = None,
        backfill_cursor: str | None = None,
        backfill_incomplete: bool = False,
        folder_id: str | None = None,
    ) -> None:
        self._merge_records(
            [
                self._record(
                    row_key=self._row_key_for_sync_state(collection_type, folder_id),
                    record_type="sync_state",
                    collection_type=collection_type,
                    folder_id=_folder_key(folder_id),
                    last_head_tweet_id=last_head_tweet_id,
                    backfill_cursor=backfill_cursor,
                    backfill_incomplete=backfill_incomplete,
                    updated_at=utc_now(),
                )
            ]
        )

    def reset_sync_state(self, collection_type: str, folder_id: str | None = None) -> None:
        row_key = self._row_key_for_sync_state(collection_type, folder_id)
        self.table.delete(f"row_key = {_expr_quote(row_key)}")

    def has_membership(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> bool:
        row_key = self._row_key_for_tweet(tweet_id, collection_type, folder_id)
        return self._get_row(row_key) is not None

    def get_archive_owner_id(self) -> str | None:
        row = self._get_row(self._row_key_for_metadata("owner_user_id"))
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str) -> None:
        self._merge_records(
            [
                self._record(
                    row_key=self._row_key_for_metadata("owner_user_id"),
                    record_type="metadata",
                    key="owner_user_id",
                    value=user_id,
                    updated_at=utc_now(),
                )
            ]
        )

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
        records = [
            self.append_raw_capture(operation, cursor_in, cursor_out, http_status, raw_json),
            self._record(
                row_key=self._row_key_for_sync_state(collection_type),
                record_type="sync_state",
                collection_type=collection_type,
                folder_id="",
                last_head_tweet_id=last_head_tweet_id,
                backfill_cursor=backfill_cursor,
                backfill_incomplete=backfill_incomplete,
                updated_at=utc_now(),
            ),
        ]
        records.extend(self.upsert_tweet_record(tweet, collection_type) for tweet in tweets)
        self._merge_records(records)

    def export_rows(self, collection: str) -> list[dict[str, Any]]:
        rows = [row for row in self._all_rows() if row["record_type"] == "tweet"]
        if collection != "all":
            rows = [row for row in rows if row["collection_type"] == collection]

        def sort_key(row: dict[str, Any]) -> tuple[str, int]:
            sort_index = int(row["sort_index"]) if row["sort_index"] else -1
            return (row["synced_at"] or "", sort_index)

        exported = []
        for row in sorted(rows, key=sort_key, reverse=True):
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
        counts = {"raw_captures": 0, "tweets": 0, "collections": 0, "sync_state": 0}
        for row in self._all_rows():
            if row["record_type"] == "raw_capture":
                counts["raw_captures"] += 1
            elif row["record_type"] == "tweet":
                counts["tweets"] += 1
                counts["collections"] += 1
            elif row["record_type"] == "sync_state":
                counts["sync_state"] += 1
        return counts

    def version_count(self) -> int:
        return len(self.table.list_versions())
