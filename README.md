# tweetxvault

A Python CLI tool for archiving your Twitter/X bookmarks, likes, and authored tweets into a local [LanceDB](https://lancedb.github.io/lancedb/) database, with support for importing official X archive exports into the same store. Runs unattended via cron, supports incremental sync with crash-safe resume, and preserves raw API responses so you never lose data.

<img src="https://raw.githubusercontent.com/lhl/tweetxvault/main/docs/screenshot.png" alt="tweetxvault view all" width="800">

## Features

- **Incremental sync** — fetches only new items by default; resumes interrupted backfills automatically
- **Official X archive import** — imports authored tweets, deleted tweets, likes, and exported media from official X archive ZIPs/directories into the same local archive
- **Raw capture preservation** — every API response page is stored verbatim alongside parsed tweet records
- **Secondary object extraction** — archives canonical tweet objects, attached-tweet relations, media metadata, URL refs, and article payloads alongside collection memberships
- **Crash-safe checkpoints** — sync state advances atomically with data writes; safe to kill mid-run
- **Full-text and semantic search** — built-in FTS (tantivy) and optional ONNX-based vector embeddings for hybrid search
- **Automatic query ID discovery** — scrapes Twitter's JS bundles to stay current with GraphQL endpoint changes
- **Browser cookie extraction** — reads session cookies from Firefox plus Chromium-family browsers like Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc
- **Rate limit handling** — exponential backoff, cooldown periods, and configurable retry limits
- **Export** — export your archive to JSON or a self-contained HTML viewer

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Unix-like runtime only today (Linux/macOS). Windows is not supported yet because the CLI currently depends on `fcntl`, `resource`, and `strftime("%-d")`.
- A Twitter/X account logged in via Firefox or a supported Chromium-family browser, or session cookies obtained manually

## Installation

Install from PyPI:

```bash
pip install tweetxvault
```

Install globally with `uv`:

```bash
uv tool install tweetxvault
```

Install globally with `pipx`:

```bash
pipx install tweetxvault
```

To enable semantic search (vector embeddings):

```bash
pip install "tweetxvault[embed]"
```

Or install the embedding extra as a global tool:

```bash
uv tool install "tweetxvault[embed]"
pipx install "tweetxvault[embed]"
```

Install from source:

```bash
git clone https://github.com/lhl/tweetxvault.git
cd tweetxvault
uv sync
```

Install your local checkout as a global editable tool while developing on `HEAD`:

```bash
uv tool install -e .
```

Re-run that command with `--force` after dependency or metadata changes in
`pyproject.toml`.

Use `tweetxvault --version` to confirm which local build you are running. In a
git checkout, the CLI includes the short commit hash and appends `dirty` when
tracked files differ from `HEAD`.

To enable semantic search from source:

```bash
uv sync --extra embed
```

Run once without installing:

```bash
uvx tweetxvault --help
```

This installs `onnxruntime`, `tokenizers`, and `huggingface-hub`. GPU acceleration (CUDA/ROCm) is used automatically when available; CPU works fine too (~150-200 tweets/s).

## Authentication

tweetxvault needs your `auth_token` and `ct0` session cookies from Twitter/X. There are three ways to provide them (checked in this order):

### 1. Environment variables (simplest)

```bash
export TWEETXVAULT_AUTH_TOKEN="your_auth_token"
export TWEETXVAULT_CT0="your_ct0_token"
export TWEETXVAULT_USER_ID="your_numeric_user_id"  # required for likes and own-tweet sync
```

### 2. Config file

Create `~/.config/tweetxvault/config.toml`:

```toml
[auth]
auth_token = "your_auth_token"
ct0 = "your_ct0_token"
user_id = "your_numeric_user_id"
```

### 3. Browser auto-extraction

If you're logged into x.com in Firefox, Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, or Arc, tweetxvault will try them in that order and stop after the first browser profile that yields valid X cookies.

Firefox is read from its profile database directly. Chromium-family browsers use `browser-cookie3` for cookie decryption and OS keyring access.

To force a specific browser or profile for one command:

```bash
uv run tweetxvault auth check --browser chrome
uv run tweetxvault sync --browser brave --profile "Profile 2"
uv run tweetxvault sync --browser firefox --profile-path /path/to/profile
```

How `--browser` behaves:

- `--browser`, `--profile`, and `--profile-path` force tweetxvault to take `auth_token` and `ct0` from that browser/profile.
- `user_id` still uses the normal precedence order: `TWEETXVAULT_USER_ID` -> `auth.user_id` -> browser `twid`.
- That means you can still pin `user_id` explicitly for likes or authored-tweet sync if needed, even while forcing cookies from a specific browser profile.
- If you do not set `user_id` explicitly, tweetxvault will use the browser profile's `twid` cookie when available.

For example, this uses Firefox cookies from the selected profile, but still pins `user_id` from the environment:

```bash
export TWEETXVAULT_USER_ID="123456789"
uv run tweetxvault sync likes --browser firefox --profile my-profile
```

This matters most if you use multiple X accounts. Make sure the selected browser profile and resolved `user_id` belong to the same account. If you mix cookies from one account with a `user_id` from another, likes/authored-tweet sync may fail, and tweetxvault's archive-owner guardrail will refuse writes if the local archive already belongs to a different user.

Run `uv run tweetxvault auth check --browser ...` first if you want to verify which sources are being used before a sync.

To persist a browser preference in the environment or config:

```bash
export TWEETXVAULT_BROWSER="chrome"
export TWEETXVAULT_BROWSER_PROFILE="Profile 2"
export TWEETXVAULT_BROWSER_PROFILE_PATH="/path/to/profile"
```

Legacy Firefox-only override is still supported:

```bash
export TWEETXVAULT_FIREFOX_PROFILE_PATH="/path/to/your/firefox/profile"
```

### Verify your setup

```bash
uv run tweetxvault auth check
uv run tweetxvault auth check --interactive
```

This probes the API without writing any data and reports credential status and endpoint readiness. `--interactive` opens a picker over discovered browser profiles with valid X cookies.

## Usage

### Syncing

```bash
# Normal archive maintenance: sync bookmarks + likes, then run archive enrich,
# thread expansion, article refresh, media download, and unfurl.
uv run tweetxvault sync

# Explicit alias for the same default sync pass
uv run tweetxvault sync all

# Sync just bookmarks, likes, or your own authored tweets
uv run tweetxvault sync bookmarks
uv run tweetxvault sync likes
uv run tweetxvault sync tweets

# Force a specific browser profile for this run
uv run tweetxvault sync --browser chrome --profile "Profile 2"

# Full re-sync from scratch (resets sync state, does not delete existing data)
uv run tweetxvault sync --full

# Continue past duplicates without resetting state
uv run tweetxvault sync --backfill

# Clear a saved historical backfill cursor and run only the head pass
uv run tweetxvault sync likes --head-only

# Rewalk existing pages to refresh article-bearing tweets after article fields change
uv run tweetxvault sync bookmarks --article-backfill

# Limit to N pages per collection
uv run tweetxvault sync --limit 5

# Opt out of one or more automatic follow-up jobs for this run
uv run tweetxvault sync --skip-media --skip-unfurl
```

If the `[embed]` extra is installed, new tweets are automatically embedded after each sync on a best-effort basis; if embedding fails, sync still succeeds and you can retry later with `tweetxvault embed`.
`--article-backfill` updates stored `raw_json` and normalized secondary rows inline, so it does not require a follow-up `tweetxvault rehydrate`.
By default, `tweetxvault sync` and `tweetxvault sync all` both cover bookmarks + likes, then visibly run the follow-up archive-maintenance passes for TweetDetail enrich, threads, preview-only articles, media, and unfurls. Authored tweets stay opt-in via `tweetxvault sync tweets`.
`--head-only` is the escape hatch when an old saved backfill cursor is no longer useful: it clears that cursor for the targeted collection and runs only the normal head pass. It cannot be combined with `--full`, `--backfill`, or `--article-backfill`.

Common sync flags:

- `--full`: clear the saved sync state for that collection and start a fresh incremental crawl without deleting stored tweets.
- `--backfill`: keep walking older pages past duplicate detection when you want more history without resetting state.
- `--head-only`: clear a saved older-history cursor and do only the normal head pass; use this to stop `resume older`.
- `--article-backfill`: rewalk existing pages to refresh article-bearing tweets after article extraction changes.
- `--skip-enrich`, `--skip-threads`, `--skip-articles`, `--skip-media`, `--skip-unfurl`: skip one or more automatic follow-up archive-maintenance jobs for just that sync run.
- `--limit N`: cap the run to `N` fetched pages for debugging, sampling, or shorter catch-up runs.
- `--browser`, `--profile`, `--profile-path`: force a specific browser/profile for cookie extraction on just that run.

Backfill status markers shown by `tweetxvault stats`:

- `resume older`: the next sync will do its normal head pass, then resume older history from a saved cursor.
- `none saved`: no older-history cursor is saved for that collection.
- `saved only`: a cursor exists without the normal incomplete marker; this is an unusual transitional state.
- `incomplete`: the sync state says older history is unfinished but no cursor is currently saved; this is also unusual.

To clear `resume older`, run `tweetxvault sync <collection> --head-only`, for example `tweetxvault sync likes --head-only`.
Use `tweetxvault sync <command> --help` for the current CLI flag descriptions.

### Importing an X archive

```bash
# Import an official X archive ZIP or extracted directory
uv run tweetxvault import x-archive ~/Downloads/twitter-archive.zip

# Clear previously imported archive-owned rows/media and reimport from scratch
uv run tweetxvault import x-archive ~/Downloads/twitter-archive.zip --regen

# Fetch TweetDetail for every remaining sparse archive tweet after the automatic bulk tweets/likes reconciliation
uv run tweetxvault import x-archive ~/Downloads/twitter-archive.zip --enrich

# Run a bounded TweetDetail follow-up after the automatic bulk tweets/likes reconciliation
uv run tweetxvault import x-archive ~/Downloads/twitter-archive --detail-lookups 100

# Sample/debug a large archive without touching the normal follow-up path
uv run tweetxvault import x-archive ~/Downloads/twitter-archive.zip --regen --debug --limit 1000

# Continue pending TweetDetail follow-up later without re-reading the archive ZIP
uv run tweetxvault import enrich

# Or run the follow-up in bounded batches
uv run tweetxvault import enrich --limit 500
```

The importer maps authored tweets, deleted authored tweets, likes, and exported `tweets_media/` files into the same LanceDB archive used by live sync. It applies the same archive-owner guardrail as sync, runs bulk live `tweets` / `likes` reconciliation automatically when auth is available, and keeps sparse archive-only rows in a tracked pending state until you choose how much per-tweet follow-up to run. If the archive only includes a video poster/thumbnail and not the main media file, that media row stays pending so `tweetxvault media download` can fill the gap later. Official X archives currently do not include bookmarks, so the importer warns about the missing bookmark dataset; that warning is expected today.

`TweetDetail` is X's per-tweet detail API: tweetxvault uses it to fill in metadata the archive often lacks for liked tweets, such as author fields, timestamps, full media metadata, and thread context.

Import follow-up options:
- Default import does **no per-tweet TweetDetail pass**. It only imports the archive and runs the bulk live collection reconciliation.
- `--detail-lookups N` runs a bounded TweetDetail pass for at most `N` pending sparse tweets after the bulk live syncs.
- `--enrich` runs the TweetDetail pass for **all** currently pending sparse tweets after the bulk live syncs.
- `--regen` clears archive-import-owned rows, import manifests, and copied archive media files before reimporting. It leaves live-synced rows intact.
- Archive import itself works without live auth, but the automatic reconciliation and any TweetDetail follow-up only run when auth is available.
- Archive import/enrich uses a head-only live reconciliation pass; it does **not** resume an old saved likes/tweets backfill from the normal sync state machine.
- If the same archive digest was already imported, a plain re-run still short-circuits, but `--enrich` reuses the existing import and runs only the follow-up enrichment instead of re-importing the ZIP contents.
- If an import is interrupted during the archive-write phase, rerunning the same `import x-archive ...` command is the normal recovery path. If the archive write already completed and only the follow-up was interrupted, use `tweetxvault import enrich` or rerun with `--enrich`.
- `tweetxvault import enrich` reruns that same archive-specific follow-up later against already imported archive data, without needing the original ZIP or directory path again.
- `tweetxvault import enrich --limit N` limits the TweetDetail phase only; it still reruns the archive-specific bulk live reconciliation first.
- `tweetxvault threads expand` is the broader TweetDetail-based context/thread capture command; use it when you want parents, replies, and linked status URLs beyond the archive-placeholder follow-up.
- Interactive TTY runs show tqdm progress bars for hashing, tweet/like import, media copy, and detail enrichment by default.
- Non-interactive runs stay quiet by default aside from warnings/errors, so cron/piped runs do not get interactive progress output.
- `--debug` adds per-phase timing diagnostics on top of that interactive progress output.
- `--limit N` requires `--debug` and is a sampled diagnostic import: tweetxvault still hashes and parses the full archive files, but only imports the first `N` authored tweets, deleted tweets, likes, and media files after load. Sampled runs are stored as `sampled`, not `completed`, and skip the automatic live follow-up unless you explicitly ask for `--enrich` / `--detail-lookups`.

### Importing old "Grailbird" archives (pre-2018)

Twitter archives exported before ~2018 use an older format called "Grailbird" (CSV-based, with `tweets.csv` in the root and monthly JS files under `data/js/tweets/`). These cannot be imported directly — convert them first with the shipped `tweetxvault import grailbird` command:

```bash
# Convert the old archive to modern format
tweetxvault import grailbird ~/TwitterArchive-2015 ~/TwitterArchive-2015-converted

# Then import normally
tweetxvault import x-archive ~/TwitterArchive-2015-converted
```

The converter reads `tweets.csv` and `data/js/user_details.js` (if present) and produces a modern archive directory with `data/tweets.js`, `data/account.js`, and `data/manifest.js`. If `user_details.js` is missing, tweetxvault still imports the converted archive, but it leaves the local archive owner unset so the first later authenticated sync can establish the real owner metadata instead of locking the archive to a fake placeholder id. For checkout-based use, the repo also keeps a compatibility wrapper at `python convert_grailbird.py ...`. See [`docs/GRAILBIRD.md`](docs/GRAILBIRD.md) for details on what gets converted and known limitations.

### Viewing your archive

```bash
# View recent bookmarks in a terminal table
uv run tweetxvault view bookmarks

# View likes, oldest first
uv run tweetxvault view likes --sort oldest

# View your authored tweets
uv run tweetxvault view tweets

# View all archived tweets
uv run tweetxvault view all --limit 50
```

Terminal views render tweet timestamps in your local timezone. Sort order uses tweet `created_at`, not collection position. For likes, that means `uv run tweetxvault view likes --sort oldest` shows the oldest liked tweet by tweet timestamp when one is known; X does not expose a reliable `liked_at` timestamp for reconstructing the exact order in which you liked posts.

### Searching

```bash
# Search posts and articles together
uv run tweetxvault search "machine learning"

# Limit search to result types and/or collections
uv run tweetxvault search "machine learning" --type article
uv run tweetxvault search "machine learning" --type post --collection bookmark,like

# Sort search results chronologically instead of by relevance
uv run tweetxvault search "machine learning" --sort newest
uv run tweetxvault search "machine learning" --sort oldest

# Force a specific search mode
uv run tweetxvault search "llama" --mode fts
uv run tweetxvault search "llama" --mode vector
uv run tweetxvault search "llama" --mode hybrid

# Adjust result count
uv run tweetxvault search "transformer architecture" --limit 50
```

Search modes:
- **fts** — keyword matching via full-text search (always available)
- **vector** — semantic similarity via embeddings (requires `uv sync --extra embed` + `tweetxvault embed`)
- **hybrid** — combines FTS and vector results with reranking (best quality)
- **auto** (default) — uses hybrid for post-only searches when embeddings exist; otherwise falls back to FTS so article results stay included

Chronological search sorting reorders the returned match set by `created_at` after retrieval, so it preserves the selected search mode and still defaults to relevance-first candidate selection.

Filters:
- `--type` — comma-delimited result types: `post`, `article`
- `--collection` — comma-delimited archive collections: `bookmark`, `like`, `tweet`
- `--sort` — `relevance` (default), `newest`, or `oldest`

### Embeddings

```bash
# Generate embeddings for all archived tweets (resumes if interrupted)
uv run tweetxvault embed

# Regenerate all embeddings from scratch
uv run tweetxvault embed --regen
```

Uses the `all-MiniLM-L6-v2` model (384 dimensions) via ONNX Runtime. The model is downloaded automatically from Hugging Face Hub on first run.
If you already generated embeddings with an older tweetxvault version, run `uv run tweetxvault embed --regen` once after upgrading so stored vectors are rebuilt with the current normalized cosine-search setup.

### Exporting

```bash
# Export all archived tweets to JSON
uv run tweetxvault export json

# Export a specific collection
uv run tweetxvault export json --collection bookmarks
uv run tweetxvault export json --collection tweets

# Export to a specific path
uv run tweetxvault export json --out ~/exports/my-bookmarks.json

# Export as a self-contained HTML viewer
uv run tweetxvault export html
uv run tweetxvault export html --collection likes --out ~/exports/likes.html
```

JSON exports now include normalized `media`, `urls`, and `article` sections alongside each exported tweet row.
HTML exports now render tweet media, URL metadata, and full article bodies when those rows exist in the archive.

### Media + URL Enrichment

```bash
# Download all pending archived media files into the local data dir
uv run tweetxvault media download

# Only download photos
uv run tweetxvault media download --photos-only

# Fetch final URL, canonical URL, title, and description metadata
uv run tweetxvault unfurl

# Retry previously failed URL unfurls
uv run tweetxvault unfurl --retry-failed
```

### Thread Expansion

```bash
# Expand archived tweets through TweetDetail to capture parents/context rows
uv run tweetxvault threads expand

# Expand a specific thread target by URL or ID
uv run tweetxvault threads expand https://x.com/dimitrispapail/status/2026531440414925307
uv run tweetxvault threads expand 2026531440414925307

# Re-fetch an explicit target even if it was already expanded before
uv run tweetxvault threads expand --refresh 2026531440414925307
```

Explicit thread targets are idempotent by default: previously expanded targets are skipped unless you pass `--refresh`. Linked status-URL targets are attempted at most once per run, even if the same target appears in multiple archived URL refs.
`tweetxvault threads expand` is incremental by default, so it is safe to rerun after archive import if you want more TweetDetail-based context capture later.

### Article Refresh

```bash
# Refresh preview-only archived article rows via TweetDetail
uv run tweetxvault articles refresh

# Refresh every archived article row, not just preview-only ones
uv run tweetxvault articles refresh --all

# Refresh a specific article-bearing tweet by URL or ID
uv run tweetxvault articles refresh https://x.com/dimitrispapail/status/2026531440414925307
uv run tweetxvault articles refresh 2026531440414925307
```

### Maintenance

```bash
# Show archive totals, per-collection coverage, sync recency, and storage health
uv run tweetxvault stats

# Compact the LanceDB archive (reduces file count after many syncs)
uv run tweetxvault optimize

# Rebuild normalized tweet fields and secondary objects from stored raw JSON,
# including any previously captured TweetDetail/thread-expansion payloads
uv run tweetxvault rehydrate

# Force-refresh query IDs from Twitter's JS bundles
uv run tweetxvault auth refresh-ids
```

`tweetxvault stats` reports overall post/article totals, per-collection counts plus first/last tweet timestamps, latest sync/capture times, storage health details such as DB/media size and version count with an actionable optimize hint, and follow-up queues for archive enrichment, rehydrate gaps, and pending thread expansion. The command now ends with a short legend that explains the backfill states plus the difference between archive enrich, local rehydrate gaps, membership thread targets, and linked-status thread targets.

Long-running archive writers such as `sync`, `import enrich`, `threads expand`,
`articles refresh`, `media download`, and `unfurl` now do a best-effort compact
on the first `Ctrl-C` after substantial committed writes. Press `Ctrl-C` again
while that compact is running to skip it and exit; if you do, run
`tweetxvault optimize` later.

## Unattended sync via cron

```cron
# Sync bookmarks/likes plus the normal follow-up archive maintenance every 6 hours
0 */6 * * * cd /path/to/tweetxvault && uv run tweetxvault sync 2>> /tmp/tweetxvault.log
```

A process lock prevents overlapping runs.

## Configuration

All configuration is optional. Defaults work out of the box with browser cookie extraction.

### Sync tuning (config.toml or env vars)

| Setting | Default | Env var |
|---------|---------|---------|
| `sync.page_delay` | `2.0` s | `TWEETXVAULT_PAGE_DELAY` |
| `sync.max_retries` | `3` | `TWEETXVAULT_MAX_RETRIES` |
| `sync.backoff_base` | `2.0` s | `TWEETXVAULT_BACKOFF_BASE` |
| `sync.detail_max_retries` | `2` | `TWEETXVAULT_DETAIL_MAX_RETRIES` |
| `sync.detail_backoff_base` | `30.0` s | `TWEETXVAULT_DETAIL_BACKOFF_BASE` |
| `sync.cooldown_threshold` | `3` consecutive 429s | `TWEETXVAULT_COOLDOWN_THRESHOLD` |
| `sync.cooldown_duration` | `300.0` s | `TWEETXVAULT_COOLDOWN_DURATION` |
| `sync.timeout` | `30.0` s | `TWEETXVAULT_TIMEOUT` |

TweetDetail-heavy jobs such as `tweetxvault import enrich`, `tweetxvault threads expand`,
and `tweetxvault articles refresh` pace themselves from X's live rate-limit headers when
those headers are present. If X does not expose reset/remaining headers for a request, the
client falls back to the shared `sync.detail_*` retry settings and cooldown controls.

## Data storage

Data paths are resolved by [platformdirs](https://platformdirs.readthedocs.io/) so they follow OS conventions, but the current runtime target is Unix-like systems only. On Linux the defaults are:

| Purpose | Default path |
|---------|-------------|
| Config | `~/.config/tweetxvault/` |
| Archive (LanceDB) | `~/.local/share/tweetxvault/archive.lancedb/` |
| Cache (query IDs) | `~/.cache/tweetxvault/` |

Override with `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`.

## How it works

tweetxvault calls Twitter's internal GraphQL API — the same endpoints the web app uses. It:

1. Resolves session cookies (env/config/browser extraction)
2. Discovers current GraphQL query IDs by parsing Twitter's JS bundles (with a 24h TTL cache and static fallbacks)
3. Fetches timeline pages with the proper headers, feature flags, and cursor pagination
4. Stores raw API responses + collection tweet rows + normalized secondary objects in a single LanceDB table
5. Tracks sync state per collection so the next run picks up where it left off

## Development

```bash
uv sync --extra embed

# Run tests
uv run pytest

# Lint and format
uv run ruff check
uv run ruff format --check
```

See [`docs/`](docs/README.md) for architecture docs, the implementation plan, and research notes.

## Similar projects

- **[twitter-web-exporter](https://github.com/prinsss/twitter-web-exporter)** — Browser extension (Tampermonkey/Violentmonkey) that intercepts Twitter's GraphQL responses in-page; exports bookmarks, likes, tweets, followers, and DMs to JSON/CSV/HTML with bulk media download
- **[tweethoarder](https://github.com/tfriedel/tweethoarder)** — Python CLI archiver for likes, bookmarks, tweets, reposts, and home feed into SQLite with JSON/Markdown/CSV/HTML export
- **[Siftly](https://github.com/nichochar/Siftly)** — Self-hosted AI bookmark manager (Next.js + SQLite + Anthropic API) with entity extraction, vision analysis, and mindmap visualization
- **[TweetVault (helioLJ)](https://github.com/helioLJ/TweetVault)** — Self-hosted bookmark archive (Go + Next.js + PostgreSQL) with tag management; imports via twitter-web-exporter ZIP
- **[twitter-likes-export](https://github.com/gasser707/twitter-likes-export)** — Minimal Python scripts to export likes via Twitter's GraphQL API with optional media download
- **[download_twitter_likes](https://github.com/raviddog/download_twitter_likes)** — Playwright-based media downloader that scrolls your likes page and saves images/GIFs/videos

## License

Apache 2.0
