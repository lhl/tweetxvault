#!/usr/bin/env python3
"""Task 0 benchmark harness for embedded database candidates.

Run each backend in a fresh process and compare the same cold-start test:
open DB, create schema, insert 1k rows, and read 10 rows back.

Examples:
  uv run python docs/ANALYSIS-db.py sqlite
  uv run python docs/ANALYSIS-db.py seekdb-sql
  uv run --with lancedb python docs/ANALYSIS-db.py lancedb
"""

from __future__ import annotations

import argparse
import json
import resource
import sqlite3
import string
import tempfile
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "backend",
        choices=("sqlite", "seekdb-sql", "lancedb"),
        help="Backend to benchmark.",
    )
    parser.add_argument("--rows", type=int, default=1000, help="Number of rows to insert.")
    parser.add_argument(
        "--raw-bytes",
        type=int,
        default=2048,
        help="Target raw_json payload size per row.",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path.home() / ".cache" / "tweetxvault" / "db-benchmarks",
        help="Directory under which fresh benchmark directories are created.",
    )
    return parser.parse_args()


def sql_quote(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def make_row(index: int, raw_bytes: int) -> dict[str, str]:
    base_text = f"tweet {index} about machine learning and bookmarks"
    alphabet = string.ascii_letters + string.digits
    pad = (alphabet * ((raw_bytes // len(alphabet)) + 1))[:raw_bytes]
    raw_obj = {
        "id": str(index),
        "text": base_text,
        "author": {"id": f"author-{index % 23}", "username": f"user{index % 23}"},
        "entities": {"urls": [f"https://example.com/{index}"]},
        "payload": pad,
    }
    raw_json = json.dumps(raw_obj, sort_keys=True)
    return {
        "tweet_id": str(index),
        "text": base_text,
        "author_username": f"user{index % 23}",
        "created_at": f"2026-03-14T00:{index % 60:02d}:00Z",
        "sort_index": f"{rows_to_sort_index(index)}",
        "raw_json": raw_json,
    }


def rows_to_sort_index(index: int) -> int:
    return 1_000_000 - index


def make_rows(count: int, raw_bytes: int) -> list[dict[str, str]]:
    return [make_row(index, raw_bytes) for index in range(count)]


def make_workdir(base_dir: Path, backend: str) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{backend}-", dir=base_dir))


def benchmark_sqlite(rows: list[dict[str, str]], workdir: Path) -> dict[str, Any]:
    db_path = workdir / "archive.sqlite3"

    total_start = time.perf_counter()
    open_start = time.perf_counter()
    conn = sqlite3.connect(db_path)
    open_s = time.perf_counter() - open_start

    schema_start = time.perf_counter()
    conn.executescript(
        """
        CREATE TABLE tweets (
            tweet_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            author_username TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sort_index INTEGER NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX idx_tweets_sort ON tweets(sort_index DESC);
        """
    )
    conn.commit()
    schema_s = time.perf_counter() - schema_start

    insert_start = time.perf_counter()
    conn.executemany(
        """
        INSERT INTO tweets (
            tweet_id, text, author_username, created_at, sort_index, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["tweet_id"],
                row["text"],
                row["author_username"],
                row["created_at"],
                int(row["sort_index"]),
                row["raw_json"],
            )
            for row in rows
        ],
    )
    conn.commit()
    insert_1k_s = time.perf_counter() - insert_start

    query_start = time.perf_counter()
    fetched = conn.execute(
        """
        SELECT tweet_id, text, author_username, created_at, raw_json
        FROM tweets
        ORDER BY sort_index DESC
        LIMIT 10
        """
    ).fetchall()
    query_10_s = time.perf_counter() - query_start
    conn.close()

    return {
        "backend": "sqlite",
        "db_path": str(db_path),
        "open_s": round(open_s, 4),
        "schema_s": round(schema_s, 4),
        "insert_1k_s": round(insert_1k_s, 4),
        "query_10_s": round(query_10_s, 4),
        "rows_inserted": len(rows),
        "rows_returned": len(fetched),
        "raw_json_bytes": len(rows[0]["raw_json"]),
        "total_s": round(time.perf_counter() - total_start, 4),
        "ru_maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def benchmark_seekdb_sql(rows: list[dict[str, str]], workdir: Path) -> dict[str, Any]:
    import pylibseekdb as seekdb

    database = "tweetxvault_bench"
    total_start = time.perf_counter()

    open_start = time.perf_counter()
    seekdb.open(db_dir=str(workdir))
    open_s = time.perf_counter() - open_start

    schema_start = time.perf_counter()
    admin = seekdb.connect(autocommit=False)
    admin_cursor = admin.cursor()
    admin_cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    admin.commit()
    admin_cursor.close()
    admin.close()

    conn = seekdb.connect(database=database, autocommit=False)
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS tweets")
    cursor.execute(
        """
        CREATE TABLE tweets (
            tweet_id VARCHAR(255) PRIMARY KEY,
            text MEDIUMTEXT NOT NULL,
            author_username VARCHAR(255) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            sort_index BIGINT NOT NULL,
            raw_json MEDIUMTEXT NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX idx_tweets_sort ON tweets(sort_index)")
    conn.commit()
    schema_s = time.perf_counter() - schema_start

    insert_start = time.perf_counter()
    batch_size = 100
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        values = []
        for row in batch:
            values.append(
                "("
                + ", ".join(
                    (
                        sql_quote(row["tweet_id"]),
                        sql_quote(row["text"]),
                        sql_quote(row["author_username"]),
                        sql_quote(row["created_at"]),
                        row["sort_index"],
                        sql_quote(row["raw_json"]),
                    )
                )
                + ")"
            )
        cursor.execute(
            """
            INSERT INTO tweets (
                tweet_id, text, author_username, created_at, sort_index, raw_json
            )
            VALUES
            """
            + ",\n".join(values)
        )
    conn.commit()
    insert_1k_s = time.perf_counter() - insert_start

    query_start = time.perf_counter()
    cursor.execute(
        """
        SELECT tweet_id, text, author_username, created_at, raw_json
        FROM tweets
        ORDER BY sort_index DESC
        LIMIT 10
        """
    )
    fetched = cursor.fetchall()
    query_10_s = time.perf_counter() - query_start
    cursor.close()
    conn.close()

    return {
        "backend": "seekdb-sql",
        "db_path": str(workdir),
        "open_s": round(open_s, 4),
        "schema_s": round(schema_s, 4),
        "insert_1k_s": round(insert_1k_s, 4),
        "query_10_s": round(query_10_s, 4),
        "rows_inserted": len(rows),
        "rows_returned": len(fetched),
        "raw_json_bytes": len(rows[0]["raw_json"]),
        "total_s": round(time.perf_counter() - total_start, 4),
        "ru_maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def benchmark_lancedb(rows: list[dict[str, str]], workdir: Path) -> dict[str, Any]:
    import lancedb
    import pyarrow as pa

    total_start = time.perf_counter()

    open_start = time.perf_counter()
    db = lancedb.connect(workdir)
    open_s = time.perf_counter() - open_start

    schema = pa.schema(
        [
            pa.field("tweet_id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("author_username", pa.string()),
            pa.field("created_at", pa.string()),
            pa.field("sort_index", pa.int64()),
            pa.field("raw_json", pa.large_string()),
        ]
    )
    schema_start = time.perf_counter()
    table = db.create_table("tweets", schema=schema, mode="overwrite")
    schema_s = time.perf_counter() - schema_start

    insert_start = time.perf_counter()
    table.add(
        [
            {
                "tweet_id": row["tweet_id"],
                "text": row["text"],
                "author_username": row["author_username"],
                "created_at": row["created_at"],
                "sort_index": int(row["sort_index"]),
                "raw_json": row["raw_json"],
            }
            for row in rows
        ]
    )
    insert_1k_s = time.perf_counter() - insert_start

    query_start = time.perf_counter()
    fetched = table.search().limit(10).to_arrow()
    query_10_s = time.perf_counter() - query_start

    return {
        "backend": "lancedb",
        "db_path": str(workdir),
        "open_s": round(open_s, 4),
        "schema_s": round(schema_s, 4),
        "insert_1k_s": round(insert_1k_s, 4),
        "query_10_s": round(query_10_s, 4),
        "rows_inserted": len(rows),
        "rows_returned": fetched.num_rows,
        "raw_json_bytes": len(rows[0]["raw_json"]),
        "total_s": round(time.perf_counter() - total_start, 4),
        "ru_maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def main() -> None:
    args = parse_args()
    rows = make_rows(args.rows, args.raw_bytes)
    workdir = make_workdir(args.base_dir, args.backend)

    if args.backend == "sqlite":
        result = benchmark_sqlite(rows, workdir)
    elif args.backend == "seekdb-sql":
        result = benchmark_seekdb_sql(rows, workdir)
    elif args.backend == "lancedb":
        result = benchmark_lancedb(rows, workdir)
    else:
        raise AssertionError(f"Unsupported backend: {args.backend}")

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
