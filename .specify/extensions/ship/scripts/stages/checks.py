"""The checks stage: reduce a rollup to one outcome, and wait within a bound.

Four outcomes, and the fourth is the one the whole feature turns on:

``passed``
    every *required* check reports success, neutral, or skipped.
``failed``
    at least one required check reports failure, timed-out, cancelled, or
    action-required.
``pending``
    at least one required check is queued or running, and the cap has not been
    reached.
``undetermined``
    the cap was reached with checks still pending, **or** the repository reports
    no checks at all, **or** the rollup could not be read, **or** a check failed
    and we cannot establish whether it was required.

Each undetermined source carries its own reason token, because they call for
different responses and collapsing them loses that. None of them is a pass
(FR-012).

On requiredness: it only matters when something failed. If every check is green
it does not matter whether any of them gated. If something failed and we could
not read branch protection, the honest answer is that we do not know whether the
merge is blocked — recorded as undetermined, not resolved in either direction.
An optional-looking failure may in fact be required.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from scripts.state import determined, make_check_result, undetermined

# Terminal conclusions, mapped to the CheckResult vocabulary in the schema.
CONCLUSION_MAP = {
    "SUCCESS": "success",
    "NEUTRAL": "neutral",
    "SKIPPED": "skipped",
    "FAILURE": "failure",
    "TIMED_OUT": "timed_out",
    "CANCELLED": "cancelled",
    "ACTION_REQUIRED": "failure",
    "STARTUP_FAILURE": "failure",
    "STALE": "failure",
    # Commit-status flavored spellings, which appear alongside CheckRun entries.
    "ERROR": "failure",
    "EXPECTED": "pending",
    "PENDING": "pending",
}

PASSING = ("success", "neutral", "skipped")
FAILING = ("failure", "timed_out", "cancelled")

# Polling schedule (FR-011): start responsive, back off, never unbounded.
INITIAL_INTERVAL = 10
MAX_INTERVAL = 30


def normalize_entry(entry: Dict[str, Any]) -> Tuple[str, str, Optional[str]]:
    """One rollup entry -> ``(name, outcome, url)``.

    Handles both shapes GitHub returns in the same array: ``CheckRun`` (with
    ``status``/``conclusion``) and ``StatusContext`` (with ``state``/``context``).
    """
    typename = entry.get("__typename")

    if typename == "StatusContext" or "context" in entry:
        name = entry.get("context") or entry.get("name") or "(unnamed status)"
        raw = (entry.get("state") or "").upper()
        return name, CONCLUSION_MAP.get(raw, "pending"), entry.get("targetUrl")

    name = entry.get("name") or "(unnamed check)"
    status = (entry.get("status") or "").upper()
    conclusion = entry.get("conclusion")

    if status in ("QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "PENDING"):
        return name, "pending", entry.get("detailsUrl")

    if not conclusion:
        # Completed with no conclusion is not a success; it is unreadable.
        return name, "pending", entry.get("detailsUrl")

    return name, CONCLUSION_MAP.get(conclusion.upper(), "failure"), entry.get("detailsUrl")


class ChecksOutcome:
    def __init__(
        self,
        outcome: str,
        *,
        reason: Optional[str] = None,
        checks: Optional[List[Dict[str, Any]]] = None,
        optional_failures: Optional[List[str]] = None,
        required_failures: Optional[List[str]] = None,
    ) -> None:
        self.outcome = outcome  # passed | failed | pending | undetermined
        self.reason = reason
        self.checks = checks or []
        self.optional_failures = optional_failures or []
        self.required_failures = required_failures or []

    @property
    def terminal(self) -> bool:
        return self.outcome != "pending"

    def summary(self) -> str:
        if not self.checks:
            return "no checks reported"
        counts: Dict[str, int] = {}
        for check in self.checks:
            counts[check["outcome"]] = counts.get(check["outcome"], 0) + 1
        return ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChecksOutcome {self.outcome} {self.reason or ''}>"


def reduce_rollup(
    view: Dict[str, Any],
    *,
    required_names: Optional[List[str]] = None,
    requiredness_known: bool = False,
) -> ChecksOutcome:
    """Reduce a ``statusCheckRollup`` to one outcome. (FR-011, FR-012, research R5)

    ``requiredness_known`` says whether branch protection could actually be read.
    When it is False, every check's ``required`` is recorded undetermined — and a
    failure among them cannot be classified as gating or not.
    """
    if not view.get("rollup_present"):
        # The key was null or absent. GitHub did not compute a rollup; that is
        # not a statement about whether the repository has checks.
        return ChecksOutcome(
            "undetermined",
            reason=(
                "rollup-unreadable: the hosting service did not report a status "
                "check rollup for this pull request"
            ),
        )

    entries = view.get("rollup") or []

    if not entries:
        # A real, determined answer about the repository — and still not green.
        return ChecksOutcome(
            "undetermined",
            reason=(
                "no-checks-configured: the repository reports no checks against "
                "this pull request, so there is no green to wait for"
            ),
        )

    required_names = required_names or []
    checks: List[Dict[str, Any]] = []
    pending: List[str] = []
    required_failures: List[str] = []
    optional_failures: List[str] = []
    unknown_requiredness_failures: List[str] = []

    for entry in entries:
        name, outcome, url = normalize_entry(entry)

        if requiredness_known:
            required = determined(name in required_names, "gh-branch-protection")
        else:
            required = undetermined(
                "requiredness-unknown: branch protection for the target branch "
                "could not be read, so whether this check gates the merge is "
                "not established"
            )

        checks.append(
            make_check_result(name=name, outcome=outcome, required=required, url=url)
        )

        if outcome == "pending":
            pending.append(name)
        elif outcome in FAILING:
            if not requiredness_known:
                unknown_requiredness_failures.append(name)
            elif name in required_names:
                required_failures.append(name)
            else:
                optional_failures.append(name)

    if required_failures:
        return ChecksOutcome(
            "failed",
            checks=checks,
            required_failures=required_failures,
            optional_failures=optional_failures,
        )

    if unknown_requiredness_failures:
        # The honest answer. Reporting "failed" would over-claim; reporting
        # "passed" would merge on a possibly-gating failure.
        return ChecksOutcome(
            "undetermined",
            reason=(
                "check-requiredness-unknown: "
                + ", ".join(unknown_requiredness_failures)
                + " reported failure, and branch protection could not be read to "
                "establish whether they gate the merge"
            ),
            checks=checks,
            optional_failures=unknown_requiredness_failures,
        )

    if pending:
        return ChecksOutcome("pending", checks=checks, optional_failures=optional_failures)

    return ChecksOutcome("passed", checks=checks, optional_failures=optional_failures)


def read_required_checks(client, branch: str) -> Tuple[List[str], bool]:
    """Names of the checks branch protection requires, and whether we could read it.

    Branch protection usually needs admin scope, so ``(…, False)`` is the common
    case rather than an error. The second element is what stops an unreadable
    protection API from silently becoming "nothing is required".
    """
    getter = getattr(client, "required_checks", None)
    if getter is None:
        return [], False

    result = getter(branch)
    if not result.ok:
        return [], False
    return list(result.value or []), True


def poll(
    client,
    pr_number: int,
    *,
    branch: str,
    deadline: float,
    on_progress: Optional[Callable[[ChecksOutcome, float], None]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> ChecksOutcome:
    """Poll until terminal or the cap. (FR-011, FR-017)

    Backs off from 10s to 30s so a 30-minute wait costs roughly 70 calls rather
    than 180. Reports progress on every pass, because a silent half-hour is
    indistinguishable from a hang.

    The cap produces ``undetermined:checks-wait-exceeded`` — a *different* reason
    from ``no-checks-configured``, since one means "we stopped waiting" and the
    other means "there was nothing to wait for".
    """
    required_names, requiredness_known = read_required_checks(client, branch)
    interval = INITIAL_INTERVAL
    last: Optional[ChecksOutcome] = None

    while True:
        view = client.pr_view(pr_number)

        if not view.ok:
            outcome = ChecksOutcome("undetermined", reason=f"rollup-unreadable: {view.reason}")
        else:
            outcome = reduce_rollup(
                view.value,
                required_names=required_names,
                requiredness_known=requiredness_known,
            )

        last = outcome

        if outcome.terminal:
            return outcome

        remaining = deadline - clock()
        if on_progress is not None:
            on_progress(outcome, remaining)

        if remaining <= 0:
            return ChecksOutcome(
                "undetermined",
                reason=(
                    "checks-wait-exceeded: the configured wait elapsed with checks "
                    f"still running ({outcome.summary()})"
                ),
                checks=last.checks,
            )

        sleeper(min(interval, max(0.0, remaining)))
        interval = min(MAX_INTERVAL, interval + 10)


def attach_failing_logs(client, outcome: ChecksOutcome, *, limit: int = 8000) -> ChecksOutcome:
    """Retrieve each failing check's own output (FR-017).

    Reporting only "a check failed" makes the developer open the web UI, which
    SC-004 measures against. The run ID is parsed from the check's own details
    URL, which is the only place the rollup carries it.
    """
    import re

    for check in outcome.checks:
        if check["outcome"] not in FAILING or not check.get("url"):
            continue

        match = re.search(r"/actions/runs/(\d+)", check["url"])
        if not match:
            continue

        logs = client.failing_logs(match.group(1), limit=limit) if _accepts_limit(client) else client.failing_logs(match.group(1))
        if logs.ok:
            check["log_excerpt"] = logs.value
        else:
            # Not fabricating an excerpt we could not retrieve.
            check["log_excerpt"] = None

    return outcome


def _accepts_limit(client) -> bool:
    import inspect

    try:
        return "limit" in inspect.signature(client.failing_logs).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False
