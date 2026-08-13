"""The release stage (FR-014, FR-015, FR-043–FR-045).

This module carries the single hardest line in the spec: **a release is never
inferred from a merge** (FR-015, SC-012). A merge succeeding tells you a merge
succeeded. Whether anything reached production is a separate question with a
separate answer, and the two are not allowed to borrow each other's evidence.

Three consequences shape the code:

* Every ``ReleaseRecord`` is written from **this** path's own confirmation and
  carries non-empty ``evidence``. The record writer refuses otherwise, so the
  untruthful record cannot be constructed even by mistake.
* Correlation is by **merge commit SHA**, never by time proximity. A release
  queued behind other releases — a named edge case — would otherwise attach the
  wrong run to the merge and report someone else's deploy as yours.
* A release that ran and failed is reported as *a failed release with the
  integration branch ahead of production* (FR-045), which is a different and
  more urgent fact than a run that never attempted one.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from scripts.engine import StageResult
from scripts.state import make_release_record

TERMINAL_SUCCESS = ("success",)
TERMINAL_FAILURE = ("failure", "cancelled", "timed_out", "action_required", "startup_failure")


# --------------------------------------------------------------------------
# Observed mode (T048)
# --------------------------------------------------------------------------


def find_release_run(
    client, *, merge_sha: str, integration_branch: str, workflow_pin: Optional[str] = None
) -> Dict[str, Any]:
    """Locate the run this merge triggered, by SHA.

    ``workflow_pin`` narrows the choice when several workflows fire on the
    integration branch. Without a pin and with several candidates, this returns
    them all and lets the caller record that the correlation was ambiguous
    rather than picking the first.
    """
    result = client.runs_for_sha(merge_sha, integration_branch)

    if not result.ok:
        return {"runs": [], "error": result.reason}

    runs = result.value or []

    if workflow_pin:
        pinned = [
            run
            for run in runs
            if workflow_pin in (run.get("name") or "")
            or workflow_pin == run.get("workflowName")
        ]
        if pinned:
            runs = pinned

    return {"runs": runs}


def observe(
    *,
    client,
    merge_sha: str,
    integration_branch: str,
    deadline: float,
    confirmation: Dict[str, Any],
    workflow_pin: Optional[str] = None,
    on_progress: Optional[Callable[[str, float], None]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> StageResult:
    """Watch the repository's own release path until it reports. (FR-044)

    No confirmation within the cap yields ``undetermined:release-not-confirmed``
    — **never** "released". The integration branch may well be live by then; we
    simply did not see it happen, and saying otherwise would be a claim we
    cannot support.
    """
    found = find_release_run(
        client,
        merge_sha=merge_sha,
        integration_branch=integration_branch,
        workflow_pin=workflow_pin,
    )

    # Give the release run a moment to be created — it is queued by the merge,
    # not created synchronously with it.
    while not found["runs"] and clock() < deadline:
        if on_progress is not None:
            on_progress("waiting for a release run to appear", deadline - clock())
        sleeper(min(10, max(0.0, deadline - clock())))
        found = find_release_run(
            client,
            merge_sha=merge_sha,
            integration_branch=integration_branch,
            workflow_pin=workflow_pin,
        )

    runs = found["runs"]

    if not runs:
        return StageResult(
            "undetermined",
            reason=(
                "release-not-confirmed: no release run was found for merge commit "
                f"{merge_sha[:8]} on {integration_branch} within the configured wait"
                + (f" ({found['error']})" if found.get("error") else "")
                + ". The merge landed; whether anything was released is not established."
            ),
            confirmation=confirmation,
            detail={"merge_sha": merge_sha, "branch": integration_branch},
        )

    if len(runs) > 1:
        names = [run.get("name") or str(run.get("databaseId")) for run in runs]
        return StageResult(
            "undetermined",
            reason=(
                f"release-correlation-ambiguous: {len(runs)} runs match merge "
                f"commit {merge_sha[:8]} ({', '.join(names)}). Pin one with "
                "release.observed_workflow rather than having this guess."
            ),
            confirmation=confirmation,
            detail={"merge_sha": merge_sha, "candidates": names},
        )

    run = runs[0]
    run_id = str(run.get("databaseId"))

    watched = client.watch_run(run_id, deadline)

    if not watched.ok:
        return StageResult(
            "undetermined",
            reason=watched.reason,
            confirmation=confirmation,
            detail={"merge_sha": merge_sha, "run_id": run_id, "url": run.get("url")},
        )

    conclusion = (watched.value.get("conclusion") or "").lower()
    url = watched.value.get("url") or run.get("url")

    if conclusion in TERMINAL_SUCCESS:
        record = make_release_record(
            mode="observed",
            from_merge_sha=merge_sha,
            outcome="released",
            identifier=run_id,
            # Non-empty by construction, and it names what actually confirmed it.
            evidence=(
                f"workflow run {run_id} ({run.get('name') or 'unnamed workflow'}) "
                f"for merge commit {merge_sha[:8]} concluded 'success' at {url}"
            ),
        )
        return StageResult(
            "succeeded",
            confirmation=confirmation,
            release=record,
            detail={"run_id": run_id, "url": url},
            message=f"Release confirmed by run {run_id} ({url})",
        )

    if conclusion in TERMINAL_FAILURE:
        record = make_release_record(
            mode="observed",
            from_merge_sha=merge_sha,
            outcome="failed",
            identifier=run_id,
            evidence=(
                f"workflow run {run_id} ({run.get('name') or 'unnamed workflow'}) "
                f"for merge commit {merge_sha[:8]} concluded '{conclusion}' at {url}"
            ),
        )
        return StageResult(
            "failed",
            classification="check_failure",
            confirmation=confirmation,
            release=record,
            detail={"run_id": run_id, "url": url, "conclusion": conclusion},
            message=(
                f"The release run failed ({conclusion}). The merge landed, so "
                f"{integration_branch} is now ahead of production. See {url}"
            ),
        )

    return StageResult(
        "undetermined",
        reason=(
            f"release-not-confirmed: run {run_id} concluded {conclusion!r}, which "
            "is not a terminal success or failure this tool recognizes"
        ),
        confirmation=confirmation,
        detail={"run_id": run_id, "url": url, "conclusion": conclusion},
    )


# --------------------------------------------------------------------------
# Executed mode (T049)
# --------------------------------------------------------------------------


def execute(
    *,
    client,
    action: Dict[str, Any],
    merge_sha: str,
    integration_branch: str,
    confirmation: Dict[str, Any],
    deadline: float,
    repo_root=None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> StageResult:
    """Run the repository's declared release action after the merge. (FR-045)

    The tool runs what the repository declares; it never composes an action.
    A failure here is reported as a **failed release**, which is distinct from a
    run that never reached this stage — the integration branch is ahead of
    production either way, but only one of them needs someone's attention now.
    """
    if "workflow" in action:
        return _execute_workflow(
            client=client,
            action=action,
            merge_sha=merge_sha,
            integration_branch=integration_branch,
            confirmation=confirmation,
            deadline=deadline,
            sleeper=sleeper,
            clock=clock,
        )

    if "release" in action:
        return _execute_release(
            client=client,
            action=action,
            merge_sha=merge_sha,
            confirmation=confirmation,
        )

    if "script" in action:
        return _execute_script(
            action=action,
            merge_sha=merge_sha,
            confirmation=confirmation,
            repo_root=repo_root,
        )

    return StageResult(
        "failed",
        classification="precondition",
        confirmation=confirmation,
        detail={"action": action},
        message=(
            "release.action declares none of 'workflow', 'release', or 'script'. "
            "This tool runs what the repository declares and does not compose a "
            "release action."
        ),
    )


def _execute_workflow(
    *, client, action, merge_sha, integration_branch, confirmation, deadline, sleeper, clock
) -> StageResult:
    ref = action.get("ref") or integration_branch
    dispatched = client.dispatch_workflow(action["workflow"], ref, action.get("inputs"))

    if not dispatched.ok:
        return StageResult(
            "failed",
            classification="permission" if "permission" in (dispatched.reason or "").lower() else "precondition",
            confirmation=confirmation,
            detail={"action": action, "error": dispatched.reason},
            message=f"Could not dispatch {action['workflow']}: {dispatched.reason}",
        )

    # `gh workflow run` returns no run id, so the dispatched run is located the
    # same way observed mode locates one: by the SHA it is building.
    found = find_release_run(client, merge_sha=merge_sha, integration_branch=integration_branch)
    while not found["runs"] and clock() < deadline:
        sleeper(min(10, max(0.0, deadline - clock())))
        found = find_release_run(
            client, merge_sha=merge_sha, integration_branch=integration_branch
        )

    if not found["runs"]:
        return StageResult(
            "undetermined",
            reason=(
                f"release-not-confirmed: {action['workflow']} was dispatched for "
                f"{merge_sha[:8]} but no matching run appeared within the "
                "configured wait, so its outcome is not established"
            ),
            confirmation=confirmation,
            detail={"action": action, "merge_sha": merge_sha},
        )

    run = found["runs"][0]
    run_id = str(run.get("databaseId"))
    watched = client.watch_run(run_id, deadline)

    if not watched.ok:
        return StageResult(
            "undetermined",
            reason=watched.reason,
            confirmation=confirmation,
            detail={"run_id": run_id, "action": action},
        )

    conclusion = (watched.value.get("conclusion") or "").lower()
    url = watched.value.get("url") or run.get("url")
    outcome = "released" if conclusion in TERMINAL_SUCCESS else "failed"

    record = make_release_record(
        mode="executed",
        from_merge_sha=merge_sha,
        outcome=outcome,
        identifier=run_id,
        evidence=(
            f"dispatched {action['workflow']} on {ref} for merge commit "
            f"{merge_sha[:8]}; run {run_id} concluded '{conclusion}' at {url}"
        ),
    )

    if outcome == "released":
        return StageResult(
            "succeeded",
            confirmation=confirmation,
            release=record,
            detail={"run_id": run_id, "url": url},
            message=f"Released via {action['workflow']} (run {run_id})",
        )

    return StageResult(
        "failed",
        classification="check_failure",
        confirmation=confirmation,
        release=record,
        detail={"run_id": run_id, "url": url, "conclusion": conclusion},
        message=(
            f"The release action {action['workflow']} failed ({conclusion}). "
            f"{integration_branch} is merged and now ahead of production. See {url}"
        ),
    )


def _execute_release(*, client, action, merge_sha, confirmation) -> StageResult:
    spec = action["release"]
    tag = spec.get("tag")

    if not tag:
        # tag_from strategies other than 'explicit' need repository knowledge
        # this tool does not have. Refusing beats inventing a version number.
        return StageResult(
            "failed",
            classification="precondition",
            confirmation=confirmation,
            detail={"action": action},
            message=(
                f"release.action.release declares tag_from={spec.get('tag_from')!r} "
                "but no explicit tag. This tool does not compose a version number."
            ),
        )

    created = client.create_release(
        tag, spec.get("notes", ""), generate_notes=spec.get("generate_notes", True)
    )

    if not created.ok:
        return StageResult(
            "failed",
            classification="precondition",
            confirmation=confirmation,
            release=make_release_record(
                mode="executed",
                from_merge_sha=merge_sha,
                outcome="failed",
                identifier=tag,
                evidence=f"`gh release create {tag}` failed: {created.reason}",
            ),
            detail={"tag": tag, "error": created.reason},
            message=f"Could not create release {tag}: {created.reason}",
        )

    return StageResult(
        "succeeded",
        confirmation=confirmation,
        release=make_release_record(
            mode="executed",
            from_merge_sha=merge_sha,
            outcome="released",
            identifier=tag,
            evidence=(
                f"created release {tag} for merge commit {merge_sha[:8]} at "
                f"{created.value.get('url')}"
            ),
        ),
        detail={"tag": tag, "url": created.value.get("url")},
        message=f"Created release {tag}",
    )


def _execute_script(*, action, merge_sha, confirmation, repo_root) -> StageResult:
    import subprocess
    from pathlib import Path

    if repo_root is None:
        return StageResult(
            "failed",
            classification="precondition",
            confirmation=confirmation,
            detail={"action": action},
            message="Cannot run a release script without a repository root.",
        )

    script = Path(repo_root) / action["script"]
    if not script.is_file():
        return StageResult(
            "failed",
            classification="precondition",
            confirmation=confirmation,
            detail={"action": action},
            message=f"The declared release script {action['script']} does not exist.",
        )

    proc = subprocess.run(
        [str(script)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )

    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-5:]
    excerpt = " / ".join(tail) if tail else "no output"
    outcome = "released" if proc.returncode == 0 else "failed"

    record = make_release_record(
        mode="executed",
        from_merge_sha=merge_sha,
        outcome=outcome,
        identifier=action["script"],
        evidence=(
            f"ran {action['script']} for merge commit {merge_sha[:8]}; exited "
            f"{proc.returncode}. Output: {excerpt}"
        ),
    )

    if outcome == "released":
        return StageResult(
            "succeeded",
            confirmation=confirmation,
            release=record,
            detail={"script": action["script"], "returncode": 0},
            message=f"Released via {action['script']}",
        )

    return StageResult(
        "failed",
        classification="check_failure",
        confirmation=confirmation,
        release=record,
        detail={"script": action["script"], "returncode": proc.returncode, "output": excerpt},
        message=(
            f"The release script {action['script']} exited {proc.returncode}. "
            "The merge landed, so the integration branch is ahead of production."
        ),
    )


# --------------------------------------------------------------------------
# Dispatch (T050)
# --------------------------------------------------------------------------


def run(
    *,
    client,
    mode: Optional[str],
    merge_sha: Optional[str],
    integration_branch: str,
    confirmation: Optional[Dict[str, Any]],
    deadline: float,
    action: Optional[Dict[str, Any]] = None,
    workflow_pin: Optional[str] = None,
    repo_root=None,
    dry_run: bool = False,
    on_progress: Optional[Callable[[str, float], None]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> StageResult:
    """Enforce the release preconditions, then dispatch to the right mode.

    The ``merge_sha`` guard is repeated here even though the engine's transition
    guard already checks it. That redundancy is deliberate: this is the stage
    that writes the record nothing else may write, so it does not rely on a
    caller having checked.
    """
    if dry_run:
        return StageResult(
            "skipped",
            reason="dry-run: no release was performed because this is a dry run",
            detail={"mode": mode},
        )

    if mode == "none":
        return StageResult(
            "skipped",
            reason=(
                "release-mode-none: this repository is configured with no release "
                "path, so there is nothing to release"
            ),
            detail={"mode": mode},
        )

    if not merge_sha:
        return StageResult(
            "undetermined",
            reason=(
                "no-merge-commit: the release stage was reached without a merge "
                "commit SHA. A release cannot be attributed to a merge without "
                "one, and attributing it by time instead is what FR-015 forbids."
            ),
            detail={"mode": mode},
        )

    if confirmation is None:
        return StageResult(
            "undetermined",
            reason=(
                "release-not-confirmed: no confirmation was granted for the "
                "release on this run"
            ),
            detail={"mode": mode},
        )

    if mode == "observed":
        return observe(
            client=client,
            merge_sha=merge_sha,
            integration_branch=integration_branch,
            deadline=deadline,
            confirmation=confirmation,
            workflow_pin=workflow_pin,
            on_progress=on_progress,
            sleeper=sleeper,
            clock=clock,
        )

    if mode == "executed":
        if not action:
            return StageResult(
                "failed",
                classification="precondition",
                confirmation=confirmation,
                detail={"mode": mode},
                message=(
                    "Release mode is 'executed' but the repository declares no "
                    "release.action. This tool never composes one."
                ),
            )
        return execute(
            client=client,
            action=action,
            merge_sha=merge_sha,
            integration_branch=integration_branch,
            confirmation=confirmation,
            deadline=deadline,
            repo_root=repo_root,
            sleeper=sleeper,
            clock=clock,
        )

    return StageResult(
        "undetermined",
        reason=(
            f"release-mode-undetermined: the release mode for this repository is "
            f"{mode!r}, so the run does not know what releasing means here. "
            "Set release.mode, or answer the preflight prompt."
        ),
        detail={"mode": mode},
    )
