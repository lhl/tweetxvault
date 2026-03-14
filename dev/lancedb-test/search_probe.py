from __future__ import annotations

import json
import tempfile
from pathlib import Path

import lancedb
import pyarrow as pa


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="tweetxvault-lancedb-search-"))
    db = lancedb.connect(base / "search.lancedb")
    table = db.create_table(
        "tweets",
        schema=pa.schema(
            [
                pa.field("tweet_id", pa.string()),
                pa.field("collection_type", pa.string()),
                pa.field("author_username", pa.string()),
                pa.field("text", pa.string()),
                pa.field("embedding", pa.list_(pa.float32(), 3)),
            ]
        ),
        mode="overwrite",
    )
    table.add(
        [
            {
                "tweet_id": "1",
                "collection_type": "bookmark",
                "author_username": "alice",
                "text": "machine learning systems",
                "embedding": [1.0, 0.0, 0.0],
            },
            {
                "tweet_id": "2",
                "collection_type": "bookmark",
                "author_username": "bob",
                "text": "gardening notes",
                "embedding": [0.0, 1.0, 0.0],
            },
            {
                "tweet_id": "3",
                "collection_type": "like",
                "author_username": "alice",
                "text": "vector search design",
                "embedding": [0.9, 0.1, 0.0],
            },
        ]
    )

    table.create_scalar_index("collection_type")
    table.create_fts_index("text", replace=True)
    table.create_index(vector_column_name="embedding", metric="cosine", index_type="IVF_FLAT")

    fts_hits = (
        table.search("machine learning", query_type="fts", fts_columns="text")
        .where("collection_type = 'bookmark'")
        .limit(5)
        .to_list()
    )
    vector_hits = (
        table.search(
            [1.0, 0.0, 0.0],
            query_type="vector",
            vector_column_name="embedding",
        )
        .where("collection_type = 'bookmark'")
        .limit(5)
        .to_list()
    )

    assert [row["tweet_id"] for row in fts_hits] == ["1"]
    assert vector_hits[0]["tweet_id"] == "1"
    assert all(row["collection_type"] == "bookmark" for row in vector_hits)

    print(
        json.dumps(
            {
                "base_dir": str(base),
                "fts_hits": [row["tweet_id"] for row in fts_hits],
                "vector_hits": [row["tweet_id"] for row in vector_hits],
                "indexes": [
                    {
                        "name": getattr(index, "name", None),
                        "type": getattr(index, "index_type", None),
                        "columns": list(getattr(index, "columns", []) or []),
                    }
                    for index in table.list_indices()
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
