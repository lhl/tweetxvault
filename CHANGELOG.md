# Changelog

This changelog tracks user-visible changes and recorded release validation status.
Entries for `v0.1.0`, `v0.1.1`, and `v0.2.0` were backfilled from git tags and
`WORKLOG.md`.

The format is loosely based on Keep a Changelog.

## [Unreleased]

### Added

- Search filters for surfaced result types and collections via
  `tweetxvault search --type ... --collection ...`.
- Search result sorting via `tweetxvault search --sort relevance|newest|oldest`.
- `tweetxvault stats` for archive totals, collection coverage, storage health,
  and follow-up queues.

### Changed

- `tweetxvault stats` now explains backfill/follow-up labels inline and reports
  optimize state as `ok` or `run optimize`.
- Archive stats and related preload helpers are much faster on large archives.
- TweetDetail-heavy jobs now wait `1s` between requests by default, with a
  `--sleep 0` escape hatch for one-off runs.
- TweetDetail-heavy jobs now retry 429s more conservatively by default before
  entering the shared cooldown window.

### Fixed

- Empty resumed backfill pages now clear stale saved cursors instead of leaving
  `resume older` stuck on the collection.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`

## [0.2.0] - 2026-03-18

### Added

- Official X archive import from ZIP files or extracted directories.
- Archive follow-up commands via `tweetxvault import x-archive --enrich` and
  `tweetxvault import enrich`.
- `tweetxvault sync ... --head-only` to clear stale saved backfill state without
  deleting archived data.

### Changed

- `tweetxvault view ... --limit N` now avoids full-export work before slicing,
  which makes large archives much faster to inspect interactively.
- Archive/tweet list rendering is shared across `view` and `search`, with
  corrected chronology for `view --sort oldest|newest`.
- Project docs and PyPI metadata now present X archive import as a first-class
  feature.

### Fixed

- Archive import rerun, progress, and follow-up edge cases across `--regen`,
  `--enrich`, and standalone enrich flows.
- Archive reconciliation now stays head-only instead of unexpectedly resuming an
  unrelated saved sync backfill.

### Validation

- `uv build`
- `uvx --from twine twine check dist/*`
- `uv run pytest -q`

## [0.1.1] - 2026-03-16

### Changed

- Semantic-search embeddings are now L2-normalized and LanceDB vector/hybrid
  search explicitly uses cosine distance.
- Existing archives can be upgraded to the new embedding scale with
  `tweetxvault embed --regen`.
- Shared query-id resolution moved into `tweetxvault.utils`, with stale sync
  helper parameters removed during the same cleanup pass.

### Validation

- `uv run pytest tests/test_sync.py tests/test_articles.py tests/test_threads.py tests/test_cli.py -q`
- `uv run ruff check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`
- `uv run ruff format --check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`
- `uv run pytest -q`
- `uv build`
- `uv run --with twine twine check dist/tweetxvault-0.1.1*`
- `uv run --isolated --with dist/tweetxvault-0.1.1-py3-none-any.whl tweetxvault --help`

## [0.1.0] - 2026-03-16

### Added

- Initial PyPI release with incremental sync for bookmarks, likes, and authored
  tweets.
- Raw API capture storage plus normalized secondary extraction for tweet
  objects, relations, media, URLs, and articles.
- Full-text and optional semantic search, plus terminal view and JSON/HTML
  export commands.
- Browser cookie extraction across Firefox and Chromium-family browsers.

### Changed

- PyPI metadata and artifact contents were tightened so release builds better
  reflect shipped capabilities and stop bundling repo-internal files.

### Validation

- `uv build`
- `uv run --with twine twine check dist/*`
- Wheel and sdist contents inspected to confirm repo-internal files no longer
  ship in release artifacts.

[Unreleased]: https://github.com/lhl/tweetxvault/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/lhl/tweetxvault/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/lhl/tweetxvault/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lhl/tweetxvault/releases/tag/v0.1.0
