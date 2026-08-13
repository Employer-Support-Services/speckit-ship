---
name: speckit-ship
description: Ship the current branch — commit, publish, PR, checks, merge, release, cleanup, resuming an in-progress run rather than restarting it
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: teamteddy
  source: ship:commands/speckit.ship.md
---

# Ship

Carry the current branch from working copy to confirmed release and back to a
clean, updated integration branch.

The pipeline is deterministic and lives in Python. **This command owns exactly
two things** — drafting a pull-request description, and proposing a
check-failure repair. Everything else is the engine's, so it can be tested.
Do not reimplement stage logic here.

## Prerequisites

- Verify Python is available by running `python3 --version`.
- If `python3` is not available, warn the user and skip:
  `[ship] Warning: python3 not detected; skipped ship`.
  Do not fail the host command.

## Execution

Run the engine from the repository root:

```bash
python3 .specify/extensions/ship/scripts/ship.py
```

Arguments, passed through from the user's invocation:

| Argument | Meaning |
|---|---|
| *(none)* | Ship the current branch using detected + configured values |
| `--target <branch>` | Override the target branch for this run only |
| `--dry-run` | Preflight and print the intended actions; change nothing |
| `--yes` | Per-run unattended authorization for the merge and release gates. **This run only** |
| `--from <stage>` | Re-enter at a named stage; still re-verifies against the world |

The engine prints the repository profile and its intended actions **before** the
first state-changing action, then runs the stages in order.

### Exit codes — report these faithfully

| Code | Meaning | How to report it |
|---|---|---|
| `0` | Complete — merged, released (or release explicitly none), cleaned up | Say what shipped |
| `10` | Refused at preflight; nothing changed | Name the blocking condition |
| `20` | Halted on a classified failure; branch and PR intact | Name the stage and the cause |
| `30` | Halted on an **undetermined** outcome | Say we do not know, and why |
| `40` | Another run holds the lock | Report who holds it |

**Never collapse 20 and 30.** "It failed" and "we do not know" are different
answers. Reporting an unresolved checks outcome as a failure is merely wrong;
reporting it as a pass would merge on a pipeline that never reported.

## Your responsibilities

### 1. Drafting a pull-request description (FR-010)

When `pr.composition` is `drafted`, compose a title and body from the branch's
**actual changes** — read the diff and the commits; do not restate the branch
name. Then **present it for review before the pull request is created**. The
engine will not create a PR from a drafted description that was not shown.

Write what changed and why. No invented context, no claims about testing you did
not verify, no filler.

### 2. Proposing a repair for a failing check (FR-019, Acceptance 2.4)

When the run halts at `checks` with classification `check_failure`, the engine
prints the failing check's **own log excerpt**. That output is the seam: read it
and propose a specific fix.

**Describe the change. Do not apply it.** Not "shall I apply this?" followed by
applying it in the same turn — propose, then stop and wait. The engine records
your proposal with authority `proposed` and no commit SHA, which is the record
saying plainly that nothing landed.

A proposal should name the file and what to change, and say what you concluded
from the log. If the log was not retrievable, the engine says so — in that case
say you cannot propose a fix rather than guessing from the check's name.

**Never "repair" a check by weakening it.** Deleting the assertion, loosening the
threshold, marking the test skipped, or adding a retry to hide a flake makes the
pipeline green and the software worse. If the honest answer is that the code is
wrong, say the code is wrong.

The repair budget is small on purpose (default 2, `0` disables it). When it is
exhausted the run halts and lists every attempt. Do not re-run the command to
get more attempts.

### 3. Reporting the outcome

Relay what the engine reported. In particular:

- An **undetermined** stage is reported as undetermined, **with its reason**.
  Never smooth it into a pass or a failure, and never fill in a plausible value.
- A **failed release** means the merge landed and the integration branch is now
  ahead of production. Say that plainly — it is more urgent than a run that never
  released.
- If the run halted, name the stage and the specific cause from the engine's
  report, so the user does not need to open github.com.

## Confirmation gates

The merge and the release each require an explicit confirmation **on every run**.
There is no persistent always-yes setting, and you must not invent one, cache
one, or pass `--yes` on the user's behalf. If the user has not authorized the
run unattended, the gate is theirs to answer.

## Output

```text
Repository profile
  …the engine's profile block, verbatim…

Intended actions
  …the engine's intent block, verbatim…

<stage transitions as the engine reports them>

<the engine's final message>
```
