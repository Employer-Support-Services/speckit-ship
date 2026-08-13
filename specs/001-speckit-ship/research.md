# Phase 0 Research: SpecKit Ship

**Feature**: `001-speckit-ship` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

Every decision below was checked against a system of record on this machine (the installed
Companion extension's own files, the `gh` binary's own help output, the repository's own
config) rather than inferred. Where something could not be checked, it is marked
**UNVERIFIED** and carries a spike task rather than an assumption.

---

## R1 — Delivery vehicle: a SpecKit extension package, not a bare skill

**Decision**: Ship the pipeline as a SpecKit *extension package* at
`.specify/extensions/ship/`, following the exact shape the installed `companion` extension
uses: an `extension.yml` manifest (`schema_version: "1.0"`), `commands/*.md` prompt files,
and `scripts/*.py`. The Claude-visible `/speckit-ship` skill is generated from the command
markdown the same way `.claude/skills/speckit-companion-*/SKILL.md` is generated from
`.specify/extensions/companion/commands/*.md`.

**Rationale**: Verified against `.specify/extensions/companion/extension.yml` and
`.claude/skills/speckit-companion-status/SKILL.md`, whose frontmatter carries
`source: companion:commands/speckit.companion.status.md`. The extension package is the
distribution unit SpecKit already understands — it gets a registry entry, hook
registration in `.specify/extensions.yml`, and command dispatch via the `claude`
integration's `invoke_separator: "-"` (so `speckit.ship` → `/speckit-ship`). The user's
description asked for "a new /speckit-ship skill **and community extension**"; this is the
one artifact that is both.

**Alternatives considered**:
- *A standalone `.claude/skills/speckit-ship/` skill only*. Rejected: not installable by
  anyone else, gets no hook registration, and cannot be published as a community extension.
- *A bundled `.specify/workflows/` workflow*. Rejected: workflows sequence existing
  commands with gates; they do not provide new commands or scripts.

---

## R2 — Runtime: Python 3 standard library, plus markdown commands for the AI seam

**Decision**: All deterministic stages (preflight, commit, publish, PR, checks polling,
merge, release, cleanup, state, locking) live in Python 3 stdlib modules under
`.specify/extensions/ship/scripts/`. The markdown commands orchestrate and own only the
stages that genuinely need a language model: drafting a PR description (FR-010) and
attempting a code repair (FR-019).

**Rationale**: `python3 --version` → **3.10.12**, present. The Companion sets the precedent
exactly — 16 stdlib-only scripts, `python3` declared `required: false` in its manifest, and
every command markdown opening with a "verify python3, warn and skip if absent" preamble.
Stdlib-only means no install step, no lockfile, and no dependency drift for a tool that must
run in *any* repository. Putting the deterministic stages in Python (not in prose) is what
makes SC-003 ("stops before changing anything, 100% of cases"), SC-006 ("zero unconfirmed
merges") and SC-012 ("zero releases inferred from a merge") testable rather than aspirational
— a prompt cannot be unit-tested, a state machine can.

**Alternatives considered**:
- *Bash*, matching `.specify/scripts/bash/`. Rejected: the ship engine needs JSON
  read-modify-write, atomic replace, a lock file with liveness detection, and a stage state
  machine. `common.sh` is 60+ lines of careful path handling for a much smaller job; this
  would be worse in bash and untestable.
- *Node/TypeScript*, sharing code with the VS Code view. Rejected for the pipeline: adds a
  build step and `node_modules` to every consuming repository. Node is used only inside the
  separate VS Code extension (R10), which already has a build.
- *Prose-only command markdown driving `git`/`gh` through the agent*. Rejected: FR-021
  resumability, FR-022 concurrency refusal, and FR-026 state recording all demand
  deterministic, testable control flow. This is also the failure mode the user's standing
  "no mocks in production" rule targets — an agent narrating stage transitions is not the
  same as stages having happened.

---

## R3 — Hosting client: the `gh` CLI, with a capability probe at preflight

**Decision**: Talk to GitHub through the `gh` CLI behind one `HostingClient` seam. Declare a
floor of `gh >= 2.0`, and **probe capabilities at preflight** (FR-004) rather than trusting
the version string. Never invoke `gh pr checks` for machine-readable output.

**Rationale — measured against the installed binary, `gh 2.4.0` (2022-03-23)**:

| Need | Available in 2.4.0? | Command used |
|---|---|---|
| Open a PR | yes | `gh pr create -B <base> -H <head> -t … -F <body-file>` |
| Adopt an existing PR (FR-009) | yes | `gh pr view <branch> --json number,state,url,baseRefName,headRefName` |
| Read checks **as data** | **NO `--json`, NO `--watch`** | `gh pr view --json statusCheckRollup` |
| Detect unmergeable (FR-018) | yes | `gh pr view --json mergeable,mergeStateStatus` |
| Merge (FR-013) | yes | `gh pr merge --squash\|--merge\|--rebase [--delete-branch]` |
| Default branch (FR-002) | yes | `gh repo view --json defaultBranchRef` |
| Watch a release run (FR-044) | yes | `gh run list --json …`, `gh run view`, `gh run watch` |
| Run a release action (FR-045) | yes | `gh workflow run`, `gh release create` |
| Escape hatch | yes | `gh api` (REST + GraphQL) |

The load-bearing finding: **`gh pr checks` in 2.4.0 accepts only `-w/--web`** — no `--json`,
no `--watch`. Those flags arrived in much later releases. A plan built on
`gh pr checks --json --watch` would fail on the very machine this feature is being written
on. Checks are therefore read by polling `gh pr view --json statusCheckRollup`, which *is*
present (confirmed in the field list `gh pr view --json` prints).

`gh auth status` confirms an authenticated host, which is the FR-004 credential precondition
— but authentication is not authorization, so preflight additionally probes the specific
capabilities the run intends to use and reports which precondition failed.

**Alternatives considered**:
- *GitHub REST/GraphQL directly over HTTPS.* Rejected: would require this feature to obtain
  and hold a token, which the spec puts explicitly out of scope (Assumptions) and which the
  operator's standing credential rule forbids handling directly. `gh` already holds the
  credential; we never see it.
- *`git` plumbing plus screen-scraping.* Rejected outright.
- *Pin to a modern `gh` and require an upgrade.* Rejected: "repo agnostic" implies
  machine-agnostic too. Probe, degrade honestly, and say what is missing.

---

## R4 — Integration-branch detection (FR-002, FR-003)

**Decision**: Resolve the integration branch by a fixed precedence, stopping at the first
source that answers unambiguously, and recording which source answered:

1. Saved ship configuration for this repository (FR-037/FR-041) — a recorded human answer.
2. `git symbolic-ref refs/remotes/<remote>/HEAD` — the local mirror of the remote's default.
3. `gh repo view --json defaultBranchRef` — the hosting service's own record.
4. `git remote show <remote>` → `HEAD branch:` — a network re-derivation when (2) is missing.

If none answers, or two disagree, present the candidates and ask once, then record the
choice (FR-003). **Never** fall back to a hardcoded `main`/`master`.

**Rationale**: This repository's own state proves the hazard is real — `git rev-parse
--is-inside-work-tree` here returns *fatal: not a git repository*, and `.spec-context.json`
nonetheless records `"branch": "main"`. A value that looks right and was never observed is
exactly the error SC-002 and FR-028 exist to prevent. Sources 2 and 3 are systems of record;
a name is not.

**Alternatives considered**:
- *Try `main`, then `master`.* Rejected by FR-002 explicitly.
- *Always ask.* Rejected: SC-002 wants ≥95% detected on the first attempt without asking.

---

## R5 — Checks terminality, and the two shapes of "undetermined" (FR-011, FR-012, FR-028)

**Decision**: Poll `gh pr view --json statusCheckRollup,mergeable,mergeStateStatus` on a
bounded schedule (default 10 s, backing off to 30 s, wall-clock cap default 30 min,
configurable). Reduce the rollup to one of four outcomes:

- **passed** — every *required* check reports success/neutral/skipped.
- **failed** — at least one required check reports failure/timed-out/cancelled/action-required.
- **pending** — at least one required check is queued or in progress, and the cap has not
  been reached.
- **undetermined** — the cap was reached with checks still pending, *or* the repository
  reports no checks at all, *or* the rollup could not be read. Recorded with a reason
  (FR-028) and never merged on (FR-012).

Two distinct undetermined sources need separate handling and separate reasons:

- `statusCheckRollup: []` — the repository genuinely has **no checks configured** (an
  edge case the spec names). This is not "green". It is `undetermined:no-checks-configured`,
  and it becomes an explicit confirmation prompt, not a silent pass.
- `mergeable: "UNKNOWN"` — GitHub computes mergeability **lazily**, so the first query after
  a push very often returns `UNKNOWN` while the background job runs. Treating that as
  "conflicting" would fire the FR-018 conflict repair against a branch that merges fine.
  It is re-polled a bounded number of times before being recorded as
  `undetermined:mergeability-not-computed`.

**Rationale**: `mergeable` and `mergeStateStatus` are both in the 2.4.0 field list, and their
`UNKNOWN` state is a documented consequence of GitHub's lazy computation, not an error. The
spec's own edge-case list names "no automated checks configured at all" and "checks never
report" as separate cases; collapsing either into pass or fail would violate FR-012.

**Alternatives considered**:
- *Treat "no required checks" as green.* Rejected: FR-012 and SC-012.
- *Wait indefinitely.* Rejected by FR-011.
- *Count optional check failures as failure.* Rejected: the spec names "a mix of required and
  optional failures" as an edge case; only required checks gate the merge, and optional
  failures are reported alongside.

---

## R6 — Release-mode detection (FR-039, FR-043, FR-044, FR-045)

**Decision**: Detect at preflight by inspecting the repository's own declarations, in order,
and record the verdict with the evidence that produced it:

- **observed** — a workflow in `.github/workflows/` triggers on `push` to the integration
  branch, or on `release`/`deployment`. After the merge, watch the run whose `headBranch` is
  the integration branch and whose head SHA is the merge commit, via `gh run list --json` +
  `gh run watch`, until terminal or the cap. No confirmation within the cap ⇒
  `undetermined:release-not-confirmed` (FR-044) — never "released".
- **executed** — the repository declares an explicit release action in its ship
  configuration (a `gh workflow run <file>`, a `gh release create`, or a named script). The
  run performs it after the merge and after a fresh confirmation (FR-013), and reports a
  failed release *as a failed release with the integration branch ahead of production*
  (FR-045).
- **none-determinable** — ask once, record the answer (FR-043).

The correlation key in both modes is the **merge commit SHA**, returned by
`gh pr view --json mergeCommit`. Time-proximity correlation is not used: a release queued
behind other releases (a named edge case) would attach the wrong run to the merge.

**Rationale**: `gh run list --json headBranch,headSha,status,conclusion,databaseId` and
`gh run watch` are both present in 2.4.0. Detection reads the repository's declared
triggers, which are a system of record; it does not guess from repository shape.

**Alternatives considered**:
- *Assume every repository is observed.* Rejected: FR-045 requires executed mode, and the
  monorepo/no-release-path edge cases exist.
- *Infer the release from a successful merge.* Rejected — this is precisely FR-015 and
  SC-012, the single hardest line in the spec.
- *Correlate by timestamp.* Rejected as above.

---

## R7 — File layout: configuration is shared, run state is machine-local

**Decision**:

| Path | Contents | Git posture |
|---|---|---|
| `.specify/extensions/ship/config.json` | Ship Configuration (FR-037…FR-041) — branches, PR composition mode, release mode + action, wait caps, repair budget | **committed** — "settings travel with the repository" (FR-041) |
| `.specify/extensions/ship/state.json` | Repository Profile + Ship Run history + Stage Outcomes (FR-026…FR-029) | **gitignored** |
| `.specify/extensions/ship/ship.lock` | Concurrency lock (FR-022) | **gitignored** |

Add the two runtime paths to `.specify/.gitignore`, which already carries a precedent block
for exactly this distinction (`feature.json` — "per-checkout state rather than something to
share" — and `extensions/*/local-config.yml`).

**Rationale**: FR-041 requires configuration to travel with the repository, so it must be
committed. Run state must *not* be: a `state.json` rewritten on every ship would conflict on
every merge into the integration branch — a tool that makes shipping harder the more it is
used. The Companion's own split is the precedent, and the ignore file even explains the
reasoning in a comment.

**Alternatives considered**:
- *One combined file.* Rejected: the two halves have opposite sharing requirements.
- *State under `.git/`.* Rejected: survives no clone, and `.git/` is not ours to write.
- *State in the user's home directory keyed by repo path.* Rejected: breaks when the
  checkout moves, and the Ship view would have to reproduce the keying.

---

## R8 — Resumability and concurrency (FR-021, FR-022)

**Decision**: Every stage transition is written to `state.json` **before** the side effect
that stage performs, and confirmed after (a write-ahead journal). A re-run reads the furthest
recorded stage for the branch and **re-verifies it against the world** — a recorded
`pr_opened` is re-checked with `gh pr view` before being trusted — then continues from there
(FR-021).

Concurrency uses a lock file holding `{pid, hostname, branch, started_at}`. A lock whose PID
is not alive on this host is **stale**: it is reported and reclaimed. A live lock causes a
refusal, never a queue (the spec's "refused, not serialized" exclusion).

**Rationale**: Recorded state can be wrong — the developer may have merged the PR in the web
UI between runs. Trusting the journal alone would let a run skip a stage that never really
happened; re-verifying makes SC-008 ("no duplicate PR, no duplicate release, 100% of
resumptions") achievable. Writing before the side effect means a crash mid-stage leaves an
`in_progress` marker, which is recoverable; writing after would lose the stage entirely.

**Alternatives considered**:
- *Trust the journal without re-verification.* Rejected as above.
- *Re-derive everything from GitHub each run, no journal.* Rejected: FR-026 needs run
  history and timings for the Ship view, and the changelog cannot be reconstructed from
  GitHub alone.
- *An OS advisory lock (`flock`).* Rejected: does not carry the diagnostic payload FR-022
  needs to report *what* is holding the lock, and behaves poorly on network filesystems.

---

## R9 — Bounded repair and the AI seam (FR-019, FR-020)

**Decision**: Repair budget defaults to **2** attempts, configurable. Two repair classes with
different authority:

- **Conflict repair (mechanical, unattended)** — bring the branch up to date with the target
  (`git fetch` + merge or rebase per configuration), resolve, push, re-enter checks. This is
  the class SC-005 measures at ≥70% autonomous resolution. Conflicts requiring a semantic
  choice are handed back, not guessed.
- **Check-failure repair (semantic, AI)** — the engine retrieves the failing check's own log
  (FR-017) via `gh run view --log-failed` / `gh api` and hands it to the command markdown.
  The model proposes a change; per FR-013 and Acceptance Scenario 2.4, a repair the run is
  not permitted to make unattended is **described and waited on**, not applied.

Every attempt is recorded as a Repair Attempt entity, and on exhaustion the run halts leaving
branch and PR intact, reporting each attempt and the residual failure (FR-020).

**Rationale**: The two classes differ in reversibility and in whether a wrong answer is
detectable, so they cannot share an authority level. The budget is small by the spec's own
stated assumption that unbounded automatic repair on a shared branch is unsafe.

---

## R10 — The Ship view: the Companion's tabs are compiled and closed

**Decision**: Deliver User Stories 4 and 5 as a **separate VS Code extension**
(`speckit-ship-companion`) that contributes a **`speckit.views.ship` view into the existing
`speckit` activity-bar container**, reading `.specify/extensions/ship/state.json` and writing
`config.json`. It is a distinct package with its own version, matching FR-042's separability
requirement and the spec's note that the tab must be packageable as `/speckit-community-ship`
without rework.

**Rationale — this is the finding that most changes the plan, so the evidence is stated in
full.** The installed Companion is `alfredoperez.speckit-companion@0.31.1`. Its
`package.json` `contributes.views` declares exactly four views under the `speckit` container
(`explorer`, `livingSpecs`, `steering`, `settings`) — no Ship, and no extension point. Its
spec-detail tabs are **compiled into `dist/webview/spec-viewer.js`**, where the strings
`"Overview"`, `"Spec"`, `"Plan"`, `"Tasks"` appear in bundled output; there is no manifest
key, no registry, and no configuration through which a third party contributes a fifth tab.
Those tabs are also scoped to a **single spec**, whereas ship state is **per repository** —
so even with an extension point, the spec-viewer tab bar would be the wrong home.

The spec's own wording accommodates this: User Story 4 says "a Ship tab **alongside the
existing SpecKit Companion views**", and the Companion's existing views are precisely
sidebar views in the `speckit` container. A sidebar view is a faithful reading, not a
downgrade.

**UNVERIFIED — carries a spike task**: whether VS Code lets one extension contribute a view
into a `viewsContainer` **owned by a different extension**. VS Code's container registry is
keyed by bare id, which suggests it works, but a scan of all extensions installed on this
machine found **no precedent** — every `contributes.views` key maps to a container its own
publisher declared, except contributions into the *built-in* `explorer`. This is unproven
here and must not be assumed. **Fallback**, requiring no redesign of anything else: declare
our own activity-bar container. The state-file contract, the view code, and every panel are
identical either way; only the manifest's container id changes.

**Alternatives considered**:
- *Fork the Companion extension.* Rejected: unmaintainable against upstream, and it would
  make our pipeline depend on shipping a competing build of somebody else's extension.
- *Upstream a PR adding a Ship tab.* Worth doing eventually; rejected as *this feature's*
  delivery path because the timeline is outside our control and FR-042 wants separability
  regardless.
- *No editor surface — a `/speckit-ship-status` text command only.* Rejected: it does not
  satisfy FR-030…FR-036 or SC-010. It is, however, being built **anyway** as the pipeline's
  own status command, and it is what makes the pipeline fully usable with no view installed
  (FR-042's first half).

---

## R11 — Live updates without a restart (FR-035)

**Decision**: The view registers a `vscode.FileSystemWatcher` on
`**/.specify/extensions/ship/state.json` and re-renders on change, debounced ~250 ms. The
engine's atomic write (temp file + `os.replace`) is what makes this safe — a watcher never
observes a partial file. Freshness (FR-034) is computed from the `captured_at` stamp each
value carries, against a configurable window (default 15 min), and the refresh affordance
re-runs the read-only probe.

**Rationale**: `os.replace` is atomic within a filesystem and is already the Companion's
documented approach for `.spec-context.json` ("writes atomically (temp file + os.replace)"),
so the pattern is proven in this exact context. Polling was rejected as strictly worse.

---

## R12 — Testing

**Decision**: `unittest` (stdlib) for the Python engine. Three layers:

1. **Unit** — stage state machine, rollup reduction, branch-detection precedence, staleness,
   lock liveness. Pure functions over fixtures.
2. **Integration against real git** — build throwaway repositories in a temp directory with
   `git init` plus a `git init --bare` local remote, and drive preflight/commit/publish/
   cleanup for real. This covers the FR-005 refusals (integration branch, detached HEAD,
   mid-rebase, mid-merge) and FR-025 (unmerged commits) with no network.
3. **Hosting client contract** — one `HostingClient` interface with two implementations: the
   real `gh` one, and a fake replaying **recorded `gh --json` payloads** captured from a real
   repository. Contract tests run both against the same expectations.

These are test doubles in test code, which is explicitly outside the operator's "no mocks in
production" rule; no fake ever backs a production path.

**Rationale**: `git version 2.34.1` is present, so layer 2 needs nothing but a temp
directory. Layer 3 keeps the CI suite hermetic while the recorded payloads keep it honest
about the real `gh` field shapes this machine's version emits.

---

## R13 — Constitution status

**Finding**: `.specify/memory/constitution.md` is the **unfilled template** — every principle
is still a `[PRINCIPLE_N_NAME]` placeholder and the version reads
`[CONSTITUTION_VERSION]`. There are no ratified principles to gate against.

**Decision**: Record the Constitution Check as *not applicable — no ratified constitution*,
and do not invent principles to satisfy the gate. Running `/speckit-constitution` before
implementation is recommended and is noted in the plan; nothing in this feature is blocked
on it.

**Rationale**: A gate that passes against placeholder text is a fabricated check. The honest
result is "no constitution exists", stated plainly.

---

## Resolved unknowns

Every Technical Context field is resolved; no `NEEDS CLARIFICATION` markers remain.

| Unknown | Resolution | Source |
|---|---|---|
| Language / runtime | Python 3.10 stdlib; TypeScript for the view only | R2 |
| Hosting client + floor | `gh` CLI ≥ 2.0 + preflight capability probe | R3 (measured on 2.4.0) |
| Checks readable as data | `gh pr view --json statusCheckRollup` — **not** `gh pr checks` | R3, R5 |
| Integration-branch source | 4-step precedence, recorded provenance | R4 |
| Release-mode detection | Workflow triggers + merge-SHA correlation | R6 |
| State/config location | `.specify/extensions/ship/{config,state}.json` | R7 |
| Ship view host | Separate VS Code extension; container-merge **UNVERIFIED**, fallback stated | R10 |
| Test approach | `unittest` + temp git repos + recorded `gh` payloads | R12 |
| Constitution gates | None ratified | R13 |

## Open spikes carried into implementation

- **S1** — Confirm a view contributed into another extension's `viewsContainer` renders
  (R10). Timebox: one throwaway extension, half a day. Fallback is already designed.
- **S2** — Capture real `gh --json` payloads (`statusCheckRollup`, `mergeable`,
  `mergeStateStatus`, `mergeCommit`, `run list`) from a live repository to seed the R12
  fixtures, including at least one `mergeable: "UNKNOWN"` and one empty rollup.
