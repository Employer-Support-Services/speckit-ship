# Ship Stages — Living Spec

> [DRAFT] Surface-first draft from existing code — every requirement is observed from the code surface unless tagged otherwise. Review before trusting.

## Purpose

The stages are where the pipeline actually touches the world — committing,
publishing, opening a pull request, waiting on checks, merging, releasing, and
cleaning up. Each owns exactly one side effect so that the sequence can be
reasoned about, resumed, and refused at a known point. Without this separation,
"the run failed" would be the most precise statement available, and a crash
would leave no way to tell which outward actions had already happened.

## Requirements

### Stages run in a fixed order and are never reordered

The sequence SHALL be fixed: establish the profile, commit, publish, open the
pull request, wait for checks, merge, release, clean up. A stage MAY be skipped
when it has nothing to do, and a skip MUST record why. A stage MUST NOT run
before an earlier one has settled.

#### Scenario: there is nothing to commit
- **WHEN** the working tree is clean
- **THEN** the commit stage is skipped with a reason
- **AND** the run proceeds to publish

#### Scenario: an earlier stage has not settled
- **WHEN** a stage is entered while a prior stage is unfinished or failed
- **THEN** entry is refused rather than warned about

### A stage records that it is starting before it acts

Each stage MUST record that it is in progress *before* performing its side
effect, and its outcome after. A crash between the two leaves a recoverable
marker; recording only afterwards would lose the stage entirely, and the run
could not tell an action it had taken from one it had not.

#### Scenario: the process dies mid-stage
- **WHEN** a run is interrupted while a stage is acting
- **THEN** the recorded history shows that stage as in progress
- **AND** a later run can tell it was attempted

### What will be committed is shown before it is committed

The commit stage MUST present what it is about to include, before anything is
staged, so that work unrelated to the change being shipped is visible rather
than swept in. Files entering version control for the first time SHALL be called
out as such.

#### Scenario: unrelated work is present in the tree
- **WHEN** the developer is shown the pending changes
- **THEN** nothing has been staged yet
- **AND** declining leaves the working copy untouched

### An existing pull request is adopted rather than duplicated

Before opening a pull request, the stage MUST establish whether one already
exists for the branch and adopt it if so. "There is no pull request" and "the
query failed" are different answers, and only the first justifies creating one —
a transient failure read as absence produces the duplicate this rule exists to
prevent.

#### Scenario: a previous run already opened one
- **WHEN** the stage runs again for the same branch
- **THEN** the existing pull request is adopted

#### Scenario: the lookup itself fails
- **WHEN** the engine cannot establish whether a pull request exists
- **THEN** it stops rather than creating one

### A machine-drafted description is reviewed before the pull request exists

When the description is composed automatically, it MUST be presented for review
*before* the pull request is created. Review after creation is not review; the
outward action has already happened.

#### Scenario: a description is drafted
- **WHEN** the description is composed automatically
- **THEN** it is shown before creation
- **AND** declining creates no pull request

### The checks outcome reduces to four answers, and undetermined is not one of the other three

The checks stage MUST reduce whatever the repository reports to exactly one of:
passed, failed, still running, or undetermined. Undetermined outcomes SHALL
carry distinct reasons, because "we stopped waiting", "there was nothing to wait
for", and "we could not read the result" call for different responses.

#### Scenario: the repository reports no checks at all
- **WHEN** no checks are configured against the pull request
- **THEN** the outcome is undetermined, not passed
- **AND** the reason states that there is no green to wait for

#### Scenario: the wait limit is reached
- **WHEN** checks are still running when the configured wait elapses
- **THEN** waiting stops and the outcome is undetermined with that reason

#### Scenario: a check failed but its requiredness cannot be established
- **WHEN** it cannot be determined whether a failing check gates the merge
- **THEN** the outcome is undetermined rather than passed or failed

#### Scenario: an optional check fails alongside passing required ones
- **WHEN** only non-gating checks failed
- **THEN** the outcome is passed
- **AND** the failures are reported alongside it

### Waiting is bounded and reports progress

The checks stage MUST stop waiting at a configured limit rather than waiting
indefinitely, SHALL report progress while it waits, and SHOULD reduce its
polling rate over a long wait. A silent half-hour is indistinguishable from a
hang.

#### Scenario: checks take a long time
- **WHEN** the stage is waiting
- **THEN** it reports what it is waiting on and how much of the wait remains

### The merge stage never merges on an unresolved pipeline

The merge MUST NOT proceed unless the checks stage recorded success. An
undetermined checks outcome is not a pass. The merge SHALL additionally require
an authorization granted during this run.

#### Scenario: checks are undetermined
- **WHEN** the merge stage is entered
- **THEN** entry is refused, naming the unresolved outcome as the cause

#### Scenario: no authorization was granted
- **WHEN** the merge stage is entered without a confirmation for this run
- **THEN** entry is refused

### Mergeability that has not been computed is not a conflict

The hosting service computes mergeability lazily, so a first answer of "unknown"
is ordinary rather than a signal. The stage MUST re-ask a bounded number of
times and, if it still cannot tell, record the outcome as undetermined. Treating
"not computed" as "conflicting" would rewrite history on a branch that merges
cleanly.

#### Scenario: mergeability is not yet computed
- **WHEN** the first query returns no verdict
- **THEN** the stage re-asks before concluding anything

#### Scenario: mergeability never resolves
- **WHEN** the answer is still unavailable after re-asking
- **THEN** the outcome is undetermined, and no conflict repair is triggered

#### Scenario: the pull request is already merged
- **WHEN** the pull request has already been merged
- **THEN** that is adopted directly, without waiting on a mergeability answer that will never come

### The release stage acts only on a merge it can point to

The release MUST NOT begin until a merge has succeeded and the resulting merge
commit is known. Without that commit there is nothing to attribute a release to,
and attributing one by timing instead would let a release queued behind others
be reported as this one.

#### Scenario: the merge commit could not be read back
- **WHEN** a merge reports success but its commit cannot be established
- **THEN** the outcome is undetermined and the release does not proceed

#### Scenario: the repository releases on merge
- **WHEN** the repository's own automation performs the release
- **THEN** the stage watches the run that corresponds to the merge commit
- **AND** reports only on that run's terminal outcome

#### Scenario: several runs match the merge
- **WHEN** more than one candidate release run corresponds to the merge
- **THEN** the outcome is undetermined rather than one being chosen

### A release that ran and failed is reported as such

When a release is attempted and fails, the stage MUST report a *failed release*
— which means the merge landed and the integration branch is now ahead of what
is deployed. That is a different and more urgent fact than a run that never
attempted a release.

#### Scenario: the release action fails
- **WHEN** the release runs and does not succeed
- **THEN** the report states that the merge landed and production is behind it

### Cleanup never destroys unmerged work

A branch MUST NOT be deleted until its merge is confirmed, and MUST NOT be
deleted at all while it carries commits the target does not have. When deletion
is refused by the hosting service, that SHALL be reported rather than failing
the run — the work shipped; only the tidying did not.

#### Scenario: the branch still holds unmerged commits
- **WHEN** cleanup runs
- **THEN** deletion is refused
- **AND** the unmerged commits are listed rather than merely counted

#### Scenario: the remote refuses the deletion
- **WHEN** the hosting service will not delete the branch
- **THEN** the run still succeeds and the refusal is reported

#### Scenario: a successful run finishes
- **WHEN** cleanup completes
- **THEN** the working copy is on the integration branch, updated from the remote

### A stage reports its own outcome and interprets no other

Each stage SHALL report what it observed and leave the consequences to the
sequence. A stage MUST NOT infer another stage's result, and MUST NOT record an
outcome on another's behalf.

#### Scenario: a stage cannot complete
- **WHEN** a stage ends without a determinate result
- **THEN** it records its own outcome and reason
- **AND** it does not mark any later stage

## Uncovered

_None — every file in the area was read._
