---
name: speckit-ship-preflight
description: Report the repository profile — integration branch and which source answered, remote, hosting capabilities, checks, release mode. Changes nothing
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: teamteddy
  source: ship:commands/speckit.ship.preflight.md
---

# Ship Preflight

Answer "can I ship from here, and where would it go?" — and change nothing while
answering it.

**Read-only.** This command writes no state file, creates no directories, and
touches no branch. A developer inspecting an unfamiliar repository should be able
to run it freely.

## Prerequisites

- Verify Python is available by running `python3 --version`.
- If `python3` is not available, warn the user and skip:
  `[ship] Warning: python3 not detected; skipped preflight`.
  Do not fail the host command.

## Execution

```bash
python3 .specify/extensions/ship/scripts/ship.py preflight
```

Options: `--target <branch>` to test a specific target, `--json` for the profile
as a document.

## Reading the output

Every value carries **how it was obtained** and **when**. That provenance is the
point of the command:

- `integration branch    trunk   [git-symbolic-ref @ …]` — observed, and from
  which system of record.
- `release mode          undetermined — none-determinable: …` — not established.
  This is a real answer. Do not fill it in, and do not guess `main`/`master` for
  a branch the tool reported as undetermined.

If the profile is blocked, the command exits `10` and names the blocking
condition. Report that condition; do not suggest working around it.

## Output

Print the engine's profile block verbatim. If it refused, print the refusal and
say plainly that nothing was changed.
