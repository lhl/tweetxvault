# X Archive Import Sample Analysis

## Sample

- Fixture: `data/twitter-2026-03-16-<redacted>.zip`
- `data/manifest.js` reports:
  - `generationDate = 2026-03-16T08:57:45.244Z`
  - `sizeBytes = 377904361`
  - `isPartialArchive = false`
- ZIP inventory:
  - `7129` total entries
  - `917` entries under `data/`
  - `6211` entries under `assets/`
  - `Your archive.html` renderer at the top level

## High-Value Overlap With tweetxvault

### Authored Tweets

- `data/tweets.js`: `6521` rows
  - Full legacy-style tweet payloads: `full_text`, `entities`, `extended_entities`, reply ids, counts, `created_at`, `edit_info`, etc.
  - `664` tweets have media entities
  - `1855` tweets have URL entities
- `data/tweet-headers.js`: `6521` rows
  - Header-only integrity list (`tweet_id`, `user_id`, `created_at`)
- `data/deleted-tweets.js`: `1` row
  - Full deleted authored tweet payload with `deleted_at`
- `data/deleted-tweet-headers.js`: `1` row
  - Header-only tombstone for the deleted tweet

### Likes

- `data/like.js`: `109251` rows, all unique `tweetId`s
- Shape is thin:
  - `tweetId`
  - `fullText`
  - `expandedUrl`
- Missing compared with live GraphQL likes:
  - no liked-at timestamp
  - no author/account block
  - no `created_at`
  - no media/url entity structure
  - no raw tweet object for secondary extraction

### Media Files

- `data/tweets_media/`: `762` files across `664` tweet ids
  - `549` `.jpg`
  - `107` `.png`
  - `106` `.mp4`
- Filename prefix is the tweet id, so these can be matched back to authored tweets without another lookup table.

## Relevant Non-Overlap / Deferred Data

- Present but not part of the current tweetxvault feature set:
  - `follower.js` (`2174`)
  - `following.js` (`1496`)
  - `direct-messages.js` (`340` conversations)
  - `direct-messages-group.js` (`34` conversations)
  - ads, contacts, profile/account, personalization, lists, Grok chat, device/IP metadata
- Present but empty in this sample:
  - `data/article.js`
  - `data/article-metadata.js`
  - `data/note-tweet.js`
  - `data/community-tweet.js`

## Missing From This Sample

- There is no `bookmark` / `bookmarks` dataset in `data/manifest.js` or the `data/` file list.
- Archive import cannot currently promise bookmark recovery from the official X archive format; we need either another fresh sample or external confirmation that bookmarks are never exported.

## Import Implications

- Authored tweet import can reuse the existing tweet/media/url extraction path via a small YTD adapter:
  - wrap `tweets.js` / `deleted-tweets.js` rows into the internal tweet shape
  - synthesize local author fields from `account.js` / `profile.js`
- Likes import cannot use the same extractor path end-to-end because `like.js` does not contain tweet objects.
  - Import likes as collection membership + sparse tweet/provenance rows.
  - Preserve file order as a fallback ordering surrogate until live sync provides a true timeline `sort_index`.
- `tweet-headers.js` and `deleted-tweet-headers.js` should be treated as integrity helpers, not the primary source of tweet content.
- `tweets_media/` should map into existing `media` rows by filling `local_path` / `download_state = done` where tweet/media metadata already exists or can be derived from the authored tweet payload.

## Proposed Precedence Rules

| Overlap case | Preferred source | Reason |
| --- | --- | --- |
| Authored tweet normalized fields (`tweet_object`, URL/article/media metadata) | `live_graphql` | Richer object graph and better downstream extraction |
| Deleted authored tweets absent from live sync | `x_archive` | Only source that still has the payload |
| Like membership existence | merge both | Archive can prove membership even when the live tweet is now gone |
| Like tweet metadata | `live_graphql` when available | `like.js` is only a sparse text/url snapshot |
| Exported media binaries vs downloadable media URLs | keep both; prefer existing archive file over re-download | Archive already contains the durable offline artifact |

Additional guardrail:

- Keep raw provenance from both sources.
- Do not let import order decide the winner.
- The current extractor/storage merge behavior is effectively “new non-empty value wins”, which is fine within one source but unsafe for live-vs-archive merges.
- Archive import therefore needs explicit source-aware merge logic instead of blindly routing `x_archive` payloads through the existing upsert/coalesce paths.

## Resolved Scaffolding Choices

- **Source-aware merge location**
  - Put the precedence logic in `ArchiveStore`, not in the import command.
  - Reuse the existing `source` column on normalized rows as the current winning-source marker.
  - Keep raw per-source provenance in `raw_capture` rows plus a dedicated import manifest row; callers should not hand-roll merge policy.

- **Import manifest shape**
  - Use a dedicated `import_manifest` record type keyed by archive digest.
  - Store: archive digest, archive generation date, detected partial/full status, import started/completed timestamps, completion status, warning list, and per-dataset counts.
  - Keep public docs/logs redacted; do not print or commit real archive digests.

- **YTD loader boundary**
  - Implement one generic `parse_ytd_js(...)` helper that strips the `window.YTD.* = ...` wrapper and parses JSON.
  - Layer per-file adapters (`tweets.js`, `deleted-tweets.js`, `like.js`, etc.) on top of that generic parser.

- **`like.js` ordering**
  - Preserve the array order with a synthetic archive-derived `sort_index`.
  - Treat it as a fallback signal only; a later live sync can replace it with the real GraphQL timeline sort index.

- **Deleted tweet handling**
  - Import deleted authored tweets as `collection_type = "tweets"` membership rows because they were authored timeline items.
  - Extend the normalized tweet shape with nullable `deleted_at` so deleted tweets remain first-class without inventing a separate tombstone model.

- **`tweets_media/` policy**
  - Copy exported media into the managed tweetxvault media layout during import.
  - Register the copied file paths on `media.local_path` / `download_state = done`.
  - Do not depend on the extracted archive directory remaining in place after import.
