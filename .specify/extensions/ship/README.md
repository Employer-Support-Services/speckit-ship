# SpecKit Ship

One command carries a branch from working copy to confirmed release and back to
a clean, updated integration branch — commit, publish, pull request, checks,
merge, release, cleanup.

It is repo-agnostic: no assumption about your branch names, language, build
tooling, or release mechanism. Python 3 standard library only, so there is no
install step and no dependency to drift.

## What makes it different

Most of the design exists to prevent one failure: **reporting something that was
not observed.**

- A value the tool could not establish is recorded as *undetermined with a
  reason*, never as a default or a plausible-looking stand-in.
- The integration branch is detected from your repository's own systems of
  record, and the record names which one answered. There is no `main`/`master`
  fallback anywhere.
- A release is reported only when the release path itself confirms it. A merge
  succeeding is evidence about a merge.
- "It failed" and "we do not know" exit with different codes, because a caller
  that collapses them re-creates exactly the inference the rest of this prevents.

## Install

From the repository you want to ship from:

```bash
cp -r <this-repo>/.specify/extensions/ship .specify/extensions/ship
python3 .specify/extensions/ship/scripts/install.py
```

The installer registers the commands and appends the run-state entries to
`.specify/.gitignore`. It is idempotent — run it again after an update.

Confirm it landed:

```bash
python3 .specify/extensions/ship/scripts/ship.py --version
ls .claude/skills | grep speckit-ship
```

### Requirements

| | Floor | Notes |
|---|---|---|
| Python | 3.9+ | standard library only |
| git | 2.20+ | |
| GitHub CLI | 2.0+ | capabilities are **probed**, not assumed from the version |

`gh 2.4.0` is deliberately supported and kept in the test matrix. It lacks
`gh pr checks --json`, so checks are read through `gh pr view --json
statusCheckRollup` instead. Do not "upgrade to fix" a failure there — a
repo-agnostic tool has to survive an old `gh`.

## Commands

| Command | What it does |
|---|---|
| `/speckit-ship` | Run the pipeline for the current branch, resuming an unfinished run |
| `/speckit-ship-preflight` | Report the repository profile. **Changes nothing** |
| `/speckit-ship-status` | Render recorded state as text; `--json` for machines. Read-only |
| `/speckit-ship-config` | Show, set, and validate configuration |

Start with `/speckit-ship-preflight`. It answers "can I ship from here, and
where would it go?" without touching anything.

### Arguments to `/speckit-ship`

| Argument | Meaning |
|---|---|
| `--target <branch>` | Override the target branch for this run only |
| `--dry-run` | Preflight and print the intended actions; change nothing |
| `--yes` | Unattended authorization for the merge and release gates — **this run only** |
| `--from <stage>` | Re-enter at a named stage; still re-verifies against the world |

There is no persistent always-yes. The recorded confirmation is scoped to a
single run, and the schema gives a permanent one nowhere to live.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Complete — merged, released (or release explicitly `none`), cleaned up |
| `10` | Refused before anything changed |
| `20` | Halted on a failure that can be named; branch and PR intact |
| `30` | Halted on an **undetermined** outcome — checks or release never resolved |
| `40` | Refused — another run holds the lock |

**Do not collapse 20 and 30.** An unresolved pipeline read as red is merely
wrong; read as green, it merges.

## Configuration

`.specify/extensions/ship/config.json` is **committed** — settings travel with
the repository. Every key is optional; absent means the documented default.

| Key | Default | Notes |
|---|---|---|
| `target_branch` | `null` | `null` = detect. Never defaults to a branch name |
| `remote` | `"origin"` | |
| `pr.composition` | `"commits"` | `manual` · `commits` · `drafted` |
| `pr.merge_method` | `"squash"` | must be one the repository permits |
| `release.mode` | `null` | `observed` · `executed` · `none`; `null` = detect |
| `release.action` | `null` | **required** when mode is `executed` |
| `limits.checks_wait_seconds` | `1800` | 60 … 86400 |
| `limits.release_wait_seconds` | `1800` | 60 … 86400 |
| `limits.repair_budget` | `2` | 0 … 5. **`0` disables repair entirely** |
| `limits.freshness_seconds` | `900` | staleness window for the Ship view |
| `cleanup.delete_branch` | `true` | |

```bash
ship.py config set limits.repair_budget 0   # disable automatic repair
ship.py config set target_branch ""         # back to detecting it
```

A rejected save names the specific problem and leaves the previous file
byte-identical.

> **Known gap:** `source_branch` is accepted by configuration but not read by
> any run — every run ships the branch currently checked out. See
> `capabilities/ship-engine/spec.md`.

### Release modes

**observed** — your repository's own automation releases on merge, and the run
watches until it reports. Correlation is by **merge commit SHA**, never by
timing, so a release queued behind others cannot be mistaken for yours.

**executed** — the run performs a release action your repository declares. The
tool runs what you declare; it never composes one.

**none** — no release step.

Undetected? The run asks once and records the answer.

## The state file

`.specify/extensions/ship/state.json` is **gitignored** — it is rewritten on
every run and would conflict on every merge into the integration branch.

It is also the entire contract with the Ship view (`editor/ship-view/`), which
reads it and never writes it. That one-directional coupling is what makes the
two halves separable: the pipeline is fully usable with no view installed, and
the view degrades to an honest empty state with no pipeline history.

Every observed value in it carries how it was obtained and when:

```json
{ "determined": true,  "value": "trunk", "captured_at": "…", "source": "git-symbolic-ref" }
{ "determined": false, "value": null,    "captured_at": "…", "reason": "no-checks-configured: …" }
```

`determined: false` with a non-null value is invalid and refused on write.

## Repair

A red pipeline is classified before anything is repaired. Two classes, with
different authority:

- **mechanical** — bring the branch up to date with the target and let git
  resolve it. Runs unattended, because git either resolves it alone or does not.
  When it cannot, the working copy is restored and the conflict handed back.
- **proposed** — a change to your code to clear a failing check. **Described and
  awaited, never applied.**

Bounded by `limits.repair_budget`. On exhaustion the run halts with the branch
and pull request intact, listing every attempt.

## Tests

```bash
bash .specify/extensions/ship/tests/run.sh
```

Hermetic: no network, no `gh`, no remote. Integration tests build real git
repositories against a `git init --bare` local remote; hosting behavior replays
recorded `gh --json` payloads (see `tests/fixtures/gh/README.md`, which
distinguishes captured payloads from constructed ones).

## Living specs

The behavior above is specified in `capabilities/ship-engine/spec.md` and
`capabilities/ship-stages/spec.md` — contracts rather than a transcription of
the code.

## License

MIT.
