# WORKLOG

## 2026-03-27

- Added TTY-only progress/status output to follow-up maintenance commands:
  - `tweetxvault articles refresh`, `tweetxvault media download`, and
    `tweetxvault unfurl` now emit useful interactive status lines and tqdm-backed
    progress bars instead of staying mostly silent until the final summary
  - article refresh also now surfaces TweetDetail retry/rate-limit pacing status
    during interactive runs, matching the behavior of archive enrich/thread jobs
  - updated the README and added focused tests that simulate an interactive
    console for all three commands
  - validation:
    - `uv run ruff check tweetxvault/interactive.py tweetxvault/articles.py tweetxvault/media.py tweetxvault/unfurl.py tests/test_articles.py tests/test_media.py tests/test_unfurl.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/interactive.py tweetxvault/articles.py tweetxvault/media.py tweetxvault/unfurl.py tests/test_articles.py tests/test_media.py tests/test_unfurl.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_articles.py tests/test_media.py tests/test_unfurl.py`

- Fixed the inconsistent archive-import sampling flag:
  - renamed `tweetxvault import x-archive --limit` to `--sample-limit` so
    `--limit` stops meaning something completely different on that one command
  - removed the extra `--debug` requirement because `--sample-limit` already
    makes the sampled/non-completed import semantics explicit
  - updated README/docs/tests to describe sampled imports with the new flag name
  - validation:
    - `uv run ruff check tweetxvault/cli.py tweetxvault/archive_import.py tests/test_cli.py tests/test_archive_import.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/cli.py tweetxvault/archive_import.py tests/test_cli.py tests/test_archive_import.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_cli.py -k 'import_x_archive or import_x_archive_help or import_enrich' tests/test_archive_import.py -k 'sample_limit or sampled or enrich_imported_archive'`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault import x-archive --help`

- Documented the manual follow-up/backfill path for older archives:
  - added a dedicated `README.md` section showing the full catch-up sequence for
    pre-default-followup archives: `import enrich`, `threads expand`, `articles
    refresh`, `media download`, and `unfurl`
  - explicitly documented that each of those follow-up commands supports
    `--limit` so bounded incremental test runs are easy before committing to a
    long archive-maintenance pass
  - added concrete `--limit` examples to the media, unfurl, thread-expansion,
    and article-refresh sections instead of leaving that fact implicit in `--help`

- Realigned the default sync UX around the actual archive-maintenance workflow:
  - bare `tweetxvault sync` now runs the same bookmarks + likes pass as `sync all`
    instead of acting like a help-only command group
  - the default sync surface now explicitly describes and forwards the automatic
    follow-up jobs for archive enrich, threads expand, articles refresh, media
    download, and unfurl, with `--skip-*` flags as the per-run escape hatches
  - updated `README.md` examples and the cron example to prefer `tweetxvault
    sync`, while keeping authored tweets explicit via `tweetxvault sync tweets`
  - validation:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_cli.py -k 'sync or version'`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_sync.py -k 'followups or interrupt'`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault sync --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault sync all --help`

- Added best-effort interrupt compaction for long-running archive writers:
  - introduced shared write tracking in `tweetxvault/jobs.py` using both
    committed batch/row counts and Lance version deltas so `Ctrl-C` cleanup is
    driven by real writes instead of guessing from command success paths
  - `sync`, archive import/enrich, `threads expand`, `articles refresh`,
    `media download`, and `unfurl` now mark writes at commit/flush points rather
    than only at the end of the runner, which lets interrupted runs compact when
    they have already done substantial work
  - first interrupt now prints a compacting message and runs a best-effort
    optimize; a second interrupt during optimize skips it and warns to run
    `tweetxvault optimize` later
  - validation:
    - `uv run ruff check tweetxvault/jobs.py tweetxvault/sync.py tweetxvault/archive_import.py tweetxvault/articles.py tweetxvault/threads.py tweetxvault/media.py tweetxvault/unfurl.py tests/test_jobs.py tests/test_sync.py tests/test_archive_import.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/jobs.py tweetxvault/sync.py tweetxvault/archive_import.py tweetxvault/articles.py tweetxvault/threads.py tweetxvault/media.py tweetxvault/unfurl.py tests/test_jobs.py tests/test_sync.py tests/test_archive_import.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_jobs.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/test_sync.py::test_sync_collection_interrupt_best_effort_optimizes_after_committed_pages tests/test_archive_import.py::test_enrich_pending_rows_batches_detail_writes tests/test_archive_import.py::test_interrupted_import_marks_manifest_failed_and_rerun_reuses_archive_captures tests/test_articles.py tests/test_threads.py tests/test_media.py tests/test_unfurl.py` (outside the sandbox because LanceDB-backed tests stalled under sandboxing)

- Tightened the repo workflow around CLI flags/help/docs:
  - updated `AGENTS.md` so future user-facing flags, command groups, and status
    markers must keep explicit CLI help text, `README.md`, and representative
    help-output tests in sync
  - this turns the recent sync/help/backfill cleanup into an explicit standing
    repo rule instead of tribal knowledge

- Expanded CLI help/documentation coverage beyond `sync`:
  - audited the remaining command surfaces and added explicit help text for the
    nested command groups plus representative bare flags such as `view --limit`
    / `--sort`, `media download --photos-only`, `unfurl --retry-failed`,
    `embed --regen`, and `search --mode`
  - updated `README.md` with short sync-flag and backfill-marker bullets,
    including the exact `tweetxvault sync <collection> --head-only` command
    used to clear `resume older`
  - validation:
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`
    - `uv run pytest -q tests/test_cli.py -k "help or version"`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault sync likes --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault view bookmarks --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault media download --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault search --help`

- Fixed the sync help surface after confirming the bare group output was too
  sparse:
  - added descriptive help text to the `sync` command group plus the
    `bookmarks`, `likes`, `tweets`, and `all` subcommands
  - added missing help strings for the sync-only `--full`, `--backfill`, and
    `--limit` flags so `tweetxvault sync likes --help` is self-explanatory
  - validation:
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`
    - `uv run pytest -q tests/test_cli.py -k "sync_help or version"`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault sync --help`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault sync likes --help`

- Added `tweetxvault --version` for local-build verification:
  - the CLI now prints `tweetxvault <semver>` and, when invoked from a git
    checkout, includes the short commit hash plus a `dirty` marker when tracked
    files differ from `HEAD`
  - this is primarily for editable `uv tool install -e .` workflows where the
    package version alone is not enough to distinguish local commits before push
  - validation:
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`
    - `uv run pytest -q tests/test_cli.py -k version`

## 2026-03-26

- Reduced LanceDB version churn for long-running archive detail enrichment:
  - root cause: `tweetxvault import enrich` committed one Lance `merge_insert`
    per TweetDetail success/failure row update, so interrupting a 30k+ item run
    could leave tens of thousands of table versions behind until a later manual
    `tweetxvault optimize`
  - changed archive detail persistence/update paths to accept a shared
    `_PageBuffer`, and batched archive follow-up writes in chunks of 100 tweets
    before the existing end-of-job optimize step
  - this keeps completed runs on the same final-compact path, but makes
    interrupted/rate-limited enrich jobs much less pathological while also
    cutting the number of versions the final optimize has to compact
  - validation:
    - `uv run ruff check tweetxvault/archive_import.py tweetxvault/storage/backend.py tests/test_archive_import.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/archive_import.py tweetxvault/storage/backend.py tests/test_archive_import.py`
    - `uv run pytest -q tests/test_archive_import.py::test_enrich_pending_rows_batches_detail_writes tests/test_archive_import.py::test_import_x_archive_detail_api_errors_become_transient_failures tests/test_archive_import.py::test_import_x_archive_detail_stale_query_id_leaves_rows_retryable`
    - `uv run pytest -q tests/test_jobs.py tests/test_threads.py tests/test_storage.py::test_persist_page_creates_single_version tests/test_storage.py::test_archive_stats_reports_followup_work`

## 2026-03-23

- Prepared release metadata for `v0.2.3`:
  - bumped package version from `0.2.2` to `0.2.3`
  - summarized the packaged Grailbird converter, owner-id fallback fix, and
    release-artifact/test coverage updates in `CHANGELOG.md`
  - release validation/build commands for this cut:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
    - `uv run ruff check`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
    - `UV_CACHE_DIR=/tmp/uv-cache uv build`
    - `uvx --from twine twine check dist/tweetxvault-0.2.3*`
    - `uv run --isolated --with dist/tweetxvault-0.2.3-py3-none-any.whl tweetxvault --help`

- Added a deferred `docs/PLAN-FUTURE.md` note for deleted-tweet handling edge cases:
  - thread-expansion tombstones currently disappear silently
  - archive-deleted/live-restored tweets may need an enrichment-state consistency check if that workflow is revisited
  - deletion reasons are still coarse (`deleted` vs `not_found`) and could be revisited if richer tombstone metadata appears later

- Restored the docs index entry for `docs/GRAILBIRD.md` in `docs/README.md` so the documentation index matches the shipped guide set after the Grailbird feature landed.

- Finished Grailbird archive integration as a shipped feature instead of a checkout-only helper:
  - moved the converter into `tweetxvault/grailbird.py`
  - added `tweetxvault import grailbird <input_dir> <output_dir> [--force]`
  - kept `convert_grailbird.py` as a thin compatibility wrapper for checkout users
  - updated `README.md`, `docs/GRAILBIRD.md`, `docs/PLAN.md`, and `docs/IMPLEMENTATION.md` to reflect the shipped CLI surface and current semantics

- Fixed the Grailbird owner-metadata mismatch path:
  - Grailbird conversions now mark synthetic archives with `archiveInfo.sourceFormat = "grailbird"`
  - when `data/js/user_details.js` is missing or unparseable, the converter leaves account id/username unset instead of writing `"unknown"`
  - the importer now accepts that sparse Grailbird identity without persisting a fake archive owner id, so a later authenticated sync can still establish the real owner metadata

- Moved Grailbird validation into the normal pytest tree:
  - replaced the repo-root `test_convert_grailbird.py` unittest with `tests/test_grailbird.py`
  - added Grailbird conversion/import round-trip coverage plus a CLI summary test in `tests/test_cli.py`
  - validation passed with:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check tweetxvault/grailbird.py tweetxvault/archive_import.py tweetxvault/cli.py tests/test_grailbird.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/grailbird.py tweetxvault/archive_import.py tweetxvault/cli.py tests/test_grailbird.py tests/test_cli.py convert_grailbird.py`
    - `uv run pytest tests/test_grailbird.py tests/test_archive_import.py tests/test_cli.py -q`
    - `uv run pytest -q`
  - the earlier aggregate-suite stall was a restricted-sandbox artifact; rerunning with full-access sandbox permissions completed normally

## 2026-03-21

- Expanded `docs/PLAN-FUTURE.md` with the next likely product-surface areas:
  - backup/portability, with the current practical story called out explicitly
    as "copy the XDG-managed archive directories to another machine"
  - richer `view` / `search` filters plus alternate output formats like JSON,
    Markdown, and CSV
  - a future TUI as a separate interactive surface rather than more ad hoc Rich
    table growth
  - low-priority multi-account support, explicitly separated from the active
    single-account archive model

- Published `v0.2.2`:
  - pushed `main` and the annotated `v0.2.2` tag to GitHub
  - published only the release artifacts with
    `uv publish dist/tweetxvault-0.2.2-py3-none-any.whl dist/tweetxvault-0.2.2.tar.gz`
  - verified live PyPI metadata via `https://pypi.org/pypi/tweetxvault/json`
    after the first immediate resolver check lagged behind the upload
  - verified the published install path with
    `uvx --refresh --from "tweetxvault==0.2.2" tweetxvault --help`

- Prepared release metadata for `v0.2.2`:
  - bumped package version from `0.2.1` to `0.2.2`
  - summarized the TweetDetail rate-limit pacing fix plus `--sleep` removal in
    `CHANGELOG.md`
  - release validation/build commands for this cut:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
    - `uv run ruff check`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
    - `UV_CACHE_DIR=/tmp/uv-cache uv build`
    - `uvx --from twine twine check dist/tweetxvault-0.2.2*`
    - `uv run --isolated --with dist/tweetxvault-0.2.2-py3-none-any.whl tweetxvault --help`

- Made TweetDetail follow-up jobs adapt to observed rate-limit headers instead
  of fixed cooldown guesswork:
  - taught the shared HTTP client to honor `Retry-After` and
    `x-rate-limit-reset` on `429` responses, so `TweetDetail` waits for the
    server-advertised reset window when available instead of always falling back
    to `30s` / `60s` / `300s`
  - added adaptive per-request pacing for archive enrich, article refresh, and
    thread expansion so successful responses with `x-rate-limit-remaining` /
    `x-rate-limit-reset` can stretch the next inter-request sleep above the
    configured `detail_delay` when the current bucket is tight
  - kept the existing fixed-delay/backoff path as the fallback when headers are
    missing, and added regression coverage for both `Retry-After` handling and
    adaptive pacing
  - validation:
    - `uv run ruff check tweetxvault/client/base.py tweetxvault/archive_import.py tweetxvault/articles.py tweetxvault/threads.py tests/test_client.py tests/test_articles.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/client/base.py tweetxvault/archive_import.py tweetxvault/articles.py tweetxvault/threads.py tests/test_client.py tests/test_articles.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_client.py tests/test_articles.py tests/test_threads.py tests/test_archive_import.py -q`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
- Simplified TweetDetail follow-up controls after confirming live header pacing:
  - removed the user-facing `--sleep` overrides from archive import/enrich,
    thread expansion, and article refresh
  - changed the default `detail_delay` floor from `1.0s` to `0.0s`, so the
    normal path relies on live `x-rate-limit-*` headers when available and only
    falls back to retry/backoff/cooldown when they are absent or a `429`
    actually occurs
  - kept the internal `detail_delay` config field as a compatibility floor, but
    stopped documenting it as a normal knob
  - validation:
    - `uv run ruff check tweetxvault/cli.py tweetxvault/config.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/cli.py tweetxvault/config.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_cli.py tests/test_client.py tests/test_articles.py tests/test_threads.py tests/test_archive_import.py -q`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`

- Published `v0.2.1`:
  - pushed `main` and the annotated `v0.2.1` tag to GitHub
  - first `uv publish` attempt failed because `dist/` still contained older
    release artifacts and `uv publish` defaulted to `dist/*`
  - reran the upload against only the release artifacts:
    `uv publish dist/tweetxvault-0.2.1-py3-none-any.whl dist/tweetxvault-0.2.1.tar.gz`
  - verified the published package from PyPI with
    `uvx --from "tweetxvault==0.2.1" tweetxvault --help`

- Prepared release metadata for `v0.2.1`:
  - bumped package version from `0.2.0` to `0.2.1`
  - added a concise `v0.2.1` feature/fix summary to `CHANGELOG.md` without
    keeping a rolling `Unreleased` section
  - tightened the publish checklist so future releases add the next version
    entry directly instead of pretending we maintain in-progress changelog notes
  - documented the existing global CLI install paths in `README.md` so users can
    use `uv tool install tweetxvault`, `pipx install tweetxvault`, or `uvx
    tweetxvault --help` instead of only `uv run` from a checkout
  - documented the editable dev workflow too: `uv tool install -e .` for a
    globally available `tweetxvault` command that follows the current checkout
  - release validation/build commands for this cut:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
    - `uv run ruff check`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`
    - `UV_CACHE_DIR=/tmp/uv-cache uv build`
    - `uvx --from twine twine check dist/tweetxvault-0.2.1*`
    - `uv run --isolated --with dist/tweetxvault-0.2.1-py3-none-any.whl tweetxvault --help`

- Expanded archive search from tweet-membership rows into surfaced search items:
  - `tweetxvault search` now defaults to searching both posts and articles, instead of only `record_type='tweet'`
  - added comma-delimited `--type` (`post`, `article`) and `--collection` (`bookmark`, `like`, `tweet`) filters
  - deduped post hits by `tweet_id` and aggregated bookmark/like/tweet memberships into one SERP label so search output no longer leaks raw storage duplication
  - kept the user-facing CLI flag as `--type` while documenting in code that it maps to internal search-result kinds rather than storage-level `record_type`
- Updated the shared search/table rendering:
  - search results now show a stacked `type · collections` label with the numeric score on the next line in the same cell
  - article hits render article text/title content while still linking back to the owning tweet URL
- Added chronological sorting to search:
  - `tweetxvault search` now accepts `--sort relevance|newest|oldest` and still defaults to relevance
  - chronological search sorting now reorders the fetched relevance/semantic result set by `created_at` before rendering, keeping `--sort newest|oldest` fast while preserving relevance-first candidate selection
  - semantic modes no longer need a forced FTS fallback just to support chronological display ordering
- Added `tweetxvault stats` for archive introspection:
  - summarizes overall archive totals for unique posts, articles, membership rows, raw captures, media, and URLs
  - breaks collections out into bookmark/like/tweet counts with first/last post timestamps, last sync time, and backfill status
  - reports storage health including DB/media disk usage, LanceDB version count, and a lightweight optimize recommendation
  - now also surfaces follow-up queues for archive enrichment state, rehydrate gaps (missing normalized tweet objects), and pending thread expansion targets from both membership tweets and linked status URLs
- Optimized `tweetxvault stats` after the first live run proved too slow:
  - removed follow-up-section rescans over full Lance rows and switched the thread/enrichment summary path to narrow-column scans plus in-memory set reuse inside `ArchiveStore.archive_stats()`
  - tightened the shared `list_membership_tweet_ids(...)`, `list_known_tweet_ids(...)`, `list_raw_capture_target_ids(...)`, and `list_url_ref_rows()` helpers to select only the columns they actually need, which also reduces `threads expand` startup overhead
  - measured on the live archive after the optimization: `uv run tweetxvault stats` completed in about `2.96s` on an archive with `116024` unique posts and `116115` membership rows
- Clarified `tweetxvault stats` output after the first user pass:
  - changed backfill labels from storage-ish wording (`resume saved`, `clear`) to more explicit user-facing states (`resume older`, `none saved`)
  - simplified the storage optimize hint to `ok` vs `run optimize`
  - added a legend directly under the stats tables so end users can distinguish archive TweetDetail follow-up, local rehydrate rebuild gaps, and the two thread-expansion target sources without knowing the internal storage model
- Fixed stale `resume older` sync-state flags after end-of-history backfill pages:
  - some X timeline backfill responses can return `page_tweets 0` while still echoing the same bottom cursor back, which left `backfill_incomplete=True` even though the resumed pass had effectively finished
  - changed the sync-state transition so an empty resumed backfill page clears the saved cursor instead of preserving it
  - added a regression covering the exact case: duplicate-stopped head pass, resumed backfill, empty page, repeated cursor
- Added release-process docs and backfilled user-facing release history:
  - added `docs/PUBLISH.md` as the canonical release punch list covering version bumps, changelog updates, validation, tagging, PyPI publication, and post-publish verification
  - added a root `CHANGELOG.md` with backfilled entries for `v0.1.0`, `v0.1.1`, and `v0.2.0`, plus room for top-level per-release summaries going forward
  - updated `AGENTS.md` and `docs/README.md` so future release work points directly at the publish checklist and changelog
- Slowed 429 retries for TweetDetail-heavy follow-up jobs without penalizing normal timeline sync:
  - added separate `sync.detail_max_retries` / `sync.detail_backoff_base` knobs, defaulting to `2` retries at `30s` / `60s` before the existing `300s` cooldown
  - wired those slower retry settings into archive enrich, thread expansion, and article refresh while leaving the timeline sync path on the faster `2s`-based retry schedule
  - added coverage for per-request retry overrides in the HTTP client and for detail-specific retry config in thread expansion tests
- Added explicit pacing for TweetDetail-heavy follow-up jobs:
  - added `sync.detail_delay` with a default of `1.0s` between detail requests so long enrich/thread/article runs are less bursty against X's per-tweet detail endpoint
  - added `--sleep` overrides on `tweetxvault import x-archive`, `tweetxvault import enrich`, `tweetxvault threads expand`, and `tweetxvault articles refresh`, with `--sleep 0` disabling the pacing for one run
  - added focused coverage for the pacing behavior in article refresh plus CLI forwarding for the new per-run override
- Validation:
  - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check`
  - `uv run ruff check`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` (outside the sandbox because LanceDB store creation stalled under sandboxing)

## 2026-03-18

- Prepared release metadata for `v0.2.0`:
  - bumped package version from `0.1.1` to `0.2.0`
  - updated the PyPI package summary to mention official X archive import support
  - verified `README.md` and CLI help still match the shipped sync/import UX
  - `uv build` succeeded and `uvx --from twine twine check dist/*` passed
  - full test suite passed with `uv run pytest -q`

- Added `sync --head-only` as a first-class escape hatch for stale saved backfill state:
  - `tweetxvault sync bookmarks|likes|tweets --head-only` now clears the saved backfill cursor for the targeted collection, runs only the head pass, and leaves the collection without a resumable historical backfill cursor
  - `tweetxvault sync all --head-only` applies the same behavior to bookmarks + likes
  - `--head-only` is rejected when combined with `--full`, `--backfill`, or `--article-backfill`
  - kept the earlier archive-import fix intact: archive import/enrich already uses head-only reconciliation internally rather than resuming normal sync backfills
  - added sync/CLI regressions for the new flag, including state clearing and forwarding coverage

## 2026-03-17

- Stopped archive follow-up from resuming old normal sync backfills:
  - added `resume_backfill=True` to `sync_collection(...)` and gated the second-pass backfill resume behind it while preserving the saved backfill cursor/state
  - changed archive import/enrich live reconciliation to use `resume_backfill=False`, so archive follow-up now does a head-only live pass instead of unexpectedly continuing an old likes/tweets backfill
  - this keeps normal `sync likes` / `sync tweets` resumable behavior intact while making archive reconciliation better match the import/enrich use case, especially when older archive rows are no longer reachable from the current X frontend timelines
  - added regressions for both the skipped-backfill sync path and the archive reconciliation wiring, and updated the README to document the new behavior

- Refreshed the root `README.md` so the PyPI/project landing page matches the current shipped UX:
  - promoted official X archive import to a first-class feature in the project overview/features list
  - updated the archive-import section with the current follow-up/rerun semantics (`--enrich`, standalone `import enrich`, `--regen`, interrupt recovery, auth-dependent reconciliation, interactive progress, and sampled `--debug --limit`)
  - clarified terminal view semantics so users know timestamps render in local time and likes sort chronologically by tweet `created_at`, not an unavailable `liked_at`

- Split the stale future-roadmap idea out of the active plan docs:
  - added `docs/PLAN-FUTURE.md` as a deferred post-v0.2 roadmap focused on discoverability work rather than current implementation
  - queued v0.3 around link-centric discovery first, with ArchiveBox integration as the main future search expansion path
  - captured VLM/media understanding, clustering, and knowledge-graph work as explicitly future-only items so they do not blur the active archive/import plan
  - updated `docs/README.md` to index the new future-plan document

- Optimized `tweetxvault view ... --limit N` for large archives so interactive views stop doing full-export work before slicing:
  - added `ArchiveStore.count_export_rows(...)` and extended `export_rows(...)` with `limit` and `include_raw_json` options
  - the CLI `view` commands now count separately, export only the visible rows, and skip `raw_json` materialization for terminal rendering while preserving the existing full JSON/HTML export path
  - added storage coverage proving limited exports only hydrate secondary rows for the selected tweet ids
  - real-data timing on the live archive: `count_export_rows('all')` ~`0.025s`, limited `export_rows('all', limit=10, include_raw_json=False)` ~`1.04s`, full `uv run tweetxvault view all --limit 10` ~`2.3s` wall-clock instead of the prior multi-second/full-scan stall

- Unified tweet/list CLI rendering behind one shared renderer in `tweetxvault/cli.py`:
  - archive `view ...` and `search` now share the same table layout, local-time formatting, divider style, text truncation, and URL column
  - this removes the older inline search table path so future tweet/list output changes only need to be made in one place
  - added CLI coverage to lock search onto the shared renderer and to keep the archive view URL/local-time formatting under test

- Fixed archive `view ... --sort oldest|newest` chronology so exported rows are ordered by parsed tweet `created_at` instead of collection `sort_index`:
  - this corrects misleading archive views where imported likes/bookmarks could surface recent rows first even when older tweet timestamps were present
  - rows missing `created_at` now sort after known timestamps instead of interleaving via synthetic archive sort indices
  - added focused storage coverage for `oldest`/`newest` ordering when `sort_index` conflicts with tweet time and when timestamps are missing

- Tracked the remaining post-rollout archive-import cleanup/perf items in the docs:
  - updated `docs/PLAN.md` to reflect that X-archive import is shipped and to capture the remaining follow-ups (media-copy row-scan narrowing, `--regen`/manifest-history semantics, and possible lighter-weight prefetching) as explicit plan items
  - added a matching unchecked follow-up block to `docs/IMPLEMENTATION.md` so those items stay visible in the implementation checklist instead of getting lost in review notes

- Tightened archive follow-up safety before the first real `import enrich` run:
  - stopped `_enrich_pending_rows(...)` from classifying systemic `TweetDetail` failures (`StaleQueryIdError`, auth expiry, feature-flag drift, rate-limit exhaustion) as per-tweet terminal/transient states; those now bubble once to follow-up warnings so rows remain retryable
  - restricted terminal archive detail classification to HTTP `410`; unsafe `404 => not found` handling is no longer used in the TweetDetail enrichment path
  - preserved prior manifest warnings when reusing an existing completed archive with `import x-archive --enrich`
  - constrained `--regen` archive-owned file cleanup to the managed `media/` subtree only
  - changed authored archive import to build secondary graphs per chunk instead of precomputing them for the full archive up front
  - clarified the missing-bookmarks warning/docs to say this is expected for current official X archives
  - validation:
    - `uv run pytest tests/test_archive_import.py -q`
    - `uv run ruff check tweetxvault/archive_import.py tests/test_archive_import.py`
    - `uv run ruff format --check tweetxvault/archive_import.py tests/test_archive_import.py`
    - `uv run pytest -q`

- Clarified live reconciliation output during archive follow-up and standalone sync/enrich runs:
  - changed the shared sync logger to print explicit `head` vs `backfill` pass labels instead of one ambiguous cumulative `tweets N` counter
  - per-page progress now includes both `page_tweets` and `total_tweets`, and resumed syncs print a `resuming saved backfill pass` line before continuing older pages
  - confirmed the archive-import speedup work does not need to be repeated for `import enrich` / `threads expand`; those follow-up jobs are dominated by network fetches and the intentional sync `page_delay`, not by the LanceDB point-lookup bottleneck that affected archive ingest
  - validation:
    - `uv run pytest tests/test_sync.py -q`
    - `uv run ruff check tweetxvault/sync.py tests/test_sync.py`
    - `uv run ruff format --check tweetxvault/sync.py tests/test_sync.py`

- Optimized archive import against large existing LanceDB archives by bulk-prefetching row state before each import chunk:
  - added `ArchiveStore.prefetch_rows(...)` so archive import can hydrate `cursor.existing_rows` in row-key batches instead of issuing per-record point lookups during merge construction
  - changed authored-tweet import to precompute tweet graphs in small chunks, bulk-prefetch the relevant `tweet` / `tweet_object` / `tweet_relation` / `media` / `url` / `url_ref` / `article` row keys, then merge the chunk in one buffered pass
  - changed like import to bulk-prefetch `tweet:like` and `tweet_object` row keys before sparse placeholder decisions, removing the large-table point-lookup bottleneck there too
  - added storage coverage for `prefetch_rows(...)`
  - copied the real optimized archive DB to `/tmp` and reran the sampled import benchmark on the copy to quantify the improvement without touching the real archive:
    - baseline on copied live archive before the prefetch change: authored `39.09s` (`25.6 tweets/s`), likes `24.69s` (`40.5 likes/s`)
    - after the prefetch change on a fresh copied live archive: authored `1.37s` (`728.1 tweets/s`), likes `0.55s` (`1806.1 likes/s`)
  - validation:
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Clarified the archive-import UX wording after the sampled/debug progress change:
  - fixed `tweetxvault import x-archive --debug` help text and README copy to state that interactive TTY runs already show tqdm progress bars by default
  - `--debug` is now documented as adding per-phase timing diagnostics on top of the normal interactive progress output, not as the switch that enables progress bars

- Finished the queued archive-import hardening and recovery work:
  - added `tweetxvault import x-archive --regen` to clear archive-import-owned rows, manifests, and copied archive media files before reimporting, while leaving live-owned rows untouched
  - changed archive dataset `raw_capture` writes to use deterministic keys per `(archive_digest, operation, filename)` so interrupted reruns overwrite instead of duplicating archive captures
  - changed archive import failure handling to mark the manifest `failed` even on `KeyboardInterrupt` / cancellation before re-raising
  - upgraded interactive archive import/detail-enrichment progress to tqdm-backed bars with per-phase debug timing summaries, and added `--debug --limit N` sampled imports that skip automatic follow-up unless explicitly requested and do not mark the archive digest `completed`
  - validation:
    - `uv run pytest tests/test_archive_import.py tests/test_cli.py -q`
    - `uv run ruff check tweetxvault/archive_import.py tweetxvault/storage/backend.py tweetxvault/cli.py tests/test_archive_import.py tests/test_cli.py`
    - `uv run ruff format tweetxvault/archive_import.py tests/test_archive_import.py`

- Profiled the new sampled/debug archive import path against the real 2026-03-16 archive ZIP in isolated temp XDG dirs:
  - command: `XDG_CONFIG_HOME=/tmp/tvx-cfg XDG_CACHE_HOME=/tmp/tvx-cache XDG_DATA_HOME=/tmp/tvx-data uv run tweetxvault import x-archive "data/twitter-2026-03-16-03f914f9c88c72e4c5999be3e546dbd4197b271ca20c8be57d5d322b73871ea2.zip" --regen --limit 1000 --debug`
  - measured phases on the sampled run: hash `1.04s`, dataset load `0.49s`, raw-capture persist `0.23s`, authored import `8.46s` (`118.2 tweets/s`), likes import `6.00s` (`166.6 likes/s`), media copy `0.25s`, optimize `0.07s`
  - conclusion: on the sampled path the cost is dominated by per-row tweet/like ingest rather than archive hashing or raw-capture persistence; authored tweets are slower per row because they run the secondary-object extraction path, but likes still dominate full imports by volume (`109251` likes)

- Captured the next archive-import follow-up tasks after the real interrupted-run report:
  - explicitly handle `KeyboardInterrupt` / cancellation during archive import so manifests are left in a clearer state
  - prevent or dedupe duplicate archive `raw_capture` rows after interrupted reruns
  - upgrade interactive import progress from coarse phase markers to rate/ETA-aware output for large like archives

- Restored long-running UX feedback for archive import jobs and codified the rule in the plan:
  - Updated `docs/PLAN.md` to make interactive progress visibility a hard requirement for long-running CLI features, while keeping non-interactive runs quiet by default
  - Added TTY-gated phase/progress logging to `tweetxvault import x-archive` across archive hashing, dataset loading, authored/likes import, media copy, and follow-up reconciliation/enrichment
  - Routed `tweetxvault import enrich` through the same TTY-gated follow-up status path and suppressed follow-up sync chatter on non-interactive runs
  - Added archive-import regression coverage that locks in TTY progress output for interactive runs
  - Validation:
    - `uv run pytest tests/test_archive_import.py -q`
    - `uv run ruff check tweetxvault/archive_import.py tests/test_archive_import.py`
    - `uv run ruff format --check tweetxvault/archive_import.py tests/test_archive_import.py`

- Tightened the archive-import edge cases from the latest review pass:
  - Kept archive-imported video/animated-GIF media rows `pending` until both the main asset and poster file are present, so poster-only archive exports no longer block later `tweetxvault media download` completion
  - Removed the archive `deleted_at` source-precedence override so a later archive deletion marker can merge into an existing live row without overwriting richer live text/author fields
  - Improved YTD parse errors to include the specific filename being parsed, and added archive-import regressions for ZIP happy-path import plus extracted root-layout archives
  - Validation:
    - `uv run pytest tests/test_archive_import.py tests/test_storage.py -q`
    - `uv run ruff check tweetxvault/archive_import.py tweetxvault/storage/backend.py tests/test_archive_import.py`
    - `uv run ruff format --check tweetxvault/archive_import.py tweetxvault/storage/backend.py tests/test_archive_import.py`

- Added a standalone archive follow-up command so users can finish pending TweetDetail work after import without the original ZIP path:
  - Added `tweetxvault import enrich [--limit N]` as the rerunnable archive-specific follow-up job, while keeping `tweetxvault import x-archive --enrich` as the one-shot import-and-finish path
  - Reused the shared archive reconciliation/pending-row enrichment runner for both entry points and restricted standalone enrich discovery to completed import manifests
  - Clarified the README/implementation notes around when to use `import enrich` versus the broader `tweetxvault threads expand` command
  - Added focused archive/CLI regressions for missing-completed-import handling, standalone enrich forwarding, and standalone enrich result reporting
  - Validation:
    - `uv run pytest tests/test_archive_import.py tests/test_cli.py -q`
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Smoothed the archive-import follow-up UX after the operator review:
  - Added `tweetxvault import x-archive --enrich` so users can rerun the same archive digest and perform the pending TweetDetail follow-up without re-importing the ZIP contents
  - Kept plain repeated imports digest-idempotent, but changed repeated imports with `--enrich` to reuse the existing import manifest/counts and run the follow-up reconciliation/enrichment path instead of short-circuiting immediately
  - Clarified the README around what `TweetDetail` means for end users, how `--detail-lookups` differs from `--enrich`, and when `tweetxvault threads expand` is the better broader follow-up command
  - Added regression coverage for “import once, enrich later” plus CLI output/forwarding for the new `--enrich` mode
  - Validation:
    - `uv run pytest tests/test_archive_import.py tests/test_cli.py -q`
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Applied the second post-Task-16 archive-import review fixes:
  - Collapsed archive media download updates per `media` row so importing both a main asset and thumbnail for the same normalized row no longer clears the first field written
  - Made `--detail-lookups` best-effort for non-terminal API failures by marking rows `transient_failure` with the HTTP status instead of aborting the whole import after archive writes succeeded
  - Preserved one attempt-scoped `import_started_at` across the `in_progress` / `completed` / `failed` manifest writes instead of recomputing it near completion
  - Added archive-import regressions for combined video+thumbnail imports, transient TweetDetail API failures, and manifest start-time preservation
  - Validation:
    - `uv run ruff format tweetxvault/archive_import.py tests/test_archive_import.py`
    - `uv run pytest tests/test_archive_import.py -q`
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Applied the first post-Task-16 archive-import review fixes:
  - Fixed the existing-thumbnail fallback in `tweetxvault/archive_import.py` so a reused poster file no longer turns missing thumbnail metadata into `"None"` or `int(None)`
  - Closed zip inputs if `_ArchiveInput` manifest loading fails, added context-manager support for the helper, and rejected `..` path segments before resolving extracted-directory reads
  - Added archive-import regressions for missing manifests, archive-owner mismatch, zip-close-on-init-failure, malicious manifest filenames, and pre-existing thumbnail destinations
  - Validation:
    - `uv run ruff format tweetxvault/archive_import.py tests/test_archive_import.py`
    - `uv run pytest tests/test_archive_import.py -q`
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Implemented Task 16 end-to-end for official X archive ingestion:
  - Added `tweetxvault/archive_import.py` plus `tweetxvault import x-archive <zip-or-dir>` in `tweetxvault/cli.py`
  - Added content-based archive digest manifests, archive-owner validation, generic `parse_ytd_js(...)` loading for zip/directory inputs, authored/deleted tweet import, sparse `like.js` placeholders, and archive media copy-in to the managed `media/` layout
  - Extended `tweetxvault/storage/backend.py` with source-aware live-vs-archive merge semantics, `deleted_at`, `import_manifest` rows, and sparse-tweet enrichment state fields (`enrichment_state`, `enrichment_checked_at`, `enrichment_http_status`, `enrichment_reason`)
  - Wired post-import reconciliation so bulk live `tweets` / `likes` syncs run automatically when auth is available, while explicit per-item `TweetDetail` lookups stay bounded by `--detail-lookups` (default `0`) to avoid unbounded follow-up crawls on huge like archives
  - Added regression coverage in `tests/test_archive_import.py`, `tests/test_cli.py`, and `tests/test_storage.py` for end-to-end import, zip-vs-dir idempotence, live/archive precedence in both directions, sparse placeholder handling, media copy registration, and CLI forwarding
  - Validation:
    - `uv run ruff check`
    - `uv run ruff format --check`
    - `uv run pytest -q`

- Tightened the archive-import docs again before implementation handoff:
  - Added concrete enrichment field names in `docs/PLAN.md` (`enrichment_state`, `enrichment_checked_at`, `enrichment_http_status`, `enrichment_reason`) instead of leaving “last-checked/result metadata” implicit
  - Added `import_manifest` to the archive-model section in `docs/PLAN.md` so the planned record type is listed alongside the other row types
  - Locked the synthetic `like.js` ordering scheme to negative numeric-string `sort_index` values for compatibility with the current integer-based sort handling
  - Clarified that `deleted_at` applies to both membership `tweet` rows and normalized `tweet_object` rows for deleted authored tweets
  - Added the missing archive-owner validation requirement and the `source = NULL` backward-compatibility rule for existing live rows

- Tightened the archive-import plan around “most complete archive wins” reconciliation:
  - Updated `docs/PLAN.md` so archive import is explicitly a seed + live-enrichment flow, not a one-shot replacement for GraphQL capture
  - Locked the follow-up policy: bulk collection syncs first, then targeted per-item GraphQL lookups only for rows that remain sparse after import
  - Added planned per-tweet enrichment status tracking (`pending` / `done` / `transient_failure` / `terminal_unavailable`) so permanently missing tweets stop requerying
  - Captured the important guardrail that absence from a later live likes/bookmarks collection does not erase archive provenance or prove tweet unavailability

- Resolved the archive-import review questions in the planning docs and cleaned up stale roadmap notes:
  - Updated `docs/ANALYSIS-archive-import.md` with concrete decisions for storage-layer merge precedence, dedicated `import_manifest` rows, generic `parse_ytd_js(...)` loading, synthetic `like.js` ordering, nullable `deleted_at`, and copy-on-import media handling
  - Updated `docs/PLAN.md` to reflect the fresh archive fixture in the early scope note, added the new archive-import decisions to the requirements section, documented `source` / nullable `deleted_at` in the archive model, and refreshed the stale Phase 1/2/3 roadmap checkboxes to match shipped functionality
  - Updated `docs/IMPLEMENTATION.md` so Task 16 now points at the chosen merge/manifest/loader/media-copy approach and explicitly notes that the review-cleanup checklist is historical/completed
  - Continued the public-doc redaction rule for archive-specific digests/filenames; keep those values out of committed docs and logs

## 2026-03-16

- Cataloged the fresh X archive sample in `data/` and updated the archive-import docs:
  - Inspected `data/manifest.js`, `tweets.js`, `deleted-tweets.js`, `tweet-headers.js`, `like.js`, and media directories directly from the ZIP via `unzip -Z1 ...` plus small Python `zipfile`/JSON summary scripts
  - Recorded the concrete inventory in `docs/ANALYSIS-archive-import.md`: authored tweets (`6521`), deleted authored tweets (`1`), likes (`109251`), and exported tweet-media files (`762` across `664` tweet ids)
  - Verified this sample has no bookmark dataset at all, while `article.js`, `article-metadata.js`, `note-tweet.js`, and `community-tweet.js` are present but empty
  - Locked the first-pass precedence decision in `docs/PLAN.md` / `docs/IMPLEMENTATION.md`: live GraphQL stays authoritative for richer normalized metadata, archive data fills deleted/offline gaps, and archive likes import as sparse membership/provenance only
  - Implementation note captured: current storage/extractor coalescing is “new non-empty wins”, so the importer needs explicit source-aware merge logic instead of naïvely reusing existing upsert paths

- Improved CLI search readability by highlighting literal query matches in result text:
  - Updated `tweetxvault/cli.py` so `tweetxvault search` now applies a yellow background highlight to case-insensitive matches for the query terms in the rendered text column
  - Kept the change local to output rendering only; it does not alter FTS/vector/hybrid retrieval logic or ranking
  - Added focused CLI coverage in `tests/test_cli.py` for the highlight span generation
  - Validation:
    - `uv run pytest tests/test_cli.py -q`
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`

- Prepared the follow-up `0.1.1` release while cleaning up stale review nits:
  - Moved the shared query-id refresh/resolve helper out of `tweetxvault/sync.py` into `tweetxvault/utils.py`, then updated `sync.py`, `articles.py`, and `threads.py` to use the public helper instead of importing a private sync-only function
  - Removed the unused `_store_state_for_page(...)` parameters in `tweetxvault/sync.py` to match the current sync-state behavior and reduce leftover historical noise
  - Tweaked the historical Firefox implementation bullet in `docs/IMPLEMENTATION.md` so it still records the original task but now points readers at the newer WAL-safe snapshot note for the current sidecar-copy details
  - Bumped package version metadata to `0.1.1`
  - Validation:
    - `uv run pytest tests/test_sync.py tests/test_articles.py tests/test_threads.py tests/test_cli.py -q`
    - `uv run ruff check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`
    - `uv run ruff format --check tweetxvault/utils.py tweetxvault/sync.py tweetxvault/articles.py tweetxvault/threads.py`
    - `uv run pytest -q`
    - `uv build`
    - `uv run --with twine twine check dist/tweetxvault-0.1.1*`
    - `uv run --isolated --with dist/tweetxvault-0.1.1-py3-none-any.whl tweetxvault --help`

- Normalized semantic-search embeddings and aligned LanceDB search to cosine distance:
  - Updated `tweetxvault/embed.py` so ONNX mean-pooled vectors are L2-normalized before storage, keeping query and archive embeddings on the same cosine-ready scale
  - Updated `tweetxvault/storage/backend.py` so both vector and hybrid search explicitly use the `embedding` column with `metric("cosine")`
  - Added focused regressions in `tests/test_embed.py` for normalized embedding output and in `tests/test_storage.py` for cosine metric wiring on vector/hybrid search
  - Added a README upgrade note telling existing users to run `uv run tweetxvault embed --regen` once so older stored vectors are rebuilt under the normalized cosine-search setup
  - Validation:
    - `uv run pytest tests/test_embed.py tests/test_storage.py tests/test_cli.py tests/test_sync.py -q`
    - `uv run ruff check tweetxvault/embed.py tweetxvault/storage/backend.py tests/test_embed.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/embed.py tweetxvault/storage/backend.py tests/test_embed.py tests/test_storage.py`

- Tightened the PyPI release surface for `0.1.0`:
  - Switched the README screenshot to the direct GitHub raw URL after confirming `https://github.com/lhl/tweetxvault/blob/main/docs/screenshot.png?raw=true` resolves to `200 image/png`; this avoids a broken repo-relative image on PyPI while keeping the same asset
  - Reworked the installation section in `README.md` so `pip install tweetxvault` / `pip install "tweetxvault[embed]"` come first, with `git clone` + `uv sync` kept as the source-install path
  - Updated `pyproject.toml` metadata to reflect current capabilities more accurately (`bookmarks`, `likes`, and authored tweets), added project URLs/keywords plus Unix-like trove classifiers, and constrained hatchling wheel/sdist targets so release artifacts stop shipping repo-internal docs/tests/worklog/dev content; Hatchling still auto-includes `.gitignore` in the sdist
  - Validation:
    - `uv build`
    - `uv run --with twine twine check dist/*`
    - Inspected wheel/sdist contents to verify repo-internal files (`docs/`, `tests/`, `WORKLOG.md`, `AGENTS.md`, `CLAUDE.md`, `dev/`) no longer ship in the sdist

- Clarified the user-facing `--browser` docs in `README.md`:
  - Replaced the terse internal-style note with explicit source precedence for `auth_token` / `ct0` / `user_id`
  - Added a concrete example showing forced browser cookies plus explicit `TWEETXVAULT_USER_ID`
  - Added a multi-account warning explaining that browser cookies and `user_id` must still refer to the same X account, or likes/authored-tweet sync may fail and the archive-owner guardrail can block writes
  - Validation: docs-only change; no code/tests run

- Resolved the remaining review-driven semantics around auth override, thread reruns, and platform support:
  - Changed `tweetxvault/cli.py` so `--browser` only forces cookie sourcing (`auth_token` / `ct0`) from the selected browser/profile; explicit env/config `user_id` now remains a fallback for likes and authored-tweet sync to avoid surprising breakage
  - Changed `tweetxvault/threads.py` so explicit `threads expand <id/url>...` is idempotent by default, added `--refresh` for intentional re-fetches, and de-duped failing linked status-URL targets to one attempt per run even if repeated across many `url_ref` rows
  - Documented the current runtime target as Unix-like only in `README.md`, with the concrete blockers called out (`fcntl`, `resource`, `strftime("%-d")`)
  - Updated `docs/PLAN.md` / `docs/IMPLEMENTATION.md` to mark those review items resolved and capture the shipped semantics
  - Added regression coverage in `tests/test_cli.py` for preserved `user_id` fallback, `threads expand --refresh`, and `--refresh` validation, plus `tests/test_threads.py` coverage for default explicit-target skipping, refresh re-fetches, and once-per-run linked-status failure dedupe
  - Validation:
    - `uv run pytest tests/test_threads.py tests/test_cli.py -q`
    - `uv run ruff check tweetxvault/cli.py tweetxvault/threads.py tests/test_cli.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/cli.py tweetxvault/threads.py tests/test_cli.py tests/test_threads.py`

- Recorded the new review-driven refactor decisions/open questions and fixed sync/embed coupling:
  - Changed `tweetxvault/sync.py` so post-sync auto-embedding is best-effort; embedding/model/runtime failures now emit a warning and leave the already-persisted sync result intact instead of failing the whole command after insertions succeeded
  - Added a sync regression in `tests/test_sync.py` that forces embedding initialization to fail after page persistence and proves the sync still returns success with stored rows intact
  - Updated `docs/PLAN.md` / `docs/IMPLEMENTATION.md` to capture the decided embedding behavior, the remaining `--browser user_id` / thread-expansion / platform-support decisions, and corrected the Firefox snapshotting notes to match the current copy-plus-sidecars implementation
  - Updated `README.md` to document best-effort auto-embedding after sync
  - Validation:
    - `uv run pytest tests/test_sync.py -q`
    - `uv run ruff check tweetxvault/sync.py tests/test_sync.py`
    - `uv run ruff format --check tweetxvault/sync.py tests/test_sync.py`

- Fixed the Firefox live-profile auth-check hang introduced by the WAL-safe cookie snapshot refactor:
  - Root cause: `tweetxvault/auth/firefox.py` switched to SQLite's backup API for `cookies.sqlite`, but that call can block indefinitely against an actively used Firefox Dev Edition profile even though the cookie DB itself is readable via a copied DB+WAL snapshot
  - Replaced the snapshot helper with a temp copy of `cookies.sqlite` plus any live `-wal` / `-shm` / `-journal` sidecars, keeping the query path unchanged while restoring bounded auth checks against busy Firefox profiles
  - Validation:
    - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_auth.py tests/test_cli.py -k auth`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check tweetxvault/auth/firefox.py tests/test_auth.py tests/test_cli.py`
    - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault/auth/firefox.py`
    - `UV_CACHE_DIR=/tmp/uv-cache timeout 12s uv run tweetxvault auth check --debug-auth`
    - `UV_CACHE_DIR=/tmp/uv-cache timeout 12s uv run tweetxvault auth check`

- Landed review cleanup item 14 for auth-resolution diagnostics:
  - Added auth-status callback plumbing in `tweetxvault/auth/cookies.py` / `tweetxvault/auth/firefox.py` so browser/profile probing steps can be surfaced without hard-coding print calls into the auth resolver
  - Added `--debug-auth` support in `tweetxvault/cli.py` for `tweetxvault threads expand` and `tweetxvault auth check`, so stalls before the archive job starts can now be traced to a specific browser/profile resolution step
  - Added auth coverage in `tests/test_auth.py` for emitted browser-probe status and CLI coverage in `tests/test_cli.py` for the new debug output plumbing
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault/auth/cookies.py tweetxvault/auth/firefox.py tweetxvault/cli.py tweetxvault/threads.py tests/test_auth.py tests/test_cli.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/auth/cookies.py tweetxvault/auth/firefox.py tweetxvault/cli.py tweetxvault/threads.py tests/test_auth.py tests/test_cli.py tests/test_threads.py`

- Landed review cleanup item 13 for early thread-expansion startup logging and lighter preload work:
  - Updated `tweetxvault/threads.py` so `tweetxvault threads expand` now prints immediately while it is preparing the job, resolving query IDs, and loading archive state instead of waiting for multiple full-table scans to finish before the first line
  - Deferred the expensive `known_tweet_ids` scan until the linked-status pass actually needs it, so explicit-target runs and some limit-bounded runs avoid one unnecessary archive-wide preload
  - Expanded `tests/test_threads.py` to lock in the new preload/startup logging for both the normal membership+linked-status path and the explicit-target/rate-limit path
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault/threads.py tests/test_threads.py tweetxvault/client/base.py tweetxvault/client/timelines.py tests/test_client.py`
    - `uv run ruff format --check tweetxvault/threads.py tests/test_threads.py tweetxvault/client/base.py tweetxvault/client/timelines.py tests/test_client.py`

- Landed review cleanup item 12 for long-running thread-expansion observability:
  - Added a shared request-status callback path in `tweetxvault/client/base.py` / `tweetxvault/client/timelines.py` so callers can surface 429 retry, cooldown, and query-id refresh events without hard-coding that logic into each runner
  - Updated `tweetxvault/threads.py` to print phase-level progress (`explicit`, `membership`, `linked-status`) plus tweet-scoped retry/cooldown/failure messages, so `tweetxvault threads expand` no longer looks hung during long backoff windows
  - Added client regressions in `tests/test_client.py` for 404 refresh-status and 429 retry/cooldown messages, plus a thread-runner regression in `tests/test_threads.py` that locks in visible rate-limit diagnostics for an explicit-target failure
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault/client/base.py tweetxvault/client/timelines.py tweetxvault/threads.py tests/test_client.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/client/base.py tweetxvault/client/timelines.py tweetxvault/threads.py tests/test_client.py tests/test_threads.py`

- Landed review cleanup item 11 for LanceDB batch updates in media/unfurl:
  - Added `ArchiveStore.merge_rows(...)` plus reusable media/url row-update builders in `tweetxvault/storage/backend.py`, then switched `tweetxvault/media.py` and `tweetxvault/unfurl.py` to flush row updates in batches instead of merging one item at a time
  - Added proof tests in `tests/test_media.py` and `tests/test_unfurl.py` that count `merge_rows(...)` calls so the new batched path is explicitly covered
  - Folded in the nearby minor fixes while touching those files: parenthesized the `cli.py` auth-override guard for readability and made `_preferred_media_url(...)` avoid unchecked dict indexing even though `_video_variants(...)` already sanitizes inputs
  - Validation:
    - `uv run pytest tests/test_media.py tests/test_unfurl.py tests/test_storage.py tests/test_extractor.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tweetxvault/extractor.py tests/test_media.py tests/test_unfurl.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tweetxvault/extractor.py tests/test_media.py tests/test_unfurl.py`

- Landed review cleanup item 10 for direct `ExtractedTweetGraph` coalescing coverage:
  - Expanded `tests/test_extractor.py` with direct unit tests for `ExtractedTweetGraph.add_tweet_object(...)`, `add_media(...)`, and `merge(...)`
  - Locked down the current precedence rules: new non-empty values beat existing ones, empty strings fall back to existing values, media keeps the minimum position, empty variant/raw-json payloads do not overwrite richer existing values, and articles promote to `body_present` when merged body text appears
  - Validation:
    - `uv run pytest tests/test_extractor.py`
    - `uv run ruff check tests/test_extractor.py`
    - `uv run ruff format --check tests/test_extractor.py`

- Landed review cleanup item 9 for runner/extractor coverage gaps:
  - Expanded `tests/test_media.py` with failed-download retry coverage plus `retry_failed` and `limit` behavior for photo downloads
  - Expanded `tests/test_unfurl.py` with failed-unfurl retry coverage, `retry_failed` + `limit` behavior, and a non-HTML response case
  - Expanded `tests/test_articles.py` with invalid-detail failure handling and `limit` behavior for preview article refreshes
  - Expanded `tests/test_threads.py` with a `limit` case that proves thread expansion stops before the linked-status pass once the requested count is reached
  - Expanded `tests/test_extractor.py` with malformed article/attached payload coverage plus sparse URL/media entry handling
  - Validation:
    - `uv run pytest tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`
    - `uv run ruff check tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`
    - `uv run ruff format --check tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py tests/test_extractor.py`

- Landed review cleanup item 8 for Firefox WAL-safe cookie snapshots:
  - Reworked `tweetxvault/auth/firefox.py` so Firefox cookie extraction now snapshots `cookies.sqlite` via SQLite's backup API instead of manually copying `cookies.sqlite` plus `-wal` / `-shm` sidecars
  - Kept the extraction query unchanged after the snapshot, but removed the race-prone assumption that sidecar file copies happen at a coherent point in time for a live Firefox profile
  - Added an auth regression in `tests/test_auth.py` that reads cookies from a WAL-mode Firefox DB while the source connection remains open
  - Validation:
    - `uv run pytest tests/test_auth.py`
    - `uv run ruff check tweetxvault/auth/firefox.py tests/test_auth.py`
    - `uv run ruff format --check tweetxvault/auth/firefox.py tests/test_auth.py`

- Landed review cleanup item 7 for thread-expansion control-flow repetition:
  - Refactored `tweetxvault/threads.py` so the repeated `_expand_target(...)` try/except/result-counting logic now lives in one `_try_expand_target(...)` helper
  - Kept the loop-specific rules unchanged: explicit targets still dedupe inputs first, membership targets still skip already-expanded tweets, and linked-status targets still skip source/self/known targets before attempting expansion
  - Added an explicit-target regression in `tests/test_threads.py` that locks in duplicate skipping plus per-target failure counting alongside the existing membership + linked-status expansion coverage
  - Validation:
    - `uv run pytest tests/test_threads.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/threads.py tests/test_threads.py`
    - `uv run ruff format --check tweetxvault/threads.py tests/test_threads.py`

- Landed review cleanup item 6 for secondary row filter pushdown:
  - Updated `tweetxvault/storage/backend.py` so `list_media_rows(...)`, `list_url_rows(...)`, and `list_article_rows(...)` push their state/type/preview predicates into LanceDB `.where(...)` clauses before materializing rows
  - Added small shared expression helpers for quoted `IN (...)` lists, pending-state handling, and combined `AND` filters so the LanceDB predicate logic stays explicit and reusable inside the backend
  - Kept the existing Python-side sort order and limit semantics unchanged; only the filtering step moved out of Python
  - Added a real archive-store regression in `tests/test_storage.py` proving pending/done media filters, URL state filters, and preview-only article selection all work against LanceDB-backed storage
  - Validation:
    - `uv run pytest tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_articles.py`
    - `uv run ruff check tweetxvault/storage/backend.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tests/test_storage.py`

- Landed review cleanup item 5 for extractor URL-candidate duplication:
  - Replaced the parallel `_canonical_url_candidate(...)` / `_final_url_candidate(...)` helpers in `tweetxvault/extractor.py` with one `_url_candidate(...)` helper parameterized by key order and absolute-URL requirements
  - Kept the remaining behavioral differences explicit in `_url_entries(...)`: final URLs still require absolute `unwound_url` / `expanded_url` values, while canonical selection can still fall back to the short `url`
  - Added extractor coverage for both an unwound absolute final URL and a short-link-only fallback path so future URL-selection changes exercise the shared helper from both call sites
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py`
    - `uv run ruff check tweetxvault/extractor.py tests/test_extractor.py`
    - `uv run ruff format --check tweetxvault/extractor.py tests/test_extractor.py`

- Landed review cleanup items 3 and 4 for storage record-builder boilerplate and duplicate time helpers:
  - Added `tweetxvault/utils.py` with the shared `utc_now()` helper and switched `tweetxvault/storage/backend.py`, `tweetxvault/media.py`, and `tweetxvault/unfurl.py` over to it
  - Refactored the LanceDB record builders in `tweetxvault/storage/backend.py` around a small `_RecordContext` helper plus `_coalesce_existing(...)` / `_record_with_context(...)` so row timestamp setup and existing-value coalescing are shared
  - Kept the storage refactor intentionally local to the backend instead of introducing a generic record-mapping abstraction
  - Added a storage regression proving a later thinner payload still preserves richer existing media/article secondary values
  - Validation:
    - `uv run pytest tests/test_storage.py tests/test_media.py tests/test_unfurl.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/utils.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/utils.py tests/test_storage.py`

- Landed review cleanup item 2 for runner lifecycle repetition:
  - Added `tweetxvault/jobs.py` with `resolve_job_context(...)` and `locked_archive_job(...)` so the shared config/path resolution, archive lock, store open/close, and conditional optimize flow lives in one place
  - Moved `tweetxvault/media.py`, `tweetxvault/unfurl.py`, `tweetxvault/articles.py`, and `tweetxvault/threads.py` onto the shared helper while preserving their existing optimize triggers
  - Kept auth resolution outside the archive lock for `articles` and `threads`, matching the pre-refactor behavior
  - Added direct helper coverage in `tests/test_jobs.py` for conditional optimize, store close, and missing-archive failure handling
  - Validation:
    - `uv run pytest tests/test_jobs.py tests/test_media.py tests/test_unfurl.py tests/test_articles.py tests/test_threads.py`
    - `uv run ruff check tweetxvault/jobs.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/articles.py tweetxvault/threads.py tests/test_jobs.py`
    - `uv run ruff format --check tweetxvault/jobs.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/articles.py tweetxvault/threads.py tests/test_jobs.py`

- Landed review cleanup item 1 for sync CLI repetition:
  - Refactored `tweetxvault/cli.py` so `sync bookmarks`, `sync likes`, and `sync tweets` are registered through one command factory and share one config/auth/error-handling helper with `sync all`
  - Kept the existing CLI command names, options, and output format stable while removing the copy-pasted sync command bodies
  - Expanded CLI coverage so the shared sync path is exercised for bookmarks, likes, tweets, and `sync all`
  - Validation:
    - `uv run pytest tests/test_cli.py tests/test_sync.py`
    - `uv run ruff check tweetxvault/cli.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/cli.py tests/test_cli.py`
    - `uv run tweetxvault sync --help`

- Landed Task 15 thread/context expansion:
  - Added `tweetxvault/threads.py` plus `tweetxvault threads expand`, which fetches archived tweet context through authenticated `TweetDetail` and also follows linked `x.com/.../status/...` / `twitter.com/.../status/...` URLs found in archived `url_ref` rows
  - Extended `tweetxvault/client/timelines.py` with `parse_tweet_detail_tweets(...)` so full detail payloads can be harvested, not just the focal tweet
  - Extended `tweetxvault/extractor.py` with linked-status detection plus `reply_to`, `thread_parent`, and `thread_child` relation extraction for multi-tweet detail payloads
  - Added `ArchiveStore.persist_thread_detail(...)` and discovery helpers in `tweetxvault/storage/backend.py`; thread/context tweets now persist as global `tweet_object` / `tweet_relation` rows without creating fake bookmark/like/tweet memberships
  - Expanded `tweetxvault rehydrate` so stored `TweetDetail` and `ThreadExpandDetail` raw captures can rebuild thread/context rows later without another network fetch
  - Added regression coverage for full-detail parsing, thread relation extraction, thread-detail storage, idempotent reruns, linked-status expansion, and CLI wiring
  - Validation:
    - `uv run pytest tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/client/timelines.py tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/threads.py tweetxvault/cli.py tests/conftest.py tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`
    - `uv run ruff format --check tweetxvault/client/timelines.py tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/threads.py tweetxvault/cli.py tests/conftest.py tests/test_client.py tests/test_extractor.py tests/test_storage.py tests/test_threads.py tests/test_cli.py`

- Landed Task 14 own-tweet capture:
  - Added live `UserTweets` support with a fresh fallback query ID reverified from the public X web bundle on 2026-03-16 (`Y59DTUMfcKmUAATiT2SlTw`)
  - Added `tweetxvault sync tweets` and `tweetxvault view tweets`; generic export commands now accept `--collection tweets`
  - Reused the existing LanceDB membership rows (`collection_type='tweet'`), duplicate detection, rehydrate, article backfill, media download, URL unfurl, and article refresh paths for authored tweets
  - Kept `tweetxvault sync all` limited to bookmarks + likes for now so authored-tweet capture stays explicit and does not surprise users with larger archive growth
  - Added regression coverage for `UserTweets` URL building/parsing, authored-tweet sync pagination + duplicate detection, CLI view/export/sync support, and secondary-object deduplication when the same authored tweet later appears in another collection
  - Validation:
    - `uv run pytest tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`
    - `uv run ruff check tweetxvault/auth/cookies.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/common.py tweetxvault/query_ids/constants.py tweetxvault/sync.py tests/conftest.py tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`
    - `uv run ruff format --check tweetxvault/auth/cookies.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/common.py tweetxvault/query_ids/constants.py tweetxvault/sync.py tests/conftest.py tests/test_client.py tests/test_query_ids.py tests/test_cli.py tests/test_sync.py tests/test_storage.py`

- Fixed a LanceDB commit-conflict path exposed by `sync --article-backfill`:
  - Root cause: CLI overwrite-style archive operations (`optimize`, `rehydrate`, `embed`) and auto-optimize retries from read commands could call `store.optimize()` without taking the shared archive lock, which could race with `sync` merge writes and trigger `Commit conflict ... concurrent transaction Overwrite`
  - Added shared lock coverage in `tweetxvault/cli.py` for `optimize`, `rehydrate`, `embed`, and auto-optimize fallbacks in `view` / `export` / `search`
  - Generalized the lock error text in `tweetxvault/sync.py` from “sync” to “archive job” because the same lock now protects multiple archive-mutating commands
  - Added CLI regressions covering locked optimize/rehydrate/embed paths plus the blocked auto-optimize failure path
  - Validation:
    - `uv run pytest`
    - `uv run ruff check tweetxvault tests`
    - `uv run ruff format --check tweetxvault tests`

- Added a dedicated thread-expansion milestone ahead of archive import:
  - Inserted Task 15 in `docs/IMPLEMENTATION.md` for `TweetDetail`-based thread/context capture plus linked X-status URL expansion
  - Shifted archive import planning to Task 16
  - Clarified in `docs/PLAN.md` that current attached-tweet extraction is not full thread expansion, and added a new open question about whether thread capture should stay an explicit command or become an opt-in sync-time flag

- Reprioritized the roadmap to add authored-tweet capture before archive import:
  - Added a new Task 14 in `docs/IMPLEMENTATION.md` for `UserTweets`-backed own-tweet sync/export support
  - Initially shifted archive import planning back one slot because it is a larger parallel ingestion problem, while own tweets reuse the existing live-sync/storage/media/export stack
  - Added `sync tweets` / `view tweets` roadmap notes in `docs/PLAN.md` plus a new open UX question about whether authored tweets should join `sync all`

- Landed the remaining article support:
  - Added `tweetxvault/articles.py` plus `tweetxvault articles refresh`, which resolves explicit tweet URLs/IDs or auto-selects preview-only archived article rows and refreshes them through authenticated `TweetDetail`
  - Added `build_tweet_detail_url(...)` / `parse_tweet_detail_response(...)` in `tweetxvault/client/timelines.py`
  - Captured a trimmed real authenticated fixture for `https://x.com/dimitrispapail/status/2026531440414925307` at `tests/fixtures/dimitris_article_tweet_detail.json`
  - Verified on 2026-03-16 that `TweetDetail` currently returns full article `plain_text`, `content_state`, `cover_media`, and `media_entities`, so no Playwright-only article fallback is needed right now
  - Reworked HTML export so article bodies, article media, tweet media, and URL metadata render directly in the local viewer
  - Validation:
    - `uv run pytest tests/test_articles.py tests/test_client.py tests/test_cli.py tests/test_export.py`
    - `uv run ruff check tweetxvault/articles.py tweetxvault/client/features.py tweetxvault/client/timelines.py tweetxvault/cli.py tweetxvault/export/html_export.py tests/test_articles.py tests/test_client.py tests/test_cli.py tests/test_export.py`

- Landed Task 12 media downloads + URL unfurls:
  - Extended `tweetxvault/storage/backend.py` with per-media download state/local-path/hash fields plus URL unfurl/final-url/title/description metadata fields
  - Expanded `tweetxvault/extractor.py` so article cover/body media are captured into `media` rows and URL rows preserve payload-provided metadata before any remote fetches
  - Added `tweetxvault/media.py` + `tweetxvault/unfurl.py` with lock-safe follow-on runners, wired to `tweetxvault media download` and `tweetxvault unfurl`
  - Kept downloads and unfurls outside the sync transaction while making JSON exports include nested `media`, `urls`, and `article` sections
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_cli.py`
    - `uv run ruff check tweetxvault/extractor.py tweetxvault/storage/backend.py tweetxvault/media.py tweetxvault/unfurl.py tweetxvault/cli.py tests/test_extractor.py tests/test_storage.py tests/test_media.py tests/test_unfurl.py tests/test_cli.py`

- Added `--article-backfill` sync mode:
  - Extended `tweetxvault sync bookmarks|likes|all` with `--article-backfill`, which rewalks existing timeline pages without resetting sync state so older tweets can pick up newly-enabled article fields
  - Kept it on the normal sync persistence path, so refetched pages update `tweet.raw_json` plus normalized `article` / secondary rows directly; no follow-up `tweetxvault rehydrate` is required after the backfill itself
  - Added coverage in `tests/test_sync.py` proving article backfill reaches older duplicate pages and persists article rows, plus a CLI forwarding test in `tests/test_cli.py`

- Landed Task 11 secondary-object extraction on the LanceDB archive:
  - Added `tweetxvault/extractor.py` to normalize raw tweet payloads into canonical `tweet_object`, `tweet_relation`, `media`, `url`, `url_ref`, and `article` records
  - Expanded `tweetxvault/storage/backend.py` schema and page buffering so those secondary rows are written in the same page-atomic merge as `raw_capture`, collection-scoped `tweet`, and `sync_state`
  - Kept collection-scoped `tweet` rows as the membership/export layer while also storing richer global tweet/media/url/article objects
  - Added coalescing semantics on secondary rows so later thinner payloads do not overwrite richer previously captured fields with nulls
  - Validation:
    - `uv run pytest tests/test_extractor.py tests/test_storage.py tests/test_client.py`
    - `uv run ruff check tweetxvault/storage/backend.py tweetxvault/extractor.py tweetxvault/client/timelines.py tweetxvault/client/features.py tweetxvault/cli.py tests/test_extractor.py tests/test_storage.py tests/test_client.py tests/conftest.py`

- Expanded rehydrate to rebuild normalized rows from stored `tweet.raw_json`:
  - Changed `tweetxvault rehydrate` to rescan all stored tweet rows, refresh canonical tweet fields (including note-tweet text), and rebuild secondary-object rows without refetching
  - Preserved the existing archive-upgrade path for older archives by letting rehydrate backfill secondary rows after the schema migration

- Enabled article field toggles on timeline requests and recorded a concrete article example URL:
  - Switched `withArticleRichContentState`, `withArticlePlainText`, and `withArticleSummaryText` on in `tweetxvault/client/features.py`
  - Added `https://x.com/dimitrispapail/status/2026531440414925307` to `docs/PLAN.md` / `docs/IMPLEMENTATION.md` as the working article validation target for the later article-capture task

## 2026-03-15

- Expanded auth extraction from Firefox-only to browser-family support:
  - Added `tweetxvault/auth/chromium.py` and runtime dependency `browser-cookie3>=0.20,<1` so Chrome, Chromium, Brave, Edge, Opera, Opera GX, Vivaldi, and Arc can supply `auth_token` / `ct0` / `twid`
  - Changed browser auto-resolution order to Firefox -> Chrome -> Chromium -> Brave -> Edge -> Opera -> Opera GX -> Vivaldi -> Arc, stopping at the first browser that yields valid X cookies
  - Added generic browser selection config/env (`auth.browser`, `auth.browser_profile`, `auth.browser_profile_path`, `TWEETXVAULT_BROWSER*`) while keeping `TWEETXVAULT_FIREFOX_PROFILE_PATH` as a legacy compatibility path
  - Added `--browser`, `--profile`, `--profile-path` flags to `tweetxvault sync ...` / `tweetxvault auth check`, plus `tweetxvault auth check --interactive` for an on-demand browser/profile picker
  - Tweaked Firefox auto-pick to prefer install-default/default profiles instead of erroring on every multi-profile setup
  - Validation:
    - `uv lock`
    - `uv run ruff check tweetxvault tests`
    - `uv run ruff format --check tweetxvault/auth/chromium.py tweetxvault/auth/cookies.py tweetxvault/auth/firefox.py tweetxvault/auth/__init__.py tweetxvault/cli.py tweetxvault/config.py tweetxvault/sync.py tests/test_auth.py tests/test_cli.py`
    - `uv run pytest`
  - Result: all checks passed (`42 passed`); repo-wide `uv run ruff format --check tweetxvault tests` still reports an unrelated pre-existing formatting issue in `tweetxvault/storage/backend.py`

- Scoped the next capture-expansion milestone in `docs/PLAN.md` / `docs/IMPLEMENTATION.md`:
  - Added a post-MVP design for canonical `tweet_object` rows plus `tweet_relation`, `media`, `url`, `url_ref`, and `article` record types on the existing single-table LanceDB archive
  - Split future work into concrete follow-on tasks for secondary-object extraction, media downloads, URL unfurls/snapshots, article capture, and X-archive import
  - Reserved a future `tweetxvault import x-archive <zip-or-dir>` ingest path with provenance/idempotency requirements
  - Verified current public X web-bundle signals before locking the spec:
    - `curl -L -sS --max-time 20 https://x.com/?lang=en`
    - `curl -L -sS --max-time 20 https://abs.twimg.com/responsive-web/client-web/main.8575d0ba.js`
    - Result: anonymous bundle still exposed `Likes`, `TweetDetail`, `UserArticlesTweets`, plus article field toggles/features (`withArticleRichContentState`, `withArticlePlainText`, `withArticleSummaryText`, `articles_preview_enabled`)

- Scrubbed a user-specific Firefox profile identifier from the repo before publication:
  - Replaced concrete Firefox profile IDs with generic `profile.*` placeholders in historical planning docs and Firefox profile test fixtures
  - Planned a `git filter-repo` rewrite so the old identifiers are removed from local git history before first public push

- Added FTS search and ONNX-based embeddings:
  - Added `embedding` column (384-dim float32 vector) to archive schema with auto-migration for existing archives
  - Created `tweetxvault/embed.py`: ONNX engine using all-MiniLM-L6-v2 via onnxruntime + tokenizers (~0.7ms/tweet batched, ~18s for 25k tweets on CPU)
  - Added `tweetxvault embed` command: resumes by default (skips already-embedded), `--regen` to clear and redo
  - Added `tweetxvault search <query>`: modes auto/fts/vector/hybrid, defaults to hybrid when embeddings exist
  - FTS uses LanceDB's built-in tantivy index on tweet text column
  - Embedding deps are optional via `[embed]` extra (`uv sync --extra embed`)
  - Added tqdm progress bar to embed command

- Added `--sort` flag to view commands (newest/oldest), fixed sort order bug (was using synced_at as primary key which inverted order)

- Fixed author username extraction: Twitter moved screen_name/name to `core.user_results.result.core` (not just `legacy`), added cascading fallback. Added `tweetxvault rehydrate` to backfill existing archives from stored raw_json.

- Added tqdm progress bars for long-running operations (rehydrate, embed)
  - Made rehydrate use batched merge_inserts (500 rows) instead of per-row updates (~18s vs ~1hr for 25k tweets)

- Fixed LanceDB "Too many open files" errors:
  - Added `ArchiveStore.optimize()` to compact table versions after sync
  - Added `tweetxvault optimize` CLI command for manual compaction
  - Added auto-optimize retry in view/export on too-many-open-files error
  - Raised soft file-descriptor limit to hard limit at CLI startup

- Fixed duplicate detection bug and added `--backfill` flag:
  - Root cause: `has_membership()` did per-tweet `search().where()` queries through LanceDB's vector-search API, which silently returned no results on large archives (25k+ tweets), so duplicate detection never triggered
  - Fix: replaced per-tweet queries with `get_collection_tweet_ids()` that bulk-loads all known tweet IDs for the collection into an in-memory set at sync start
  - Added `--backfill` CLI flag: continues past duplicates without resetting sync state (unlike `--full` which resets state AND skips dedup)
  - Default behavior (no flags) now correctly stops on the first page containing already-archived tweets
  - Added tests: `test_duplicate_detection_stops_sync`, `test_backfill_flag_skips_duplicate_stop`
  - Validation: `uv run pytest` (37 passed), `uv run ruff check`, `uv run ruff format --check` all clean

- Added local inspection/export commands on top of the LanceDB archive:
  - Added `tweetxvault view bookmarks|likes|all` to print recent archived rows in a terminal table
  - Added `tweetxvault export html` to write a local HTML viewer alongside the existing JSON export
  - Normalized collection names so `bookmarks`/`likes` work consistently for user-facing export/view commands
  - Added direct CLI tests covering terminal view output, JSON export aliases, and HTML export output

- Tightened Firefox profile autodiscovery for multi-profile setups:
  - Changed `discover_default_profile(...)` to scan discovered Firefox profiles and auto-pick the only profile that actually contains `x.com` session cookies
  - Added explicit ambiguity/no-cookie errors that list discovered profile paths and point users at `TWEETXVAULT_FIREFOX_PROFILE_PATH` / `auth.firefox_profile_path`
  - Added auth tests covering the real Developer Edition case, ambiguous profile selection, and no-cookie profile listing

- Tightened LanceDB export filtering after post-migration review:
  - Changed `ArchiveStore.export_rows(...)` to push `record_type = 'tweet'` and optional collection filtering into LanceDB instead of materializing the full archive table in Python
  - Added a storage regression test that monkeypatches `table.to_arrow()` to fail so export coverage proves the search path is used
  - Validation: `uv run pytest tests/test_storage.py tests/test_sync.py`, `uv run ruff check tweetxvault tests`

- Landed the LanceDB backend migration in the shipped package:
  - Replaced the SQLite fallback backend with `tweetxvault/storage/backend.py`
  - Removed `tweetxvault/storage/seekdb.py`
  - Switched runtime deps from `pyseekdb` to `lancedb` + `pyarrow`
  - Switched the archive path convention from `archive.sqlite3` to `archive.lancedb/`
- Folded the queued sync/storage cleanup into the migration:
  - Removed the redundant `sync_all` double-preflight by sharing preflight results across collections
  - Returned parsed payloads from `_fetch_and_parse_page(...)` so the sync loop no longer reparses JSON
  - Removed the outer post-loop state commit and kept head-state updates inside backend-managed page persistence
- Extended validation for LanceDB-specific semantics:
  - Added regression coverage for one table-version increment per successful `persist_page(...)`
  - Added export coverage confirming `export_rows(...)` only returns tweet records
  - Updated sync tests to seed collection-scoped duplicates through backend APIs instead of SQLite connection access
- Validation:
  - `uv lock`
  - `uv sync`
  - `uv run ruff format --check tweetxvault tests`
  - `uv run ruff check tweetxvault tests`
  - `uv run pytest`
  - `uv run tweetxvault --help`
  - Result: `28 passed in 1.43s`, lint/format clean, CLI help still works

- Queued follow-on cleanup work into the LanceDB migration plan after architecture review feedback:
  - Use `tweetxvault/storage/backend.py` as the concrete backend module name instead of a backend-specific filename
  - Remove the redundant `sync_all` double-preflight during the migration
  - Fold the final `last_head_tweet_id` write into backend-owned state semantics instead of an outer `commit()`
  - Remove stale `pyseekdb` runtime dependency as part of the backend switch
- Locked the next backend plan to LanceDB:
  - Updated `docs/PLAN.md` to make LanceDB the planned backend, with a single-table `row_key` archive model and LanceDB-native FTS/vector search phases
  - Updated `docs/IMPLEMENTATION.md` with a new active migration task for replacing the shipped SQLite backend
  - Updated `docs/ANALYSIS-db.md` and `docs/README.md` so the docs agree that SQLite is the shipped fallback and LanceDB is the planned next backend
- Spiked pure LanceDB archive viability in `dev/lancedb-test/` instead of the shipped package to keep the experiment isolated:
  - Added `dev/lancedb-test/archive_store.py`, a single-table LanceDB archive prototype keyed by `row_key`
  - Added `dev/lancedb-test/storage_spike.py` to verify current archive semantics against the prototype
  - Added `dev/lancedb-test/search_probe.py` to verify local scalar filter, FTS, and vector-index search behavior
  - Added `dev/lancedb-test/README.md` with repro commands
- Ran the isolated LanceDB spike:
  - `uv run --with lancedb python dev/lancedb-test/storage_spike.py`
  - Result: collection-scoped duplicate detection, sync-state persistence/reset, archive-owner guardrail, export ordering, and page-batched writes all worked
  - Observed that each `persist_page(...)` call advanced the Lance table by exactly one version, supporting the single-table batch-write design
  - `uv run --with lancedb python dev/lancedb-test/search_probe.py`
  - Result: scalar index, FTS index, and vector index all worked locally with filtered queries
- Conclusion from the LanceDB spike:
  - Pure LanceDB looks viable enough for a real migration if we are willing to redesign the archive into a denormalized single-table model
  - This is not a drop-in backend swap for the normalized SQLite schema; it is a storage-model change
  - The lower-risk alternative remains SQLite as source of truth with LanceDB as a search sidecar

## 2026-03-14

- Re-ran the storage spike with full sandbox permissions and added a reproducible harness in [docs/ANALYSIS-db.py](docs/ANALYSIS-db.py):
  - Verified embedded SeekDB now initializes successfully on writable `/home`-backed btrfs paths, so the original failure was not purely POSIX permissions
  - Verified `seekdb.open()` still fails on `/tmp`/`tmpfs` with `not support tmpfs directory`, and behaves as a process-global singleton (`initialized twice` when reopening a different path in the same process)
  - Benchmarked the original Task 0 cold path (`open -> create schema -> insert 1k rows -> query 10`) and recorded the results in `docs/ANALYSIS-db.md`
  - `uv run python docs/ANALYSIS-db.py sqlite` -> total `0.0433s`, max RSS `32328 KB`
  - `uv run python docs/ANALYSIS-db.py seekdb-sql` -> total `3.2295s`, max RSS `1024032 KB`
  - `uv run --with lancedb python docs/ANALYSIS-db.py lancedb` -> total `0.0668s`, max RSS `164792 KB`
  - Re-checked large raw JSON handling with the same harness (`--rows 1 --raw-bytes 200000`): SQLite OK, LanceDB OK, SeekDB OK with `MEDIUMTEXT`
  - Conclusion: SeekDB is functional now on supported filesystems, but still fails the MVP Task 0 threshold badly; SQLite remains the shipped backend and LanceDB is the strongest sidecar candidate if we later want a separate vector store
- Implemented the first end-to-end MVP codebase (`pyproject.toml`, `tweetxvault/`, `tests/`, `uv.lock`):
  - Built Typer CLI commands for `sync`, `auth check`, `auth refresh-ids`, and JSON export
  - Implemented XDG config loading, env/config/Firefox cookie resolution, query-id cache + scraper, async GraphQL client, per-page sync orchestration, and JSON export
  - Added 25 tests covering auth, query-id discovery, HTTP retry/query-id refresh, storage atomicity, incremental/head+backfill resume, `--full` resume, preflight behavior, process locking, partial `sync all`, and first-run UX
- Task 0 storage spike changed the storage backend decision for the working MVP:
  - Reproduced embedded SeekDB failures with `pylibseekdb.open/connect` across repo and non-tmpfs writable paths (`open seekdb failed 4016`, `connect failed 4006`)
  - Switched the implemented backend to SQLite in `tweetxvault/storage/seekdb.py` while preserving the planned schema and atomic page-write semantics
  - Kept raw JSON inline in the DB for the MVP; no sidecar gzip path was needed after the fallback
- Captured current X web artifacts to avoid placeholder query IDs / feature flags:
  - Used `curl` against `https://x.com/?lang=en` plus the current `main`, bookmark shared chunk, and bookmark bundle to extract current `Bookmarks`, `Likes`, `BookmarkFolderTimeline`, `TweetDetail`, and `UserArticlesTweets` query IDs
  - Sourced the committed feature-switch defaults from the same live X page fetch on 2026-03-14
- Verified the repo-level definition of done:
  - `UV_CACHE_DIR=/tmp/uv-cache uv sync --python 3.12`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run tweetxvault --help`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check tweetxvault tests`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check tweetxvault tests`
  - `UV_CACHE_DIR=/tmp/uv-cache uv run pytest`
  - Result: all commands passed; `uv run pytest` finished with `25 passed`

- Additional architecture sweep for idempotency/race risks in planning docs:
  - Found missing single-writer protection; added explicit process-lock requirement and atomic temp-file writes in `docs/PLAN.md`
  - Found a bigger sync-state flaw: bottom cursor cannot be the default starting point for later incremental runs because it only paginates older items; updated PLAN/IMPLEMENTATION to separate head sync from interrupted backfill resume
  - Added archive-owner guardrail so one local DB cannot silently mix data from two different X accounts
  - Added implementation/test requirements for atomic per-page DB commits, lock behavior, interrupted-first-run head+backfill correctness, and atomic raw-json sidecar writes if Task 0 chooses file-backed raw storage
- Final failure-semantics pass:
  - Clarified `auth check` exit-code semantics and `sync all` partial-failure behavior after runtime starts
  - Defined `--limit` as per-collection, not global across `sync all`
  - Defined `--full` reset/resume behavior (reset sync state only, never delete data)
  - Added explicit lock-release and partial-runtime-failure test requirements; removed duplicate “document date sourced” line in IMPLEMENTATION.md
- Critical pass on MVP planning docs after reviewing `docs/PLAN.md` + `docs/IMPLEMENTATION.md` (`git status -sb`, `sed`, `rg`):
  - PLAN.md: added shared preflight semantics (local auth + query-id resolution + lightweight remote probe) so first-run sync/auth-check behavior is explicit
  - PLAN.md: made `sync all` all-or-nothing at preflight time; clarified `--limit` excludes probe requests
  - PLAN.md + IMPLEMENTATION.md: fixed duplicate detection to be collection-scoped via `collections`, not global tweet existence (important because the same tweet can be both bookmarked and liked)
  - IMPLEMENTATION.md: added explicit handling/tests for feature-flag drift (`400`), probe-without-write behavior, and no partial writes when `sync all` preflight fails
- Filled PLAN.md gaps for OOTB first-run experience and rewrote IMPLEMENTATION.md:
  - PLAN.md: documented bearer token constant, env var names, cookie resolution chain, user_id resolution for Likes
  - PLAN.md: added Bookmarks vs Likes endpoint differences (user_id requirement)
  - PLAN.md: added "Sync Loop + Stop Conditions" section (empty page, duplicate detection, --limit, rate limit exhaustion)
  - PLAN.md: specified concrete rate limit defaults (3 retries, 2s base delay, 5min cooldown)
  - PLAN.md: added "First-Run Behavior" section (auto-create dirs, validate auth pre-sync, actionable errors)
  - PLAN.md: clarified --limit = pages, simplified Adapter Boundary (no premature Fetcher protocol)
  - IMPLEMENTATION.md: full rewrite — renamed phases to "Task N" (avoid collision with PLAN phases), merged config+auth into one task, added spike exit criteria/fallbacks with timeboxes, dropped Fetcher Protocol from MVP, added end-to-end integration test task
- Rewrote PLAN.md to remove all clone-any-tool framing. Key changes:
  - "Why a Clean Rewrite (Not a Fork)" -> "Why Build From Scratch" — no longer singles out one tool
  - "Prior Art (Loose Reference)" -> "Twitter API Reverse Engineering" — reframed as empirical data about Twitter's undocumented API, not patterns to adopt from a specific tool
  - Added multi-tool reference notes (TweetHoarder, twitter-web-exporter, twitter-likes-export, twitter-advanced-scraper) for query ID approaches instead of treating one as canonical
  - Removed "Adopt TweetHoarder's spirit", "(TweetHoarder Approach)", "ported from bird" and similar language
  - Added framing to Key Decisions: "independent decisions, not inherited from any existing tool"
  - Added rule: reference code is for understanding Twitter's undocumented API, not for copying implementation patterns
- Created `docs/IMPLEMENTATION.md` as a detailed MVP punchlist (including Phase 0 spikes for SeekDB open questions).
- Clarified in PLAN.md that TweetHoarder is a loose prior-art reference (not a reference implementation); updated wording/rationale accordingly.
- Locked in Python tooling decisions in PLAN.md: Typer + Rich (CLI), Pydantic v2 (data models), loguru (logging), uv/ruff/hatchling (project tooling). Reviewed shisad (Click) and TweetHoarder (Typer) for reference; chose Typer for this project.
- Restructured PLAN.md Dependencies section into Runtime / Dev / Optional.
- Assessed open questions: SeekDB perf questions become first implementation spike; articles endpoint is Phase 4, not blocking.
- Reviewed existing docs and `reference/tweethoarder/` patterns (query-id scraping + caching, feature flags, cursor parsing, backoff).
- Rewrote [docs/PLAN.md](docs/PLAN.md) to lock decisions: SeekDB, TweetHoarder-style direct GraphQL API as primary, Playwright as deferred fallback adapter (no Playwright CLI in MVP), and added a clearer MVP scope + remaining open questions.
- Updated [docs/README.md](docs/README.md) to explicitly mark `docs/initial/` as historical/frozen planning snapshots.
- Slimmed [AGENTS.md](AGENTS.md) (and `CLAUDE.md` symlink) back to repo workflow/safety rules and pointed implementation specifics to [docs/PLAN.md](docs/PLAN.md).
- Tightened `.gitignore` to ignore Python caches/venvs and common credential/db artifacts (`*.sqlite`, `*.db`, session JSON).
- Fixed `AGENTS.md` pointer to reference `docs/PLAN.md`/`docs/README.md` instead of a missing `README.md`.
