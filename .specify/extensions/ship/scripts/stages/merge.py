"""The merge stage (FR-013, FR-018).

Two things here are easy to get wrong and expensive when wrong:

**1. ``mergeable: "UNKNOWN"`` is not a conflict.** GitHub computes mergeability
lazily, so the first query after a push very often returns UNKNOWN while a
background job runs. Reading that as CONFLICTING fires the FR-018 conflict
repair against a branch that merges perfectly — rewriting history on a branch
that had nothing wrong with it. So UNKNOWN is re-polled a bounded number of
times, and if it never resolves it is recorded ``undetermined``, never guessed.

**2. The confirmation is per run.** Merging is outward-facing and hard to
reverse. There is no persistable always-yes; the engine's guard checks for a
confirmation scoped to this run, and this stage does not merge without one.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from scripts.engine import StageResult

# How many times to re-ask before accepting that mergeability is not computed.
# Small: this is a background job that normally settles in seconds, and a long
# wait here delays a merge the developer has already confirmed.
MERGEABILITY_POLLS = 5
MERGEABILITY_INTERVAL = 3


def resolve_mergeability(
    client,
    pr_number: int,
    *,
    polls: int = MERGEABILITY_POLLS,
    interval: float = MERGEABILITY_INTERVAL,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """Re-poll until mergeability is computed, or give up honestly.

    Returns ``{"mergeable": "MERGEABLE"|"CONFLICTING"|None, "view": …, "polls": n}``.
    ``None`` means *not computed*, which is distinct from *conflicting*.
    """
    view: Optional[Dict[str, Any]] = None

    for attempt in range(1, polls + 1):
        result = client.pr_view(pr_number)
        if not result.ok:
            return {"mergeable": None, "view": None, "polls": attempt, "error": result.reason}

        view = result.value

        # A terminal PR state settles the question before mergeability does.
        # An already-merged pull request reports `mergeable: UNKNOWN` forever —
        # there is nothing left to compute — so polling for it would burn the
        # whole budget and then report "not computed" about a PR that plainly
        # merged.
        if view.get("state") in ("MERGED", "CLOSED"):
            return {
                "mergeable": None,
                "view": view,
                "polls": attempt,
                "terminal_state": view["state"],
            }

        if view.get("mergeable") is not None:
            return {"mergeable": view["mergeable"], "view": view, "polls": attempt}

        if attempt < polls:
            sleeper(interval)

    return {"mergeable": None, "view": view, "polls": polls}


def run(
    *,
    client,
    pr_number: int,
    method: str,
    confirmation: Dict[str, Any],
    delete_branch: bool = False,
    dry_run: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
) -> StageResult:
    """Merge the pull request after resolving mergeability and confirming."""
    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: the pull request was not merged because this is a dry run",
            detail={"pr": pr_number, "method": method},
            message=f"Would merge #{pr_number} using {method}",
        )

    resolved = resolve_mergeability(client, pr_number, sleeper=sleeper)
    mergeable = resolved["mergeable"]
    view = resolved["view"] or {}

    # Terminal states first — they answer the question mergeability was asked
    # about, and they are how a run notices the developer merged in the web UI
    # between attempts (SC-008).
    if resolved.get("terminal_state") == "MERGED":
        return StageResult(
            "succeeded",
            confirmation=confirmation,
            detail={
                "pr": pr_number,
                "merge_commit_sha": view.get("merge_commit_sha"),
                "already_merged": True,
            },
            message=(
                f"#{pr_number} was already merged (commit "
                f"{(view.get('merge_commit_sha') or 'unknown')[:8]}); adopting that "
                "rather than merging again."
            ),
        )

    if resolved.get("terminal_state") == "CLOSED":
        return StageResult(
            "failed",
            classification="precondition",
            confirmation=confirmation,
            detail={"pr": pr_number, "state": "CLOSED"},
            message=f"#{pr_number} is closed and cannot be merged.",
        )

    if mergeable is None:
        return StageResult(
            "undetermined",
            reason=(
                "mergeability-not-computed: the hosting service did not report "
                f"whether #{pr_number} can merge cleanly after {resolved['polls']} "
                "attempts"
                + (f" ({resolved['error']})" if resolved.get("error") else "")
                + ". Not treating this as a conflict — GitHub computes "
                "mergeability lazily, and a branch that merges fine reports "
                "UNKNOWN while that job runs."
            ),
            detail={"pr": pr_number, "polls": resolved["polls"]},
        )

    if mergeable == "CONFLICTING":
        view = resolved["view"] or {}
        return StageResult(
            "failed",
            classification="merge_conflict",
            detail={
                "pr": pr_number,
                "merge_state_status": view.get("merge_state_status"),
                "polls": resolved["polls"],
            },
            message=(
                f"#{pr_number} cannot merge cleanly into {view.get('base')} — "
                "the branch conflicts with the target."
            ),
        )

    result = client.merge_pr(pr_number, method=method, delete_branch=delete_branch)

    if not result.ok:
        return StageResult(
            "failed",
            classification="permission" if _looks_like_permission(result.reason) else "precondition",
            detail={"pr": pr_number, "method": method, "error": result.reason},
            message=f"Could not merge #{pr_number}: {result.reason}",
        )

    merge_sha = result.value.get("merge_commit_sha")

    if not merge_sha:
        # The merge command succeeded but we could not read back the commit that
        # resulted. Without it the release stage cannot correlate, and
        # correlating by time instead is exactly what FR-015 forbids.
        return StageResult(
            "undetermined",
            reason=(
                f"merge-commit-unresolved: #{pr_number} reported a successful "
                "merge but the resulting merge commit could not be read back. "
                "Without that SHA a release cannot be attributed to this merge."
            ),
            confirmation=confirmation,
            detail={"pr": pr_number, "method": method, "raw": result.value},
        )

    return StageResult(
        "succeeded",
        confirmation=confirmation,
        detail={
            "pr": pr_number,
            "method": method,
            "merge_commit_sha": merge_sha,
            "polls": resolved["polls"],
        },
        message=f"Merged #{pr_number} using {method} as {merge_sha[:8]}",
    )


def _looks_like_permission(reason: Optional[str]) -> bool:
    haystack = (reason or "").lower()
    return any(
        needle in haystack
        for needle in (
            "permission",
            "not authorized",
            "review required",
            "protected",
            "required status check",
            "at least",
        )
    )
