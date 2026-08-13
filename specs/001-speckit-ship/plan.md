# Implementation Plan: SpecKit Ship

**Branch**: `001-speckit-ship` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-speckit-ship/spec.md`

## Summary

Give any repository one command that carries a branch from working copy to confirmed release
and back to a clean integration branch, and a per-repository editor view that reports what
that pipeline actually did.

The approach: a **SpecKit extension package** (`.specify/extensions/ship/`) whose deterministic
stages — preflight, commit, publish, PR, checks, merge, release, cleanup — live in a Python 3
stdlib engine, with command markdown owning only the two steps that genuinely need a model
(drafting a PR description, proposing a check-failure repair). GitHub is reached through the
`gh` CLI behind a single `HostingClient` seam. The engine writes a per-repository
`state.json`; the Ship view reads it and never writes it. That file is the entire coupling
between the two halves, which is what makes FR-042's separability real rather than asserted.

Two findings from research shaped the plan more than anything in the spec text:

1. **`gh pr checks` cannot be used for machine-readable output.** On the installed
   `gh 2.4.0` it accepts only `-w/--web` — no `--json`, no `--watch`. Checks are read by
   polling `gh pr view --json statusCheckRollup`, which *is* present. A plan built on the
   modern flags would have failed on the machine it was written on.
2. **The Companion's tabs are compiled and closed.** `alfredoperez.speckit-companion@0.31.1`
   declares four views and no extension point, and its spec-detail tabs are bundled strings in
   `dist/webview/spec-viewer.js`. Those tabs are also per-*spec*, while ship state is
   per-*repository*. So the Ship surface ships as a **separate VS Code extension**
   contributing a view into the SpecKit activity-bar container — which is what "alongside the
   existing SpecKit Companion views" already describes.

## Technical Context

**Language/Version**: Python 3.9+ (developed against 3.10.12), standard library only — pipeline
engine. TypeScript 5.x / Node 20+ — Ship view only.

**Primary Dependencies**: `git` ≥ 2.20 (2.34.1 present); `gh` ≥ 2.0 (2.4.0 present) with a
preflight capability probe rather than version trust; VS Code Extension API ≥ 1.85 for the
view. Zero third-party Python packages — the tool must run in any repository with no install
step.

**Storage**: Two JSON documents under `.specify/extensions/ship/` —
`config.json` (committed; settings travel with the repository, FR-041) and `state.json`
(gitignored; run history would otherwise conflict on every merge). Plus `ship.lock`.
Atomic writes via temp file + `os.replace`.

**Testing**: `unittest` (stdlib). Three layers — unit over pure functions; integration against
real git in temp directories with a `git init --bare` local remote; hosting-client contract
tests run against both `GhClient` and a `RecordedClient` replaying captured `gh --json`
payloads. No network in CI. Vitest for the view.

**Target Platform**: Developer machines — Linux, macOS, WSL2. VS Code 1.85+ (desktop and
remote) for the view.

**Project Type**: Developer CLI tool distributed as a SpecKit extension, plus a companion
VS Code extension.

**Performance Goals**: Preflight completes in < 3 s on a warm repository. Checks polling at
10 s, backing off to 30 s, so a 30-minute wait costs ~70 API calls. View renders in < 200 ms
and reflects a state change within ~250 ms (debounced file watch). SC-009 — halve the
hand-driven time — is met by removing waiting and context switching, not by raw speed.

**Constraints**: No third-party Python dependencies. No credential ever read, printed, or
stored by this feature — `gh` holds the token. No network in the automated suite. Every
refusal path leaves the working copy byte-identical. No displayed value may be a placeholder,
a sample, or an inferred default.

**Scale/Scope**: Single developer, single repository, one run at a time (concurrent runs
refused, not queued). ~45 functional requirements across five user stories; roughly 2,500
lines of Python plus ~1,500 of TypeScript.

No `NEEDS CLARIFICATION` items remain — see [research.md](./research.md) "Resolved unknowns".

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Result: not applicable — no ratified constitution exists.**

`.specify/memory/constitution.md` is the unfilled template: every principle is still a
`[PRINCIPLE_N_NAME]` placeholder and the version reads `[CONSTITUTION_VERSION]`. There are no
principles to gate against, and inventing some in order to pass would be a fabricated check.

**Initial gate (pre-Phase 0)**: passed vacuously — no principles, no violations.
**Post-design re-check (post-Phase 1)**: passed vacuously — unchanged; nothing in the design
was constrained or waived by a principle.

**Recommendation, not a blocker**: run `/speckit-constitution` before implementation. Three
commitments this design already makes are exactly the kind that belong there, and writing them
down would make them enforceable beyond this feature: *no simulated behavior on a
user-facing surface*; *an unobserved value is recorded as undetermined, never inferred*; and
*outward-facing irreversible actions are confirmed per run*. Nothing in this feature is
blocked on that happening.

## Project Structure

### Documentation (this feature)

```text
specs/001-speckit-ship/
├── plan.md                        # This file
├── spec.md                        # Feature specification
├── research.md                    # Phase 0 — 13 decisions, 2 open spikes
├── data-model.md                  # Phase 1 — entities, rules, transitions
├── quickstart.md                  # Phase 1 — 8 validation scenarios
├── contracts/                     # Phase 1
│   ├── README.md
│   ├── commands.md                # Command surface + exit codes
│   ├── ship-state.schema.json     # The pipeline ↔ view contract
│   ├── ship-config.schema.json
│   └── hosting-client.md
└── tasks.md                       # Phase 2 — /speckit-tasks, NOT created here
```

### Source Code (repository root)

```text
.specify/extensions/ship/              # The SpecKit extension package — the shippable unit
├── extension.yml                      # schema_version 1.0 manifest, mirrors companion's shape
├── LICENSE
├── commands/
│   ├── speckit.ship.md                # Orchestration + the two AI-seam steps
│   ├── speckit.ship.status.md         # Read-only text rendering of state.json
│   ├── speckit.ship.config.md
│   └── speckit.ship.preflight.md
├── scripts/
│   ├── ship.py                        # CLI entry; argument parsing; exit codes
│   ├── engine.py                      # Stage state machine + transition guards
│   ├── preflight.py                   # FR-001..FR-006 detection and refusals
│   ├── stages/
│   │   ├── commit.py                  # FR-007
│   │   ├── publish.py                 # FR-008
│   │   ├── pull_request.py            # FR-009, FR-010
│   │   ├── checks.py                  # FR-011, FR-012, FR-017 + rollup reduction
│   │   ├── merge.py                   # FR-013, FR-018
│   │   ├── release.py                 # FR-014, FR-015, FR-043..FR-045
│   │   └── cleanup.py                 # FR-023..FR-025
│   ├── repair.py                      # FR-016, FR-019, FR-020 — bounded, two authorities
│   ├── hosting.py                     # HostingClient protocol + GhClient
│   ├── gitops.py                      # git plumbing wrappers
│   ├── state.py                       # Determined<T>, atomic RMW, degradation (FR-026..FR-029)
│   ├── config.py                      # Load + validate (FR-037..FR-041)
│   ├── lock.py                        # FR-022 lock with liveness + stale reclaim
│   └── install.py                     # Register commands, append .gitignore entries
└── tests/
    ├── unit/                          # State machine, rollup reduction, precedence, staleness
    ├── integration/                   # Real git in temp dirs + bare local remote
    ├── contract/                      # HostingClient, both implementations
    └── fixtures/gh/                   # Recorded gh --json payloads
                                       #   incl. mergeable:UNKNOWN and empty rollup

editor/ship-view/                      # The separate VS Code extension (User Stories 4, 5)
├── package.json                       # contributes speckit.views.ship
├── src/
│   ├── extension.ts                   # Activation, FileSystemWatcher (FR-035)
│   ├── stateReader.ts                 # Parses state.json; NEVER writes it
│   ├── configWriter.ts                # Writes config.json; shared validation (FR-040)
│   └── panels/                        # local · published · pr · checks · release · changelog
└── media/

.specify/.gitignore                    # += extensions/ship/state.json, extensions/ship/ship.lock
```

**Structure Decision**: Two independently packaged components, chosen because FR-042 requires
them to be separable and the spec asks that the view be packageable as
`/speckit-community-ship` without rework.

`.specify/extensions/ship/` follows the installed Companion extension's layout exactly —
`extension.yml` + `commands/*.md` + `scripts/*.py`, stdlib only — because that is the shape
SpecKit already installs, registers, and dispatches, and copying it costs nothing while
inventing a new one costs compatibility. `editor/ship-view/` lives outside `.specify/` because
it is a VS Code extension with a build, not a SpecKit artifact.

The two never call each other. They meet at `state.json`, whose schema is fixed in
`contracts/` before either is built.

## Implementation Sequencing

Ordered so each phase is independently demonstrable, matching the spec's story priorities.

| Phase | Delivers | Stories | Demonstrable as |
|---|---|---|---|
| 1 | `state.py`, `config.py`, `lock.py`, `hosting.py` + probe, `preflight.py` | US3 (P2) | `/speckit-ship-preflight` — "can I ship from here, and where would it go?" |
| 2 | commit → publish → pull_request → checks (read-only stop before merge) | US1 (P1) partial | A PR opened and checks reported, nothing merged |
| 3 | merge → release → cleanup, with both release modes | US1 (P1) complete | SC-001 end to end |
| 4 | `repair.py` — conflict class, then check-failure class | US2 (P2) | Scenarios 5a–5c |
| 5 | `editor/ship-view/` reading `state.json` | US4 (P3) | Scenario 8 read-only |
| 6 | Config editing in the view | US5 (P3) | Round-trip: change in view, honored by run |

Phase 1 leads even though User Story 3 is P2, because every later stage depends on the profile
and the state store — and because the riskiest wrong answer in the whole feature is a wrong
integration branch, which is decided there.

**Spike S1 (from research R10) belongs in phase 5's first task**, not later: whether VS Code
renders a view contributed into another extension's `viewsContainer` is unproven, and the
fallback (our own container) is a one-line manifest change *if it is discovered before the
view is built* and a packaging surprise if it is discovered after.

**Spike S2** — capture the recorded `gh --json` fixtures — belongs in phase 2's first task,
since every checks test depends on them.

## Complexity Tracking

No constitution, therefore no violations to justify. Two design choices nonetheless cost more
than the obvious alternative and are recorded here so a reviewer can challenge them:

| Choice | Why | Simpler alternative rejected because |
|---|---|---|
| `Determined<T>` wrapper on every observed value | Makes FR-027 and FR-028 structural, and SC-007 machine-checkable | Bare values with a sibling `*_at` field rely on per-field discipline; the first missed field is invisible and reads as fact |
| A second VS Code extension rather than a tab in the Companion | The Companion has no extension point and its tabs are per-spec, not per-repo (research R10) | Forking the Companion makes our pipeline depend on shipping a competing build of someone else's extension |

## Risks

| Risk | Mitigation |
|---|---|
| Cross-extension `viewsContainer` contribution may not work (**UNVERIFIED**) | Spike S1 first in phase 5; fallback is our own container, no other redesign |
| `gh` surface varies widely by version | Capability probe at preflight, not version trust; `gh 2.4.0` kept in the test matrix as the floor |
| A repository releases in a way neither mode fits | Third verdict `none-determinable`, ask once, record (FR-043); multi-target repos reported unsupported, never partially released |
| Semantic repair makes things worse on a shared branch | Budget default 2; semantic repairs are *proposed and awaited*, not applied; only mechanical conflict repair runs unattended |
| Recorded state drifts from reality between runs | Resumption re-verifies the last succeeded stage against the world before trusting it (research R8) |

## Phase Status

- [x] Phase 0 — research complete, all unknowns resolved, 2 spikes carried forward
- [x] Phase 1 — data model, 4 contracts, quickstart complete
- [x] Constitution check — initial and post-design (both vacuous; no constitution exists)
- [ ] Phase 2 — `/speckit-tasks` (not this command's output)
