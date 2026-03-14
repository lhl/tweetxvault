"""Storage backend.

Task 0 showed embedded SeekDB failing to initialize in this environment, so the MVP
stores data in SQLite while keeping the schema and write semantics documented in PLAN.md.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tweetxvault.client.timelines import TimelineTweet
from tweetxvault.config import XDGPaths
from tweetxvault.exceptions import ArchiveOwnerMismatchError


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _folder_key(folder_id: str | None) -> str:
    return folder_id or ""


@dataclass(slots=True)
class SyncState:
    collection_type: str
    last_head_tweet_id: str | None = None
    backfill_cursor: str | None = None
    backfill_incomplete: bool = False
    updated_at: str | None = None


class ArchiveStore:
    def __init__(self, db_path: Path, *, create: bool) -> None:
        self.db_path = db_path
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        if create:
            self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_captures (
                id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                cursor_in TEXT,
                cursor_out TEXT,
                captured_at TEXT NOT NULL,
                http_status INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'api',
                raw_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tweets (
                tweet_id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                author_id TEXT,
                author_username TEXT,
                author_display_name TEXT,
                created_at TEXT,
                raw_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collections (
                tweet_id TEXT NOT NULL,
                collection_type TEXT NOT NULL,
                folder_id TEXT NOT NULL DEFAULT '',
                sort_index TEXT,
                added_at TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (tweet_id, collection_type, folder_id),
                FOREIGN KEY (tweet_id) REFERENCES tweets(tweet_id)
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                collection_type TEXT NOT NULL,
                folder_id TEXT NOT NULL DEFAULT '',
                last_head_tweet_id TEXT,
                backfill_cursor TEXT,
                backfill_incomplete INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (collection_type, folder_id)
            );

            CREATE TABLE IF NOT EXISTS archive_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_collections_type_sort
            ON collections (collection_type, synced_at DESC);
            """
        )
        self.connection.commit()

    @contextmanager
    def page_transaction(self):
        cursor = self.connection.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            yield cursor
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        finally:
            cursor.close()

    def append_raw_capture(
        self,
        operation: str,
        cursor_in: str | None,
        cursor_out: str | None,
        http_status: int,
        raw_json: dict[str, Any],
        *,
        cursor: sqlite3.Cursor | None = None,
    ) -> str:
        cursor = cursor or self.connection.cursor()
        capture_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO raw_captures (
                id, operation, cursor_in, cursor_out, captured_at, http_status, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capture_id,
                operation,
                cursor_in,
                cursor_out,
                utc_now(),
                http_status,
                json.dumps(raw_json, sort_keys=True),
            ),
        )
        return capture_id

    def upsert_tweet(self, tweet: TimelineTweet, *, cursor: sqlite3.Cursor | None = None) -> None:
        cursor = cursor or self.connection.cursor()
        now = utc_now()
        cursor.execute(
            """
            INSERT INTO tweets (
                tweet_id, text, author_id, author_username, author_display_name,
                created_at, raw_json, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tweet_id) DO UPDATE SET
                text = excluded.text,
                author_id = excluded.author_id,
                author_username = excluded.author_username,
                author_display_name = excluded.author_display_name,
                created_at = excluded.created_at,
                raw_json = excluded.raw_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                tweet.tweet_id,
                tweet.text,
                tweet.author_id,
                tweet.author_username,
                tweet.author_display_name,
                tweet.created_at,
                json.dumps(tweet.raw_json, sort_keys=True),
                now,
                now,
            ),
        )

    def upsert_membership(
        self,
        tweet_id: str,
        collection_type: str,
        *,
        sort_index: str | None = None,
        folder_id: str | None = None,
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        cursor = cursor or self.connection.cursor()
        now = utc_now()
        cursor.execute(
            """
            INSERT INTO collections (
                tweet_id, collection_type, folder_id, sort_index, added_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tweet_id, collection_type, folder_id) DO UPDATE SET
                sort_index = excluded.sort_index,
                synced_at = excluded.synced_at
            """,
            (tweet_id, collection_type, _folder_key(folder_id), sort_index, now, now),
        )

    def get_sync_state(self, collection_type: str, folder_id: str | None = None) -> SyncState:
        row = self.connection.execute(
            """
            SELECT
                collection_type,
                last_head_tweet_id,
                backfill_cursor,
                backfill_incomplete,
                updated_at
            FROM sync_state
            WHERE collection_type = ? AND folder_id = ?
            """,
            (collection_type, _folder_key(folder_id)),
        ).fetchone()
        if row is None:
            return SyncState(collection_type=collection_type)
        return SyncState(
            collection_type=row["collection_type"],
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
        cursor: sqlite3.Cursor | None = None,
    ) -> None:
        cursor = cursor or self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO sync_state (
                collection_type,
                folder_id,
                last_head_tweet_id,
                backfill_cursor,
                backfill_incomplete,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_type, folder_id) DO UPDATE SET
                last_head_tweet_id = excluded.last_head_tweet_id,
                backfill_cursor = excluded.backfill_cursor,
                backfill_incomplete = excluded.backfill_incomplete,
                updated_at = excluded.updated_at
            """,
            (
                collection_type,
                _folder_key(folder_id),
                last_head_tweet_id,
                backfill_cursor,
                int(backfill_incomplete),
                utc_now(),
            ),
        )

    def reset_sync_state(self, collection_type: str, folder_id: str | None = None) -> None:
        self.connection.execute(
            "DELETE FROM sync_state WHERE collection_type = ? AND folder_id = ?",
            (collection_type, _folder_key(folder_id)),
        )
        self.connection.commit()

    def has_membership(
        self, tweet_id: str, collection_type: str, folder_id: str | None = None
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM collections
            WHERE tweet_id = ? AND collection_type = ? AND folder_id = ?
            LIMIT 1
            """,
            (tweet_id, collection_type, _folder_key(folder_id)),
        ).fetchone()
        return row is not None

    def get_archive_owner_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM archive_metadata WHERE key = 'owner_user_id'"
        ).fetchone()
        return row["value"] if row else None

    def set_archive_owner_id(self, user_id: str, *, cursor: sqlite3.Cursor | None = None) -> None:
        cursor = cursor or self.connection.cursor()
        cursor.execute(
            """
            INSERT INTO archive_metadata (key, value, updated_at)
            VALUES ('owner_user_id', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (user_id, utc_now()),
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
            self.connection.commit()

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
        with self.page_transaction() as cursor:
            self.append_raw_capture(
                operation, cursor_in, cursor_out, http_status, raw_json, cursor=cursor
            )
            for tweet in tweets:
                self.upsert_tweet(tweet, cursor=cursor)
                self.upsert_membership(
                    tweet.tweet_id,
                    collection_type,
                    sort_index=tweet.sort_index,
                    cursor=cursor,
                )
            self.set_sync_state(
                collection_type,
                last_head_tweet_id=last_head_tweet_id,
                backfill_cursor=backfill_cursor,
                backfill_incomplete=backfill_incomplete,
                cursor=cursor,
            )

    def export_rows(self, collection: str) -> list[dict[str, Any]]:
        conditions = []
        params: list[str] = []
        if collection != "all":
            conditions.append("c.collection_type = ?")
            params.append(collection)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"""
            SELECT
                t.tweet_id,
                t.text,
                t.author_id,
                t.author_username,
                t.author_display_name,
                t.created_at,
                t.raw_json AS tweet_raw_json,
                c.collection_type,
                c.folder_id,
                c.sort_index,
                c.added_at,
                c.synced_at
            FROM collections AS c
            JOIN tweets AS t ON t.tweet_id = c.tweet_id
            {where_clause}
            ORDER BY c.synced_at DESC, c.sort_index DESC
            """,
            params,
        ).fetchall()
        exported = []
        for row in rows:
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
                    "raw_json": json.loads(row["tweet_raw_json"]),
                }
            )
        return exported

    def counts(self) -> dict[str, int]:
        result = {}
        for table in ("raw_captures", "tweets", "collections", "sync_state"):
            row = self.connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            result[table] = int(row["count"])
        return result


def open_archive_store(paths: XDGPaths, *, create: bool) -> ArchiveStore | None:
    if not create and not paths.database_file.exists():
        return None
    return ArchiveStore(paths.database_file, create=create)
