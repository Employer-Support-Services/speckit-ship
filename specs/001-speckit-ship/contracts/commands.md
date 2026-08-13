# Contract: Command Surface

Commands are declared in `.specify/extensions/ship/extension.yml` under `provides.commands`,
each backed by a markdown prompt file. The `claude` integration's `invoke_separator: "-"`
(from `.specify/integration.json`) maps `speckit.ship.status` → `/speckit-ship-status`.

## Commands

### `speckit.ship` → `/speckit-ship`

Run the pipeline for the current branch. Resumes an in-progress run for that branch rather
than starting a new one (FR-021).

| Argument | Meaning |
|---|---|
| *(none)* | Ship the current branch using detected + configured values |
| `--target <branch>` | Override the target branch for this run only |
| `--dry-run` | Preflight and print the intended actions; change nothing (FR-006) |
| `--yes` | Per-run unattended authorization for the merge and release gates. **This run only** — there is no persistable equivalent (FR-013) |
| `--from <stage>` | Re-enter at a named stage; still re-verifies against the world (R8) |

**Exit contract**

| Code | Meaning |
|---|---|
| `0` | Run complete — merged, released (or release explicitly `none`), cleaned up |
| `10` | Refused at preflight — nothing was changed (FR-001, FR-004, FR-005) |
| `20` | Halted on a classified failure; branch and PR left intact (FR-020) |
| `30` | Halted on an undetermined outcome — checks or release never resolved (FR-012, FR-044) |
| `40` | Refused — another run holds the lock (FR-022) |

Distinct codes for `20` and `30` are load-bearing: "it failed" and "we do not know" are
different answers, and a caller that collapses them re-creates the exact inference this
feature exists to prevent.

### `speckit.ship.status` → `/speckit-ship-status`

**Read-only.** Renders the same state the Ship view renders, as text. This is what makes the
pipeline fully usable with no view installed (FR-042). Never writes `state.json`.

`--json` emits the state document verbatim for machine consumers.

### `speckit.ship.config` → `/speckit-ship-config`

Show, set, and validate `config.json` from the command line. Applies the same validation the
view applies (FR-040) — a rejected save names the problem and retains the previous
configuration.

### `speckit.ship.preflight` → `/speckit-ship-preflight`

Run detection alone and print the Repository Profile: is this a repository, which integration
branch (and which source answered), which remote, hosting reachability and probed
capabilities, whether checks exist, which release mode applies. Changes nothing. This is
User Story 3's independent test surface — "can I ship from here, and where would it go?"

## Hook registration

None. The pipeline does not register `before_*`/`after_*` hooks in `.specify/extensions.yml`;
shipping is not a spec-lifecycle step and must not fire as a side effect of `implement`.

## Prompt-file conventions

Each command markdown follows the Companion's established shape:

1. YAML frontmatter with a `description` matching `extension.yml` exactly.
2. A **Prerequisites** block verifying `python3 --version`, warning and skipping — never
   failing the host command — when absent.
3. An **Execution** block invoking the engine by explicit path.
4. An **Output** block showing the literal text block to print.

The markdown owns only the two AI-seam responsibilities (R9): drafting a PR description for
review before the PR is created (FR-010), and proposing a check-failure repair. Every other
stage is the engine's, so it can be tested.
