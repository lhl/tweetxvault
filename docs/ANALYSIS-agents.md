# AGENTS.md Pattern Analysis

Survey of AGENTS.md conventions across 20 repos (lhl/ and shisa-ai/), extracted 2026-03-14. The goal is to identify what works at what scale, how conventions adapt to project type, and what this project adopted.

## Project Taxonomy

AGENTS.md conventions vary along two primary axes: **project type** (what kind of work) and **project scale** (complexity, lifespan, agent count). Most conventions are tuned to the intersection of these two.

### By Project Type

#### 1. Long-lived Codebases (frameworks, platforms, apps)

Full software projects with architecture, test suites, and multi-milestone development.

| Repo | Domain | Scale | Distinguishing Pattern |
|------|--------|-------|----------------------|
| shisad | Security agent framework | Heavy | Design philosophy doc as governing document, behavioral tests as closure gate, security checklists, adversarial test suites |
| qsvault | Health data platform | Heavy | Spec→plan→test→implement cycle, parser/router checklists, validation matrix, data integrity focus |
| realitycheck | Analysis framework | Heavy | Roles (planner/coder/reviewer), claim integrity triad, review trace IDs, LanceDB patterns |
| vibecheck | Hackathon PWA | Medium-heavy | Multi-agent contention protocol, WU-based punchlist, WORKLOG for everything, frontend deploy rebuild rules |
| edit-md | Desktop editor | Medium | Autonomous oneshot mode, milestone-scoped commits, reference code index, round-trip fidelity as key metric |
| localmodels | LLM calculator | Medium | Architecture diagram in AGENTS.md, core formulas as invariants ("do not invent simplified formulas"), spot-check values |
| ChatLHL | Chat app | Medium | Multi-agent safety block, conventional commits, WORKLOG + IMPLEMENTATION split |
| re-vibed | AI agent platform | Medium | STATUS.txt for team assignments, change request process, code quality framework ("11 Aspects"), review docs |
| learn-japanese | Learning tracker | Medium | Domain-specific session protocol, resume protocol, activity tracking, multi-model agent roster with strengths |
| randomfoo.net | Digital garden | Light | Low-priority/long-lived framing, ADR system, devlog session export, content source audit table |

**Common patterns at this scale:**
- Spec → plan → test → implement cycle (heavier projects enforce strictly, lighter ones apply flexibly)
- docs/IMPLEMENTATION.md as authoritative punchlist
- WORKLOG.md in root for cross-session state
- Validation matrix (test commands by scope: targeted → focused → full)
- Reference directories with provenance tracking

#### 2. Research / Analysis Projects

Knowledge collection, literature review, claim extraction, experiment-driven.

| Repo | Domain | Scale | Distinguishing Pattern |
|------|--------|-------|----------------------|
| agentic-memory | Memory systems survey | Light | YAML frontmatter on references, vendor snapshot rules, triage pipeline (REVIEWED.md → ANALYSIS.md promotion) |
| agentic-security | Security research | Minimal | Generated artifacts list (don't hand-edit), reference sync scripts |
| ai-coding-measurement | Usage analysis | Light | Source capture workflow (curl → extract → playwright escalation), WORKLOG as handoff, claim tracking |
| cross-lingual-token-leakage | NLP research | Light | Standard lhl-dev-misc template, RESEARCH.*.md notebooks |
| postsingularity-economic-theories | Economics | Light | Extensive claim tracking, source-batch files, provenance over dedup |

**Common patterns at this scale:**
- Research hygiene: separate author claims from our verification
- Source/reference management with provenance
- WORKLOG.md as primary handoff (lighter than IMPLEMENTATION.md)
- Lightweight validation (py_compile, smoke tests) rather than test suites
- Append-only logs, avoid overwriting others' research notes

#### 3. Optimization / Benchmarking Projects

Iterative performance work with measurable metrics and keep/revert decisions.

| Repo | Domain | Scale | Distinguishing Pattern |
|------|--------|-------|----------------------|
| fsr4-rdna3 | GPU shader optimization | Minimal AGENTS.md | Environment setup (ROCm, mamba, conda vars), smoke test commands, IMPLEMENTATION.md with mean/stddev/cv metrics |
| shisa-moe | MoE model optimization | Minimal AGENTS.md | Optimization loop: implement → test → benchmark → compare → keep/revert → document → repeat |
| better-moe-training | MoE training research | Light | Multi-agent coordination, RESEARCH.*.md notebooks, no-submodules rule, checkout-reference-repos.sh |
| benchmarks | LLM benchmarking | Medium | Schema-first development, fresh-benchmarks-only (no old data reuse), move-don't-delete for archives |
| claudescycles-revisited | Math research | Medium | Non-negotiable logging discipline, optimization loop (benchmark-gated), state/CONTEXT.md restart capsule |

**Common patterns at this scale:**
- **Optimization loop** is the key pattern: implement → test → benchmark → compare baseline → keep or revert → document
- Environment setup is critical (conda/mamba, specific versions, GPU targets)
- Results must be recorded with exact commands and metrics before continuing
- state/CONTEXT.md or equivalent restart capsule for experiment-heavy work
- Baseline preservation: keep immutable baselines, edit only in opt/ directories

#### 4. Small Tools / Scripts / Hacks

Focused utilities, personal tools, one-off scripts.

| Repo | Domain | Scale | Distinguishing Pattern |
|------|--------|-------|----------------------|
| msi-prestige13-a2vm-webcam | Hardware debugging | Medium-light | Communication rules (lead with findings), privilege boundaries (sudo/reboot as user-run), evidence vs inference separation |
| chotto-subtitler | Subtitle tool | Light | Conda/mamba rules, basic coordination hygiene |
| juku | Synthetic data framework | Light | Quality gates (ruff, mypy, pytest), no network in tests, interface-first PRs |
| **twitter-export** | **Scraping tool** | **Light** | **Cookie safety, browser automation rules, hash-agnostic design principle** |

**Common patterns at this scale:**
- Minimal AGENTS.md — just the essentials
- Focus on project-specific gotchas (environment, credentials, privilege)
- Skip formal roles, TDD mandates, claim integrity — they don't pay for themselves
- WORKLOG.md is sufficient without full IMPLEMENTATION.md punchlist in many cases

### By Scale (AGENTS.md Weight)

The weight of AGENTS.md correlates with: agent count, project lifespan, consequence of errors, and code complexity.

```
Minimal (~20 lines)          Light (~50-80 lines)         Medium (~100-200 lines)       Heavy (~200-340 lines)
┌──────────────────┐    ┌──────────────────────┐    ┌──────────────────────────┐   ┌─────────────────────────┐
│ fsr4-rdna3       │    │ agentic-memory       │    │ vibecheck                │   │ shisad                  │
│ shisa-moe        │    │ ai-coding-measurement│    │ edit-md                  │   │ qsvault                 │
│ agentic-security │    │ chotto-subtitler     │    │ localmodels              │   │ realitycheck            │
│                  │    │ better-moe-training  │    │ claudescycles-revisited  │   │                         │
│                  │    │ cross-lingual-*      │    │ msi-prestige13           │   │                         │
│                  │    │ juku                 │    │ benchmarks               │   │                         │
│                  │    │ randomfoo.net        │    │ ChatLHL                  │   │                         │
│                  │    │                      │    │ re-vibed                 │   │                         │
│                  │    │                      │    │ learn-japanese           │   │                         │
└──────────────────┘    └──────────────────────┘    └──────────────────────────┘   └─────────────────────────┘
 Environment setup       + Research/ref hygiene      + Execution loops            + Roles, claim integrity
 Pointers to docs        + Git basics                + Contention protocols       + Security checklists
 Optimization loop       + WORKLOG conventions       + Validation matrices        + Design philosophy docs
                                                     + Communication rules        + Closure checklists
                                                     + Blocker tables             + Deferral tracking
```

**What drives weight up:**
- Multi-agent concurrent work (vibecheck, re-vibed, learn-japanese)
- Security/data integrity consequences (shisad, qsvault)
- Complex build/deploy pipeline (vibecheck, edit-md, localmodels)
- Need for formal review (realitycheck, shisad)

**What keeps weight down:**
- Single-agent or single-developer (fsr4-rdna3, agentic-memory)
- Research/exploration focus (ai-coding-measurement, agentic-security)
- Short lifespan or focused scope (chotto-subtitler, juku)
- Environment is the main complexity, not process (fsr4-rdna3, better-moe-training)

## Pattern Deep-Dives

### 1. Project Memory / State Files

Most repos converge on a two-or-three-file system for durable memory:

| File | Location | Purpose | Repos Using |
|------|----------|---------|-------------|
| `WORKLOG.md` | Root | Chronological session log, cross-session handoff | vibecheck, claudescycles, msi-prestige13, ai-coding-measurement, edit-md, fsr4-rdna3, localmodels, juku, ChatLHL |
| `IMPLEMENTATION.md` | docs/ (or root in older repos) | Active punchlist/checklist | realitycheck, qsvault, shisad, vibecheck, edit-md, claudescycles, fsr4-rdna3, localmodels, learn-japanese, juku, ChatLHL, re-vibed |
| `state/CONTEXT.md` | state/ | Short restart capsule (objective, latest evidence, next actions) | claudescycles, msi-prestige13 |
| `STATUS.md` / `STATUS.txt` | Root | Current focus, team assignments, metrics | learn-japanese, re-vibed |

**Trend**: WORKLOG.md in root is now universal for active projects. IMPLEMENTATION.md has migrated to docs/ in newer repos. STATUS files appear when multiple humans (not just agents) coordinate.

### 2. Execution Modes

| Mode | When to Use | Repos |
|------|-------------|-------|
| **Interactive** | Research, exploration, unclear requirements | lhl-dev-misc, chotto-subtitler, ai-coding-measurement, agentic-memory |
| **Punchlist-driven** | Evolving projects with clear task breakdown | realitycheck, qsvault, shisad, vibecheck, claudescycles, ChatLHL |
| **Autonomous oneshot** | Greenfield with complete spec, single agent | edit-md |
| **Optimization loop** | Performance work with measurable metrics | fsr4-rdna3, shisa-moe, claudescycles |
| **Session-based** | Coaching, learning, periodic check-ins | learn-japanese |

**Observation**: The optimization loop (shisa-moe, fsr4-rdna3, claudescycles) is a distinct execution mode that doesn't fit neatly into the interactive/punchlist/oneshot spectrum. It's a tight implement→benchmark→decide cycle that needs its own logging discipline.

### 3. Commit Discipline

Universal across all 20 repos (no exceptions):
- Never `git add .` / `git add -A` / `git commit -a`
- Stage explicitly, verify with `git diff --staged --name-only` + `git diff --staged`
- Conventional commit prefixes (`feat:`, `fix:`, `docs:`, etc.)
- No bylines, co-author footers, or AI attribution

The "when to commit" spectrum:

| Policy | Repos | Description |
|--------|-------|-------------|
| Commit freely, often | re-vibed, randomfoo.net | "git makes it easy to roll back" |
| Commit on logical unit completion | vibecheck, edit-md, shisad, ChatLHL | Most common — completed tasks, not file edits |
| Commit after lead review | juku, cross-lingual-*, better-moe-training | More cautious; lead approval required |
| Commit working code only | chotto-subtitler, lhl-dev-misc | Don't commit broken state |

**Trend**: Newer repos are converging on "commit immediately on logical completion, don't wait to be asked" as the default, with lead-review as an explicit override for sensitive repos.

### 4. Multi-Agent Safety

Evolution timeline:

| Gen | Pattern | Example Repos |
|-----|---------|--------------|
| Gen 1 | "Be careful with others' work" | lhl-dev-misc, chotto-subtitler, better-moe-training |
| Gen 2 | Explicit destructive command blocklist | realitycheck, ChatLHL |
| Gen 3 | Single-file contention protocol + decision table | vibecheck, shisad |
| Gen 4 | Task claiming in IMPLEMENTATION.md + agent roster | learn-japanese, re-vibed |

**Notable from learn-japanese**: Multi-model agent roster with explicit strengths (gpt-5.2-medium for coaching, Claude Opus for code review, ChatGPT Voice for pronunciation). Task claiming via IMPLEMENTATION.md "CURRENTLY IN PROGRESS" table. This is the most sophisticated multi-agent coordination but also the most domain-specific.

**Notable from re-vibed**: STATUS.txt for team assignments (human+AI pairs), formal change request process for mid-project feature additions.

### 5. Domain-Specific Patterns Worth Noting

**Optimization repos (shisa-moe, fsr4-rdna3, claudescycles):**
```
Optimization loop:
1. Implement candidate
2. Smoke test / correctness check
3. Benchmark against baseline
4. Record metrics (mean/stddev/cv)
5. Keep + commit if improved; revert code (keep docs) if regressed
6. Repeat
```

**Hardware debugging (msi-prestige13):**
- Privilege boundaries: `sudo` and `reboot` are user-run; agent gives exact commands + expected validation
- Evidence hierarchy: separate direct evidence, inference, and working assumption
- Hypothesis challenging: after multiple low-signal negatives, run a fresh-context challenge pass

**Security framework (shisad):**
- "Security enables functionality" as governing principle
- Behavioral tests as hard gate (if they fail, milestone is not closeable regardless of other tests)
- Opportunistic cleanup on file touch (remove dead code in same-file edits, not separate refactor scope)

**Learning/coaching (learn-japanese):**
- Resume protocol at every session start (read STATUS, STUDENT_PROFILE, LESSON_PLAN, latest session)
- Default session questions (asked every time)
- Activity tracking for practice consistency visibility
- "Always write outcomes into repo files; never rely on any agent's memory between sessions"

**Digital garden (randomfoo.net):**
- Low-priority/long-lived framing sets expectations
- ADR (Architecture Decision Records) system for decision trail
- Devlog session export (copy JSONL from Claude/Codex to devlog/)

**Synthetic data (juku):**
- Interface-first PRs (types + stubs + tests) so other agents can build in parallel
- No network in tests (ever) — mock all I/O
- Quality gates as explicit commands: `make fmt`, `make lint`, `make typecheck`, `make test`

**Hackathon (re-vibed):**
- "11 Aspects of Good Code" quality framework with quick-reference table
- Change request process for mid-hackathon "twist" requirements
- Review docs as structured artifacts (status, categorized findings, actionable items)

## Evolution Trends

1. **WORKLOG.md** adoption is universal in active projects — earlier repos used only IMPLEMENTATION.md, newer ones always add WORKLOG for richer session logging
2. **Commit timing** has converged on "commit on logical completion, immediately" — older repos were vague or overly cautious, newer ones are explicit
3. **Contention protocols** emerged in vibecheck after real multi-agent conflicts — now included by default even in single-agent projects (low cost)
4. **Autonomous execution** (edit-md) is the newest pattern — only appropriate for greenfield with complete specs
5. **Optimization loop** as a distinct execution mode is well-established in ML/GPU repos but not yet formalized as a reusable pattern
6. **Claim integrity** (shisad/realitycheck) is the most mature but also most expensive pattern — worth the cost for frameworks, not for scripts
7. **Design philosophy docs** (shisad) as a separate governing document is powerful for projects where agents systematically violate non-obvious principles
8. **Agent rosters** (learn-japanese) with explicit model strengths are emerging for multi-model projects
9. **Lightweight is fine** — fsr4-rdna3 and agentic-security prove that a 20-line AGENTS.md works perfectly for focused projects; weight should match actual need

## Choosing Your AGENTS.md Weight

Decision guide based on observed patterns:

```
Is this a focused script/tool with 1 agent?
  → Minimal: environment setup, project-specific gotchas, git basics
  → Examples: fsr4-rdna3, agentic-security, shisa-moe

Is this a research/analysis project?
  → Light: + research hygiene, source management, WORKLOG conventions
  → Examples: agentic-memory, ai-coding-measurement

Is this a multi-milestone coding project?
  → Medium: + execution loops, validation matrix, contention protocols, blocker tables
  → Examples: vibecheck, edit-md, localmodels, benchmarks

Is this a framework/platform with security or data integrity concerns?
  → Heavy: + roles, claim integrity, security checklists, closure procedures
  → Examples: shisad, qsvault, realitycheck

Does the project involve multiple human+AI teams?
  → Add: STATUS file, task claiming, agent roster, change request process
  → Examples: re-vibed, learn-japanese
```

## What twitter-export Adopted and Why

twitter-export is a **small tool** (focused scope, single developer, personal use) in the **research-transitioning-to-implementation** phase. We chose **light-to-medium** weight.

| Pattern | Adopted? | Source | Why |
|---------|----------|--------|-----|
| WORKLOG.md in root | Yes | Universal | Low-cost, high-value cross-session handoff |
| IMPLEMENTATION.md in docs/ | Yes | Standard | Punchlist for implementation phase |
| docs/README.md as index | Yes | localmodels, edit-md | Navigability |
| Conventional commits | Yes | Universal | No exceptions across 20 repos |
| Explicit staging + verification | Yes | Universal | No exceptions across 20 repos |
| Multi-agent contention protocol | Yes | vibecheck | Low-cost safety net |
| Blocker decision table | Yes | vibecheck | Quick reference |
| Communication rules | Yes | msi-prestige13 | Universally useful |
| Reference README with URLs | Yes | agentic-memory | Lighter than frontmatter |
| Credential safety rules | Yes | shisad (adapted) | Essential for this project |
| Browser automation rules | Yes | Original | Project-specific gotcha |
| Strict TDD | No | — | Overkill for scraping tool |
| Roles system | No | — | Single-developer project |
| Claim integrity triad | No | — | Too heavy for personal tool |
| Optimization loop | No | — | Not an optimization project |
| state/CONTEXT.md | No | — | WORKLOG.md sufficient |
| Autonomous oneshot mode | No | — | Interactive/punchlist better fit |
| Design philosophy doc | No | — | Principles simple enough to inline |
| Agent roster | No | — | Single agent |
| Validation matrix | Not yet | — | Will add when test suite exists |
| Change request process | No | — | Solo developer, no mid-project twists |
