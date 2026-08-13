---
description: "Task list for SpecKit Ship implementation"
---

# Tasks: SpecKit Ship

**Input**: Design documents from `/specs/001-speckit-ship/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: **Included.** The spec's measurable outcomes SC-003, SC-006, SC-007, SC-008 and
SC-012 are assertions about what the tool must *never* do — no unconfirmed merge, no inferred
release, no placeholder value, no duplicate PR on resume. A negative like that is only real if
something executes it, so the test tasks below are part of the requirement, not an optional
extra. [research.md](./research.md) R12 fixes the three layers and quickstart.md the scenarios.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths in every description

## Path Conventions

Per [plan.md](./plan.md) "Project Structure" — two independently packaged components:

- **Pipeline**: `.specify/extensions/ship/` (Python 3 stdlib; `scripts/`, `commands/`, `tests/`)
- **Ship view**: `editor/ship-view/` (TypeScript VS Code extension)

They never call each other; they meet at `.specify/extensions/ship/state.json`.

## Sequencing note

plan.md sequences its Phase 1 (state store, config, lock, hosting client, preflight) ahead of
User Story 1 even though preflight belongs to US3 (P2) — every later stage depends on the
profile and the state store, and the riskiest wrong answer in the feature is a wrong
integration branch. That content is absorbed into **Phase 2: Foundational** here, which is how
"Phase 1 leads" is honored inside a priority-ordered task list. The story phases that follow
run in spec priority order: US1 (P1) → US2 (P2) → US3 (P2) → US4 (P3) → US5 (P3).

US3's own phase is therefore not "build preflight" but "make preflight *refuse* correctly and
stand alone" — the refusal matrix, ambiguity handling, and the standalone command that is US3's
independent test surface.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the extension package skeleton SpecKit can install, register, and dispatch.

- [X] T001 Create the extension package tree at `.specify/extensions/ship/` with `commands/`, `scripts/`, `scripts/stages/`, `tests/unit/`, `tests/integration/`, `tests/contract/`, `tests/fixtures/gh/`
- [X] T002 Author `.specify/extensions/ship/extension.yml` — `schema_version: "1.0"`, extension id `ship`, `requires.speckit_version: ">=0.9.5.dev0"`, `tools: [{name: python3, required: false}]`, and the four `provides.commands` entries from [contracts/commands.md](./contracts/commands.md). Declare **no** `hooks:` block — shipping must never fire as a side effect of a spec lifecycle step
- [X] T003 [P] Add `.specify/extensions/ship/LICENSE` (MIT, matching the Companion package)
- [X] T004 [P] Append `extensions/ship/state.json` and `extensions/ship/ship.lock` to `.specify/.gitignore`, under a comment explaining that run state would otherwise conflict on every merge into the integration branch (research R7)
- [X] T005 [P] Write `.specify/extensions/ship/scripts/install.py` — register commands with the host integration, generate the `.claude/skills/speckit-ship*/SKILL.md` wrappers with `source: ship:commands/...` frontmatter, and append the T004 gitignore entries idempotently
- [X] T006 [P] Add `.specify/extensions/ship/tests/run.sh` invoking `python3 -m unittest discover -s .specify/extensions/ship/tests`, and a `tests/__init__.py` in each test package so discovery works from the repository root
- [X] T007 [P] Add `.specify/extensions/ship/tests/contract/test_no_fake_in_production.py` — walk `scripts/` and assert nothing imports `RecordedClient`, enforcing invariant 5 of [contracts/hosting-client.md](./contracts/hosting-client.md) mechanically rather than by convention

**Checkpoint**: `python3 .specify/extensions/ship/scripts/install.py` runs clean and the four commands appear in `.claude/skills/`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The state store, the seams, and the stage machine every user story stands on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### State store — the truthfulness substrate

- [X] T008 Implement `Determined[T]` in `.specify/extensions/ship/scripts/state.py` — the two constructors `determined(value, source)` and `undetermined(reason)`, both stamping `captured_at` in ISO-8601 UTC, per [data-model.md](./data-model.md) "Cross-cutting"
- [X] T009 Implement `validate_determined()` in `.specify/extensions/ship/scripts/state.py` rejecting `determined: false` paired with a non-null `value`, and call it on every write path. This one rule is what makes SC-007 machine-checkable instead of a matter of inspection
- [X] T010 Implement atomic read-modify-write in `.specify/extensions/ship/scripts/state.py` — temp file plus `os.replace`, preserving unknown top-level keys, matching the Companion's `.spec-context.json` handling
- [X] T011 Implement the four degradation paths in `.specify/extensions/ship/scripts/state.py` — missing file, unparseable (move aside as `state.json.corrupt-<timestamp>`), newer `schema_version` (read-only), older (migrate on write). None may abort a ship run (FR-029)
- [X] T012 [P] Implement run/stage/check/release/repair record writers in `.specify/extensions/ship/scripts/state.py`, enforcing the schema's conditional requirements: `classification` on `failed`, `reason` on `undetermined`/`skipped`, `confirmation` on `merge`/`release`, non-empty `evidence` on any release record

### Configuration

- [X] T013 [P] Implement load-with-defaults in `.specify/extensions/ship/scripts/config.py` against [contracts/ship-config.schema.json](./contracts/ship-config.schema.json), returning the documented defaults for every absent key
- [X] T014 [P] Implement `validate_config()` in `.specify/extensions/ship/scripts/config.py` — target branch resolves on the remote, source ≠ target, `executed` mode requires `release.action`, every `limits.*` within range; reject the whole document naming the specific problem and retain the previous configuration (FR-040)

### Concurrency

- [X] T015 [P] Implement `.specify/extensions/ship/scripts/lock.py` — acquire/release writing `{pid, hostname, branch, run_id, started_at}`, PID liveness check, same-host stale reclaim with a report, and refusal (never a queue) on a live lock (FR-022)

### Git and hosting seams

- [X] T016 [P] Implement `.specify/extensions/ship/scripts/gitops.py` — `is_inside_work_tree`, `root`, `current_branch`, `is_detached`, `in_progress_rebase_or_merge`, `porcelain_status`, `remotes`, `symbolic_ref`, `remote_show`, `fetch`, `ahead_behind`, `unmerged_commits`. Every function returns a result object, never raises for an expected condition
- [X] T017 Define the `HostingClient` protocol in `.specify/extensions/ship/scripts/hosting.py` with the twelve methods in [contracts/hosting-client.md](./contracts/hosting-client.md), each returning a result object that can be `undetermined` with a reason
- [X] T018 Implement `GhClient` in `.specify/extensions/ship/scripts/hosting.py`. **Read checks via `gh pr view --json statusCheckRollup,mergeable,mergeStateStatus,state,mergeCommit,url` — never `gh pr checks`**, which on the floor version `gh 2.4.0` accepts only `-w/--web`. Filter `gh run list --json` client-side, since 2.4.0 has no `--branch` flag
- [X] T019 Implement `GhClient.probe()` in `.specify/extensions/ship/scripts/hosting.py` returning `{reachable, authenticated, host, gh_version, capabilities}`, keyed by intended operation rather than version number. An unprobeable capability reports `undetermined`, never assumed present (FR-004)

### Preflight detection core

- [X] T020 Implement repository and remote detection in `.specify/extensions/ship/scripts/preflight.py` — `is_repository`, `root`, `remote`, each as a `Determined` value (FR-001)
- [X] T021 Implement the four-step integration-branch precedence in `.specify/extensions/ship/scripts/preflight.py` — saved config → `git symbolic-ref refs/remotes/<remote>/HEAD` → `gh repo view --json defaultBranchRef` → `git remote show <remote>`, recording which step answered in `source`, and populating `integration_branch_candidates` when ambiguous. **No hardcoded `main`/`master` fallback anywhere** (FR-002)
- [X] T022 [P] Implement `has_checks`, `multi_target`, and release-mode detection in `.specify/extensions/ship/scripts/preflight.py` — read `.github/workflows/` triggers to classify `observed` vs `executed` vs `none-determinable`, recording the evidence that produced the verdict (FR-043, research R6)

### Stage machine

- [X] T023 Implement the eight-stage state machine in `.specify/extensions/ship/scripts/engine.py` — ordered `preflight → commit → publish → pull_request → checks → merge → release → cleanup`, with skip-with-reason but never reorder
- [X] T024 Implement the transition guards in `.specify/extensions/ship/scripts/engine.py`: `merge` requires `checks == succeeded` (an `undetermined` is not a pass, FR-012); `release` requires `merge == succeeded` and a non-null `merge_commit_sha` (FR-014); `cleanup` requires `merge == succeeded` (FR-023); `merge` and `release` each require a `confirmation` scoped to **this run** (FR-013)
- [X] T025 Implement write-ahead journaling in `.specify/extensions/ship/scripts/engine.py` — record the stage `in_progress` *before* its side effect and its outcome after, so a crash mid-stage is recoverable rather than lost (research R8)
- [X] T026 Implement `.specify/extensions/ship/scripts/ship.py` — argument parsing for `--target`, `--dry-run`, `--yes`, `--from`, and the exit-code contract `0/10/20/30/40`. **20 and 30 stay distinct**: "it failed" and "we do not know" are different answers (contracts/commands.md)

### Foundational tests

- [X] T027 [P] Unit tests for `Determined[T]` and its validator in `.specify/extensions/ship/tests/unit/test_state.py`, including the rejection of `determined: false` with a non-null value
- [X] T028 [P] Unit tests for the four degradation paths in `.specify/extensions/ship/tests/unit/test_state_degradation.py`, asserting no path raises
- [X] T029 [P] Unit tests for config validation in `.specify/extensions/ship/tests/unit/test_config.py`, including that a rejected save leaves the previous file byte-identical
- [X] T030 [P] Unit tests for lock liveness and stale reclaim in `.specify/extensions/ship/tests/unit/test_lock.py`
- [X] T031 [P] Unit tests for the branch-detection precedence in `.specify/extensions/ship/tests/unit/test_branch_detection.py`. **Fixtures must use a non-`main` default branch** — a fixture named `main` proves nothing about detection
- [X] T032 [P] Unit tests for the transition guards in `.specify/extensions/ship/tests/unit/test_engine_guards.py`, asserting each forbidden transition raises rather than warns
- [X] T033 Integration harness in `.specify/extensions/ship/tests/integration/harness.py` building throwaway repositories in a temp directory with a `git init --bare` local remote, so git-level tests need no network

**Checkpoint**: `python3 -m unittest discover` passes. Detection, state, locking, and guards work; no stage performs a side effect yet.

---

## Phase 3: User Story 1 — Ship the current branch end to end (Priority: P1) 🎯 MVP

**Goal**: One command carries a branch from working copy to confirmed release and back to a clean, updated integration branch.

**Independent Test**: quickstart.md Scenario 2 — ship a clean feature branch and confirm from GitHub and git themselves that the PR merged, the release confirmed, the branch is gone locally and remotely, and the working copy sits on an up-to-date integration branch.

### Spike and fixtures (blocks the checks work)

- [X] T034 [US1] **Spike S2** — capture real `gh --json` payloads from a live repository into `.specify/extensions/ship/tests/fixtures/gh/`: `pr_view` rollups (all-success, required-failure, mixed required/optional, **empty `statusCheckRollup`**), **at least one `mergeable: "UNKNOWN"`**, a `mergeCommit`, and a `run list` page. The two undetermined shapes are what a naive implementation silently gets wrong, so they are not optional fixtures
- [X] T035 [US1] Implement `RecordedClient` in `.specify/extensions/ship/tests/contract/recorded_client.py` replaying the T034 payloads through the T017 protocol. Lives under `tests/` only — T007 enforces that

### Tests for User Story 1

- [X] T036 [P] [US1] Contract tests in `.specify/extensions/ship/tests/contract/test_hosting_client.py` running the same expectations against `GhClient` and `RecordedClient`
- [X] T037 [P] [US1] Unit tests for rollup reduction in `.specify/extensions/ship/tests/unit/test_checks_reduction.py` — the four outcomes, with an empty rollup reducing to `undetermined:no-checks-configured` and **never** to passed
- [X] T038 [P] [US1] Integration test for commit → publish in `.specify/extensions/ship/tests/integration/test_publish.py` against the bare local remote
- [X] T039 [P] [US1] Integration test for cleanup in `.specify/extensions/ship/tests/integration/test_cleanup.py` — deletion after a confirmed merge, and refusal to delete a branch with unmerged commits (FR-025)
- [X] T040 [P] [US1] Test in `.specify/extensions/ship/tests/contract/test_release_truthfulness.py` asserting a release record claiming `outcome: released` with empty `evidence` is rejected on write (SC-012)
- [X] T041 [P] [US1] Test in `.specify/extensions/ship/tests/contract/test_confirmation_required.py` asserting no `merge` or `release` stage can be recorded `succeeded` without a `confirmation` for its own run (SC-006)

### Implementation for User Story 1

- [X] T042 [P] [US1] Implement `.specify/extensions/ship/scripts/stages/commit.py` — show the staged diff summary *before* committing, skip-with-reason on a clean tree (FR-007)
- [X] T043 [P] [US1] Implement `.specify/extensions/ship/scripts/stages/publish.py` — push and set upstream tracking on first publish (FR-008)
- [X] T044 [US1] Implement `.specify/extensions/ship/scripts/stages/pull_request.py` — adopt an existing open PR for the branch before ever creating one (FR-009); compose title and body per `pr.composition`, and present a drafted description for review **before** the PR is created (FR-010)
- [X] T045 [US1] Implement rollup reduction in `.specify/extensions/ship/scripts/stages/checks.py` — required-only gating, optional failures reported alongside, and the two undetermined shapes (`no-checks-configured`, `checks-wait-exceeded`) as separate reasons (FR-011, FR-012, research R5)
- [X] T046 [US1] Implement bounded polling in `.specify/extensions/ship/scripts/stages/checks.py` — 10 s backing off to 30 s, capped by `limits.checks_wait_seconds`, reporting progress while waiting, and retrieving the failing check's own log excerpt via `gh run view --log-failed` (FR-017)
- [X] T047 [US1] Implement `.specify/extensions/ship/scripts/stages/merge.py` — bounded re-poll of `mergeable: "UNKNOWN"` before concluding anything (a first-query UNKNOWN is lazy computation, not a conflict), the per-run confirmation gate, and merge via the configured method, capturing `merge_commit_sha`
- [X] T048 [US1] Implement observed mode in `.specify/extensions/ship/scripts/stages/release.py` — correlate the release run to the merge by **merge commit SHA**, never by time proximity; watch to a terminal outcome or record `undetermined:release-not-confirmed` at the cap (FR-044)
- [X] T049 [US1] Implement executed mode in `.specify/extensions/ship/scripts/stages/release.py` — run the repository's declared `release.action` after a fresh confirmation, and report a failure as *a failed release with the integration branch ahead of production*, distinct from a run that never attempted one (FR-045)
- [X] T050 [US1] Enforce in `.specify/extensions/ship/scripts/stages/release.py` that a `ReleaseRecord` is written **only** from the release path's own confirmation, with non-empty `evidence` — never derived from the merge stage (FR-015)
- [X] T051 [US1] Implement `.specify/extensions/ship/scripts/stages/cleanup.py` — delete locally and remotely only after a confirmed merge, report rather than fail a refused remote deletion, refuse any branch with unmerged commits, then switch to the integration branch and update it from the remote (FR-023, FR-024, FR-025)
- [X] T052 [US1] Implement resumption in `.specify/extensions/ship/scripts/engine.py` — re-enter at the first stage not `succeeded`/`skipped`, **re-verifying the last succeeded stage against the world** before trusting the journal, since the developer may have merged in the web UI between runs (FR-021, research R8)
- [X] T053 [US1] Write `.specify/extensions/ship/commands/speckit.ship.md` — Companion-style frontmatter and Prerequisites/Execution/Output blocks, orchestrating `ship.py` and owning only the PR-description drafting seam
- [X] T054 [US1] Implement the preflight summary renderer in `.specify/extensions/ship/scripts/ship.py` — print the detected profile and intended actions before the first state-changing action, and stop there on `--dry-run` (FR-006)
- [X] T055 [US1] Implement the nothing-to-ship path in `.specify/extensions/ship/scripts/engine.py` — stop before opening a PR with exit `10` when the branch is identical to the target (Acceptance 1.4)
- [X] T056 [US1] Integration test for resumption in `.specify/extensions/ship/tests/integration/test_resume.py` — interrupt, re-run, assert exactly one PR and one `run_id`; then merge externally and assert the re-run re-verifies and skips to release rather than re-merging (SC-008)

**Checkpoint**: quickstart.md Scenarios 2, 3 and 4 pass. SC-001 is demonstrable end to end.

---

## Phase 4: User Story 2 — Recover from a red pipeline (Priority: P2)

**Goal**: A failed run is diagnosed, bounded repair is attempted, and a halt hands over a precise account rather than a bare shell.

**Independent Test**: quickstart.md Scenario 5 — ship a branch with a broken test and a separate branch that conflicts with the integration branch; confirm each detects the failure class, attempts repair, and either re-enters checks or halts with a specific report.

### Tests for User Story 2

- [X] T057 [P] [US2] Unit tests for failure classification in `.specify/extensions/ship/tests/unit/test_classification.py` covering `merge_conflict`, `check_failure`, `precondition`, `permission`, and asserting an undetermined outcome is **not** given a classification
- [X] T058 [P] [US2] Unit tests for the repair budget in `.specify/extensions/ship/tests/unit/test_repair_budget.py` — attempts never exceed `limits.repair_budget`, and `0` disables repair entirely
- [X] T059 [P] [US2] Integration test for conflict repair in `.specify/extensions/ship/tests/integration/test_conflict_repair.py` against the bare local remote
- [X] T060 [P] [US2] Test in `.specify/extensions/ship/tests/unit/test_exit_codes.py` asserting an unresolved checks outcome exits `30`, not `20` — collapsing them re-creates the inference FR-012 exists to prevent

### Implementation for User Story 2

- [X] T061 [US2] Implement failure classification in `.specify/extensions/ship/scripts/repair.py` into the four classes, reported **before** any repair is attempted (FR-016, Acceptance 2.1)
- [X] T062 [US2] Implement mechanical conflict repair in `.specify/extensions/ship/scripts/repair.py` — detect unmergeability before attempting a merge, bring the branch up to date with the target per `pr.merge_method`, resolve, push, re-enter checks. Conflicts needing a semantic choice are handed back, not guessed (FR-018, SC-005)
- [X] T063 [US2] Implement the check-failure repair seam in `.specify/extensions/ship/scripts/repair.py` — retrieve the failing log, hand it to the command markdown, and record the model's proposal. Authority `proposed`: **described and awaited, never applied** unattended (Acceptance 2.4)
- [X] T064 [US2] Implement budget accounting and halt reporting in `.specify/extensions/ship/scripts/repair.py` — re-enter checks after each attempt, and on exhaustion halt leaving branch and PR intact while reporting every attempt and the residual failure (FR-019, FR-020)
- [X] T065 [US2] Extend `.specify/extensions/ship/commands/speckit.ship.md` with the repair-proposal seam — present the failing output and the proposed change, and wait
- [X] T066 [US2] Implement the halt report renderer in `.specify/extensions/ship/scripts/ship.py` so the failing stage and specific cause are nameable from the report alone, without opening github.com (SC-004)

**Checkpoint**: quickstart.md Scenarios 5a, 5b and 5c pass.

---

## Phase 5: User Story 3 — Ship from an unfamiliar repository (Priority: P2)

**Goal**: Before touching anything, the run establishes ground truth, asks once about what it cannot determine, and refuses — with the reason — on anything that makes the run unsafe.

**Independent Test**: quickstart.md Scenario 1 — run preflight alone against a `main` repo, a `trunk` repo, a repo with no remote, and a non-repository directory; confirm the profile is right each time and the refusal is clear in the last.

### Tests for User Story 3

- [X] T067 [P] [US3] Integration tests for the refusal matrix in `.specify/extensions/ship/tests/integration/test_refusals.py` — not a repository, on the integration branch, detached HEAD, mid-rebase, mid-merge, no remote
- [X] T068 [P] [US3] Test in `.specify/extensions/ship/tests/integration/test_refusal_no_side_effects.py` capturing `git status --porcelain` plus a working-tree hash before and after every refusal and asserting them byte-identical (SC-003)
- [X] T069 [P] [US3] Unit tests for ambiguity handling in `.specify/extensions/ship/tests/unit/test_branch_ambiguity.py` — candidates presented, choice recorded, second run does not ask again (FR-003)

### Implementation for User Story 3

- [X] T070 [US3] Implement the refusal matrix in `.specify/extensions/ship/scripts/preflight.py` — integration branch, detached HEAD, unfinished rebase or merge, each naming the blocking condition and exiting `10` before any state changes (FR-005)
- [X] T071 [US3] Implement the credential and reachability preconditions in `.specify/extensions/ship/scripts/preflight.py` using the T019 probe, stopping before committing anything and naming which precondition failed (FR-004)
- [X] T072 [US3] Implement one-time prompting in `.specify/extensions/ship/scripts/preflight.py` — present integration-branch candidates, record the answer with `source: user-answer`, and never re-ask in that repository (FR-003)
- [X] T073 [US3] Implement the release-mode ask-once path in `.specify/extensions/ship/scripts/preflight.py` for `none-determinable`, recording the answer (FR-043)
- [X] T074 [US3] Implement multi-target detection and refusal in `.specify/extensions/ship/scripts/preflight.py` — a repository whose integration branch feeds several independent release targets is reported **unsupported**, never partially released
- [X] T075 [US3] Write `.specify/extensions/ship/commands/speckit.ship.preflight.md` — the standalone, changes-nothing profile command that is this story's independent test surface
- [X] T076 [US3] Implement profile re-verification on every run in `.specify/extensions/ship/scripts/preflight.py` — a recorded profile is a cache of observations, never an authority (data-model.md)

**Checkpoint**: quickstart.md Scenario 1 passes in all five setups; SC-002 and SC-003 measurable.

---

## Phase 6: User Story 4 — See ship state at a glance (Priority: P3)

**Goal**: A per-repository Ship view reporting local, published, PR, checks, release, and changelog state — every value carrying its capture time, and anything undetermined shown as undetermined.

**Independent Test**: quickstart.md Scenario 8 — point the view at a repository with recorded history and at one with none; confirm each panel reflects recorded state, and that the empty state is explicit rather than a placeholder.

- [X] T077 [US4] **Spike S1 (do this first)** — build a throwaway extension at `editor/spike-container/package.json` declaring a view under the existing `speckit` `viewsContainer`, and record the result in `editor/spike-container/FINDING.md`: does it render alongside the Companion's four views? **UNVERIFIED**: no extension installed on this machine contributes into another publisher's container. If it does not render, fall back to our own activity-bar container — a one-line manifest change *if found now*, a packaging surprise if found after the view is built (research R10)
- [X] T078 [US4] Scaffold `editor/ship-view/` — `package.json` contributing `speckit.views.ship` into the container S1 settled on, `tsconfig.json`, esbuild config, and a Vitest setup
- [X] T079 [P] [US4] Implement `editor/ship-view/src/stateReader.ts` parsing `.specify/extensions/ship/state.json` against [contracts/ship-state.schema.json](./contracts/ship-state.schema.json). **Read-only** — this module has no write path at all
- [X] T080 [P] [US4] Implement `editor/ship-view/src/determined.ts` — the render contract for `Determined<T>`: a determined value renders with its `captured_at`; an undetermined one renders as *undetermined* with its `reason` and **no fallback** (FR-031, FR-032)
- [X] T081 [US4] Implement the six panels in `editor/ship-view/src/panels/` — local (committed but unpublished), published (behind-count), pr, checks, release, changelog (FR-030)
- [X] T082 [US4] Implement the explicit empty state in `editor/ship-view/src/panels/`, distinguishable from a zero value — absence renders as empty, a real zero renders as `0` (FR-033)
- [X] T083 [US4] Implement staleness in `editor/ship-view/src/staleness.ts` — compare each value's `captured_at` against `limits.freshness_seconds`, mark stale, and offer a refresh that re-runs the read-only probe (FR-034)
- [X] T084 [US4] Implement the `FileSystemWatcher` on `**/.specify/extensions/ship/state.json` in `editor/ship-view/src/extension.ts`, debounced ~250 ms, so an in-progress run advances the view with no editor restart (FR-035)
- [X] T085 [P] [US4] Vitest tests in `editor/ship-view/src/__tests__/render.test.ts` asserting every panel renders undetermined-as-undetermined and empty-as-empty, with **no hardcoded sample values anywhere in the module** (SC-007)
- [X] T086 [P] [US4] Write `.specify/extensions/ship/commands/speckit.ship.status.md` and the read-only renderer in `.specify/extensions/ship/scripts/ship.py` — the same state as text, plus `--json`. This is what makes the pipeline fully usable with no view installed (FR-042)
- [X] T087 [US4] Script the FR-042 separability check in `.specify/extensions/ship/tests/integration/test_separability.sh` — run the pipeline to completion with the view uninstalled, then render the view against a fixture repository with no history and confirm an honest empty state rather than an error

**Checkpoint**: quickstart.md Scenario 8 read-only cases pass; SC-007 and SC-010 measurable.

---

## Phase 7: User Story 5 — Configure ship behavior from the tab (Priority: P3)

**Goal**: Set branches, PR composition, and release definition from the view; settings persist with the repository and take effect on the next run.

**Independent Test**: quickstart.md Scenario 8 config cases — change the target branch and composition mode in the view, run a ship, and confirm the run honors both.

- [X] T088 [US5] Implement `editor/ship-view/src/configWriter.ts` writing `.specify/extensions/ship/config.json` atomically. It writes **only** config — never `state.json`
- [X] T089 [US5] Port the T014 validation rules to `editor/ship-view/src/configValidation.ts` so the view and the CLI reject identically, and add a test asserting parity against the shared schema. Two validators that drift are worse than one (FR-040)
- [X] T090 [P] [US5] Implement branch, composition-mode, and merge-method controls in `editor/ship-view/src/panels/configPanel.ts`, backed by `repo_meta()` for which merge methods the repository permits
- [X] T091 [P] [US5] Implement release-mode configuration in `editor/ship-view/src/panels/configPanel.ts` — observed/executed/none, with `release.action` required and validated when `executed` is chosen (FR-039)
- [X] T092 [US5] Implement the disabled-control rule in `editor/ship-view/src/panels/configPanel.ts` — any control whose backing capability is unavailable renders **visibly disabled with the reason stated** and cannot be toggled. A control that looks operable and is not is the exact defect this feature exists to avoid (FR-036)
- [X] T093 [US5] Implement rejected-save handling in `editor/ship-view/src/configWriter.ts` — name the specific problem, retain the previous configuration, leave no half-written file (FR-040)
- [X] T094 [P] [US5] Write `.specify/extensions/ship/commands/speckit.ship.config.md` and the `config` subcommand in `.specify/extensions/ship/scripts/ship.py` for show/set/validate from the CLI
- [X] T095 [US5] Round-trip test in `editor/ship-view/src/__tests__/config-roundtrip.test.ts` plus a manual run: change the target branch in the view, ship, confirm the run used it (Acceptance 5.1)

**Checkpoint**: All five user stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T096 [P] Write `.specify/extensions/ship/README.md` — install, the four commands, the config reference, and the state-file contract for community consumers
- [X] T097 [P] Document the `/speckit-community-ship` packaging path in `editor/ship-view/README.md`, since the state file was designed to make that repackaging need no rework
- [X] T098 Run the quickstart.md Scenario 6d audit across every recorded run and confirm it returns `none` — zero release records claiming `released` with empty evidence (SC-012)
- [X] T099 [P] Add a CI workflow at `.github/workflows/ship-tests.yml` running `python3 -m unittest discover` plus the T007 import check, with network access disabled to prove the suite is hermetic
- [X] T100 [P] Package `editor/ship-view/` as a `.vsix` and verify installation into a clean VS Code profile
- [X] T101 Execute the full quickstart.md Definition of Done checklist and record the result
- [X] T102 Run `/speckit-constitution` and consider ratifying the three commitments plan.md names — no simulated behavior on a user-facing surface, unobserved values recorded as undetermined, per-run confirmation on irreversible outward actions. Currently `.specify/memory/constitution.md` is the unfilled template, so nothing in this feature was gated

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on US1 — repair re-enters the checks stage US1 builds
- **US3 (Phase 5)**: depends on Foundational only. **Can run in parallel with US1/US2**
- **US4 (Phase 6)**: depends on Foundational for the state schema; richer with US1 history but testable against a fixture `state.json`
- **US5 (Phase 7)**: depends on US4's scaffold
- **Polish (Phase 8)**: depends on the desired stories being complete

### The two real cross-story couplings

Everything else is independent; these two are not, and pretending otherwise would produce a
plan that breaks in week three:

1. **US2 → US1**: repair re-enters the checks stage, so `stages/checks.py` must exist first.
2. **US5 → US4**: the config panel needs the view to exist.

US4 does **not** depend on US1 being finished — it reads a file, and a fixture `state.json`
built from the contract stands in. That is FR-042 being true in practice rather than declared.

### Within each user story

- Tests before implementation where the test defines the contract (T037 before T045, T040/T041 before T048–T050)
- State writers before stages that call them
- Stages before the command markdown that orchestrates them

### Parallel Opportunities

- **Setup**: T003–T007 all parallel
- **Foundational**: T012–T016 parallel; T027–T032 all parallel once their subjects exist
- **US1**: T036–T041 parallel; T042/T043 parallel
- **US2**: T057–T060 parallel
- **US3**: T067–T069 parallel
- **US4**: T079/T080 parallel; T085/T086 parallel
- **US5**: T090/T091 parallel
- **Cross-story**: US3 can proceed alongside US1 and US2 with a second developer

---

## Parallel Example: User Story 1

```bash
# Tests first — these define the contracts the stages must satisfy:
Task: "Contract tests for HostingClient in tests/contract/test_hosting_client.py"
Task: "Unit tests for rollup reduction in tests/unit/test_checks_reduction.py"
Task: "Release truthfulness test in tests/contract/test_release_truthfulness.py"
Task: "Confirmation-required test in tests/contract/test_confirmation_required.py"

# Then the two independent stages:
Task: "Implement scripts/stages/commit.py"
Task: "Implement scripts/stages/publish.py"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup
2. Phase 2 Foundational — **blocks everything**
3. Phase 3 US1
4. **STOP and VALIDATE** against quickstart.md Scenarios 2, 3, 4 and the 6d audit
5. This is a genuinely shippable tool: one command, working, gated at merge and release

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. + US1 → **MVP**: ship end to end
3. + US2 → red pipelines diagnosed and bounded-repaired
4. + US3 → safe in unfamiliar repositories (parallelizable with US2)
5. + US4 → editor visibility
6. + US5 → editor configuration

### Sequencing the two spikes

Both spikes gate work that is expensive to redo, and both sit at the front of the phase they
gate rather than being discovered inside it:

- **S2 (T034)** — first task of US1. Every checks test depends on the fixtures.
- **S1 (T077)** — first task of US4. If cross-extension container contribution does not work,
  the fallback is one manifest line *now* and a packaging surprise *later*.

---

## Notes

- `[P]` = different files, no dependency on incomplete work
- The pipeline is stdlib-only Python; no `pip install` step appears anywhere by design
- Never introduce `gh pr checks` for machine-readable output — the floor version cannot do it
- No task reads, prints, or stores a credential; `gh` holds the token
- Commit after each task or logical group; every checkpoint is a valid stopping point
