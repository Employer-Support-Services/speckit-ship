"""The stage state machine, its guards, and the write-ahead journal.

Eight stages, in one order, always::

    preflight → commit → publish → pull_request → checks → merge → release → cleanup

A stage may be **skipped with a recorded reason**. No stage is ever reordered,
and no stage runs on an assumption about one before it.

The guards in ``assert_can_enter`` are the enforceable form of the spec's
hardest requirements. They raise rather than warn, because a warning in a
pipeline that runs unattended is a log line nobody reads:

* ``merge`` requires ``checks == succeeded``. An **undetermined checks outcome
  is not a pass** (FR-012) — that single substitution is how a tool ends up
  merging on a pipeline that never reported.
* ``release`` requires ``merge == succeeded`` *and* a non-null merge commit SHA
  (FR-014).
* ``cleanup`` requires ``merge == succeeded`` (FR-023).
* ``merge`` and ``release`` each require a confirmation scoped to **this run**
  (FR-013, SC-006).

Journaling (research.md R8): a stage is recorded ``in_progress`` *before* its
side effect and its outcome *after*. A crash mid-stage therefore leaves a
recoverable marker rather than no trace at all. Writing after the fact would
lose the stage entirely — which is the difference between a resumable run and a
run that silently repeats an outward-facing action.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scripts import state as state_mod
from scripts.state import STAGES, StateError

# Stages considered "done" for the purpose of advancing past them.
SETTLED = ("succeeded", "skipped")


class GuardError(Exception):
    """A forbidden transition was attempted. Never downgraded to a warning."""


class StageResult:
    """What a stage function returns to the engine."""

    def __init__(
        self,
        outcome: str,
        *,
        reason: Optional[str] = None,
        classification: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        checks: Optional[List[Dict[str, Any]]] = None,
        release: Optional[Dict[str, Any]] = None,
        confirmation: Optional[Dict[str, Any]] = None,
        message: str = "",
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.classification = classification
        self.detail = detail
        self.checks = checks
        self.release = release
        self.confirmation = confirmation
        self.message = message

    @property
    def settled(self) -> bool:
        return self.outcome in SETTLED

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StageResult {self.outcome}{f' {self.reason}' if self.reason else ''}>"


def stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError:
        raise GuardError(f"unknown stage {stage!r}")


# --------------------------------------------------------------------------
# T024 — transition guards
# --------------------------------------------------------------------------


def assert_can_enter(
    run: Dict[str, Any], stage: str, *, confirmation: Optional[Dict[str, Any]] = None
) -> None:
    """Raise ``GuardError`` unless ``stage`` may legally start now.

    Called immediately before the journal marker is written, so a forbidden
    transition leaves no trace of having been attempted.
    """
    index = stage_index(stage)

    # The stage-specific guards run FIRST, deliberately. The generic ordering
    # check below would also refuse each of these cases, but with a message that
    # merely says a predecessor is unsettled. SC-004 asks that the developer be
    # able to name the specific cause from the report alone, and "checks are
    # undetermined, which is not a pass" is a different diagnosis from "some
    # earlier stage has not finished".
    if stage == "merge":
        checks = state_mod.stage_outcome(run, "checks")
        if checks == "undetermined":
            record = state_mod.stage_record(run, "checks") or {}
            raise GuardError(
                "cannot enter 'merge': the checks outcome is undetermined "
                f"({record.get('reason')}). An undetermined checks outcome is "
                "not a pass — FR-012 forbids merging on one."
            )
        if checks != "succeeded":
            raise GuardError(
                f"cannot enter 'merge': checks recorded {checks!r}, not 'succeeded'."
            )
        _assert_confirmed("merge", run, confirmation)

    if stage == "release":
        merge_outcome = state_mod.stage_outcome(run, "merge")
        if merge_outcome != "succeeded":
            raise GuardError(
                f"cannot enter 'release': merge recorded {merge_outcome!r}, not "
                "'succeeded' (FR-014)."
            )
        if not run.get("merge_commit_sha"):
            raise GuardError(
                "cannot enter 'release': no merge_commit_sha is recorded. That "
                "SHA is the correlation key between the merge and the release "
                "(research R6); without it a release cannot be attributed to "
                "this merge, and attributing it by time proximity is exactly "
                "what FR-015 forbids."
            )
        _assert_confirmed("release", run, confirmation)

    if stage == "cleanup":
        merge_outcome = state_mod.stage_outcome(run, "merge")
        if merge_outcome != "succeeded":
            raise GuardError(
                f"cannot enter 'cleanup': merge recorded {merge_outcome!r}. A "
                "branch is deleted only after its merge is confirmed (FR-023)."
            )

    # Ordering: every earlier stage must have settled. This catches everything
    # the specific guards above do not.
    for earlier in STAGES[:index]:
        outcome = state_mod.stage_outcome(run, earlier)
        if outcome is None:
            raise GuardError(
                f"cannot enter {stage!r}: {earlier!r} has no recorded outcome. "
                "Stages run in order; none may be skipped implicitly."
            )
        if outcome not in SETTLED:
            raise GuardError(
                f"cannot enter {stage!r}: {earlier!r} is {outcome!r}, not "
                f"succeeded or skipped."
            )


def _assert_confirmed(
    stage: str, run: Dict[str, Any], confirmation: Optional[Dict[str, Any]]
) -> None:
    """FR-013: a confirmation for *this* run, or no entry.

    There is no persistable always-yes value to check against, by design — the
    schema gives one nowhere to live, so this cannot be satisfied by anything
    other than an answer given during this run.
    """
    if confirmation is None:
        raise GuardError(
            f"cannot enter {stage!r}: no confirmation was granted for this run. "
            "Merging and releasing are outward-facing and hard to reverse, so "
            "each requires an explicit confirmation on every run (FR-013). "
            "`--yes` grants it for this run only."
        )
    if confirmation.get("scope") != "run":
        raise GuardError(
            f"cannot enter {stage!r}: confirmation scope is "
            f"{confirmation.get('scope')!r}, not 'run'."
        )
    if not confirmation.get("granted_at"):
        raise GuardError(f"cannot enter {stage!r}: confirmation carries no granted_at.")


# --------------------------------------------------------------------------
# T025 — write-ahead journaling
# --------------------------------------------------------------------------


def begin_stage(repo_root: Path, run_id: str, stage: str) -> Dict[str, Any]:
    """Record ``in_progress`` *before* the stage performs its side effect."""
    marker = {
        "stage": stage,
        "outcome": "in_progress",
        "started_at": state_mod.now_iso(),
        "ended_at": None,
    }

    def mutate(document: Dict[str, Any]) -> None:
        run = state_mod.find_run(document, run_id)
        if run is None:
            raise StateError(f"begin_stage: no run {run_id!r} in recorded state")
        state_mod.append_stage(run, dict(marker))

    state_mod.update(repo_root, mutate)
    return marker


def end_stage(
    repo_root: Path, run_id: str, stage: str, result: StageResult
) -> Dict[str, Any]:
    """Record the outcome, superseding the in-progress marker."""
    record_holder: Dict[str, Any] = {}

    def mutate(document: Dict[str, Any]) -> None:
        run = state_mod.find_run(document, run_id)
        if run is None:
            raise StateError(f"end_stage: no run {run_id!r} in recorded state")

        existing = state_mod.stage_record(run, stage)
        started_at = existing.get("started_at") if existing else None

        record = state_mod.make_stage(
            stage=stage,
            outcome=result.outcome,
            reason=result.reason,
            classification=result.classification,
            detail=result.detail,
            checks=result.checks,
            release=result.release,
            confirmation=result.confirmation,
            started_at=started_at,
        )
        state_mod.append_stage(run, record)
        record_holder.update(record)

    state_mod.update(repo_root, mutate)
    return record_holder


# --------------------------------------------------------------------------
# T052 — resumption
# --------------------------------------------------------------------------


def next_stage(run: Dict[str, Any]) -> Optional[str]:
    """The first stage that is not ``succeeded`` or ``skipped``.

    Returns None when every stage has settled — the run is complete.
    """
    for stage in STAGES:
        if state_mod.stage_outcome(run, stage) not in SETTLED:
            return stage
    return None


def resume_point(
    run: Dict[str, Any], *, reverifier: Optional[Callable[[str, Dict[str, Any]], bool]] = None
) -> str:
    """Where to re-enter, after re-verifying the last settled stage. (FR-021)

    The re-verification is the load-bearing half. Recorded state can be wrong:
    the developer may have merged the PR in the web UI between runs, or closed
    it, or force-pushed. Trusting the journal alone lets a run skip a stage that
    never really happened — or repeat one that did, which for ``merge`` and
    ``release`` means a duplicate outward-facing action.

    ``reverifier(stage, run) -> bool`` answers "is this recorded outcome still
    true in the world?". A False rewinds to that stage. Absent a reverifier the
    journal is taken at face value, which is correct only for a dry run.
    """
    pending = next_stage(run)
    if pending is None:
        settled_stages = [s for s in STAGES if state_mod.stage_outcome(run, s) in SETTLED]
        pending = STAGES[-1] if not settled_stages else None

    if reverifier is None:
        return pending or STAGES[-1]

    boundary = stage_index(pending) if pending else len(STAGES)
    for stage in STAGES[:boundary]:
        outcome = state_mod.stage_outcome(run, stage)
        if outcome != "succeeded":
            continue
        if not reverifier(stage, run):
            return stage

    return pending or STAGES[-1]


def is_complete(run: Dict[str, Any]) -> bool:
    return next_stage(run) is None


# --------------------------------------------------------------------------
# T055 — the nothing-to-ship path
# --------------------------------------------------------------------------


class NothingToShip(Exception):
    """The branch is identical to the target. Stop before opening a PR (exit 10)."""


def assert_something_to_ship(
    *, ahead: Optional[int], branch: str, target: str, tree_dirty: bool
) -> None:
    """Acceptance 1.4.

    ``ahead is None`` means the comparison could not be made — which is *not*
    "nothing to ship". An unknown is escalated to the caller rather than
    resolved in either direction.
    """
    if tree_dirty:
        return
    if ahead is None:
        return
    if ahead == 0:
        raise NothingToShip(
            f"{branch!r} is identical to {target!r} — no commits ahead and no "
            "uncommitted changes. There is nothing to ship."
        )


# --------------------------------------------------------------------------
# Driving a run
# --------------------------------------------------------------------------


def run_stage(
    repo_root: Path,
    run_id: str,
    stage: str,
    fn: Callable[[], StageResult],
    *,
    confirmation: Optional[Dict[str, Any]] = None,
    run: Optional[Dict[str, Any]] = None,
) -> StageResult:
    """Guard, journal, execute, journal. The whole contract in one place.

    A stage function that raises is recorded as a failure rather than leaving an
    orphaned ``in_progress`` marker — an unexplained marker on the next run
    would be indistinguishable from a genuine crash mid-side-effect, and the two
    call for different recoveries.
    """
    if run is None:
        loaded = state_mod.load(repo_root)
        run = state_mod.find_run(loaded.document, run_id)
        if run is None:
            raise StateError(f"run_stage: no run {run_id!r} in recorded state")

    assert_can_enter(run, stage, confirmation=confirmation)
    begin_stage(repo_root, run_id, stage)

    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        end_stage(
            repo_root,
            run_id,
            stage,
            StageResult(
                "failed",
                classification="precondition",
                detail={"exception": f"{type(exc).__name__}: {exc}"},
                message=str(exc),
            ),
        )
        raise

    if confirmation is not None and result.confirmation is None:
        result.confirmation = confirmation

    end_stage(repo_root, run_id, stage, result)
    return result
