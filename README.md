# tweetxvault

A Python CLI tool for archiving your Twitter/X bookmarks and likes into a local [LanceDB](https://lancedb.github.io/lancedb/) database. Runs unattended via cron, supports incremental sync with crash-safe resume, and preserves raw API responses so you never lose data.

## Features

- **Incremental sync** — fetches only new items by default; resumes interrupted backfills automatically
- **Raw capture preservation** — every API response page is stored verbatim alongside parsed tweet records
- **Crash-safe checkpoints** — sync state advances atomically with data writes; safe to kill mid-run
- **Automatic query ID discovery** — scrapes Twitter's JS bundles to stay current with GraphQL endpoint changes
- **Firefox cookie extraction** — reads session cookies directly from your Firefox profile (or set them via env vars / config)
- **Rate limit handling** — exponential backoff, cooldown periods, and configurable retry limits
- **JSON export** — export your archive to JSON for use in other tools

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A Twitter/X account logged in via Firefox, or session cookies obtained manually

## Installation

```bash
git clone https://github.com/lhl/tweetxvault.git
cd tweetxvault
uv sync
```

## Authentication

tweetxvault needs your `auth_token` and `ct0` session cookies from Twitter/X. There are three ways to provide them (checked in this order):

### 1. Environment variables (simplest)

```bash
export TWEETXVAULT_AUTH_TOKEN="your_auth_token"
export TWEETXVAULT_CT0="your_ct0_token"
export TWEETXVAULT_USER_ID="your_numeric_user_id"  # required for likes sync
```

### 2. Config file

Create `~/.config/tweetxvault/config.toml`:

```toml
[auth]
auth_token = "your_auth_token"
ct0 = "your_ct0_token"
user_id = "your_numeric_user_id"
```

### 3. Firefox auto-extraction

If you're logged into x.com in Firefox, tweetxvault will automatically find and read your cookies. No configuration needed — just make sure Firefox isn't running when you sync (to avoid database locks).

To use a specific Firefox profile:

```bash
export TWEETXVAULT_FIREFOX_PROFILE_PATH="/path/to/your/profile"
```

### Verify your setup

```bash
uv run tweetxvault auth check
```

This probes the API without writing any data and reports credential status and endpoint readiness.

## Usage

### Sync bookmarks and likes

```bash
# Sync everything (incremental by default)
uv run tweetxvault sync all

# Sync just bookmarks or likes
uv run tweetxvault sync bookmarks
uv run tweetxvault sync likes

# Full re-sync from scratch (does not delete existing data)
uv run tweetxvault sync all --full

# Limit to N pages per collection
uv run tweetxvault sync all --limit 5
```

### Export to JSON

```bash
# Export all archived tweets
uv run tweetxvault export json

# Export a specific collection
uv run tweetxvault export json --collection bookmarks

# Export to a specific path
uv run tweetxvault export json --out ~/exports/my-bookmarks.json
```

### Other commands

```bash
# Force-refresh query IDs from Twitter's JS bundles
uv run tweetxvault auth refresh-ids
```

## Unattended sync via cron

```cron
# Sync bookmarks and likes every 6 hours
0 */6 * * * cd /path/to/tweetxvault && uv run tweetxvault sync all 2>> /tmp/tweetxvault.log
```

A process lock prevents overlapping runs.

## Configuration

All configuration is optional. Defaults work out of the box with Firefox cookie extraction.

### Sync tuning (config.toml or env vars)

| Setting | Default | Env var |
|---------|---------|---------|
| `sync.page_delay` | `2.0` s | `TWEETXVAULT_PAGE_DELAY` |
| `sync.max_retries` | `3` | `TWEETXVAULT_MAX_RETRIES` |
| `sync.backoff_base` | `2.0` s | `TWEETXVAULT_BACKOFF_BASE` |
| `sync.cooldown_threshold` | `3` consecutive 429s | `TWEETXVAULT_COOLDOWN_THRESHOLD` |
| `sync.cooldown_duration` | `300.0` s | `TWEETXVAULT_COOLDOWN_DURATION` |
| `sync.timeout` | `30.0` s | `TWEETXVAULT_TIMEOUT` |

## Data storage

Data lives in XDG-standard directories:

| Purpose | Default path |
|---------|-------------|
| Config | `~/.config/tweetxvault/` |
| Archive (LanceDB) | `~/.local/share/tweetxvault/archive.lancedb/` |
| Cache (query IDs) | `~/.cache/tweetxvault/` |

Override with `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`.

## How it works

tweetxvault calls Twitter's internal GraphQL API — the same endpoints the web app uses. It:

1. Resolves session cookies (env/config/Firefox)
2. Discovers current GraphQL query IDs by parsing Twitter's JS bundles (with a 24h TTL cache and static fallbacks)
3. Fetches timeline pages with the proper headers, feature flags, and cursor pagination
4. Stores raw API responses + parsed tweet records + collection memberships in a single LanceDB table
5. Tracks sync state per collection so the next run picks up where it left off

## Development

```bash
uv sync

# Run tests
uv run pytest

# Lint and format
uv run ruff check
uv run ruff format --check
```

See [`docs/`](docs/README.md) for architecture docs, the implementation plan, and research notes.

## License

MIT
