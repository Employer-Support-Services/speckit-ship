---
description: "Show, set, and validate ship configuration, applying the same validation the Ship view applies"
---

# Ship Config

Inspect and validate this repository's ship configuration.

`config.json` is **committed** — settings travel with the repository (FR-041),
unlike run state, which is gitignored because it would conflict on every merge.

## Prerequisites

- Verify Python is available by running `python3 --version`.
- If `python3` is not available, warn the user and skip:
  `[ship] Warning: python3 not detected; skipped config`.
  Do not fail the host command.

## Execution

```bash
python3 .specify/extensions/ship/scripts/ship.py config show
python3 .specify/extensions/ship/scripts/ship.py config set <KEY> <VALUE>
python3 .specify/extensions/ship/scripts/ship.py config validate
```

`show` prints the effective configuration — the file merged over the documented
defaults, so every key is visible whether or not it is set.

`set` takes a dotted key (`target_branch`, `limits.repair_budget`,
`pr.merge_method`, `cleanup.delete_branch`). An **empty value unsets** the key
rather than storing an empty string:

```bash
ship.py config set target_branch ""      # back to detecting it
ship.py config set limits.repair_budget 0  # disable automatic repair
```

The same settings can be edited from the Ship view, which applies identical
validation — the two are checked against each other in the test suite, because
two validators that disagree are worse than one.

## Validation

A rejected configuration names the **specific** problem and the previous file is
retained byte-identical. Relay the named problem; do not summarize it as
"invalid config".

Two rules worth knowing when advising:

- `target_branch` defaults to `null`, meaning *detect*. It never defaults to
  `main`. Setting it to a branch that does not resolve on the remote is rejected —
  but a branch that could not be **checked** (offline, no credential) is not.
- `release.mode: "executed"` requires `release.action`. This tool runs the
  release action a repository declares; it never composes one.

## Output

Print the engine's output verbatim.
