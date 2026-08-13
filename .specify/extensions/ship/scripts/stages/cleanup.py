"""The cleanup stage (FR-023, FR-024, FR-025).

Deletion is the one irreversible thing this stage does, so it is guarded twice:
the engine will not enter cleanup without a confirmed merge (FR-023), and this
module independently refuses any branch that still carries unmerged commits
(FR-025) — reporting those commits rather than a bare refusal.

A remote that refuses the deletion is **reported, not failed** (FR-023). The
work shipped; a protected branch or a dependent pull request blocking the tidy-up
is not a reason to call the run unsuccessful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts import gitops
from scripts.engine import StageResult


def unmerged_commits(cwd: Path, *, branch: str, target: str, remote: str = "origin") -> Dict[str, Any]:
    """Commits on ``branch`` not reachable from ``target``.

    Prefers the remote-tracking copy of the target: the local one may predate
    the merge that just happened, which would make every shipped commit look
    unmerged and block a legitimate cleanup.
    """
    for ref in (f"{remote}/{target}", target):
        result = gitops.unmerged_commits(branch, ref, cwd=cwd)
        if result.ok:
            return {
                "readable": True,
                "compared_against": ref,
                "commits": [line for line in result.stdout.splitlines() if line.strip()],
            }
    return {"readable": False, "compared_against": None, "commits": []}


def run(
    cwd: Path,
    *,
    branch: str,
    target: str,
    remote: str = "origin",
    delete_branch: bool = True,
    return_to_integration: bool = True,
    dry_run: bool = False,
) -> StageResult:
    """Delete the shipped branch and return to an updated integration branch."""
    actions: List[str] = []
    reported: List[str] = []
    detail: Dict[str, Any] = {"branch": branch, "target": target, "remote": remote}

    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: nothing was cleaned up because this is a dry run",
            detail=detail,
            message=f"Would delete {branch} and switch to {target}",
        )

    # Refresh the remote-tracking refs first, so the unmerged check below
    # compares against the merge that just landed rather than a stale copy.
    gitops.fetch(remote, cwd=cwd)

    if delete_branch:
        unmerged = unmerged_commits(cwd, branch=branch, target=target, remote=remote)
        detail["unmerged"] = unmerged

        if not unmerged["readable"]:
            return StageResult(
                "undetermined",
                reason=(
                    f"unmerged-check-failed: could not establish whether {branch} "
                    f"has commits missing from {target}, so it was not deleted"
                ),
                detail=detail,
            )

        if unmerged["commits"]:
            # FR-025: report the commits instead of deleting.
            listing = "\n".join(f"    {line}" for line in unmerged["commits"])
            return StageResult(
                "undetermined",
                reason=(
                    f"unmerged-commits: {branch} carries "
                    f"{len(unmerged['commits'])} commit(s) not reachable from "
                    f"{unmerged['compared_against']}, so it was not deleted"
                ),
                detail=detail,
                message=(
                    f"Refusing to delete {branch} — it still has "
                    f"{len(unmerged['commits'])} unmerged commit(s):\n{listing}"
                ),
            )

    # Switch away before deleting: git will not delete the checked-out branch,
    # and being left on a deleted branch is a confusing place to end a run.
    if return_to_integration or delete_branch:
        switched = gitops.checkout(target, cwd=cwd)
        if not switched.ok:
            return StageResult(
                "undetermined",
                reason=(
                    f"checkout-failed: could not switch to {target} "
                    f"({switched.error}), so the branch was left in place"
                ),
                detail={**detail, "error": switched.error},
            )
        actions.append(f"switched to {target}")

    if delete_branch:
        local = gitops.delete_local_branch(branch, cwd=cwd)
        if local.ok:
            actions.append(f"deleted {branch} locally")
        else:
            reported.append(f"local deletion of {branch} was refused: {local.error}")

        remote_result = gitops.delete_remote_branch(remote, branch, cwd=cwd)
        if remote_result.ok:
            actions.append(f"deleted {remote}/{branch}")
        else:
            # Reported, not failed. The work shipped; the tidy-up did not.
            reported.append(
                f"remote deletion of {remote}/{branch} was refused: {remote_result.error}"
            )

    if return_to_integration:
        pulled = gitops.pull(remote, target, cwd=cwd)
        if pulled.ok:
            actions.append(f"updated {target} from {remote}")
        else:
            reported.append(f"could not update {target} from {remote}: {pulled.error}")

    detail["actions"] = actions
    detail["reported"] = reported

    message = "; ".join(actions) if actions else "nothing to clean up"
    if reported:
        message += "\n" + "\n".join(f"  note: {item}" for item in reported)

    return StageResult("succeeded", detail=detail, message=message)
