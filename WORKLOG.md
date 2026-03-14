# WORKLOG

## 2026-03-14

- Reviewed existing docs and `reference/tweethoarder/` patterns (query-id scraping + caching, feature flags, cursor parsing, backoff).
- Rewrote [docs/PLAN.md](docs/PLAN.md) to lock decisions: SeekDB, TweetHoarder-style direct GraphQL API as primary, Playwright as deferred fallback adapter (no Playwright CLI in MVP), and added a clearer MVP scope + remaining open questions.
- Updated [docs/README.md](docs/README.md) to explicitly mark `docs/initial/` as historical/frozen planning snapshots.
- Slimmed [AGENTS.md](AGENTS.md) (and `CLAUDE.md` symlink) back to repo workflow/safety rules and pointed implementation specifics to [docs/PLAN.md](docs/PLAN.md).
