# Data Model: SpecKit Ship

**Feature**: `001-speckit-ship` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

Two persisted documents, with opposite sharing requirements ([research.md](./research.md) R7):

| File | Holds | Git |
|---|---|---|
| `.specify/extensions/ship/config.json` | Ship Configuration | committed |
| `.specify/extensions/ship/state.json` | Repository Profile, Ship Runs, Stage Outcomes, Check Results, Release Records, Repair Attempts | gitignored |
| `.specify/extensions/ship/ship.lock` | the concurrency lock | gitignored |

The exact wire schemas live in [contracts/](./contracts/); this document defines the entities,
their fields, their rules, and their transitions.

---

## Cross-cutting: the `Determined<T>` shape

FR-028 forbids recording a default or inferred value in place of something the tool could not
establish, and FR-027 requires every recorded value to carry its capture time. Both are
structural, not per-field discipline, so **every observed value in `state.json` is wrapped**:

```
Determined<T> = { value: T,    determined: true,  captured_at: <ISO-8601 UTC>, source: <string> }
              | { value: null, determined: false, captured_at: <ISO-8601 UTC>, reason: <string> }
```

- `source` names *how* the value was obtained (`git-symbolic-ref`, `gh-repo-view`,
  `user-answer`, `config`) — this is what lets FR-004 and SC-002 be audited after the fact.
- `reason` is a stable machine token plus a human sentence, e.g.
  `"no-checks-configured: the repository reports no checks against this pull request"`.
- **`determined: false` with a non-null `value` is invalid** and readers must reject it.
  This single rule is what makes SC-007 checkable by a linter rather than by inspection.

Consumers render `determined: false` as *undetermined, with the reason* (FR-032). No
consumer may substitute a placeholder, a sample, or a zero.

**Configuration is not wrapped.** A config value is a human's stated intent, not an
observation; it is either present or absent.

---

## Entity: Ship Configuration

The developer's per-repository choices. Written by the Ship view (User Story 5) or by hand.
Lives in `config.json`; travels with the repository (FR-041).

| Field | Type | Default | Rules |
|---|---|---|---|
| `schema_version` | int | `1` | Reader tolerates unknown values (FR-029) |
| `source_branch` | string \| null | `null` | `null` = "the current branch". FR-037 |
| `target_branch` | string \| null | `null` | `null` = detect per R4. Must exist on the remote at save time (FR-040) |
| `remote` | string | `"origin"` | Must be a configured remote |
| `pr.composition` | enum | `"commits"` | `manual` \| `commits` \| `drafted` (FR-038) |
| `pr.merge_method` | enum | `"squash"` | `squash` \| `merge` \| `rebase`; must be enabled on the repository |
| `release.mode` | enum \| null | `null` | `observed` \| `executed` \| `none`; `null` = detect per R6 (FR-039, FR-043) |
| `release.action` | object \| null | `null` | **Required when `mode == "executed"`**; one of `{workflow: <file>, ref: <branch>}`, `{release: {tag_from: …}}`, `{script: <path>}` |
| `limits.checks_wait_seconds` | int | `1800` | 60 … 86400 (FR-011) |
| `limits.release_wait_seconds` | int | `1800` | 60 … 86400 (FR-044) |
| `limits.repair_budget` | int | `2` | 0 … 5 (FR-019). `0` disables repair |
| `limits.freshness_seconds` | int | `900` | Staleness window for the view (FR-034) |
| `cleanup.delete_branch` | bool | `true` | FR-023 |

### Validation (FR-040)

Validation runs on save and rejects the whole document on the first violation, naming the
specific problem and **retaining the previous configuration** — a rejected save never leaves
a half-written file.

- `target_branch`, when set, must resolve on `remote`.
- `source_branch` and `target_branch` must differ (this is the FR-005 self-PR refusal,
  enforced at configuration time as well as at run time).
- `release.mode == "executed"` without `release.action` is invalid — the tool never composes
  a release action (spec Assumptions).
- Every `limits.*` value must fall in its stated range.
- `pr.merge_method` must be one the repository permits; if that cannot be checked, the
  control is rendered **disabled with the reason** (FR-036) rather than saved optimistically.

---

## Entity: Repository Profile

What preflight established about this repository. Recorded in `state.json`, re-verified on
every run (a profile is a cache of observations, never an authority).

| Field | Type | Notes |
|---|---|---|
| `is_repository` | `Determined<bool>` | FR-001. `false` ⇒ the run stops, nothing else is written |
| `root` | `Determined<string>` | Absolute path of the work tree |
| `remote` | `Determined<{name, url, host}>` | FR-004 |
| `integration_branch` | `Determined<string>` | `source` records which R4 step answered — FR-002 |
| `integration_branch_candidates` | string[] | Non-empty only when ambiguous; drives the FR-003 prompt |
| `hosting` | `Determined<{service, reachable, authenticated, capabilities}>` | `capabilities` is the R3 probe result, e.g. `{"pr_view_json": true, "pr_checks_json": false}` |
| `has_checks` | `Determined<bool>` | FR-011; `false` is a real answer, distinct from undetermined |
| `release_mode` | `Determined<"observed"\|"executed"\|"none">` | FR-043; `source` is `workflow-trigger`, `config`, or `user-answer` |
| `release_evidence` | string \| null | What produced the release-mode verdict, e.g. the workflow filename |
| `multi_target` | `Determined<bool>` | `true` ⇒ report unsupported and refuse (spec Assumptions) |
| `verified_at` | ISO-8601 | When this profile was last re-verified |

---

## Entity: Ship Run

One end-to-end attempt. Appended to `state.json.runs[]`, newest last. Runs are never
rewritten except for their own stage list and terminal fields.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | `<ISO-8601 compact>-<branch-slug>`; stable across resumptions |
| `branch` | string | The source branch |
| `target_branch` | string | Resolved at preflight |
| `head_sha` | string | Latest published commit |
| `pr` | object \| null | `{number, url, state, base, head}` once opened |
| `merge_commit_sha` | string \| null | The R6 correlation key |
| `stages` | StageOutcome[] | Append-only within a run |
| `repairs` | RepairAttempt[] | FR-019 |
| `status` | enum | `in_progress` \| `halted` \| `complete` |
| `halt_reason` | object \| null | `{classification, message, stage}` (FR-016, FR-020) |
| `started_at` / `ended_at` | ISO-8601 | `ended_at` null while in progress |

### Stage sequence and transitions

```
preflight → commit → publish → pull_request → checks → merge → release → cleanup
```

Rules that make FR-014/FR-015/FR-021 enforceable rather than advisory:

- Stages run in order. A stage may be **skipped** (recorded explicitly with a reason, e.g.
  `commit` skipped because the tree was clean) but never reordered.
- **`merge` cannot start unless `checks` recorded `succeeded`.** An `undetermined` checks
  outcome is not a pass (FR-012).
- **`release` cannot start unless `merge` recorded `succeeded`** and `merge_commit_sha` is
  non-null (FR-014).
- **`release.outcome` is set only from the release path's own confirmation** — never derived
  from the merge stage (FR-015, SC-012). The writer refuses a `release` stage whose evidence
  field is empty.
- **`cleanup` cannot start unless `merge` recorded `succeeded`** (FR-023), and it refuses any
  branch carrying unmerged commits (FR-025).
- `merge` and `release` each require a recorded confirmation token for **this run**
  (see below) — FR-013, SC-006.
- Resumption re-enters at the first stage that is not `succeeded` or `skipped`, after
  re-verifying the last `succeeded` stage against the world (R8).

---

## Entity: Stage Outcome

| Field | Type | Notes |
|---|---|---|
| `stage` | enum | The eight stages above |
| `outcome` | enum | `succeeded` \| `failed` \| `skipped` \| `undetermined` \| `in_progress` |
| `classification` | enum \| null | Required when `failed`: `merge_conflict` \| `check_failure` \| `precondition` \| `permission` (FR-016) |
| `reason` | string \| null | Required when `undetermined` or `skipped` (FR-028) |
| `detail` | object \| null | Stage-specific payload — check results, failing log excerpt (FR-017), commit list |
| `confirmation` | object \| null | `{granted_by, granted_at, scope: "run", prompt}`. **Mandatory on `merge` and `release`** |
| `started_at` / `ended_at` | ISO-8601 | Yields the FR-026 timings |

`confirmation.scope` is always `"run"`. There is no persistable always-yes value — the spec
excludes it, so the schema gives it nowhere to live.

---

## Entity: Check Result

One check reported against the PR, from `statusCheckRollup` (R5).

| Field | Type | Notes |
|---|---|---|
| `name` | string | Check or context name |
| `required` | `Determined<bool>` | Undetermined when branch protection cannot be read — an optional-looking failure may in fact be required |
| `outcome` | enum | `success` \| `failure` \| `pending` \| `skipped` \| `neutral` \| `cancelled` \| `timed_out` |
| `url` | string \| null | Where the output lives (FR-017) |
| `log_excerpt` | string \| null | Retrieved failing output, truncated with a marker |
| `captured_at` | ISO-8601 | FR-027 |

Rollup reduction to a single outcome, and the two undetermined shapes, are specified in
[research.md](./research.md) R5.

---

## Entity: Release Record

Written **only** on confirmation from the release path itself.

| Field | Type | Notes |
|---|---|---|
| `mode` | enum | `observed` \| `executed` |
| `from_merge_sha` | string | Must equal the run's `merge_commit_sha` — the correlation key |
| `identifier` | string | Workflow run id, release tag, or deployment id |
| `outcome` | enum | `released` \| `failed` \| `undetermined` |
| `evidence` | string | **Non-empty, required.** What confirmed it |
| `confirmed_at` | ISO-8601 | When the release path reported |

A record with `outcome: "released"` and an empty `evidence` is invalid and must be rejected
on write. That rule is the schema-level expression of FR-015 — the one thing the tool must
never be able to say untruthfully.

`outcome: "failed"` means the release ran and failed, leaving the integration branch ahead of
production (FR-045). It is distinct from a run that never reached the release stage.

---

## Entity: Repair Attempt

| Field | Type | Notes |
|---|---|---|
| `attempt` | int | 1-based; must not exceed `limits.repair_budget` |
| `targets` | enum | `merge_conflict` \| `check_failure` |
| `authority` | enum | `mechanical` \| `proposed` — `proposed` was shown and awaited, not applied (Acceptance 2.4) |
| `description` | string | What was changed, or proposed |
| `pushed_sha` | string \| null | Null for a proposed-only attempt |
| `subsequent_checks` | enum | `cleared` \| `still_failing` \| `undetermined` \| `not_reached` |
| `started_at` / `ended_at` | ISO-8601 | |

---

## Entity: Run Lock

`ship.lock`, JSON, gitignored (FR-022).

| Field | Type |
|---|---|
| `pid` | int |
| `hostname` | string |
| `branch` | string |
| `run_id` | string |
| `started_at` | ISO-8601 |

A lock whose `hostname` matches this host and whose `pid` is not alive is **stale** — it is
reported and reclaimed. A lock from another host is never reclaimed automatically; it is
reported with its identity so the developer can judge. A live lock is a refusal, never a
queue.

---

## Degradation rules (FR-029, FR-033)

The reader distinguishes four conditions and **never aborts a ship run** for any of them:

| Condition | Behavior |
|---|---|
| File missing | Treat as empty state; the view shows the explicit empty state (FR-033) |
| File unparseable | Report the condition; move it aside as `state.json.corrupt-<timestamp>`; start fresh |
| `schema_version` newer than known | Report; **read-only**; do not write, do not abort the run |
| `schema_version` older than known | Migrate forward on write; keep unknown keys |

Unknown top-level keys are preserved through read-modify-write, matching the Companion's
`.spec-context.json` handling. Empty state and zero values are distinguishable at the schema
level: absence is `null`/missing, a real zero is `{value: 0, determined: true, …}`.

---

## Entity relationships

```
Ship Configuration ──(governs)──▶ Ship Run
Repository Profile ──(preflight produces, run consumes)──▶ Ship Run
Ship Run ──1:N──▶ Stage Outcome
   Stage Outcome[checks]  ──1:N──▶ Check Result
   Stage Outcome[release] ──0:1──▶ Release Record   (from_merge_sha = run.merge_commit_sha)
Ship Run ──0:N──▶ Repair Attempt
Ship Run ──0:1──▶ Run Lock   (while status == in_progress)
```
