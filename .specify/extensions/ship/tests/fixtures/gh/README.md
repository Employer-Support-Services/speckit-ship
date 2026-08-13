# Recorded `gh --json` payloads (Spike S2, task T034)

These seed `RecordedClient`, which replays them through the same `HostingClient`
protocol `GhClient` implements. Contract tests run both against the same
expectations, which is what keeps this directory honest about the shapes a real
`gh` emits.

## Provenance — read this before adding a file

Every file is named for where it came from, and the two prefixes mean different
things:

| Prefix | Meaning |
|---|---|
| `captured-` | Real `gh` output from a live repository, with **one** edit: the owner/repo path inside URLs was rewritten to `acme/thing`. Field names, value shapes, null-vs-absent behavior, enum spellings, SHAs, run IDs and check names are untouched. |
| `synth-` | **Constructed** from the field structure of a captured payload, because no live example of that shape existed to capture. |

The redaction is named rather than silent because this repository is public and
the source repository is not: publishing the owner/repo path would advertise a
private repository's CI job structure for no testing benefit. Nothing the tests
assert on was changed — the reduction logic reads `statusCheckRollup`,
`mergeable`, `state` and `mergeCommit`, none of which the rewrite touches.

The distinction is not bookkeeping. A fixture that claims to be captured and is
not would make the contract tests assert against a shape GitHub may never
actually produce — and the whole point of recording real payloads is that the
field names and null-vs-absent behavior are exactly what a hand-written fake
gets wrong.

If you later capture a real example of one of the `synth-` shapes, replace the
file and rename it.

## Captured

Taken 2026-08-13 from a private GitHub repository (owner/repo redacted, see above) using
`gh version 2.4.0+dfsg1 (2022-03-23)` — the floor version in the test matrix.

| File | Command | Shape it pins |
|---|---|---|
| `captured-all-success.json` | `gh pr view 901 --json …` | 6 checks, all `SUCCESS`; `mergeable: MERGEABLE` |
| `captured-merged-unknown-mergeable.json` | `gh pr view 903 --json …` | **`mergeable: "UNKNOWN"` on a MERGED PR**, plus a real `mergeCommit.oid` |
| `captured-conflicting-null-rollup.json` | `gh pr view 898 --json …` | `mergeable: CONFLICTING` with **`statusCheckRollup: null`** |
| `captured-run-list.json` | `gh run list --limit 6 --json …` | A real run-list page |

### Two findings these captures produced

**1. `statusCheckRollup` is present-but-`null` on a conflicting PR.** Not absent,
not `[]`. A membership test (`"statusCheckRollup" in data`) reads that as
"present and empty" and reduces it to `no-checks-configured` — turning "GitHub
did not compute a rollup" into a claim about the repository. The client tests
the value, not the key. `captured-conflicting-null-rollup.json` is what makes
that a regression test rather than a comment.

**2. `gh run list --json` on 2.4.0 has no `workflowName` field.** It is `name`,
and asking for `workflowName` fails outright with `Unknown JSON field`. This is
the same class of finding as the `gh pr checks` one in research.md R3: a
plausible field name from a later release fails at run time, mid-pipeline, after
state has already changed. This version also has no `--branch` filter, so
`runs_for_sha` filters client-side.

**A correlation worth noting:** `captured-run-list.json`'s first entry has
`headSha` equal to `captured-merged-unknown-mergeable.json`'s `mergeCommit.oid`
(`f9d8724…`), and `conclusion: "failure"`. That is the observed-mode correlation
key working on real data — and a real example of a release that ran and failed,
which FR-045 requires be reported as *a failed release with the integration
branch ahead of production*, not as a run that never released.

## Synthesized

No live example existed for these when the fixtures were seeded. Each was built
from `captured-all-success.json`'s field structure, so the entry shape
(`__typename`, `status`/`conclusion` split, `detailsUrl`) matches what GitHub
really emits.

| File | Shape it pins | Why it must exist |
|---|---|---|
| `synth-required-failure.json` | A required check reporting `FAILURE` | The ordinary red path |
| `synth-mixed-required-optional.json` | Required checks green, optional ones failing | The spec names this edge case; only required checks gate (FR-011) |
| `synth-empty-rollup.json` | `statusCheckRollup: []` | The repository genuinely has no checks. **This is not green** — it reduces to `undetermined:no-checks-configured` (FR-012) |
| `synth-pending.json` | A check `IN_PROGRESS` | The wait path, before the cap |
| `synth-open-unknown-mergeable.json` | `mergeable: UNKNOWN` on an **open** PR | GitHub computes mergeability lazily; reading this as `CONFLICTING` fires conflict repair against branches that merge fine |
| `synth-cancelled.json` | A required check `CANCELLED` | Terminal, and not a success |

The two the task list calls out as non-optional — an empty rollup and an
`UNKNOWN` mergeable — are the shapes a naive implementation silently gets wrong,
which is why they are pinned in both a captured and a synthesized form where
possible.
