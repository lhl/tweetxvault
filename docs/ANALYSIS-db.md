# Database Choice Analysis

## Context

tweetxvault needs an embedded database that handles:
1. **Structured storage** — tweets, authors, collections, sync state, media metadata
2. **Raw JSON preservation** — full GraphQL responses stored as-is
3. **Vector search** — semantic similarity over tweet text embeddings
4. **Full-text search** — keyword/phrase search across tweet content
5. **Hybrid queries** — combine structured filters with text/vector search (e.g., "bookmarks from @user about machine learning")
6. **Media tracking** — content-hash dedup, download state, local file paths
7. **Incremental sync** — checkpoint/resume, cursor tracking per collection

The DB is local-only, single-user, single-writer. Dataset size is modest (tens of thousands of tweets, not millions). The tool should be cronnable with fast startup.

## Empirical Task 0 Results (2026-03-14)

We now have a reproducible benchmark harness in [ANALYSIS-db.py](ANALYSIS-db.py) for the original Task 0 storage spike:

- cold open
- create schema
- insert 1,000 rows
- query 10 rows back
- record `ru_maxrss`

These are intentionally "storage floor" measurements, not end-to-end vector-index or hybrid-search benchmarks.

Versioned test stack:

- SQLite: `sqlite3` 3.49.1
- SeekDB: `pyseekdb` 1.1.0.post3, `pylibseekdb` 1.1.0
- LanceDB: `lancedb` 0.29.2

Cold benchmark, 1,000 rows, ~2.2KB `raw_json` per row:

| Backend | Open | Schema | Insert 1k | Query 10 | Total | Max RSS | Notes |
|---------|------|--------|-----------|----------|-------|---------|-------|
| SQLite | 0.0032s | 0.0190s | 0.0208s | 0.0002s | 0.0433s | 32MB | Easy baseline; trivially passes Task 0 exit criteria |
| SeekDB (raw SQL) | 2.1601s | 0.9850s | 0.0742s | 0.0100s | 3.2295s | 1000MB | Functional on `/home`-backed paths, but misses both Task 0 thresholds |
| LanceDB | 0.0166s | 0.0182s | 0.0127s | 0.0193s | 0.0668s | 161MB | Passes the same cold-start/RSS threshold comfortably |

Large raw JSON probe, 1 row, ~200KB `raw_json`:

| Backend | Result | Notes |
|---------|--------|-------|
| SQLite | OK | Plain `TEXT` handled the probe without issue |
| SeekDB (raw SQL) | OK with `MEDIUMTEXT` | Plain `TEXT` failed in a separate probe with `Data too long for column 'raw_json'`; `MEDIUMTEXT` and `LONGTEXT` both worked |
| LanceDB | OK | `pa.large_string()` handled the probe without issue |

Important SeekDB runtime findings:

- The original failure was not just POSIX permissions. In the current full-permission environment, embedded SeekDB does initialize successfully on normal writable paths under `/home`.
- SeekDB does **not** support `tmpfs` for `db_dir`; `seekdb.open()` on `/tmp/...` failed with `not support tmpfs directory`.
- `seekdb.open()` is process-global for one `db_dir`. Reopening a different path in the same process hit `The object is initialized twice`.
- The public `pyseekdb.Client` proxy only exposes collection operations. Raw SQL works either through `pylibseekdb` directly or via the proxy's internal `_server._execute(...)`.
- The Collection API is heavier than raw SQL for our workload. Default collection creation also triggered local embedding-model download and pushed RSS even higher in testing.

What this means for tweetxvault:

- **SeekDB is technically viable now**, but only if we accept a very heavy embedded runtime and use it on a non-tmpfs data directory.
- **SeekDB is not viable for the current MVP constraints.** Task 0 explicitly said to evaluate alternatives if cold start exceeds 3s or RSS exceeds 200MB for an empty DB. The current raw-SQL benchmark came in at ~3.23s total and ~1.0GB RSS.
- **LanceDB is the most plausible future vector sidecar** if we want a second empirical option beyond SQLite. It passes the cold-start test, but it still does not solve the "single engine for SQL + FTS + vectors + checkpoints" problem because it is not a relational SQL database.

Current recommendation:

1. Keep SQLite as the shipped MVP backend.
2. If we want a phase-2 vector store spike, compare `SQLite + LanceDB` against the current SQLite-only baseline before revisiting SeekDB again.
3. If we insist on SeekDB later, use raw SQL on an XDG data path under `/home`, keep `raw_json` columns at `MEDIUMTEXT` or larger, and avoid the Collection API for MVP-style sync storage.

## Candidates

### SQLite

The default embedded DB. TweetHoarder uses it. Python stdlib includes `sqlite3`.

**Strengths:**
- 20+ years of battle-testing, zero-config, everywhere
- Full SQL, ACID, excellent tooling (DB Browser, Datasette, etc.)
- FTS5 extension for full-text search (good enough for keyword search)
- Tiny footprint, instant startup
- TweetHoarder's schema is a proven reference for this exact use case

**Weaknesses:**
- No native vector search. Extensions exist (sqlite-vss, sqlite-vec) but they're young, require separate install, and add friction
- Hybrid queries (vector + structured filters) would require application-level stitching — fetch vector results, then filter in SQL, or vice versa
- JSON support (JSON1 extension) works but is clunky compared to native JSON DBs
- Single-writer limitation (fine for us, but worth noting)

**Verdict:** Safe choice for phase 1 (structured storage + FTS5). Becomes awkward in phase 3 when we add embeddings — we'd likely end up running two DBs (SQLite for structured + something else for vectors).

### DuckDB

Analytical embedded DB. Columnar storage, excellent for queries over structured data.

**Strengths:**
- Full SQL (richer than SQLite — window functions, CTEs, UNNEST, etc.)
- Native JSON support (`json_extract`, `json_array_length`, etc.)
- Excellent for analytical queries ("top 10 authors by bookmark count", "bookmarks per month")
- Parquet/CSV/JSON import/export built in
- Vector similarity search via `vss` extension (HNSW index)
- Good Python bindings, actively maintained

**Weaknesses:**
- `vss` extension is still experimental/young
- Columnar storage is optimized for reads/analytics, not frequent small writes (our sync pattern)
- Larger memory footprint than SQLite for small datasets
- No FTS built-in (would need to combine with something else or use LIKE/regex)
- Less ubiquitous tooling than SQLite

**Verdict:** Best for ad-hoc analytical queries over export data. The vss extension is promising but not mature. Write pattern (frequent small inserts during sync) isn't its strength.

### LanceDB

Purpose-built for AI/ML workloads. Columnar (Lance format), native vector search.

**Strengths:**
- Native vector search (IVF-PQ indexing), designed for this from the ground up
- Python-first API, good ergonomics for embedding workflows
- Stores structured metadata alongside vectors — can filter by metadata fields
- Lance columnar format is efficient for large embedding tables
- Active development, growing community (~2 years old)
- Embedded, no server needed

**Weaknesses:**
- **No SQL** — Python API only. Queries are method chains, not SQL strings
- Limited full-text search (basic keyword matching, not FTS5-grade)
- Hybrid queries are possible (vector search + metadata filters) but less flexible than SQL WHERE clauses
- Less mature for pure structured data operations (no JOINs, no aggregations, no window functions)
- Would likely need a second DB (SQLite/DuckDB) for structured queries, sync state, checkpoint tracking
- Documentation has gaps for edge cases

**Verdict:** Best pure vector search option. The empirical Task 0 benchmark above also shows that its embedded footprint is modest enough to be operationally comfortable. But the lack of SQL means we'd still need it paired with SQLite or DuckDB for structured operations — the "two databases" problem.

### SeekDB

AI-native hybrid search database from OceanBase (Ant Group/Alibaba). Unifies relational, vector, full-text, JSON, and GIS in one engine.

**Strengths:**
- **Hybrid search in a single query** — vector similarity + full-text scoring + SQL filters combined. This is the dream query: `SELECT * FROM tweets WHERE collection = 'bookmark' AND MATCH(text) AGAINST ('machine learning') ORDER BY l2_distance(embedding, ?) LIMIT 20`
- MySQL-compatible SQL with vector extensions (`l2_distance()`, `VECTOR INDEX`, `APPROXIMATE`)
- **Built-in embedding generation** — default 384d model, or plug in custom (HuggingFace, etc.)
- Built-in reranking and LLM inference primitives
- HNSW vector indexing via VSAG library
- Full ACID transactions
- Embedded mode (`path="./seekdb.db"`) — no server needed
- Collection-based Python API (ChromaDB-like) as alternative to SQL
- Apache 2.0 license

**Weaknesses:**
- **SeekDB product is new** (v1.0.0 Nov 2025, v1.1.0 Jan 2026) — but the underlying engine is OceanBase, an open-source distributed database in production since 2010 powering Alipay's core transaction system at massive scale. The database engine itself is battle-tested; the SeekDB packaging (embedded mode, vector extensions, AI primitives, Python SDK) is what's new.
- macOS support only added in v1.1.0; Linux is primary target
- Documentation is sparse/early-stage for SeekDB-specific features (OceanBase docs are extensive)
- The embedded mode and Python SDK are the youngest parts — less certain about edge cases
- C++ core — unknown embedded footprint size
- SeekDB-specific community is small (OceanBase community is large)
- Some features marked "experimental" (e.g., FORK TABLE)

**Verdict:** Does everything we want in one engine. The underlying OceanBase engine is proven at scale; the risk is around the newness of the embedded packaging and Python SDK. The fresh empirical spike shows that embedded SeekDB now initializes on supported filesystems here, but its cold-start and RSS are still far outside our MVP threshold. It remains interesting architecturally, but not justified as the default storage engine for the current cron-oriented MVP.

## Vector Indexing: IVF-PQ vs HNSW

Two dominant approaches for approximate nearest neighbor (ANN) vector search:

### HNSW (Hierarchical Navigable Small World)

Used by: SeekDB (via VSAG library), most modern vector DBs

- **How it works**: Builds a multi-layer graph where each node is a vector. Higher layers have fewer, more spread-out nodes for fast coarse navigation; lower layers are denser for precise search. Query traverses from top layer down.
- **Strengths**: Very high recall (typically 95-99%+), fast query time, no training step needed
- **Weaknesses**: High memory usage (stores full vectors + graph structure in RAM), slow index build, inserts are expensive
- **Memory**: Stores full-precision vectors. For 384d float32: ~1.5KB per vector + graph overhead
- **Best for**: Small-to-medium datasets where recall matters more than memory. Perfect for our scale (tens of thousands of tweets).

### IVF-PQ (Inverted File Index + Product Quantization)

Used by: LanceDB (IVF-PQ), FAISS

- **How it works**: Two-stage compression. IVF partitions the vector space into clusters (like k-means); PQ compresses vectors within each cluster by splitting them into subvectors and quantizing each independently.
- **Strengths**: Much lower memory usage (compressed vectors), scales to billions of vectors, fast batch queries
- **Weaknesses**: Lower recall than HNSW (typically 85-95%), requires training step on representative data, tuning cluster count and PQ parameters is fiddly
- **Memory**: Compressed vectors. For 384d with 48 subquantizers: ~48 bytes per vector (vs ~1.5KB for HNSW)
- **Best for**: Large datasets (millions+) where memory is constrained. Overkill for our scale.

### For tweetxvault

**HNSW is clearly the right choice.** Our dataset is small (tens of thousands, maybe low hundreds of thousands of tweets). We want high recall for semantic search. Memory is not a constraint. HNSW's "no training needed" property also means we can add tweets incrementally without rebuilding the index.

Both SeekDB and LanceDB support HNSW (LanceDB also offers IVF-PQ). SeekDB uses HNSW via VSAG; LanceDB defaults to IVF-PQ but supports HNSW as well.

## SeekDB Embedding & Multimodal Details

Based on source code inspection of pyseekdb 1.1.0:

### Default Embedding Model

- **Model**: `all-MiniLM-L6-v2` (sentence-transformers)
- **Dimensions**: 384
- **Runtime**: ONNX via onnxruntime (CPU, no GPU required)
- **Max tokens**: 256
- **Auto-download**: Model files fetched from HuggingFace on first use, cached in `~/.cache/pyseekdb/onnx_models/`
- On Python 3.14+, falls back to SentenceTransformerEmbeddingFunction instead of ONNX

### Supported Embedding Providers (14 total)

| Provider | Class | Notes |
|----------|-------|-------|
| Default (ONNX) | `DefaultEmbeddingFunction` | all-MiniLM-L6-v2, 384d, local, no API key |
| Sentence Transformers | `SentenceTransformerEmbeddingFunction` | Any HuggingFace sentence-transformer model, local |
| OpenAI | `OpenAIEmbeddingFunction` | text-embedding-3-small/large, API key |
| Ollama | `OllamaEmbeddingFunction` | Local LLM server, any Ollama model |
| Jina AI | `JinaEmbeddingFunction` | jina-embeddings-v3/v4, **jina-clip-v2** (multimodal) |
| Cohere | `CohereEmbeddingFunction` | embed-v3, API key |
| Google Vertex | `GoogleVertexEmbeddingFunction` | text-embedding-004, API key |
| Amazon Bedrock | `AmazonBedrockEmbeddingFunction` | Titan/Cohere on AWS |
| Mistral | `MistralEmbeddingFunction` | mistral-embed, API key |
| Voyage AI | `VoyageaiEmbeddingFunction` | voyage-3, API key |
| Qwen | `QwenEmbeddingFunction` | Alibaba Qwen embeddings |
| Morph | `MorphEmbeddingFunction` | |
| Siliconflow | `SiliconflowEmbeddingFunction` | |
| Tencent Hunyuan | `TencentHunyuanEmbeddingFunction` | |
| LiteLLM Base | `LiteLLMBaseEmbeddingFunction` | Generic wrapper for any LiteLLM-supported provider |

All providers implement `EmbeddingFunction` protocol with `name()`, `get_config()`, `build_from_config()` for persistence.

### Multimodal Support

**Not built into SeekDB directly**, but achievable through embedding providers:

- **Jina CLIP v2** (`jina-clip-v2`, 1024d) supports text-to-image and image-to-text search. Available via `JinaEmbeddingFunction`. Requires Jina AI API key.
- **Custom embedding functions** can be registered via `@register_embedding_function` decorator — you could wire up any multimodal model (CLIP, SigLIP, etc.)
- The Collection API accepts raw embeddings (`query_embeddings`) so you can generate multimodal embeddings externally and store/query them directly.

For our use case (tweet text + downloaded media), we could:
1. Use the default 384d model for text embeddings (free, local, fast)
2. Add a second collection with multimodal embeddings (Jina CLIP or local CLIP) for image-aware search later

### Distance Metrics

SeekDB supports (from Collection constructor and SQL syntax):
- **L2** (Euclidean distance) — default
- **Cosine** distance
- **Inner product**

Set at collection creation: `client.create_collection("tweets", distance="cosine")`

### Hybrid Search API

SeekDB has a dedicated `collection.hybrid_search()` method that combines full-text and vector search in one call with RRF (Reciprocal Rank Fusion) ranking:

```python
results = collection.hybrid_search(
    query={
        "where_document": {"$contains": "machine learning"},
        "where": {"collection_type": {"$eq": "bookmark"}},
        "n_results": 10
    },
    knn={
        "query_texts": ["AI safety research"],
        "where": {"author_username": {"$eq": "elonmusk"}},
        "n_results": 10
    },
    rank={"rrf": {"rank_window_size": 60, "rank_constant": 60}},
    n_results=5,
    include=["documents", "metadatas"]
)
```

This is exactly the "bookmarks about AI from @user" query pattern we need. The `where` filters work on metadata fields, `where_document` does full-text, and `knn` does vector similarity — all fused via RRF.

### Filter Operators

Metadata filters support: `$eq`, `$lt`, `$gt`, `$lte`, `$gte`, `$ne`, `$in`, `$nin`
Logical operators: `$or`, `$and`, `$not`
Document filters: `$contains` (full-text), `$regex` (regex matching)

## Comparison Matrix

| Requirement | SQLite | DuckDB | LanceDB | SeekDB |
|-------------|--------|--------|---------|--------|
| **Structured storage** | Excellent | Excellent | Limited (no SQL) | Excellent (MySQL SQL) |
| **Raw JSON preservation** | Good (JSON1 ext) | Excellent (native) | Good (metadata) | Excellent (native) |
| **Vector search** | Poor (ext required) | Experimental (vss) | Excellent (native) | Excellent (HNSW) |
| **Full-text search** | Good (FTS5) | Poor (no FTS) | Poor (basic) | Excellent (native) |
| **Hybrid queries** | No | Manual stitching | Partial (meta filters) | **Yes, single query** |
| **Built-in embeddings** | No | No | No | **Yes** |
| **SQL support** | Full | Full+ | None | MySQL-compatible |
| **Write pattern (small inserts)** | Excellent | OK (columnar) | Good | Good (ACID) |
| **Python ergonomics** | stdlib | Good | Excellent | Good |
| **Multimodal-ready** | No | No | No | Via Jina CLIP / custom |
| **Maturity** | 20+ years | ~5 years | ~2 years | Engine: 15+ years (OceanBase); SDK: ~4 months |
| **Tooling ecosystem** | Excellent | Good | Growing | Minimal |
| **Footprint** | Tiny | Small | Small | Heavy in current embedded tests (~1GB RSS cold start) |
| **Single-engine solution** | No (needs vector ext) | No (needs FTS) | No (needs SQL DB) | **Yes** |

## Schema Considerations

Regardless of DB choice, our schema needs to handle:

### Core Entities

**tweets** — The primary entity. Key fields:
- `id` (tweet rest_id, TEXT) — primary key
- `text` — tweet content
- `author_id`, `author_username`, `author_display_name`
- `created_at` — tweet timestamp
- `conversation_id` — for thread grouping
- `in_reply_to_tweet_id`, `in_reply_to_user_id` — reply chain
- `quoted_tweet_id` — quote tweets
- `is_retweet`, `retweeted_tweet_id`
- Engagement: `reply_count`, `retweet_count`, `like_count`, `quote_count`, `view_count`
- `media_json` — structured media metadata (URLs, types, dimensions, video variants)
- `urls_json` — expanded URLs
- `hashtags_json`, `mentions_json`
- `raw_json` — full GraphQL response (never lose data)
- `first_seen_at`, `last_updated_at`
- `embedding` — vector (phase 3)

**collections** — Many-to-many link between tweets and collection types:
- `tweet_id`, `collection_type` (like, bookmark, tweet, repost, reply, feed, article)
- `bookmark_folder_id`, `bookmark_folder_name`
- `sort_index` — Twitter's timeline ordering
- `added_at`, `synced_at`

**media** — Downloaded media tracking:
- `id` — auto
- `tweet_id` — foreign key
- `url` — original URL
- `type` — photo, video, gif, article_image
- `content_hash` — SHA-256 of downloaded file (for dedup)
- `local_path` — path relative to data/media/
- `width`, `height`, `bitrate` (for video)
- `downloaded_at`, `status` (pending, downloaded, failed, skipped)

**sync_state** — Checkpoint/resume per collection:
- `collection_type` — primary key
- `cursor` — pagination cursor
- `last_tweet_id`
- `total_synced`
- `status` (pending, in_progress, completed)
- `started_at`, `completed_at`

**articles** — If X Articles contain full body content beyond tweet stubs:
- `tweet_id` — foreign key to the article tweet
- `title`
- `body_html` or `body_text` — full article content
- `cover_image_url`
- `word_count`
- `raw_json` — full article response

### Schema Open Questions

1. **Denormalized vs normalized authors?** TweetHoarder embeds author fields directly in the tweets table. Simpler, but means author display_name/avatar updates create inconsistency across rows. For our scale (personal archive) denormalized is probably fine — we can always re-extract from raw_json.

2. **Embedding dimensions and model?** Affects column definition. 384d (MiniLM), 768d (larger sentence-transformers), 1536d (OpenAI), or other? Local models preferred for privacy. 384d is likely sufficient for tweet-length text.

3. **Media storage layout?**
   - Flat by content hash: `data/media/{sha256[:2]}/{sha256}.{ext}` — simple dedup
   - By date: `data/media/2026/03/14/{sha256}.{ext}` — browsable
   - By collection: `data/media/bookmarks/...` vs `data/media/likes/...` — a tweet can be in multiple collections, so this gets messy
   - **Recommendation**: Flat by content hash. Simple, dedup-native, let the DB handle the metadata/browsing layer.

4. **Article body storage?** We don't know yet what `UserArticlesTweets` returns. If it's just a tweet stub with a link, we may need to fetch the article content separately (possibly via Playwright page scrape). If it returns full body, we store inline.

5. **Versioning/history?** Should we track how engagement metrics change over time? TweetHoarder's UPSERT overwrites metrics on re-sync. For a personal archive this is probably fine — we care about the content, not the metric history. But raw_json preservation means we could reconstruct history from stored responses if needed.

6. **attention-export cross-schema?** This tool is part of a broader system. Do we need a shared schema convention (common tweet/post table, unified embeddings namespace) across exporters? TBD based on other attention-export modules.

7. **SeekDB embedded footprint?** The `pylibseekdb` wheel is ~170MB. What's the runtime memory footprint? How fast is startup for a cron job? Need to prototype.

8. **Multimodal embedding strategy?** For tweet images/media, do we want a separate collection with CLIP embeddings, or a single collection with text-only embeddings + media as metadata? Separate collections are simpler but don't enable "find tweets with images similar to X" queries inline.

## Evaluation Plan

### Quick Prototype (30 min each)

For SeekDB and LanceDB specifically (the two strongest contenders for vector search), do a hands-on eval:

1. **Install and basic setup** — How smooth is `pip install`? Any native deps that cause friction? Embedded mode startup time?

2. **Store 100 tweets** — Insert parsed tweet data. How natural is the API? Batch insert performance?

3. **Structured query** — "All bookmarks from @user created after 2026-01-01." How ergonomic?

4. **Full-text search** — "tweets containing 'machine learning'." Quality and speed.

5. **Vector search** — Generate embeddings for 100 tweets, store them, query "tweets about AI safety." Relevance of results.

6. **Hybrid query** — "Bookmarks about AI from @user." Does it work in a single query or require application-level stitching?

7. **Raw JSON storage and retrieval** — Store a full GraphQL response blob, retrieve it. Any size limits or performance issues?

8. **Incremental writes** — Simulate sync: insert 10 tweets, then 10 more with 3 overlapping (upsert). Does it handle this cleanly?

### Fallback Strategy

If neither SeekDB nor LanceDB feels right after prototyping:
- **SQLite + sqlite-vec** is the conservative fallback. Proven structured storage, good-enough vector search for our scale, excellent tooling.
- **DuckDB** if we want richer analytics (e.g., generating reports, dashboards from bookmark data).

### Decision Criteria (Ranked)

1. **Reliability** — Must not lose data. ACID or equivalent guarantees.
2. **Hybrid query ergonomics** — The "bookmarks about X from @user" query should be natural, not a multi-step hack.
3. **Embedding support** — Native vector storage and search without bolting on a second system.
4. **Python API quality** — Clean, well-documented, minimal boilerplate.
5. **Footprint** — Embedded, no server, fast startup for cron jobs.
6. **Maturity** — Prefer battle-tested, but willing to accept young if the ergonomics are significantly better.
7. **SQL** — Nice to have for ad-hoc exploration, but not strictly required if the Python API is good enough.
