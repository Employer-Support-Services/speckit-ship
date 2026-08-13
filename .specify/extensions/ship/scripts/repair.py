"""Bounded repair of a red pipeline (FR-016, FR-018, FR-019, FR-020).

Two repair classes with **different authority**, because they differ in
reversibility and in whether a wrong answer is detectable:

``mechanical`` — bring the branch up to date with the target and let git resolve
    it. Runs unattended. The key property is that git either resolves it alone or
    it does not: there is no judgment call, so there is nothing to get wrong. If
    git reports conflicts, the branch is restored and the conflict is handed
    back.

``proposed`` — a change to the code itself, to clear a failing check. This is
    **described and awaited, never applied** (Acceptance 2.4). A semantic fix on
    a shared branch that turns out to be wrong is expensive and not obviously
    wrong at the time, which is exactly the situation where a human should
    decide.

Classification happens **before** any repair is attempted (FR-016, Acceptance
2.1) — the developer learns what broke before the tool starts changing things,
not after.

The budget is small (default 2) and `0` disables repair outright. On exhaustion
the run halts leaving the branch and pull request intact, reporting every attempt
and the failure that remains (FR-020).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scripts import gitops
from scripts.state import (
    FAILURE_CLASSIFICATIONS,
    make_repair_attempt,
    now_iso,
)

# Substrings that identify a permission or policy refusal rather than a defect
# in the branch. Kept separate from precondition because the remedy differs
# entirely: one needs someone with rights, the other needs the branch fixed.
PERMISSION_MARKERS = (
    "permission denied",
    "protected branch",
    "pre-receive hook declined",
    "access denied",
    "authentication failed",
    "not authorized",
    "review required",
    "at least 1 approving review",
    "required status check",
    "resource not accessible",
    "must be a member",
    "403",
)

CONFLICT_MARKERS = (
    "conflict",
    "not mergeable",
    "cannot merge",
    "merge_conflict",
    "dirty",
)


def classify(
    *,
    stage: str,
    outcome: str,
    message: str = "",
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Classify a failure into one of the four classes. (FR-016)

    Returns ``None`` for an **undetermined** outcome — deliberately. An
    undetermined outcome is not a failure with an unknown cause; it is a
    different answer entirely, and giving it a classification would let a caller
    treat "we do not know" as "we know it broke, somehow". That collapse is what
    exit codes 20 and 30 exist to keep apart.
    """
    if outcome == "undetermined":
        return None
    if outcome not in ("failed",):
        return None

    haystack = f"{message} {detail or ''}".lower()

    if any(marker in haystack for marker in PERMISSION_MARKERS):
        return "permission"

    if stage == "merge" and any(marker in haystack for marker in CONFLICT_MARKERS):
        return "merge_conflict"

    if stage == "checks":
        return "check_failure"

    if stage == "release":
        # A release that ran and failed is a check-style failure of the release
        # path, not a precondition of ours.
        return "check_failure"

    if any(marker in haystack for marker in CONFLICT_MARKERS):
        return "merge_conflict"

    return "precondition"


def describe_classification(classification: Optional[str]) -> str:
    """A sentence a developer can act on, for the pre-repair report."""
    return {
        "merge_conflict": (
            "the branch cannot merge cleanly into the target branch"
        ),
        "check_failure": (
            "one or more required checks reported a failure"
        ),
        "permission": (
            "the repository's rules or your credentials refused the operation — "
            "this tool reports such a rule, it never attempts to bypass one"
        ),
        "precondition": (
            "a precondition of the stage was not met"
        ),
        None: (
            "the outcome could not be determined, which is not a failure with an "
            "unknown cause — it is a separate answer, and no repair is attempted "
            "for it"
        ),
    }.get(classification, "unclassified")


# --------------------------------------------------------------------------
# Budget accounting (T064)
# --------------------------------------------------------------------------


class RepairLedger:
    """Tracks repair attempts against the configured budget (FR-019).

    ``budget == 0`` disables repair entirely — not "one free attempt", not
    "unbounded". The spec's assumption is that unbounded automatic repair on a
    shared branch is unsafe, so the off switch has to actually be off.
    """

    def __init__(self, budget: int) -> None:
        if budget < 0:
            raise ValueError(f"repair budget cannot be negative: {budget}")
        self.budget = budget
        self.attempts: List[Dict[str, Any]] = []

    @property
    def used(self) -> int:
        return len(self.attempts)

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def enabled(self) -> bool:
        return self.budget > 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.budget

    def can_attempt(self) -> bool:
        return self.enabled and not self.exhausted

    def record(self, attempt: Dict[str, Any]) -> Dict[str, Any]:
        """Append an attempt, refusing to exceed the budget.

        Raising rather than silently capping: a caller that asks for one more
        attempt than the budget allows has a bug, and quietly granting it would
        make the budget advisory.
        """
        if self.exhausted:
            raise RuntimeError(
                f"repair budget of {self.budget} is exhausted; "
                f"{self.used} attempt(s) already made"
            )
        self.attempts.append(attempt)
        return attempt

    def next_attempt_number(self) -> int:
        return self.used + 1

    def render(self) -> str:
        """Every attempt made, for the halt report (FR-020)."""
        if not self.attempts:
            if not self.enabled:
                return "  No repair was attempted — the repair budget is 0, which disables repair."
            return "  No repair was attempted."

        lines = [f"  {self.used} of {self.budget} repair attempt(s) made:"]
        for attempt in self.attempts:
            authority = attempt["authority"]
            applied = "applied" if authority == "mechanical" else "proposed, not applied"
            lines.append(
                f"    {attempt['attempt']}. [{attempt['targets']}] {applied} — "
                f"{attempt['description']}"
            )
            lines.append(f"       subsequent checks: {attempt['subsequent_checks']}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Mechanical conflict repair (T062)
# --------------------------------------------------------------------------


class RepairResult:
    def __init__(
        self,
        *,
        repaired: bool,
        attempt: Optional[Dict[str, Any]] = None,
        message: str = "",
        handed_back: bool = False,
        conflicted_paths: Optional[List[str]] = None,
    ) -> None:
        self.repaired = repaired
        self.attempt = attempt
        self.message = message
        # True when the conflict needs a semantic choice this tool will not make.
        self.handed_back = handed_back
        self.conflicted_paths = conflicted_paths or []


def repair_conflict(
    cwd: Path,
    *,
    branch: str,
    target: str,
    remote: str = "origin",
    merge_method: str = "squash",
    attempt_number: int = 1,
    push: bool = True,
) -> RepairResult:
    """Bring ``branch`` up to date with ``target`` and push. (FR-018, SC-005)

    Most "conflicts" GitHub reports are not semantic disagreements at all — the
    branch is simply behind, and merging the target in resolves cleanly. That is
    the class this repairs, unattended, and it is where SC-005's ≥70% comes from.

    When git **cannot** resolve it alone, the working copy is restored to exactly
    where it was and the conflicted paths are handed back. This tool does not
    choose between two people's edits.

    ``merge_method`` picks how the branch is updated: ``rebase`` replays the
    branch onto the target, anything else merges the target in. Matching the
    configured merge method keeps the branch's shape consistent with how it will
    eventually land.
    """
    fetched = gitops.fetch(remote, cwd=cwd)
    if not fetched.ok:
        return RepairResult(
            repaired=False,
            message=(
                f"Could not fetch {remote} to compare against {target}: "
                f"{fetched.error}"
            ),
        )

    target_ref = f"{remote}/{target}"

    # Refuse to start from a dirty tree: an aborted merge would not restore
    # uncommitted work, so there would be nothing to roll back to.
    if not gitops.is_clean(cwd=cwd):
        return RepairResult(
            repaired=False,
            handed_back=True,
            message=(
                "The working tree has uncommitted changes, so a conflict repair "
                "cannot be rolled back safely if it fails. Commit or stash them "
                "and re-run."
            ),
        )

    before = gitops.head_sha(cwd=cwd).text

    behind = gitops.ahead_behind(branch, target_ref, cwd=cwd)
    if behind.known and behind.behind == 0:
        # Already up to date, so bringing it up to date cannot be the fix. Say
        # so rather than performing a no-op merge and calling it a repair.
        return RepairResult(
            repaired=False,
            handed_back=True,
            message=(
                f"{branch} is already up to date with {target_ref} "
                f"({behind.ahead} commit(s) ahead, 0 behind), so the conflict is "
                "not staleness. It needs a decision this tool will not make."
            ),
        )

    if merge_method == "rebase":
        result = gitops.rebase(target_ref, cwd=cwd)
        abort = gitops.abort_rebase
        how = f"rebased onto {target_ref}"
    else:
        result = gitops.merge(
            target_ref, cwd=cwd, message=f"Merge {target_ref} into {branch}"
        )
        abort = gitops.abort_merge
        how = f"merged {target_ref} into {branch}"

    if not result.ok:
        conflicted = gitops.conflicted_paths(cwd=cwd)
        abort(cwd=cwd)

        after = gitops.head_sha(cwd=cwd).text
        restored = after == before

        return RepairResult(
            repaired=False,
            handed_back=True,
            conflicted_paths=conflicted,
            message=(
                f"Could not update {branch} from {target_ref} without a conflict "
                f"in {len(conflicted)} file(s): {', '.join(conflicted) or 'unknown'}.\n"
                "  Resolving this requires choosing between two people's changes, "
                "which this tool does not do. The branch was restored"
                + ("" if restored else " — WARNING: HEAD did not return to its prior commit")
                + " and left for you."
            ),
        )

    pushed_sha: Optional[str] = None
    if push:
        # A rebase rewrites history, so the push must be forced — but only with
        # --force-with-lease, which refuses if someone else pushed meanwhile.
        # A plain --force here would silently discard a colleague's commit.
        if merge_method == "rebase":
            pushed = gitops.run(
                ["push", "--force-with-lease", remote, branch],
                cwd=cwd,
                timeout=gitops.NETWORK_TIMEOUT,
            )
        else:
            pushed = gitops.push(remote, branch, cwd=cwd)

        if not pushed.ok:
            return RepairResult(
                repaired=False,
                message=(
                    f"{how.capitalize()}, but the result could not be pushed: "
                    f"{pushed.error}"
                ),
            )
        pushed_sha = gitops.head_sha(cwd=cwd).text

    attempt = make_repair_attempt(
        attempt=attempt_number,
        targets="merge_conflict",
        authority="mechanical",
        description=f"{how} to bring the branch up to date, then pushed",
        pushed_sha=pushed_sha,
        subsequent_checks="not_reached",
    )

    return RepairResult(
        repaired=True,
        attempt=attempt,
        message=f"{how.capitalize()} and pushed {(pushed_sha or '')[:8]}.",
    )


def detect_unmergeable(
    cwd: Path, *, branch: str, target: str, remote: str = "origin"
) -> Dict[str, Any]:
    """Would this branch merge cleanly? Checked locally, before any merge. (FR-018)

    Uses ``git merge-tree``, which computes the merge without touching the
    working copy or creating a commit. This is what lets the run detect
    unmergeability *before* attempting a merge rather than discovering it
    halfway through one.

    Returns ``{"mergeable": True|False|None, ...}`` — ``None`` when it could not
    be computed, which is not the same as conflicting.
    """
    gitops.fetch(remote, cwd=cwd)
    target_ref = f"{remote}/{target}"

    base = gitops.run(["merge-base", branch, target_ref], cwd=cwd)
    if not base.ok:
        return {
            "mergeable": None,
            "reason": f"no merge base between {branch} and {target_ref}: {base.error}",
        }

    # The three-argument form is available on git 2.20+, the declared floor.
    tree = gitops.run(["merge-tree", base.text, branch, target_ref], cwd=cwd)
    if not tree.ok:
        return {"mergeable": None, "reason": f"git merge-tree failed: {tree.error}"}

    conflicted = "<<<<<<<" in tree.stdout or "changed in both" in tree.stdout
    return {
        "mergeable": not conflicted,
        "reason": "computed with git merge-tree, without touching the working copy",
    }


# --------------------------------------------------------------------------
# Check-failure repair seam (T063)
# --------------------------------------------------------------------------


def collect_failure_evidence(checks_outcome) -> Dict[str, Any]:
    """Gather what a proposer needs: which checks failed, and their own output.

    Returns only what was actually retrieved. A check whose log could not be
    read carries ``log_excerpt: None`` rather than a placeholder — handing a
    model an invented log would produce a confident repair for a failure that
    never happened.
    """
    failing = [
        check
        for check in getattr(checks_outcome, "checks", [])
        if check.get("outcome") in ("failure", "timed_out", "cancelled")
    ]

    return {
        "failing_checks": [
            {
                "name": check["name"],
                "outcome": check["outcome"],
                "url": check.get("url"),
                "log_excerpt": check.get("log_excerpt"),
                "log_retrieved": check.get("log_excerpt") is not None,
            }
            for check in failing
        ],
        "required_failures": list(getattr(checks_outcome, "required_failures", [])),
        "optional_failures": list(getattr(checks_outcome, "optional_failures", [])),
        "captured_at": now_iso(),
    }


def propose_check_repair(
    checks_outcome,
    *,
    proposer: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, str]]]],
    attempt_number: int = 1,
) -> RepairResult:
    """Ask the command markdown to propose a fix — and do not apply it.

    ``authority='proposed'`` and ``pushed_sha=None`` are enforced by the record
    writer, so a proposal cannot be recorded as though it had been applied. That
    is Acceptance 2.4 made structural: the failure mode here is a run that
    "repairs" a test by changing it and reports green, and the only reliable
    guard is that this path has no way to write code at all.
    """
    evidence = collect_failure_evidence(checks_outcome)

    if not evidence["failing_checks"]:
        return RepairResult(
            repaired=False,
            message="No failing check was reported, so there is nothing to propose a repair for.",
        )

    if proposer is None:
        return RepairResult(
            repaired=False,
            handed_back=True,
            message=(
                "A check failed and no repair-proposal seam is available in this "
                "context, so the failure is handed back unmodified:\n"
                + _render_evidence(evidence)
            ),
        )

    proposal = proposer(evidence)

    if not proposal or not proposal.get("description"):
        return RepairResult(
            repaired=False,
            handed_back=True,
            message=(
                "No repair was proposed for the failing check(s):\n"
                + _render_evidence(evidence)
            ),
        )

    attempt = make_repair_attempt(
        attempt=attempt_number,
        targets="check_failure",
        authority="proposed",
        description=proposal["description"],
        pushed_sha=None,  # never applied — the writer enforces this
        subsequent_checks="not_reached",
    )

    return RepairResult(
        repaired=False,  # nothing changed; the proposal awaits a human
        attempt=attempt,
        handed_back=True,
        message=(
            "A repair was proposed and is waiting for you. It has NOT been applied.\n\n"
            f"  {proposal['description']}\n\n"
            + _render_evidence(evidence)
        ),
    )


def _render_evidence(evidence: Dict[str, Any]) -> str:
    lines = []
    for check in evidence["failing_checks"]:
        lines.append(f"  {check['name']} — {check['outcome']}")
        if check.get("url"):
            lines.append(f"    {check['url']}")
        if check["log_retrieved"]:
            excerpt = "\n".join(
                f"      {line}" for line in (check["log_excerpt"] or "").splitlines()[-20:]
            )
            lines.append(excerpt)
        else:
            # Named rather than blank: "we could not read it" is information.
            lines.append("      (the failing output could not be retrieved)")
    return "\n".join(lines)
