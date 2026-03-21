# Future Plan

This document tracks post-archive work that is intentionally **not** part of the
current implementation plan in [`PLAN.md`](./PLAN.md).

Current status:

- `PLAN.md` remains the active implementation plan.
- The archive/import/follow-up work is shipped enough that the next major area is
  **discoverability** rather than ingestion.
- Everything below is deferred future work. None of it should be treated as an
  in-flight milestone unless it is explicitly promoted into `PLAN.md` or
  `IMPLEMENTATION.md`.

## Near-Term Product Surface

These items are less ambitious than the deeper discoverability work below, but
they are practical quality-of-life areas that may be worth promoting sooner.

### Backup And Portability

Current practical story:

- moving the XDG-managed tweetxvault directories to another machine is the
  simplest backup/restore path today
- in practice that means the data dir matters most, with config/cache copied as
  needed

Future work:

- Document a clean "move this archive to another machine" workflow explicitly
  instead of relying on users to infer it from the XDG layout.
- Add a first-class backup/export command for archive snapshots if the
  copy-the-directory story proves too implicit or too easy to get wrong.
- Decide whether backup/export should mean:
  - a raw archive snapshot for lossless restore
  - a portable content export for interchange
  - both, as separate commands with different goals
- Add integrity/restore verification so a backup can be checked before the
  original machine is discarded.

Outcome we want:

- Users should have an obvious, documented way to move or back up the archive
  without having to understand LanceDB/XDG internals.

### Richer Filters And Output Formats

We already have a decent archive/search/view baseline, but the query surface is
still narrow.

Future work:

- Add more filtering options for `view` and `search`:
  - date ranges
  - author filters
  - media/article/link presence
  - reply/repost/quote distinctions where the stored data supports it
  - enrichment/follow-up state filters for operational use
- Add alternate output modes beyond the current table-oriented CLI rendering:
  - JSON
  - Markdown
  - CSV
  - possibly newline-delimited JSON for scripting
- Decide whether output formatting belongs as a shared renderer layer across
  `view`, `search`, and future stats/export commands.

Outcome we want:

- The archive should be usable both interactively and as a composable command
  line data source.

### TUI

Future work:

- Design a proper TUI for browsing/searching the archive instead of treating the
  current Rich table output as the final interactive experience.
- Decide whether the TUI is primarily:
  - a search-first interface
  - a collection browser
  - an operational dashboard for sync/enrichment status
  - or some combination of the above
- Reuse the same search/filter primitives as the CLI instead of inventing a
  separate query model.

Outcome we want:

- A stronger interactive archive experience without forcing users into exports
  or external notebooks for exploratory use.

## v0.3: Better Discoverability

Primary goal: make the archive useful as a research/search environment, not just
as a local copy of tweets.

### Priority 1: Link-Centric Discovery

This is the highest-value future direction.

We already have building blocks:

- normalized `url` / `url_ref` rows
- URL unfurl fetching via `tweetxvault unfurl`
- tweet/media/article storage in the main archive

Future work:

- Treat links as first-class searchable objects, not just tweet adornments.
- Index canonical URLs, titles, descriptions, site names, and fetch status in a
  dedicated search/discovery path.
- Add ArchiveBox integration so fetched pages/snapshots become part of the
  archive's searchable surface area.
- Connect tweet search and linked-page search so users can find a concept even
  when the tweet text is weak but the linked content is strong.
- Decide whether ArchiveBox remains an external companion with references stored
  in tweetxvault, or whether a tighter job-runner integration is warranted.

Outcome we want:

- "Search my archive" should eventually mean searching tweet text, unfurled link
  metadata, and archived linked content together.

### Priority 2: Multimodal Discovery

Future work:

- Add VLM-based image understanding for archived tweet media and article media.
- Generate captions/tags/embeddings for images (and later representative video
  frames) so media becomes searchable by meaning rather than filename or tweet
  text alone.
- Decide whether multimodal search should reuse the main LanceDB table or live in
  a derived search index.

Outcome we want:

- Users should be able to find "the diagram about X" or "that screenshot of Y"
  even when the tweet text is not a good search key.

### Priority 3: Clustering And Knowledge Graphs

Future work:

- Cluster tweets/links/media into topics or themes.
- Build entity/relation extraction pipelines over tweets, URLs, and article
  content.
- Experiment with a lightweight knowledge graph or graph-like navigation layer
  over people, topics, URLs, and tweet relationships.
- Decide whether this should stay as offline derived artifacts or become a
  first-class navigable surface in the CLI/export layer.

Outcome we want:

- Move beyond single-tweet retrieval into "show me the neighborhood around this
  idea/person/link/topic."

## Lower-Priority Structural Work

### Multi-Account Support

This is valid future work, but not a current priority.

Future work:

- Decide whether multi-account support means:
  - one archive per account with better switching ergonomics
  - one shared archive with account-scoped partitions
  - or both
- Revisit archive-owner guardrails, config layout, and XDG paths so account
  separation stays explicit and safe.
- Define how search/view/export should behave when multiple accounts are present:
  strict account scoping by default, cross-account search as an explicit opt-in,
  or some other model.

Outcome we want:

- Support multiple accounts without weakening the current "one archive belongs
  to one account" safety guarantees by accident.

## Not Current Work

The following items are intentionally future-only for now:

- first-class backup/restore commands beyond copying the XDG-managed archive
  directories
- a dedicated TUI surface
- broad new filter/output-mode expansion across `view` / `search`
- multi-account archive support
- generalized ArchiveBox integration
- VLM/image pipelines
- clustering/topic modeling
- knowledge-graph extraction
- competitive comparison work against tools like Siftly as a formal product
  track

Those are valid directions, but they should not blur the active archive/import
execution plan.
