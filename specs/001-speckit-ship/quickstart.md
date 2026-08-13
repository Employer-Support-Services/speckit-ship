# Quickstart & Validation: SpecKit Ship

**Feature**: `001-speckit-ship` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

How to run this feature and prove it works. Entities are in [data-model.md](./data-model.md),
interfaces in [contracts/](./contracts/); neither is repeated here.

---

## Prerequisites

| Requirement | Check | Verified on this machine |
|---|---|---|
| Python 3.9+ | `python3 --version` | 3.10.12 |
| git 2.20+ | `git --version` | 2.34.1 |
| GitHub CLI 2.0+ | `gh --version` | 2.4.0 |
| Authenticated host | `gh auth status` | authenticated |
| SpecKit project | `test -d .specify` | present |
| VS Code 1.85+ | *(Ship view scenarios only)* | — |

`gh 2.4.0` is deliberately in the matrix: it lacks `gh pr checks --json/--watch`, so it is the
version that catches any regression back onto that command. Do not "upgrade to fix" a failure
here — a repo-agnostic tool has to survive an old `gh`, and the preflight probe is what makes
that survivable.

---

## Install

```bash
# From the repository you want to ship from
cp -r <this-repo>/.specify/extensions/ship .specify/extensions/ship
python3 .specify/extensions/ship/scripts/install.py     # registers commands, appends .gitignore entries
```

Confirm registration:

```bash
python3 .specify/extensions/ship/scripts/ship.py --version
ls .claude/skills/ | grep speckit-ship
```

Expected: `speckit-ship`, `speckit-ship-status`, `speckit-ship-config`, `speckit-ship-preflight`.

---

## Scenario 1 — Preflight against an unfamiliar repository (User Story 3)

The cheapest thing that proves the riskiest requirement. Run it first.

```bash
/speckit-ship-preflight
```

**Expect** a Repository Profile naming the integration branch **and the source that answered**
(`git-symbolic-ref`, `gh-repo-view`, `config`, or `user-answer`), the remote, hosting
reachability with the probed capability map, whether checks exist, and the detected release
mode with its evidence.

Then run the same command in each of these, from `/tmp`:

| Setup | Expected |
|---|---|
| `mkdir /tmp/notarepo && cd /tmp/notarepo` | Stops immediately, states no repository found, writes nothing (FR-001) |
| A repo whose default branch is `trunk` | Reports `trunk` — **never** `main` (FR-002) |
| A repo with no remote | Stops before committing, names the failed precondition (FR-004) |
| Checked out on the integration branch | Refuses, explains what it expected (FR-005) |
| Mid-rebase (`git rebase -i` then stop) | Refuses, names the condition (FR-005) |

**Pass** = correct branch in all cases, and every refusal leaves `git status` byte-identical.

**Watch for**: an answer that is right by luck. If the fixture's branch is `main`, you have not
tested detection. `trunk` is the case that matters.

---

## Scenario 2 — Ship a clean branch end to end (User Story 1, SC-001)

Needs a scratch GitHub repository you may merge into. Do not rehearse on a real one.

```bash
git checkout -b feat/quickstart-probe
echo "probe $(date -u +%s)" >> PROBE.md
/speckit-ship
```

Answer the two gates. **Expect**, in order: preflight summary → commit (showing what will be
committed *before* committing) → publish → PR opened → checks polled with progress → merge
gate → merge → release gate → release → cleanup → back on the integration branch, updated.

**Verify against the systems of record, not the run's own narration:**

```bash
gh pr view feat/quickstart-probe --json state,mergedAt,mergeCommit
git branch --list feat/quickstart-probe            # empty
git ls-remote --heads origin feat/quickstart-probe # empty
git rev-parse --abbrev-ref HEAD                    # the integration branch
git status -sb                                     # up to date with remote
```

**Pass** = PR `MERGED`, branch gone locally and remotely, up-to-date integration branch, and
`state.json` carrying a `release` stage whose `releaseRecord.evidence` is non-empty.

That last clause is the point of the scenario. A run that reports "released" with empty
evidence has violated FR-015 and SC-012, and it is the failure mode most likely to look fine.

---

## Scenario 3 — Nothing to ship (Acceptance 1.4)

```bash
git checkout -b feat/empty && /speckit-ship
```

**Expect**: stops before opening a PR, reports nothing to ship. **Pass** = exit `10`, no PR
created (`gh pr list --head feat/empty` empty).

---

## Scenario 4 — Resume an interrupted run (SC-008)

```bash
/speckit-ship            # Ctrl-C during the checks stage
/speckit-ship            # same branch
```

**Expect**: the second run adopts the existing PR, reports the stage it resumed from, and
creates no duplicate. **Pass** = `gh pr list --head <branch>` returns exactly one PR, and
`state.json` shows **one** `run_id` with both attempts' stages, not two runs.

Then the harder half — merge the PR in the web UI while the run is stopped, and re-run.
**Expect**: the run re-verifies rather than trusting its journal, finds the PR merged, and
proceeds to release/cleanup instead of re-merging (research.md R8).

---

## Scenario 5 — Red pipeline (User Story 2, SC-004, SC-005)

**5a — check failure**

```bash
git checkout -b feat/red && <break a test> && /speckit-ship
```

**Expect**: checks reported `failed`, classification `check_failure`, and **the failing
check's own log excerpt** shown (FR-017) — not merely "a check failed". Repair is attempted
within budget; a semantic repair is *described and awaited*, not applied (Acceptance 2.4).
On exhaustion: halt, branch and PR intact, every attempt listed (FR-020).

**Pass** = exit `20`, PR still open, and the failing stage and cause nameable from the report
alone without opening github.com (SC-004).

**5b — merge conflict**

Push a conflicting change to the integration branch, then ship a branch touching the same
lines. **Expect**: conflict detected **before** the merge is attempted, branch brought up to
date, conflict resolved, checks re-entered (FR-018).

**5c — checks never resolve**

Set `limits.checks_wait_seconds: 60` on a repository whose checks take longer.
**Expect**: exit `30`, checks recorded `undetermined` with a reason, **no merge**. Exit `30`
rather than `20` is the assertion — "we do not know" must not be reported as "it failed".

---

## Scenario 6 — Release truthfulness (SC-012)

The single most important check in this file.

**6a — observed mode**: ship into a repository that releases on merge. **Expect** the release
stage waits for the run correlated by **merge SHA** and reports only on its terminal outcome.

**6b — observed, no confirmation in window**: set `limits.release_wait_seconds: 60` against a
slow release. **Expect** `undetermined:release-not-confirmed` — **not** "released" (FR-044).

**6c — executed mode, failing action**: point `release.action` at a workflow that fails.
**Expect** the release reported as a **failed release with the integration branch ahead of
production** (FR-045) — not as a run that never attempted a release.

**6d — the audit.** Across every run above:

```bash
python3 - <<'PY'
import json
s = json.load(open('.specify/extensions/ship/state.json'))
bad = [ (r['run_id'], st)
        for r in s['runs'] for st in r['stages']
        if st['stage']=='release' and (st.get('release') or {}).get('outcome')=='released'
        and not (st.get('release') or {}).get('evidence') ]
print("VIOLATIONS:", bad or "none")
PY
```

**Pass** = `none`. Any row is a direct SC-012 failure.

---

## Scenario 7 — Concurrency refused (FR-022)

Start `/speckit-ship` in one terminal; start it again in another on the same branch.
**Expect**: the second refuses with exit `40` and reports the holder's pid, host, and start
time. Then kill the first and re-run: the stale lock is reported and reclaimed, not
silently ignored.

---

## Scenario 8 — Ship view (User Stories 4 and 5)

```bash
code --install-extension ./speckit-ship-companion-*.vsix
```

Open the SpecKit activity bar. **Expect** a Ship view listing local/published, behind-count,
open PRs, last checks outcome, last release, and a run changelog — **each with its capture
time** (FR-031).

| Case | Expected |
|---|---|
| Repository with history | Every panel populated, each showing capture time |
| Repository with no history | Explicit empty state, distinguishable from zero (FR-033) |
| A value recorded `determined: false` | Rendered as *undetermined* with the reason, never a placeholder (FR-032) |
| A run in progress | Panel advances as stages change, **no editor restart** (FR-035) |
| State older than `freshness_seconds` | Marked stale with a refresh affordance (FR-034) |
| A control whose capability is unavailable | Visibly disabled with the reason (FR-036) |

**Uninstall the extension and re-run `/speckit-ship`** — the pipeline must be fully usable
without it. **Point the view at a repository that has never shipped** — it must show the empty
state, not an error. Both halves of FR-042.

### SC-007 audit

Every value on every panel must trace to a `state.json` field or be labelled undetermined.
Grep the view source for hardcoded sample values, and confirm no panel renders a fallback
when its backing field is absent — an absent field renders as empty or undetermined, never as
a default.

---

## Automated suites

```bash
python3 -m unittest discover -s .specify/extensions/ship/tests -v   # engine
npm --prefix editor/ship-view test                                   # view
```

The engine suite needs **no network**: git-level scenarios run against a `git init --bare`
local remote, and hosting-level scenarios replay recorded `gh --json` payloads through
`RecordedClient` (contracts/hosting-client.md). Fixtures must include at least one
`mergeable: "UNKNOWN"` and one empty `statusCheckRollup` — the two undetermined shapes that
the naive implementation silently gets wrong.

---

## Definition of done

- [ ] Scenarios 1–7 pass, including every refusal case
- [ ] Scenario 6d audit returns `none`
- [ ] Scenario 8 passes in both directions of the FR-042 separability check
- [ ] `git status` byte-identical after every refusal path (SC-003)
- [ ] No `state.json` object with `determined: false` and a non-null value (SC-007)
- [ ] No `merge` or `release` stage lacking a `confirmation` for its own run (SC-006)
