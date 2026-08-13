# Ship View — Living Spec

> [DRAFT] Surface-first draft from existing code — every requirement is observed from the code surface unless tagged otherwise. Review before trusting.

## Purpose

The view answers "what is committed, what is open, what is deployed, and how
fresh is that answer" without leaving the editor, and lets a developer set how
this repository ships. Its entire value rests on one property: that nothing it
displays was invented. A reporting surface that fills a gap with a plausible
default is worse than no surface at all, because the reader has no way to tell
the filled gap from an observation — and will act on it.

## Requirements

### Every displayed value traces to recorded state or is labelled undetermined

A value the pipeline recorded SHALL be displayed with the moment it was
captured. A value the pipeline could not establish MUST be displayed as
undetermined, with the recorded reason. The view MUST NOT substitute a
placeholder, a sample, a default, or a dash for either.

#### Scenario: a value was never established
- **WHEN** a panel renders a value the pipeline recorded as undetermined
- **THEN** it renders as undetermined together with the reason
- **AND** no stand-in value appears in its place

#### Scenario: recorded state contains a forbidden pairing
- **WHEN** a value claims to be undetermined while carrying a value
- **THEN** the view refuses to render the carried value

#### Scenario: a reader wants to know how current something is
- **WHEN** any value is displayed
- **THEN** its capture time is displayed with it

### Absence, zero, and undetermined are three different displays

Nothing recorded, a real zero, and a value the pipeline tried and failed to
establish are three distinct facts, and the view MUST render them distinctly. A
zero collapsed into "empty" hides a real answer; an absence rendered as zero
invents one.

#### Scenario: a count is genuinely zero
- **WHEN** the recorded value is zero
- **THEN** it renders as zero, not as absent

#### Scenario: nothing was recorded for a field
- **WHEN** a field has no recorded value at all
- **THEN** it renders as not recorded, distinctly from undetermined

### A repository with no history shows an explicit empty state

When no run has been recorded, every panel MUST say so in words. An error, a
blank panel, or a zeroed-out panel are all wrong: the first is alarming, and the
other two are indistinguishable from a repository that shipped and produced
nothing.

#### Scenario: the view opens on a repository that has never shipped
- **WHEN** no recorded state exists
- **THEN** every panel states that no runs have been recorded
- **AND** no panel reports an error

#### Scenario: recorded state cannot be read
- **WHEN** the state file is unreadable or from an unknown version
- **THEN** the view reports the condition and shows what it can
- **AND** it does not present a partial picture as a whole one

### The view never writes pipeline state

The view MUST NOT write the pipeline's recorded state under any circumstance.
The record belongs to the process that performs the work; a view that could
write it could assert that things happened which never did, and the reader would
have no way to tell.

#### Scenario: the view is asked to change something
- **WHEN** the developer edits a setting
- **THEN** only configuration is written
- **AND** the recorded run history is untouched

### State older than the freshness window is marked, not hidden

Displayed state that has aged beyond a configurable window MUST be marked stale
and offered a refresh. Stale is not the same as wrong — a release confirmed
hours ago is still a release — so the view SHALL continue to show it rather than
suppress it.

#### Scenario: recorded state has aged
- **WHEN** a value is older than the freshness window
- **THEN** the panel marks it stale and shows how old it is
- **AND** the value itself remains visible

#### Scenario: a run advances while the view is open
- **WHEN** the pipeline records a new stage
- **THEN** the view reflects it without the editor being restarted

### A control whose backing capability is unavailable is disabled with its reason, and cannot be changed

A setting the repository cannot honor MUST render visibly disabled, MUST state
why, and MUST be refused if a change for it arrives anyway. Rendering it as
disabled is the visible half; refusing the change is the enforcement. A control
that only looks disabled while the change is accepted is the exact defect this
capability exists to prevent.

#### Scenario: the repository's permitted options were never established
- **WHEN** the pipeline has not recorded which options the repository allows
- **THEN** the control is disabled with that reason stated
- **AND** the options are treated as unknown rather than as all-permitted

#### Scenario: a change arrives for a disabled control
- **WHEN** a change is submitted for a control the view rendered as disabled
- **THEN** it is refused, and the refusal is reported

#### Scenario: the repository permits only some options
- **WHEN** the permitted options are known and limited
- **THEN** only those are offered, and the limitation is stated

### The view and the pipeline accept and reject exactly the same configurations

A configuration the view accepts MUST be one the pipeline accepts, and vice
versa. A view that saves something the pipeline will later refuse is worse than
one with no validation: the developer sees no complaint and discovers the
problem at the next ship, with no reason to suspect the settings.

#### Scenario: a setting is out of its permitted range
- **WHEN** the developer saves it from the view
- **THEN** it is rejected with the same verdict the pipeline would give

#### Scenario: a release mode requires an action the repository has not declared
- **WHEN** that mode is selected without the action
- **THEN** the save is rejected, because the tool never composes the action itself

### A rejected save changes nothing

When a save is rejected, the previously saved configuration MUST remain exactly
as it was, and no partial document may be left behind. A developer who sees an
error SHALL be able to trust that what is on disk is still what was working.

#### Scenario: an invalid value is saved
- **WHEN** validation rejects it
- **THEN** the specific problem is named
- **AND** the previous configuration is unchanged

#### Scenario: the write itself fails partway
- **WHEN** writing cannot complete
- **THEN** no partially written configuration is left in place

### Settings take effect on the next run and travel with the repository

Configuration written from the view SHALL be the same configuration the pipeline
reads, stored so that it is shared with everyone working in the repository.

#### Scenario: a setting is changed and a run follows
- **WHEN** the next run starts
- **THEN** it honors the saved value without further prompting

## Uncovered

_None — every file in the area was read, excluding test files._
