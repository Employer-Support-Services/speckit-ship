---
name: speckit-ship-status
description: Render recorded ship state as text; --json emits the state document verbatim. Read-only
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: teamteddy
  source: ship:commands/speckit.ship.status.md
---

# Ship Status

Report what the ship pipeline has actually recorded for this repository.

**Read-only.** This command never writes `state.json`. It is what makes the
pipeline fully usable with no editor view installed.

## Prerequisites

- Verify Python is available by running `python3 --version`.
- If `python3` is not available, warn the user and skip:
  `[ship] Warning: python3 not detected; skipped status`.
  Do not fail the host command.

## Execution

```bash
python3 .specify/extensions/ship/scripts/ship.py status
```

Options: `--json` emits the state document verbatim for machine consumers,
`--limit <n>` bounds how many runs are shown.

## Reading the output

- **No runs recorded** is an explicit empty state, not an error and not a zero.
  Report it as "no ship runs recorded for this repository".
- A stage shown as `undetermined` carries a reason. Report the reason. Never
  substitute a placeholder, a sample, or a default for a value the tool did not
  establish.
- A degraded state file (missing, unparseable, or written by a newer version) is
  reported and the command continues. That is by design — recorded state is a
  record of runs, not a precondition for them.

## Output

Print the engine's output verbatim.
