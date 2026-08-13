# SpecKit Ship Constitution

Three principles, ratified because this project's whole value rests on them and
none of them survives being merely intended. Each states what is forbidden, not
what is encouraged — a principle you cannot violate is not a principle, it is a
preference.

Each also names how it is enforced, and says plainly where it is **not**. A rule
that exists only in this file is a reminder a session can fail to apply at the
moment it matters, and calling that "enforcement" is the first thing these
principles forbid.

## Core Principles

### I. No simulated behavior on a user-facing surface (NON-NEGOTIABLE)

No user-facing surface may present behavior it does not have. Where a capability
cannot be wired, the affordance is **absent**, or **visibly disabled with a
truthful reason**. Never simulated.

**The test**: could a user reasonably believe real work happened — or that what
they are looking at is real data — when it did not and is not? If yes, it is a
violation, regardless of intent, labelling, or whether a design called for it.

Violations include a control whose backend does not exist, a progress indicator
not driven by real state, a toggle that appears to persist and does not, and
placeholder values rendered as live data.

**Not violations**: test doubles in test code; local and development
environments; a disabled control with a stated reason — which is the sanctioned
alternative, not a lesser violation; genuine loading and empty states driven by
real request state.

**A design cannot authorize a simulation.** A design specifies what a surface
*presents*, never fabricated behavior. An unbackable control in a design ships
disabled, and the gap is reported as a gap.

*Enforced by*: `tests/contract/test_no_fake_in_production.py` (an import walk
proving no production module can reach the recorded test double);
`test_separability.sh` Direction 3; the view's SC-007 source audit; and the
disabled-control refusal in `configPanel.applyChange`, which refuses the change
rather than only styling the control.

*Not enforced*: nothing prevents a new surface from being added without any of
the above. The mechanisms cover what exists today.

### II. An unobserved value is recorded as undetermined, never inferred (NON-NEGOTIABLE)

Every recorded value carries how it was obtained and when. A value the system
could not establish is recorded as **undetermined with a reason**, and is never
replaced by a default, a sample, a last-known value, or a conventional guess.

Absence, a genuine zero, and "we could not tell" are three different facts and
must remain three. Collapsing any pair destroys a real answer.

The corollary that does the work: **an unresolved outcome is not a failed one,
and is certainly not a successful one.** A caller that collapses "it failed" and
"we do not know" re-creates the inference this principle exists to prevent.

*Enforced by*: `validate_determined`, which refuses `determined: false` paired
with a non-null value on every write path; the release-record writer, which
refuses an outcome with empty evidence; distinct exit codes for failure and
undetermined outcomes; and the view's render contract, which offers no fallback
parameter to pass a substitute through.

*Not enforced*: a new field can be added outside the `Determined` wrapper. The
wrapper makes the right thing easy and the wrong thing loud; it does not make
the wrong thing impossible.

### III. Outward-facing irreversible actions are confirmed per run (NON-NEGOTIABLE)

An action that reaches outside this machine and is hard to undo — merging,
releasing, deleting a branch — requires an explicit authorization scoped to the
run performing it.

**There is no persistent always-yes**, and none may be added. An unattended
authorization applies to one run and expires with it. The record must give a
permanent authorization nowhere to live, so the feature cannot be
half-implemented into existence by a single well-meaning change.

Nothing may derive one action's authorization from another's. A confirmation to
merge is not a confirmation to release.

*Enforced by*: `confirmation.scope` being the constant `"run"` in the schema;
`make_stage` refusing a terminal merge or release stage without a confirmation,
and refusing any other scope; the engine's transition guards, which raise rather
than warn.

*Not enforced*: a future stage performing an outward action could be written
without a gate. The guards cover the stages that exist.

## Applying These Principles

**They bind surfaces and records, not process.** Nothing here mandates a
development methodology, a test framework, or a review workflow. They constrain
what may be shown to a person and what may be written down as having happened.

**Where a principle is inconvenient, the inconvenience is the point.** Each was
ratified because the convenient alternative — filling a gap with a plausible
default, shipping a control ahead of its backend, remembering an approval —
produces a system that is confidently wrong, which is worse than one that is
visibly incomplete.

**Reporting a gap is compliance, not failure.** Shipping a disabled control with
a truthful reason, recording an outcome as undetermined, or halting a run
because an authorization was not granted are all correct outcomes. A report that
says "we could not establish this" satisfies these principles fully.

## Development Workflow

Every feature's Constitution Check evaluates against these three. A design that
violates one is changed, not waived.

**Removing a principle is not symmetric with adding one.** These were ratified
after a feature was built against them, so each has evidence behind it. Removal
requires stating what changed such that the failure the principle prevents can
no longer occur.

## Governance

This constitution supersedes convenience and precedent within this project.

Amendments require: the proposed change, the failure it prevents or the evidence
it is no longer needed, and a version bump. Amendments are dated.

**Honest note on enforcement.** The mechanisms named under each principle are
real and run in CI. Everything beyond them — a new surface, a new field, a new
stage — is covered by nothing but attention. This file does not close that gap
and should not be read as closing it. The principles make the right action cheap
and the wrong action visible where they reach; where they do not reach, they are
a reminder like any other.

**Version**: 1.0.0 | **Ratified**: 2026-08-13 | **Last Amended**: 2026-08-13
