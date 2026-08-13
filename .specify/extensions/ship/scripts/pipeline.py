"""Drives one ship run across the eight stages.

Kept separate from ``ship.py`` (which owns argument parsing and rendering) so the
orchestration can be tested without going through a CLI.

The resumption logic here is the part worth reading closely. A recorded stage is
**re-verified against the world** before it is trusted (research.md R8): the
developer may have merged the pull request in the web UI, closed it, or
force-pushed the branch between runs. Trusting the journal alone would let a run
skip a stage that never really happened — or repeat one that did, which for
``merge`` and ``release`` means a duplicate outward-facing action.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from scripts import config as config_mod
from scripts import engine, gitops, lock as lock_mod
from scripts import state as state_mod
from scripts.engine import GuardError, NothingToShip, StageResult
from scripts.stages import checks as checks_stage
from scripts.stages import cleanup as cleanup_stage
from scripts.stages import commit as commit_stage
from scripts.stages import merge as merge_stage
from scripts.stages import publish as publish_stage
from scripts.stages import pull_request as pr_stage
from scripts.stages import release as release_stage

EXIT_OK = 0
EXIT_REFUSED = 10
EXIT_FAILED = 20
EXIT_UNDETERMINED = 30
EXIT_LOCKED = 40


class Interaction:
    """The seams where a human (or the command markdown) is consulted.

    Defaults are deliberately conservative: with no interaction supplied, the
    confirmation gates return None — meaning *not granted* — so an embedding
    that forgets to wire them cannot merge or release. Failing closed is the
    only safe default for an irreversible action.
    """

    def __init__(
        self,
        *,
        confirm_commit: Optional[Callable[[str], bool]] = None,
        review_pr: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, str]]]] = None,
        draft_pr: Optional[Callable[[Dict[str, Any]], Dict[str, str]]] = None,
        confirm_gate: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
        report: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.confirm_commit = confirm_commit
        self.review_pr = review_pr
        self.draft_pr = draft_pr
        self._confirm_gate = confirm_gate
        self.report = report or (lambda _message: None)

    def gate(self, stage: str, prompt: str) -> Optional[Dict[str, Any]]:
        """Ask for a per-run confirmation. None means not granted."""
        if self._confirm_gate is None:
            return None
        return self._confirm_gate(stage, prompt)


def unattended(granted_by: str = "--yes") -> Interaction:
    """The ``--yes`` interaction: authorization for **this run only**.

    There is no persistable equivalent by design, and this function is not it —
    it produces a fresh confirmation per gate, per invocation, and nothing it
    creates outlives the process.
    """
    return Interaction(
        confirm_commit=lambda _text: True,
        review_pr=lambda composed: {"title": composed["title"], "body": composed["body"]},
        confirm_gate=lambda stage, prompt: state_mod.make_confirmation(
            granted_by=granted_by, prompt=prompt
        ),
    )


# --------------------------------------------------------------------------
# Re-verification (T052, research R8)
# --------------------------------------------------------------------------


def make_reverifier(client, cwd: Path, *, remote: str) -> Callable[[str, Dict[str, Any]], bool]:
    """Build the callable ``engine.resume_point`` uses to check the journal.

    Returns True when the recorded outcome is still true in the world. Only the
    stages whose recorded state can be invalidated between runs are checked; the
    rest are taken as recorded, because nothing outside this tool can undo them.

    An *unreadable* world is treated as still-valid rather than invalid. Rewinding
    on a network hiccup would re-run a stage that genuinely succeeded, and for
    ``publish`` or ``pull_request`` that means duplicate outward effects.
    """

    def reverify(stage: str, run: Dict[str, Any]) -> bool:
        if stage == "publish":
            branch = run["branch"]
            result = gitops.run(
                ["ls-remote", "--heads", remote, f"refs/heads/{branch}"], cwd=cwd
            )
            if not result.ok:
                return True  # could not check; do not rewind on ignorance
            return bool(result.text)

        if stage == "pull_request":
            pr = run.get("pr")
            if not pr:
                return False
            view = client.find_pr(run["branch"])
            if not view.ok:
                return True
            current = view.value
            if current is None:
                # The PR we recorded is gone. Rewinding re-opens one rather than
                # proceeding to merge a pull request that no longer exists.
                return False
            return current.get("state") == "OPEN" or current.get("state") == "MERGED"

        if stage == "merge":
            pr = run.get("pr")
            if not pr:
                return True
            view = client.pr_view(pr["number"])
            if not view.ok:
                return True
            return view.value.get("state") == "MERGED"

        return True

    return reverify


def adopt_external_merge(client, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect a merge that happened outside this tool, between runs.

    The developer merging in the web UI is common and entirely legitimate. The
    run must notice, record the merge commit, and continue to release — not
    attempt a second merge (SC-008).
    """
    pr = run.get("pr")
    if not pr:
        return None

    view = client.pr_view(pr["number"])
    if not view.ok or view.value.get("state") != "MERGED":
        return None

    return {
        "merge_commit_sha": view.value.get("merge_commit_sha"),
        "state": "MERGED",
        "url": view.value.get("url"),
    }


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


class RunOutcome:
    def __init__(self, exit_code: int, message: str, *, run_id: Optional[str] = None) -> None:
        self.exit_code = exit_code
        self.message = message
        self.run_id = run_id


def execute(
    repo_root: Path,
    *,
    profile,
    config: Dict[str, Any],
    client,
    interaction: Interaction,
    branch: str,
    target: str,
    dry_run: bool = False,
    from_stage: Optional[str] = None,
    commit_message: Optional[str] = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> RunOutcome:
    """Carry one branch through the pipeline, resuming an in-progress run."""
    remote = config.get("remote", "origin")
    limits = config.get("limits", {})
    report = interaction.report

    loaded = state_mod.load(repo_root)
    if loaded.degraded:
        report(f"[ship] {loaded.message}")

    existing = state_mod.latest_run_for_branch(loaded.document, branch)
    # A *halted* run is resumable too, not only an in-progress one. Halting is
    # how this tool stops on a red pipeline or an undetermined outcome, and
    # FR-021's whole point is that re-issuing the command continues that run
    # rather than starting a parallel history. Treating only `in_progress` as
    # resumable meant every interrupted run came back as a second run_id — and a
    # second run has no memory of the pull request the first one opened.
    resuming = existing is not None and existing.get("status") != "complete"

    if resuming:
        run_id = existing["run_id"]
        report(f"Resuming run {run_id} for {branch}.")
    else:
        run_id = state_mod.make_run_id(branch)

    # -- lock (FR-022) ---------------------------------------------------
    acquisition = lock_mod.acquire(repo_root, branch=branch, run_id=run_id)
    if not acquisition.acquired:
        return RunOutcome(EXIT_LOCKED, acquisition.message)
    if acquisition.message:
        report(f"[ship] {acquisition.message}")

    try:
        return _execute_locked(
            repo_root,
            profile=profile,
            config=config,
            client=client,
            interaction=interaction,
            branch=branch,
            target=target,
            remote=remote,
            limits=limits,
            run_id=run_id,
            resuming=resuming,
            dry_run=dry_run,
            from_stage=from_stage,
            commit_message=commit_message,
            clock=clock,
            sleeper=sleeper,
        )
    finally:
        lock_mod.release(repo_root, run_id=run_id)


def _execute_locked(
    repo_root: Path,
    *,
    profile,
    config,
    client,
    interaction: Interaction,
    branch,
    target,
    remote,
    limits,
    run_id,
    resuming,
    dry_run,
    from_stage,
    commit_message,
    clock,
    sleeper,
) -> RunOutcome:
    report = interaction.report

    # -- create or adopt the run record ----------------------------------
    def ensure_run(document: Dict[str, Any]) -> None:
        document["profile"] = profile.to_state()
        if state_mod.find_run(document, run_id) is None:
            document.setdefault("runs", []).append(
                state_mod.new_run(branch=branch, target_branch=target, run_id=run_id)
            )

    state_mod.update(repo_root, ensure_run)

    loaded = state_mod.load(repo_root)
    run = state_mod.find_run(loaded.document, run_id)

    # -- preflight is already done by the caller; record it ---------------
    if state_mod.stage_outcome(run, "preflight") not in engine.SETTLED:
        engine.run_stage(
            repo_root,
            run_id,
            "preflight",
            lambda: StageResult(
                "succeeded",
                detail={
                    "integration_branch": target,
                    "source": (profile.integration_branch or {}).get("source"),
                },
            ),
            run=run,
        )
        run = state_mod.find_run(state_mod.load(repo_root).document, run_id)

    # -- decide where to re-enter ----------------------------------------
    if from_stage:
        entry = from_stage
    elif resuming:
        reverifier = make_reverifier(client, repo_root, remote=remote)
        entry = engine.resume_point(run, reverifier=reverifier)
        report(f"Re-entering at {entry} after re-verifying recorded state.")
    else:
        entry = engine.next_stage(run) or "commit"

    order = list(state_mod.STAGES)
    start = order.index(entry) if entry in order else 1

    # An externally merged PR is adopted rather than re-merged (SC-008).
    if resuming and start <= order.index("merge"):
        adopted = adopt_external_merge(client, run)
        if adopted and adopted.get("merge_commit_sha"):
            report(
                f"#{run['pr']['number']} was merged outside this run "
                f"({adopted['merge_commit_sha'][:8]}); adopting it and continuing "
                "to the release stage."
            )

    for stage in order[start:]:
        outcome = _run_one(
            stage,
            repo_root=repo_root,
            run_id=run_id,
            client=client,
            interaction=interaction,
            config=config,
            profile=profile,
            branch=branch,
            target=target,
            remote=remote,
            limits=limits,
            dry_run=dry_run,
            commit_message=commit_message,
            clock=clock,
            sleeper=sleeper,
        )

        if outcome is not None:
            return outcome

    def finish(document: Dict[str, Any]) -> None:
        state_mod.set_complete(state_mod.find_run(document, run_id))

    state_mod.update(repo_root, finish)
    return RunOutcome(EXIT_OK, f"Run {run_id} complete.", run_id=run_id)


def _run_one(
    stage: str,
    *,
    repo_root,
    run_id,
    client,
    interaction: Interaction,
    config,
    profile,
    branch,
    target,
    remote,
    limits,
    dry_run,
    commit_message,
    clock,
    sleeper,
) -> Optional[RunOutcome]:
    """Execute one stage. Returns a RunOutcome to stop, or None to continue."""
    report = interaction.report
    document = state_mod.load(repo_root).document
    run = state_mod.find_run(document, run_id)
    confirmation: Optional[Dict[str, Any]] = None

    # -- build the stage callable ----------------------------------------
    if stage == "commit":
        fn = lambda: commit_stage.run(  # noqa: E731
            repo_root,
            message=commit_message or f"ship: {branch}",
            confirm=interaction.confirm_commit,
            dry_run=dry_run,
        )

    elif stage == "publish":
        # Acceptance 1.4 — stop before opening a PR when there is nothing to ship.
        comparison = gitops.ahead_behind(branch, f"{remote}/{target}", cwd=repo_root)
        try:
            engine.assert_something_to_ship(
                ahead=comparison.ahead,
                branch=branch,
                target=target,
                tree_dirty=not gitops.is_clean(cwd=repo_root),
            )
        except NothingToShip as exc:
            return RunOutcome(EXIT_REFUSED, str(exc), run_id=run_id)

        fn = lambda: publish_stage.run(  # noqa: E731
            repo_root, remote=remote, branch=branch, dry_run=dry_run
        )

    elif stage == "pull_request":
        pr_config = config.get("pr", {})
        fn = lambda: pr_stage.run(  # noqa: E731
            repo_root,
            client=client,
            branch=branch,
            base=target,
            composition=pr_config.get("composition", "commits"),
            remote=remote,
            title_template=pr_config.get("title_template"),
            draft=pr_config.get("draft", False),
            drafter=interaction.draft_pr,
            review=interaction.review_pr,
            dry_run=dry_run,
        )

    elif stage == "checks":
        pr = run.get("pr")
        if not pr:
            fn = lambda: StageResult(  # noqa: E731
                "skipped",
                reason="no-pull-request: no pull request was opened, so there are no checks to read",
            )
        else:
            deadline = clock() + limits.get("checks_wait_seconds", 1800)

            def run_checks():
                def progress(outcome, remaining):
                    report(
                        f"  checks: {outcome.summary()} "
                        f"({int(max(0, remaining))}s of wait remaining)"
                    )

                result = checks_stage.poll(
                    client,
                    pr["number"],
                    branch=target,
                    deadline=deadline,
                    on_progress=progress,
                    sleeper=sleeper,
                    clock=clock,
                )
                if result.outcome == "failed":
                    checks_stage.attach_failing_logs(client, result)

                mapped = {
                    "passed": "succeeded",
                    "failed": "failed",
                    "undetermined": "undetermined",
                }[result.outcome]

                return StageResult(
                    mapped,
                    reason=result.reason,
                    classification="check_failure" if mapped == "failed" else None,
                    checks=result.checks,
                    detail={
                        "required_failures": result.required_failures,
                        "optional_failures": result.optional_failures,
                    },
                    message=result.summary(),
                )

            fn = run_checks

    elif stage == "merge":
        pr = run.get("pr")
        if not pr:
            fn = lambda: StageResult(  # noqa: E731
                "skipped", reason="no-pull-request: there is no pull request to merge"
            )
        elif dry_run:
            fn = lambda: StageResult(  # noqa: E731
                "skipped", reason="dry-run: the pull request was not merged"
            )
        else:
            confirmation = interaction.gate(
                "merge",
                f"Merge pull request #{pr['number']} ({branch} → {target}) "
                f"using {config['pr']['merge_method']}?",
            )
            if confirmation is None:
                return RunOutcome(
                    EXIT_REFUSED,
                    f"Merge of #{pr['number']} was not confirmed. Nothing was merged.",
                    run_id=run_id,
                )
            fn = lambda: merge_stage.run(  # noqa: E731
                client=client,
                pr_number=pr["number"],
                method=config["pr"]["merge_method"],
                confirmation=confirmation,
                delete_branch=False,  # cleanup owns deletion, after its own checks
                sleeper=sleeper,
            )

    elif stage == "release":
        mode = state_mod.value_of(profile.release_mode)
        merge_sha = run.get("merge_commit_sha")

        if dry_run:
            fn = lambda: StageResult(  # noqa: E731
                "skipped", reason="dry-run: no release was performed"
            )
        elif mode == "none":
            fn = lambda: StageResult(  # noqa: E731
                "skipped",
                reason="release-mode-none: this repository has no release path configured",
            )
        elif mode is None:
            fn = lambda: StageResult(  # noqa: E731
                "undetermined",
                reason=(
                    "release-mode-undetermined: this repository's release mode "
                    "could not be established, so the run does not know what "
                    "releasing means here. Set release.mode in ship configuration."
                ),
            )
        else:
            confirmation = interaction.gate(
                "release",
                f"Perform the {mode} release for merge commit "
                f"{(merge_sha or 'unknown')[:8]}?",
            )
            if confirmation is None:
                return RunOutcome(
                    EXIT_REFUSED,
                    "The release was not confirmed. The merge landed, so "
                    f"{target} may now be ahead of production.",
                    run_id=run_id,
                )
            deadline = clock() + limits.get("release_wait_seconds", 1800)
            release_config = config.get("release", {})
            fn = lambda: release_stage.run(  # noqa: E731
                client=client,
                mode=mode,
                merge_sha=merge_sha,
                integration_branch=target,
                confirmation=confirmation,
                deadline=deadline,
                action=release_config.get("action"),
                workflow_pin=release_config.get("observed_workflow"),
                repo_root=repo_root,
                on_progress=lambda text, remaining: report(f"  release: {text}"),
                sleeper=sleeper,
                clock=clock,
            )

    elif stage == "cleanup":
        cleanup_config = config.get("cleanup", {})
        fn = lambda: cleanup_stage.run(  # noqa: E731
            repo_root,
            branch=branch,
            target=target,
            remote=remote,
            delete_branch=cleanup_config.get("delete_branch", True),
            return_to_integration=cleanup_config.get("return_to_integration", True),
            dry_run=dry_run,
        )

    else:  # pragma: no cover - preflight handled above
        return None

    # -- execute ---------------------------------------------------------
    try:
        result = engine.run_stage(
            repo_root, run_id, stage, fn, confirmation=confirmation, run=run
        )
    except GuardError as exc:
        _halt(repo_root, run_id, stage, "precondition", str(exc))
        return RunOutcome(EXIT_FAILED, str(exc), run_id=run_id)

    if result.message:
        report(f"{stage}: {result.message}")

    # -- record side facts the later stages depend on ---------------------
    _persist_stage_facts(repo_root, run_id, stage, result)

    if result.outcome == "failed":
        _halt(repo_root, run_id, stage, result.classification or "precondition", result.message)
        return RunOutcome(EXIT_FAILED, result.message or f"{stage} failed.", run_id=run_id)

    if result.outcome == "undetermined":
        _halt_undetermined(repo_root, run_id)
        return RunOutcome(
            EXIT_UNDETERMINED,
            result.reason or f"{stage} did not resolve.",
            run_id=run_id,
        )

    if dry_run and stage == "publish":
        return RunOutcome(
            EXIT_OK,
            "Dry run complete — preflight and intended actions reported; nothing was changed.",
            run_id=run_id,
        )

    return None


def _persist_stage_facts(repo_root: Path, run_id: str, stage: str, result: StageResult) -> None:
    """Lift the facts later stages read out of a stage's detail onto the run."""
    detail = result.detail or {}

    def mutate(document: Dict[str, Any]) -> None:
        run = state_mod.find_run(document, run_id)
        if run is None:
            return
        if stage == "pull_request" and detail.get("pr"):
            run["pr"] = detail["pr"]
        if stage == "publish" and detail.get("head_sha"):
            run["head_sha"] = detail["head_sha"]
        if stage == "commit" and detail.get("sha"):
            run["head_sha"] = detail["sha"]
        if stage == "merge" and detail.get("merge_commit_sha"):
            run["merge_commit_sha"] = detail["merge_commit_sha"]

    state_mod.update(repo_root, mutate)


def _halt(repo_root: Path, run_id: str, stage: str, classification: str, message: str) -> None:
    def mutate(document: Dict[str, Any]) -> None:
        run = state_mod.find_run(document, run_id)
        if run is not None:
            state_mod.set_halted(
                run, classification=classification, message=message or "", stage=stage
            )

    state_mod.update(repo_root, mutate)


def _halt_undetermined(repo_root: Path, run_id: str) -> None:
    """An undetermined outcome halts the run but is not a classified failure.

    ``halt_reason.classification`` is deliberately not set from an undetermined
    outcome — the schema's four classifications describe failures, and calling an
    unresolved pipeline a ``check_failure`` would be the exact collapse that exit
    codes 20 and 30 exist to keep apart.
    """

    def mutate(document: Dict[str, Any]) -> None:
        run = state_mod.find_run(document, run_id)
        if run is not None:
            run["status"] = "halted"
            run["ended_at"] = state_mod.now_iso()

    state_mod.update(repo_root, mutate)
