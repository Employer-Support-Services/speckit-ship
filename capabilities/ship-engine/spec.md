# Ship Engine — Living Spec

> [DRAFT] Surface-first draft from existing code — every requirement is observed from the code surface unless tagged otherwise. Review before trusting.

## Purpose

The engine carries a branch from working copy to confirmed release without a
human remembering the sequence, and — more importantly — without ever reporting
something it did not observe. Without it, the sequence is done by hand, and the
failure that matters is not a missed step but a confident wrong answer: a merge
onto a pipeline that never reported, a release announced because a merge
succeeded, an integration branch guessed from a common name. Every rule below
exists to make one of those impossible rather than merely discouraged.

## Requirements

### An unobserved value is recorded as undetermined, never inferred

Every observed value SHALL be recorded together with how it was obtained and
when. A value the engine could not establish MUST be recorded as *undetermined
with a reason*, and MUST NOT be replaced by a default, a sample, or a
last-known value. Recording "we could not tell" alongside a plausible stand-in
is forbidden, because every consumer downstream reads the stand-in as fact.

#### Scenario: a value cannot be established
- **WHEN** the engine cannot determine a value it needs to record
- **THEN** it records the value as undetermined with a machine token and a human sentence
- **AND** the recorded value itself is null

#### Scenario: a writer tries to keep the old value "for context"
- **WHEN** a write pairs an undetermined marker with a non-null value
- **THEN** the write is refused rather than accepted and rendered later

#### Scenario: a genuine zero is recorded
- **WHEN** an observed value is zero or false
- **THEN** it is recorded as a determined value
- **AND** it remains distinguishable from a value that was never established

### Recorded state never blocks a run

Recorded run history is a record of what happened, not a precondition for
anything happening. A missing, unreadable, or version-incompatible state file
MUST be reported and worked around, and MUST NOT abort a ship run. A file the
engine cannot parse SHALL be preserved rather than overwritten.

#### Scenario: the state file is corrupt
- **WHEN** recorded state cannot be parsed
- **THEN** the run continues
- **AND** the unreadable file is moved aside intact rather than discarded

#### Scenario: the state file came from a newer version
- **WHEN** recorded state declares a schema the engine does not understand
- **THEN** the engine reads it without writing to it
- **AND** the run proceeds rather than refusing

### Settings travel with the repository; run history does not

Configuration SHALL be stored so that it is shared with everyone who clones the
repository. Run state SHALL be stored so that it is not — it is rewritten on
every run and would otherwise conflict on every merge into the integration
branch, making the tool harder to use the more it is used.

#### Scenario: a developer records a repository-wide choice
- **WHEN** a setting is saved
- **THEN** a colleague cloning the repository inherits it without being asked again

#### Scenario: two developers ship from the same repository
- **WHEN** each has their own run history
- **THEN** neither history is offered for merge into the shared branch

### Detection names the source that answered, and never guesses a branch name

The integration branch MUST be established from the repository's own systems of
record, and the recorded value SHALL name which source answered. The engine MUST
NOT fall back to a conventional branch name. When sources disagree or none
answers, the engine SHALL present what it found and ask once, rather than
choosing.

#### Scenario: the repository's default branch is unconventionally named
- **WHEN** detection runs against a repository whose integration branch is not conventionally named
- **THEN** the actual branch is reported
- **AND** the record names the source that supplied it

#### Scenario: two systems of record disagree
- **WHEN** two sources report different branches
- **THEN** neither is chosen
- **AND** both are offered to the developer as candidates

#### Scenario: nothing can answer and nobody is at the terminal
- **WHEN** detection fails and the session is not interactive
- **THEN** the run refuses rather than selecting a candidate

### A recorded profile is a cache of observations, never an authority

What the engine previously established about a repository SHALL be re-verified
on every run. A recorded profile describes what was true when it was written;
only the repository is authoritative about what is true now.

#### Scenario: the integration branch was renamed since the last run
- **WHEN** a run starts with a previously recorded profile
- **THEN** detection runs again against the repository
- **AND** the live answer supersedes the recorded one

### A one-time answer is recorded so it is asked only once

When the developer resolves something the engine could not determine, the answer
MUST be persisted where subsequent runs will find it before asking again.
Re-asking every run trains a developer to answer without reading.

#### Scenario: the developer chooses among candidates
- **WHEN** the developer selects an integration branch
- **THEN** the choice is saved as configuration and marked as a human's answer
- **AND** the next run resolves it without prompting

#### Scenario: the answer cannot be saved
- **WHEN** persisting the answer fails
- **THEN** the run proceeds using the answer
- **AND** it states that the question will be asked again

### Irreversible outward actions require a confirmation scoped to the run

Merging and releasing MUST each be authorized on every run. There SHALL be no
persistent always-yes setting, and the record MUST provide nowhere for one to be
stored — an authorization that could outlive its run would make the gate
decorative.

#### Scenario: an unattended run is authorized for this run only
- **WHEN** the developer authorizes a run to proceed unattended
- **THEN** that authorization applies to that run and no other

#### Scenario: an authorization claims to be permanent
- **WHEN** a confirmation is recorded with a scope wider than one run
- **THEN** the write is refused

### A release is never inferred from a merge

A release SHALL be reported only when the release path itself confirms it, and
the record MUST carry the evidence that confirmed it. A merge succeeding is
evidence about a merge. A release that could not be confirmed within the
configured wait MUST be recorded as undetermined, never as released.

#### Scenario: the merge succeeds and the release is still running
- **WHEN** the configured wait elapses without a terminal release outcome
- **THEN** the release is recorded undetermined with the reason
- **AND** it is not reported as released

#### Scenario: a release record is written with nothing behind it
- **WHEN** a release outcome is recorded without evidence
- **THEN** the write is refused, for every outcome including success

#### Scenario: a release must be attributed to a merge
- **WHEN** the engine correlates a release to the merge that caused it
- **THEN** it correlates on the merge commit rather than on time proximity

### One run at a time, refused rather than queued

A second run against the same repository MUST be refused while another holds
the lock, and the refusal SHALL identify what is holding it. A run whose owning
process is no longer alive on this host is reclaimable; a lock from another host
is not, because its liveness cannot be observed from here.

#### Scenario: a run is already in progress
- **WHEN** a second run starts
- **THEN** it refuses immediately and reports the holder's identity and start time

#### Scenario: a previous run died without releasing its lock
- **WHEN** the holding process is not alive on this host
- **THEN** the lock is reclaimed and the reclamation is reported

### A resumed run continues rather than repeats

Re-issuing the command against a branch with an unfinished run MUST continue
that run rather than start a new one, and MUST NOT duplicate an outward-facing
action. Before trusting what it recorded, the engine SHALL re-verify against the
repository — the developer may have acted outside the tool between runs.

#### Scenario: an interrupted run is re-issued
- **WHEN** the command runs again on the same branch
- **THEN** it continues the same run and creates no second pull request

#### Scenario: the developer merged outside the tool
- **WHEN** the pull request was merged elsewhere between runs
- **THEN** the run adopts that merge and continues rather than merging again

#### Scenario: recorded work has disappeared
- **WHEN** a stage's recorded outcome is no longer true in the repository
- **THEN** the run re-enters that stage rather than skipping past it

### A failure is classified before any repair is attempted

A failed run MUST be classified, and the classification reported, before the
engine changes anything in response. The developer learns what broke before the
tool starts acting on it — not afterwards, and not only if the repair fails.

#### Scenario: a run fails
- **WHEN** a stage fails
- **THEN** the classification is reported first
- **AND** only then is a repair attempted

#### Scenario: the outcome could not be determined
- **WHEN** a stage ends undetermined
- **THEN** it receives no failure classification
- **AND** no repair is attempted, because nothing is known to be broken

### Repair is bounded, and a repair requiring judgment is proposed rather than applied

Automatic repair MUST be limited to a configured number of attempts, and MUST be
disableable outright. A repair the engine can make mechanically — where the
repository itself either resolves it or does not — MAY run unattended. A repair
requiring a choice between people's work MUST be described and awaited, never
applied. On exhaustion the run SHALL halt leaving the branch and pull request
intact, reporting every attempt.

#### Scenario: the branch is merely behind the target
- **WHEN** bringing the branch up to date resolves cleanly
- **THEN** the repair is applied and the checks stage is re-entered

#### Scenario: resolving requires choosing between two changes
- **WHEN** the repository cannot resolve the conflict alone
- **THEN** the working copy is restored to its prior state
- **AND** the conflict is handed back unresolved

#### Scenario: the repair budget is set to zero
- **WHEN** repair is disabled
- **THEN** no repair is attempted at all, not even one

### "It failed" and "we do not know" are reported as different answers

The engine MUST distinguish a classified failure from an unresolved outcome in
what it returns to its caller. Collapsing them re-creates the inference the rest
of this specification exists to prevent: an unresolved pipeline read as red is
merely wrong, but read as green it merges.

#### Scenario: checks never report
- **WHEN** the configured wait elapses with checks unresolved
- **THEN** the engine reports an undetermined outcome distinctly from a failure

#### Scenario: a caller scripts against the result
- **WHEN** a caller inspects the outcome
- **THEN** refusal, failure, undetermined, and lock contention are each distinguishable

### A refusal leaves the repository byte-identical

When the engine refuses to proceed, it MUST change nothing — no commit, no
branch, no recorded state, no lock, and no directories created on the way to
any of them. A read-only inspection of an unfamiliar repository SHALL be safe to
run.

#### Scenario: the working copy is in an unsafe state
- **WHEN** the engine refuses because of the working copy's state
- **THEN** the working copy, index, and stash are unchanged
- **AND** no state file or lock has been created

#### Scenario: a developer inspects a repository they do not intend to ship
- **WHEN** the profile is reported
- **THEN** nothing is written to that repository

### The repository's own rules are respected, never circumvented

Branch protection, required reviews, and required signatures belong to the
repository. The engine SHALL report when such a rule blocks a stage and MUST NOT
attempt to work around one.

#### Scenario: a protected branch refuses a push
- **WHEN** the repository's rules refuse an operation
- **THEN** the refusal is classified as a permissions matter and reported
- **AND** no alternative route is attempted

### The configured target branch is honored; the shipped branch is the one checked out

A developer SHALL be able to set which branch a repository ships *into*, and a
run MUST honor that saved value. The branch being shipped *from* is the one
currently checked out — the engine does not switch branches on the developer's
behalf, because doing so silently would change what work a run is about.

Selecting a source branch from configuration is **not** currently a guarantee
this capability provides. See the defect noted below.

#### Scenario: a target branch is configured
- **WHEN** a target branch is saved
- **THEN** the run opens its pull request against that branch

#### Scenario: a branch is checked out and shipped
- **WHEN** a run starts
- **THEN** it ships the branch currently checked out
- **AND** it does not switch away from it to ship something else

> **Known defect (2026-08-13).** A `source_branch` setting is accepted by both
> validators and rendered as an enabled control in the Ship view, but no run
> reads it — there are zero references to it outside the configuration module.
> A developer can therefore set it, see it saved, and have it silently ignored.
> By this project's own rule that a control must not appear operable when it is
> not, the control is a violation and should either be disabled with a stated
> reason or the behavior implemented. Recorded here rather than specified as a
> guarantee, because the spec describes what the code does.

## Uncovered

_None — every file in the area was read._
