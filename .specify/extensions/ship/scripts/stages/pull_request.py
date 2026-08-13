"""The pull-request stage (FR-009, FR-010).

**Adoption comes before creation, always.** An existing open PR for this branch
is adopted rather than duplicated — that is what makes a resumed run idempotent
(SC-008), and a duplicate PR is a visible, embarrassing, outward-facing mistake.

The subtlety is distinguishing "there is no PR" from "the query failed". Only
the first justifies creating one. A transient error read as "no PR exists"
produces exactly the duplicate this requirement forbids, which is why
``find_pr`` returns a result object rather than an optional.

Composition (FR-038) has three modes. Only ``drafted`` involves a model, and its
output is **presented for review before the PR is created** (FR-010) — the
drafting seam lives in the command markdown, and this module takes the text it
returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from scripts import gitops
from scripts.engine import StageResult


def commit_subjects(cwd: Path, *, base: str, head: str, remote: str = "origin") -> List[str]:
    """Subjects of the commits this branch adds, for ``composition == 'commits'``.

    Compares against the *remote* base where possible: the local copy of the
    integration branch may be days stale, which would pad the PR body with
    commits that are already merged.
    """
    for ref in (f"{remote}/{base}", base):
        result = gitops.commits_between(ref, head, cwd=cwd)
        if result.ok:
            return [line for line in result.stdout.splitlines() if line.strip()]
    return []


def compose(
    cwd: Path,
    *,
    branch: str,
    base: str,
    composition: str,
    remote: str = "origin",
    title_template: Optional[str] = None,
    drafter: Optional[Callable[[Dict[str, Any]], Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Build the title and body per the configured composition mode.

    Returns a dict carrying ``title``, ``body``, and ``needs_review`` — the last
    telling the caller whether FR-010's pre-creation review applies.
    """
    subjects = commit_subjects(cwd, base=base, head=branch, remote=remote)

    if composition == "drafted":
        if drafter is None:
            # No drafting seam supplied. Fall back to the deterministic mode and
            # say so — never silently present a commit-assembled body as a
            # drafted one.
            return {
                **_from_commits(branch, subjects, title_template),
                "mode": "commits",
                "needs_review": True,
                "note": (
                    "composition is 'drafted' but no drafting seam was supplied; "
                    "composed from commits instead"
                ),
            }

        drafted = drafter({"branch": branch, "base": base, "commits": subjects})
        return {
            "title": drafted.get("title") or _default_title(branch, subjects, title_template),
            "body": drafted.get("body", ""),
            "mode": "drafted",
            "commits": subjects,
            # FR-010: a machine-drafted description is presented for review
            # before the PR is created, never after.
            "needs_review": True,
        }

    if composition == "manual":
        return {
            "title": _default_title(branch, subjects, title_template),
            "body": "",
            "mode": "manual",
            "commits": subjects,
            "needs_review": True,
        }

    return {**_from_commits(branch, subjects, title_template), "mode": "commits", "needs_review": False}


def _default_title(branch: str, subjects: List[str], template: Optional[str]) -> str:
    if template:
        return template.replace("{branch}", branch).replace(
            "{first_commit}", subjects[0] if subjects else branch
        )
    if len(subjects) == 1:
        return subjects[0]
    return branch


def _from_commits(branch: str, subjects: List[str], template: Optional[str]) -> Dict[str, Any]:
    title = _default_title(branch, subjects, template)

    if subjects:
        body = "\n".join(f"- {subject}" for subject in subjects)
    else:
        # An honest empty body beats an invented summary.
        body = "_No commits found between the target branch and this branch._"

    return {"title": title, "body": body, "commits": subjects}


def run(
    cwd: Path,
    *,
    client,
    branch: str,
    base: str,
    composition: str = "commits",
    remote: str = "origin",
    title_template: Optional[str] = None,
    draft: bool = False,
    drafter: Optional[Callable[[Dict[str, Any]], Dict[str, str]]] = None,
    review: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, str]]]] = None,
    dry_run: bool = False,
) -> StageResult:
    """Adopt an existing PR, or create one after presenting the description.

    ``review(composed) -> dict | None`` is FR-010's gate: it returns the
    (possibly edited) title and body to use, or None to decline. Declining is a
    skip, not a failure.
    """
    existing = client.find_pr(branch)

    if not existing.ok:
        # We could not tell whether a PR exists. Creating one now risks the
        # duplicate FR-009 forbids, so the honest move is to stop.
        return StageResult(
            "undetermined",
            reason=(
                f"pr-lookup-failed: could not establish whether an open pull "
                f"request already exists for {branch} ({existing.reason}). "
                "Refusing to create one, because a duplicate pull request is "
                "worse than a halted run."
            ),
            detail={"branch": branch, "error": existing.reason},
        )

    if existing.value:
        pr = existing.value
        if pr.get("state") == "OPEN":
            return StageResult(
                "succeeded",
                detail={"pr": pr, "adopted": True},
                message=f"Adopted existing pull request #{pr['number']} ({pr['url']})",
            )
        # A closed or merged PR for this branch does not block a new one, but it
        # is worth recording that we saw it.
        adopted_note = {"previous_pr": pr}
    else:
        adopted_note = {}

    composed = compose(
        cwd,
        branch=branch,
        base=base,
        composition=composition,
        remote=remote,
        title_template=title_template,
        drafter=drafter,
    )

    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: no pull request was created because this is a dry run",
            detail={**adopted_note, "composed": composed},
            message=f"Would open a pull request: {composed['title']}",
        )

    if composed.get("needs_review") and review is not None:
        approved = review(composed)
        if approved is None:
            return StageResult(
                "skipped",
                reason="declined: the developer declined the drafted pull-request description",
                detail={**adopted_note, "composed": composed},
            )
        composed = {**composed, **approved}

    created = client.create_pr(
        head=branch,
        base=base,
        title=composed["title"],
        body=composed["body"],
        draft=draft,
    )

    if not created.ok:
        return StageResult(
            "failed",
            classification="permission" if "permission" in (created.reason or "").lower() else "precondition",
            detail={**adopted_note, "composed": composed, "error": created.reason},
            message=f"Could not open a pull request: {created.reason}",
        )

    pr = created.value
    return StageResult(
        "succeeded",
        detail={
            **adopted_note,
            "pr": {
                "number": pr.get("number"),
                "url": pr.get("url"),
                "state": "OPEN",
                "base": base,
                "head": branch,
            },
            "adopted": False,
            "composition": composed.get("mode"),
        },
        message=f"Opened pull request #{pr.get('number')} ({pr.get('url')})",
    )
