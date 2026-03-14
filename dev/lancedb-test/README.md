# LanceDB Spike

This folder holds isolated LanceDB spike scripts. They are intentionally outside the
shipped `tweetxvault` package until the storage/search tradeoffs are settled.

Commands:

```bash
uv run --with lancedb python dev/lancedb-test/storage_spike.py
uv run --with lancedb python dev/lancedb-test/search_probe.py
```

What each script checks:

- `storage_spike.py`
  - Single-table archive design using `merge_insert` keyed by `row_key`
  - Page writes represented as one table-version increment
  - Sync-state persistence/reset
  - Collection-scoped duplicate detection
  - Export ordering
  - Archive owner guardrail
- `search_probe.py`
  - Scalar filter index
  - Full-text search index
  - Vector index with manual embeddings
  - Filtered FTS and filtered vector search
