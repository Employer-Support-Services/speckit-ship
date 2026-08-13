# Definition of Done — execution record

**Feature**: `001-speckit-ship` · **Executed**: 2026-08-13 · **Task**: T101

The checklist from [quickstart.md](./quickstart.md), run and recorded. Every row
states what was actually executed, not what is believed to work.

**Headline: this feature has never shipped anything.** No ship run has merged a
pull request or released. The Ship view has never been rendered in an editor.
Everything below that says PASS is a test, a script, or a real command run
against a real repository — none of it is a production ship.

---

## Summary

| | Count |
|---|---|
| Rows fully verified | 4 |
| Rows partially verified | 2 |
| Rows **not** verified | 3 |

## Suites, as executed

| Suite | Result | Command |
|---|---|---|
| Engine, network removed | **371 passed** | `unshare -rn bash .specify/extensions/ship/tests/run.sh` |
| View | **76 passed** | `npm --prefix editor/ship-view test` |
| Separability (FR-042) | **13 passed, 0 failed** | `bash …/tests/integration/test_separability.sh` |
| Typecheck (strict) | clean | `npm run typecheck` |
| Package | 7 files, 15.75 KB | `npx @vscode/vsce package` |

The engine suite is run with the network **removed**, not merely absent. That
check found a real defect on its first use: `--no-network` placed before a
subcommand was silently discarded by argparse, so preflight reached `gh`
anyway. It passed locally only because the developer was authenticated. Fixed;
the suite now passes identically with and without a network.

---

## The checklist

### ☑ `git status` byte-identical after every refusal path (SC-003)

**VERIFIED.** `tests/integration/test_refusal_no_side_effects.py`, 12 tests,
driving the **real CLI as a subprocess** across six refusal states. Each
captures HEAD, the porcelain status, the index tree, and the stash list before
and after, and requires byte-identity — plus asserts that no state file, no
lock, and no extension directories were created.

### ☑ No `merge` or `release` stage lacking a `confirmation` for its own run (SC-006)

**VERIFIED, structurally.** `tests/contract/test_confirmation_required.py`, 12
tests. The record writer refuses a terminal merge or release stage without a
confirmation, and refuses any scope other than `run` — so the violating record
cannot be constructed, rather than being audited for after the fact.

### ☑ No `state.json` object with `determined: false` and a non-null value (SC-007)

**VERIFIED, structurally.** `validate_determined` refuses that pairing on every
write path, and `validate_tree` walks whole documents. The view refuses to
render it if one ever appeared. Additionally, an audit of the view's own source
rejects `?? "main"`-shaped fallbacks and placeholder identifiers.

### ☑ Scenario 8's FR-042 separability check, both directions

**VERIFIED.** 13 checks. The pipeline runs preflight, status, `--json`, and a
full dry run in a repository containing no view code at all. The view's real
reader and all six panels render an honest empty state against a repository
that has never shipped. Every filesystem write in the view lives in
`configWriter.ts`; that module never references `state.json`; `stateReader.ts`
has no write path.

**This is not the whole of Scenario 8** — see below.

### ◐ Scenarios 1–7, including every refusal case

**PARTIAL.**

| Scenario | Status |
|---|---|
| 1 — preflight against an unfamiliar repository | **verified**, incl. a real run against a live repository with a non-guessed integration branch and release mode |
| 2 — ship a clean branch end to end | **NOT DONE** — needs a scratch GitHub repository; nothing has been merged or released |
| 3 — nothing to ship | verified by test |
| 4 — resume an interrupted run | verified by test, incl. external-merge adoption |
| 5a/5b/5c — red pipeline | verified by test |
| 6 — release truthfulness | logic verified by test; **never exercised against a live release** |
| 7 — concurrency refused | verified by test |

Scenario 2 is the one that matters most and is the one not done.

### ◐ Scenario 6d audit returns `none`

**NOT APPLICABLE — and reported as such rather than as a pass.**

```
$ python3 .specify/extensions/ship/scripts/audit_releases.py
No recorded ship state … Nothing to audit — this is not a pass, it is an empty set.
$ echo $?
2
```

There is no recorded run history because no run has ever shipped. The audit
exits `2` (nothing to audit) rather than `0` (clean), deliberately: an empty set
returning "none" would read as a passing audit.

The audit itself was verified against a forged state file carrying a release
claimed with empty evidence, no merge commit, and no confirmation — it caught
all three.

### ☐ The Ship view rendered in an editor

**NOT DONE.** The `code` CLI on this machine resolves to a Windows binary
through WSL and fails with `Exec format error`. The extension compiles, bundles,
packages, and passes 76 tests; **no human or machine has seen it draw**.

### ☐ Spike S1 — cross-extension `viewsContainer`

**UNPROVEN.** Same cause. The probe extension was written and never run. What
was measured: across all 11 installed extensions, zero contribute a view into a
container declared by another publisher — weak evidence, recorded as weak. The
fallback (our own container) was taken, which is correct under either answer.
See [`editor/spike-container/FINDING.md`](../../editor/spike-container/FINDING.md).

### ☐ `.vsix` installs into a clean VS Code profile

**NOT DONE.** The package builds cleanly and contains only the bundle, manifest,
icon, and readme. Installation is unverified for the same reason as above.
Treat the first install as the first real test.

---

## Known defect, not fixed

`source_branch` is accepted by both validators and rendered as an **enabled**
control in the Ship view, and **no run reads it** — every run ships the branch
currently checked out. A developer can set it, watch it save, and have it
silently ignored.

By this project's own rule that a control must not appear operable when it is
not, that control is a violation, and FR-037 is half-implemented. Recorded in
[`capabilities/ship-engine/spec.md`](../../capabilities/ship-engine/spec.md).
The fix is either to disable the control with a stated reason or to implement
the behavior.

---

## What would close the gaps

1. **A scratch GitHub repository you may merge into.** That closes Scenario 2,
   Scenario 6a–6c, and turns the 6d audit from an empty set into a real one. It
   is the single highest-value thing left.
2. **A machine where VS Code runs natively.** That closes Scenario 8's render
   half, the `.vsix` install, and settles Spike S1 in about five minutes.

Neither is a code change.
