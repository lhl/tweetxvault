# twitter-export — Agent Guide

See `README.md` for project overview, `docs/` for plans and research, `reference/` for third-party repo snapshots.
This `AGENTS.md`/`CLAUDE.md` covers ground rules, workflow, and repo conventions.

Instruction precedence: if this file conflicts with platform/system/developer instructions, follow those first.

## Project Overview

twitter-export is a Python tool for regularly exporting Twitter/X bookmarks and likes into a local embedded database. It extracts authentication credentials automatically from the user's Firefox profile and uses Playwright to intercept Twitter's internal GraphQL API responses.

Part of the broader `attention-export` project (~/github/lhl/attention-export) — a personal system for collecting saved/written content from various online platforms into a searchable local repository.

### Design Principles

1. **Local/private** — all data stays on-machine. No external services, no telemetry, no cloud sync.
2. **Zero-maintenance auth** — credentials extracted automatically from Firefox cookies.sqlite (plaintext on Linux). No manual token copying.
3. **Hash-agnostic** — intercept GraphQL responses by operation name, never hardcode query hashes. The browser supplies current hashes automatically.
4. **Raw-first storage** — store full API responses in the DB. Parse/transform as a separate concern so nothing is lost.
5. **Cronnable** — designed to run unattended on a schedule with no human interaction.

### Tech Stack

- **Language**: Python 3.12+
- **Browser automation**: Playwright (Chromium, headless)
- **Database**: TBD (LanceDB or DuckDB)
- **CLI**: argparse (keep it minimal)
- **Auth**: Firefox cookies.sqlite extraction (sqlite3 stdlib)

## Key Directories

```
twitter-export/
├── twitter/              # Core Python source (the tool we're building)
│   ├── firefox_creds.py  # Firefox cookie extraction
│   ├── scraper.py        # Playwright browser automation + GraphQL capture
│   ├── db.py             # Embedded DB storage layer
│   ├── models.py         # Data models / schemas
│   ├── config.py         # Constants
│   └── cli.py            # CLI entry point
├── docs/                 # Plans, research, implementation notes (separate git repo)
│   ├── initial/          # Initial plans (PLAN-A, PLAN-B, COMPARISON)
│   └── research/         # Research notes
├── reference/            # Third-party repo snapshots (read-only, see reference/README.md)
└── tests/                # Test suite
```

## Work Tracking

### WORKLOG.md (repo root)

Maintain `WORKLOG.md` in the repo root. This is the cross-session handoff file. Log all major actions — not just task completions:
- Implementation decisions and rationale
- Dependency additions and version info
- Blockers encountered and how they were resolved
- Config or infrastructure changes
- Key findings or results

Format: reverse-chronological, date headers, bullet points. Keep entries concise with evidence (commands run, outcomes).

### docs/IMPLEMENTATION.md

For multi-step work, maintain `docs/IMPLEMENTATION.md` as a checklist:
- Update checkboxes as items complete
- Note blockers or deferrals inline
- Multiple agents may coordinate here — append/patch carefully, don't overwrite others' work

### docs/README.md

Index of all documentation. Keep in sync when adding/removing docs.

## Concurrent Work

- Run `git status -sb` before starting work and before each commit.
- Other agents or the human may be working in the repo at the same time.
- Pre-existing dirty or untracked files are non-blocking; leave them untouched.
- Stage and commit only files for your active task.

### Single-File Contention

- If two agents need the same file and edits may overlap, **stop and await instruction**.
- The designated agent stages and commits their scoped hunks first to unblock others.
- Never resolve contention by force-staging the whole file or reverting another agent's work.

### Never Discard Others' Work

Do not use destructive commands unless explicitly instructed:
- `git restore`, `git checkout --`, `git reset --hard`, `git clean`
- `rm -rf`, overwriting redirects like `> file`
- Bulk rewrites that destroy local edits

## Development Workflow

### Before Picking Up Work

- Read `WORKLOG.md` and `docs/IMPLEMENTATION.md` for current state
- Check git status/log for recent changes
- Review relevant docs (see `docs/README.md` for index)
- Confirm your approach aligns with documented plans

### During Execution

- Keep changes incremental and testable
- Log decisions and progress in `WORKLOG.md`
- Test before committing

### After Changes

- Run targeted tests for changed scope
- Update relevant docs if behavior changed
- Commit promptly when a logical unit of work is complete (see Git Practices)

## Project-Specific Rules

### Credential Safety

- **NEVER** commit cookies, auth tokens, session files, or any credential material
- Firefox profile paths should be auto-detected or passed as CLI args, never hardcoded with user-specific paths
- `.gitignore` must exclude: `*.sqlite`, `*.db`, `twitter_session.json`, any `data/` output directories

### Browser Automation

- Default to headless Chromium via Playwright
- Always copy cookies.sqlite to a temp file before reading (Firefox holds WAL lock on the live DB)
- Include `--headful` flag for debugging
- Use reasonable scroll delays (1.5-3s) to avoid triggering rate limits

### Reference Directory

- `reference/` contains third-party repos for study — treat as read-only
- See `reference/README.md` for source URLs and descriptions
- Do not modify vendored code for style or cleanup
- Research notes about reference code go in `docs/`, not inside `reference/`

### Database

- Store raw GraphQL JSON responses as-is before any parsing/transformation
- Deduplicate by tweet `rest_id`
- Track capture metadata: operation type, timestamp, source (playwright/api)

## Git Practices

### Commit Timing

**Commit on completed logical units of work.** A task is a coherent change — not every file edit, but not only milestone closures either. Config changes, doc updates, dependency additions, and working feature increments are all committable units.

- **Commit immediately** after validation passes — do not wait to be asked
- Do not commit mid-task while exploring, debugging, or in a broken state

### Commit Mechanics

- **NEVER** use `git add .`, `git add -A`, or `git commit -a`
- **NEVER** revert, checkout, or restore files you did not modify for the current task
- **ALWAYS** add files explicitly: `git add <file1> <file2> ...`
- **ALWAYS** verify before commit:
  ```bash
  git diff --staged --name-only   # verify only your files
  git diff --staged               # review the actual diff
  ```
- If unrelated changes exist in the worktree, leave them unstaged

### Commit Messages

```
type: short summary (imperative mood)

- Bullet points if needed
- What changed and why
```

- **Conventional prefixes**: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`
- **No bylines** — no co-author footers, no agent attribution, no generated text

## Handling Blockers

| Situation | Action |
|-----------|--------|
| Cookie extraction fails | Check Firefox installed, profile exists, user logged into x.com |
| Playwright detected as bot | Try `--headful` mode; check for new bot detection |
| GraphQL schema changes | Storage unaffected (raw JSON); parsing/extraction may need updates |
| Rate limiting | Increase scroll delays; reduce export frequency |
| Unclear requirements | Check docs first, then ask the lead |
| Unrecognized files in worktree | Leave them — another agent or the human is working on those |
| Need to modify a file with others' edits | Stop and ask before proceeding |

## Communication

- Lead with the substantive finding or result, not just what command was run
- If work is still in progress, state the current concrete result or explicitly say there is no result yet
- Separate what is verified from what is inferred or assumed

## Meta

Update this file when:
- A workflow pattern proves helpful or causes confusion
- A new tool or process gets introduced
- Project structure changes

Keep changes focused on process/behavior. Project-specific details go in `docs/`.
