# Changelog

This changelog tracks user-visible changes and recorded release validation
status for published versions. Entries for `v0.1.0`, `v0.1.1`, and `v0.2.0`
were backfilled from git tags and `WORKLOG.md`.

The format is loosely based on Keep a Changelog.

## [0.2.4] - 2026-04-23

### Added

- `tweetxvault --version` prints the installed package version and, when run
  from a git checkout, includes the short commit hash plus a `dirty` marker.
- `tweetxvault sync` now runs archive enrich, thread expansion, article
  refresh, media download, and URL unfurl by default after the sync pass, with
  `--skip-enrich`, `--skip-threads`, `--skip-articles`, `--skip-media`, and
  `--skip-unfurl` escape hatches for users who want the older sync-only
  behavior.
- Follow-up jobs (`articles refresh`, `media download`, `unfurl`, `threads
  expand`) now emit TTY progress bars and status lines during interactive runs
  instead of staying mostly silent until the final summary.

### Changed

- CLI help text and `README.md` were expanded to document sync flags, backfill
  markers, and representative nested command options so `tweetxvault <cmd>
  --help` is self-explanatory.
- Archive enrich now batches TweetDetail success/failure writes into chunked
  Lance `merge_insert` calls to drastically reduce LanceDB version churn on
  long-running or interrupted enrich jobs.
- Interrupted archive imports now run `ArchiveStore.optimize()` on the way
  out so the stored archive stays compact even when a run is aborted.
- The sampled archive-import debug flag was renamed for clarity (old help text
  and README copy updated to match).

### Fixed

- Security: refreshed the lock to pull in `pygments` 2.20.0 (CVE-2026-4539, a
  ReDoS in an unused lexer reached via transitive deps) and bumped the `pytest`
  dev pin to 9.0.3 (CVE-2025-71176, predictable `/tmp/pytest-of-*` directory);
  `pip-audit` now reports no known vulnerabilities against the locked runtime
  + dev graph.
- Cleaned up the dev-dependency configuration: moved dev deps to a PEP 735
  `[dependency-groups].dev` and removed the deprecated
  `[tool.uv].dev-dependencies` block, so `uv sync` no longer warns.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `UV_CACHE_DIR=/tmp/uv-cache uv build`
- `uvx --from twine twine check dist/tweetxvault-0.2.4*`
- `uv run --isolated --with dist/tweetxvault-0.2.4-py3-none-any.whl tweetxvault --help`

## [0.2.3] - 2026-03-23

### Added

- Shipped Grailbird archive conversion via `tweetxvault import grailbird`, so
  pre-2018 CSV-based Twitter exports can be converted from an installed package
  instead of requiring a repo checkout.

### Changed

- Release artifacts now include the packaged Grailbird converter module plus the
  Grailbird conversion guide in the source distribution.
- Grailbird validation now runs through the normal `pytest` suite instead of a
  standalone repo-root unittest.

### Fixed

- Grailbird archives without `data/js/user_details.js` no longer persist a fake
  archive owner id of `"unknown"`, so later authenticated sync/import follow-up
  can still establish the real archive owner metadata.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `UV_CACHE_DIR=/tmp/uv-cache uv build`
- `uvx --from twine twine check dist/tweetxvault-0.2.3*`
- `uv run --isolated --with dist/tweetxvault-0.2.3-py3-none-any.whl tweetxvault --help`

## [0.2.2] - 2026-03-21

### Changed

- TweetDetail-heavy follow-up jobs now pace themselves from X's live
  `x-rate-limit-*` headers when available instead of relying on a fixed
  per-request delay.
- The shared GraphQL client now honors `Retry-After` and `x-rate-limit-reset`
  on `429` responses before falling back to the existing retry/cooldown path.
- Removed the manual `--sleep` overrides for archive enrich/import follow-up,
  thread expansion, and article refresh; the default TweetDetail delay floor is
  now `0s`.

### Fixed

- Archive enrich no longer burns through the TweetDetail bucket at a fixed
  `1s/request` pace before failing after a guessed cooldown on accounts where X
  exposes the actual rate-limit window.

### Validation

- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
- `uv run ruff check`
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- `UV_CACHE_DIR=/tmp/uv-cache uv build`
- `uvx --from twine twine check dist/tweetxvault-0.2.2*`
- `uv run --isolated --with dist/tweetxvault-0.2.2-py3-none-any.whl tweetxvault --help`

## [0.2.1] - 2026-03-21

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
- Installation docs now cover global `uv tool` / `pipx` usage and editable
  `uv tool install -e .` development installs from a local checkout.
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
- `UV_CACHE_DIR=/tmp/uv-cache uv build`
- `uvx --from twine twine check dist/tweetxvault-0.2.1*`
- `uv run --isolated --with dist/tweetxvault-0.2.1-py3-none-any.whl tweetxvault --help`

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

[0.2.4]: https://github.com/lhl/tweetxvault/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/lhl/tweetxvault/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/lhl/tweetxvault/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/lhl/tweetxvault/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/lhl/tweetxvault/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/lhl/tweetxvault/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/lhl/tweetxvault/releases/tag/v0.1.0
