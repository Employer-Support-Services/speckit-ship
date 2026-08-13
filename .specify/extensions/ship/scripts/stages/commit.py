"""The commit stage (FR-007).

Two rules:

* **Show what will be committed, before committing it.** The spec's edge-case
  list names "the working copy contains changes unrelated to the feature being
  shipped", and a tool that stages everything silently ships those too.
* **A clean tree is a skip, not a failure.** Re-running ship after a successful
  commit must not error; there is simply nothing to do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts import gitops
from scripts.engine import StageResult


def summarize_pending(cwd: Path) -> Dict[str, Any]:
    """What is currently uncommitted, as data the caller can present.

    Porcelain codes are kept verbatim rather than translated: ``??`` for
    untracked is meaningfully different from ``M`` for modified when the question
    is "am I about to ship something I did not mean to".
    """
    status = gitops.porcelain_status(cwd=cwd)
    if not status.ok:
        return {"readable": False, "error": status.error, "entries": []}

    entries: List[Dict[str, str]] = []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        code, _, path = line.partition(" ")
        entries.append({"code": line[:2].strip() or code, "path": path.strip() or line[2:].strip()})

    untracked = [e for e in entries if e["code"] == "??"]

    return {
        "readable": True,
        "entries": entries,
        "count": len(entries),
        "untracked": [e["path"] for e in untracked],
        "diffstat": gitops.diff_stat(cwd=cwd).stdout.strip(),
    }


def render_pending(summary: Dict[str, Any]) -> str:
    """The block shown to the developer before anything is staged."""
    if not summary.get("readable"):
        return f"Could not read the working tree: {summary.get('error')}"

    if not summary["entries"]:
        return "Working tree is clean — nothing to commit."

    lines = [f"{summary['count']} path(s) would be committed:", ""]
    for entry in summary["entries"]:
        lines.append(f"  {entry['code']:<3} {entry['path']}")

    if summary["untracked"]:
        lines.append("")
        lines.append(
            f"  {len(summary['untracked'])} of these are untracked and would be "
            "added to the repository for the first time."
        )

    if summary.get("diffstat"):
        lines.append("")
        lines.append(summary["diffstat"])

    return "\n".join(lines)


def run(
    cwd: Path,
    *,
    message: str,
    confirm=None,
    dry_run: bool = False,
) -> StageResult:
    """Stage and commit outstanding work.

    ``confirm(summary_text) -> bool`` is the presentation seam. When it returns
    False the stage is a skip with a reason, not a failure — the developer
    declining to commit unrelated changes is a legitimate answer.
    """
    summary = summarize_pending(cwd)

    if not summary.get("readable"):
        return StageResult(
            "failed",
            classification="precondition",
            detail=summary,
            message=f"Could not read the working tree: {summary.get('error')}",
        )

    if not summary["entries"]:
        return StageResult(
            "skipped",
            reason="clean-tree: there were no uncommitted changes to commit",
            detail={"count": 0},
        )

    rendered = render_pending(summary)

    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: the commit was not made because this is a dry run",
            detail={**summary, "rendered": rendered},
            message=rendered,
        )

    if confirm is not None and not confirm(rendered):
        return StageResult(
            "skipped",
            reason="declined: the developer declined to commit the pending changes",
            detail={**summary, "rendered": rendered},
            message=rendered,
        )

    staged = gitops.stage_all(cwd=cwd)
    if not staged.ok:
        return StageResult(
            "failed",
            classification="precondition",
            detail={"error": staged.error},
            message=f"git add failed: {staged.error}",
        )

    result = gitops.commit(message, cwd=cwd)
    if not result.ok:
        # A pre-commit hook rejecting the commit lands here, which is a
        # precondition of the repository's own making, not our failure.
        return StageResult(
            "failed",
            classification="precondition",
            detail={"error": result.error, "output": result.stdout},
            message=f"git commit failed: {result.error}",
        )

    sha = gitops.head_sha(cwd=cwd)

    return StageResult(
        "succeeded",
        detail={
            "sha": sha.text if sha.ok else None,
            "paths": [e["path"] for e in summary["entries"]],
            "count": summary["count"],
            "message": message,
        },
        message=f"Committed {summary['count']} path(s) as {sha.text[:8] if sha.ok else 'unknown'}",
    )
