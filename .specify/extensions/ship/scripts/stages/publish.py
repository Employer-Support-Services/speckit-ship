"""The publish stage (FR-008): push the branch, establishing tracking if new."""

from __future__ import annotations

from pathlib import Path

from scripts import gitops
from scripts.engine import StageResult


def run(cwd: Path, *, remote: str, branch: str, dry_run: bool = False) -> StageResult:
    """Push ``branch`` to ``remote``.

    ``--set-upstream`` is passed only on a first publish. Passing it
    unconditionally would silently repoint an existing upstream if the developer
    had deliberately set it elsewhere.
    """
    upstream = gitops.tracking_branch(branch, cwd=cwd)
    first_publish = upstream is None

    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: the branch was not pushed because this is a dry run",
            detail={"remote": remote, "branch": branch, "would_set_upstream": first_publish},
            message=(
                f"Would push {branch} to {remote}"
                + (" and set upstream tracking" if first_publish else "")
            ),
        )

    result = gitops.push(remote, branch, set_upstream=first_publish, cwd=cwd)

    if not result.ok:
        # A rejected push is usually a protected branch or a stale local ref —
        # both are the repository's rules, which are respected as given.
        classification = "permission" if _looks_like_permission(result) else "precondition"
        return StageResult(
            "failed",
            classification=classification,
            detail={
                "remote": remote,
                "branch": branch,
                "error": result.error,
                "stderr": result.stderr.strip(),
            },
            message=f"Could not push {branch} to {remote}: {result.error}",
        )

    sha = gitops.head_sha(cwd=cwd)

    return StageResult(
        "succeeded",
        detail={
            "remote": remote,
            "branch": branch,
            "head_sha": sha.text if sha.ok else None,
            "set_upstream": first_publish,
        },
        message=(
            f"Pushed {branch} to {remote}"
            + (" and set upstream tracking" if first_publish else "")
        ),
    )


def _looks_like_permission(result: gitops.GitResult) -> bool:
    haystack = f"{result.stderr} {result.stdout}".lower()
    return any(
        needle in haystack
        for needle in (
            "permission denied",
            "protected branch",
            "pre-receive hook declined",
            "access denied",
            "authentication failed",
            "not authorized",
        )
    )
