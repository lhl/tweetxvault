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

## Not Current Work

The following items are intentionally future-only for now:

- generalized ArchiveBox integration
- VLM/image pipelines
- clustering/topic modeling
- knowledge-graph extraction
- competitive comparison work against tools like Siftly as a formal product
  track

Those are valid directions, but they should not blur the active archive/import
execution plan.
