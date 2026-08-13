#!/usr/bin/env python3
"""CLI entry point for the ship pipeline.

The exit-code contract (contracts/commands.md)::

     0  run complete — merged, released (or release explicitly none), cleaned up
    10  refused at preflight; nothing was changed
    20  halted on a classified failure; branch and PR left intact
    30  halted on an undetermined outcome — checks or release never resolved
    40  refused; another run holds the lock

**20 and 30 are deliberately distinct.** "It failed" and "we do not know" are
different answers, and a caller that collapses them re-creates the exact
inference this tool exists to prevent: an unresolved pipeline read as a red one
is merely wrong, but read as a green one it merges.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow `python3 .specify/extensions/ship/scripts/ship.py` to resolve
# `scripts.*` imports without requiring PYTHONPATH to be set by the caller.
_EXTENSION_ROOT = Path(__file__).resolve().parent.parent
if str(_EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXTENSION_ROOT))

from scripts import config as config_mod  # noqa: E402
from scripts import gitops, pipeline, preflight  # noqa: E402
from scripts import state as state_mod  # noqa: E402
from scripts.hosting import GhClient  # noqa: E402

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_REFUSED = 10
EXIT_FAILED = 20
EXIT_UNDETERMINED = 30
EXIT_LOCKED = 40


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_determined(label: str, wrapped: Optional[Dict[str, Any]], width: int = 22) -> str:
    """One profile line.

    An undetermined value renders as *undetermined, with its reason* — never as
    a blank, a dash, or a plausible default. That substitution is the defect
    this whole feature is organized against (FR-032, SC-007).
    """
    pad = label.ljust(width)

    if wrapped is None:
        return f"  {pad}undetermined (not observed in this run)"

    if wrapped.get("determined") is True:
        value = wrapped.get("value")
        if isinstance(value, dict):
            rendered = ", ".join(f"{k}={v}" for k, v in value.items() if not isinstance(v, dict))
        elif isinstance(value, bool):
            rendered = "yes" if value else "no"
        else:
            rendered = str(value)
        return f"  {pad}{rendered}   [{wrapped.get('source')} @ {wrapped.get('captured_at')}]"

    return f"  {pad}undetermined — {wrapped.get('reason')}"


def render_profile(profile: preflight.Profile, *, config: Dict[str, Any]) -> str:
    lines = ["Repository profile", ""]
    lines.append(render_determined("repository", profile.is_repository))
    lines.append(render_determined("root", profile.root))
    lines.append(render_determined("remote", profile.remote))
    lines.append(render_determined("integration branch", profile.integration_branch))

    if profile.integration_branch_candidates:
        lines.append(
            "  candidates            "
            + ", ".join(profile.integration_branch_candidates)
        )

    lines.append(render_determined("hosting", profile.hosting))
    lines.append(render_determined("checks configured", profile.has_checks))
    lines.append(render_determined("release mode", profile.release_mode))
    if profile.release_evidence:
        lines.append(f"  release evidence      {profile.release_evidence}")
    lines.append(render_determined("multi-target", profile.multi_target))

    if profile.current_branch:
        lines.append(f"  current branch        {profile.current_branch}")

    lines.append("")
    lines.append("Configuration")
    lines.append("")
    lines.append(f"  pr.composition        {config['pr']['composition']}")
    lines.append(f"  pr.merge_method       {config['pr']['merge_method']}")
    lines.append(f"  limits.checks_wait    {config['limits']['checks_wait_seconds']}s")
    lines.append(f"  limits.release_wait   {config['limits']['release_wait_seconds']}s")
    lines.append(f"  limits.repair_budget  {config['limits']['repair_budget']}")
    lines.append(f"  cleanup.delete_branch {config['cleanup']['delete_branch']}")

    return "\n".join(lines)


def render_halt(outcome, *, branch: str, target: str) -> str:
    """The halt report. (T066, SC-004)

    The measure is specific: a developer must be able to name the failing stage
    and the cause **from this text alone**, without opening github.com. So the
    exit code is spelled out rather than left as a number, and the distinction
    between "it failed" and "we do not know" is stated in words — a reader who
    only skims should not come away thinking an unresolved pipeline was a red
    one.
    """
    meaning = {
        EXIT_OK: "Complete.",
        EXIT_REFUSED: "Refused before anything changed.",
        EXIT_FAILED: "Halted on a failure we can name.",
        EXIT_UNDETERMINED: (
            "Halted on an outcome we could NOT determine. This is not a failure "
            "— it means the run does not know, and it did not guess."
        ),
        EXIT_LOCKED: "Refused — another run holds the lock.",
    }.get(outcome.exit_code, "Halted.")

    lines = ["", "─" * 72, f"exit {outcome.exit_code} — {meaning}", "─" * 72, ""]
    lines.append(outcome.message)
    lines.append("")

    if outcome.exit_code in (EXIT_FAILED, EXIT_UNDETERMINED):
        lines.append(f"The branch {branch!r} and its pull request are intact.")
        lines.append(f"Nothing was merged into {target!r}, and nothing was rolled back.")
        lines.append("Re-run /speckit-ship to resume from here once the cause is addressed.")

    return "\n".join(lines)


def render_refusals(refusals: List[preflight.Refusal]) -> str:
    lines = []
    for refusal in refusals:
        lines.append(refusal.render())
    lines.append("")
    lines.append("Nothing was changed.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def build_profile(
    repo_root: Path,
    *,
    config: Dict[str, Any],
    client=None,
    target_override: Optional[str] = None,
    allow_network: bool = True,
) -> preflight.Profile:
    """Run detection in dependency order, stopping at the first hard refusal.

    Ordered so nothing expensive or network-bound runs after a condition that
    already blocks the run.
    """
    profile = preflight.Profile()

    if not preflight.detect_repository(profile, cwd=repo_root):
        return profile

    if not preflight.detect_remote(
        profile, configured=config.get("remote", "origin"), cwd=repo_root
    ):
        return profile

    remote_name = state_mod.value_of(profile.remote, {}).get("name", "origin")

    if client is not None:
        preflight.check_hosting(profile, client)

    integration = target_override or config.get("target_branch")
    branch = preflight.detect_integration_branch(
        profile,
        remote=remote_name,
        configured=integration,
        client=client,
        cwd=repo_root,
        allow_network=allow_network,
    )

    preflight.check_working_copy(profile, integration_branch=branch, cwd=repo_root)

    root_path = Path(state_mod.value_of(profile.root) or repo_root)
    preflight.detect_has_checks(profile, root_path)
    preflight.detect_release_mode(
        profile,
        root_path,
        integration_branch=branch,
        configured_mode=(config.get("release") or {}).get("mode"),
    )
    preflight.detect_multi_target(profile, root_path, integration_branch=branch)

    if state_mod.value_of(profile.multi_target) is True:
        profile.refusals.append(
            preflight.Refusal(
                "multi-target",
                "this repository's integration branch feeds more than one "
                "independent release target, which this tool does not support. "
                "It will not partially release.",
                expected="a single release target per integration branch",
            )
        )

    return profile


def record_profile(repo_root: Path, profile: preflight.Profile) -> None:
    """Persist the profile. Never aborts the run if state cannot be written."""

    def mutate(document: Dict[str, Any]) -> None:
        document["profile"] = profile.to_state()

    try:
        state_mod.update(repo_root, mutate)
    except Exception as exc:  # noqa: BLE001 - FR-029: never abort over state
        print(f"[ship] Warning: could not record the repository profile ({exc}).", file=sys.stderr)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def resolve_root(explicit: Optional[Path]) -> Path:
    if explicit:
        return explicit.resolve()
    result = gitops.root()
    if result.ok:
        return Path(result.text)
    return Path.cwd()


def cmd_preflight(args: argparse.Namespace) -> int:
    repo_root = resolve_root(args.root)
    loaded = config_mod.load(repo_root)
    if loaded.degraded:
        print(f"[ship] {loaded.message}", file=sys.stderr)

    client = None if args.no_network else GhClient(cwd=str(repo_root))
    profile = build_profile(
        repo_root,
        config=loaded.config,
        client=client,
        target_override=args.target,
        allow_network=not args.no_network,
    )

    if args.json:
        print(json.dumps(profile.to_state(), indent=2))
    else:
        print(render_profile(profile, config=loaded.config))

    if profile.blocked:
        if not args.json:
            print()
            print(render_refusals(profile.refusals))
        return EXIT_REFUSED

    # Deliberately does NOT record the profile. contracts/commands.md specifies
    # this command as "changes nothing", and a state file appearing in a
    # repository because someone asked a read-only question is a change — one
    # that also creates directories in a repository the developer may only have
    # been inspecting. The profile is recorded by a real ship run instead.
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = resolve_root(args.root)
    loaded = state_mod.load(repo_root)

    if args.json:
        print(json.dumps(loaded.document, indent=2))
        return EXIT_OK

    if loaded.degraded:
        print(f"[ship] {loaded.message}")
        print()

    runs = loaded.document.get("runs", [])
    if not runs:
        # An explicit empty state, distinguishable from a zero (FR-033).
        print("No ship runs recorded for this repository.")
        return EXIT_OK

    for run in runs[-args.limit :]:
        print(f"{run['run_id']}  {run['branch']} → {run['target_branch']}  [{run['status']}]")
        for stage in run.get("stages", []):
            detail = ""
            if stage.get("reason"):
                detail = f" — {stage['reason']}"
            elif stage.get("classification"):
                detail = f" — {stage['classification']}"
            print(f"    {stage['stage']:<13} {stage['outcome']}{detail}")
        if run.get("halt_reason"):
            halt = run["halt_reason"]
            print(f"    halted at {halt['stage']}: {halt['classification']} — {halt['message']}")
        print()

    return EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    repo_root = resolve_root(args.root)
    loaded = config_mod.load(repo_root)

    if args.config_action == "show":
        print(json.dumps(loaded.config, indent=2))
        return EXIT_OK

    if args.config_action == "validate":
        problems = config_mod.validate_config(
            loaded.config,
            known_remotes=gitops.remotes(cwd=repo_root) or None,
        )
        if problems:
            print("Configuration is invalid:")
            for problem in problems:
                print(f"  - {problem}")
            return EXIT_FAILED
        print("Configuration is valid.")
        return EXIT_OK

    return EXIT_FAILED


# --------------------------------------------------------------------------
# Argument parsing (T026)
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ship",
        description="Ship a branch: commit, publish, PR, checks, merge, release, cleanup.",
    )
    parser.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--version",
        action="version",
        version=f"speckit-ship {VERSION}",
        help="Print the extension version and exit",
    )

    sub = parser.add_subparsers(dest="command")

    # Default (no subcommand) is the ship run itself.
    for target in (parser,):
        target.add_argument("--target", default=None, help="Override the target branch for this run only")
        target.add_argument("--dry-run", action="store_true", help="Preflight and print intended actions; change nothing")
        target.add_argument(
            "--yes",
            action="store_true",
            help="Per-run unattended authorization for the merge and release gates. "
            "THIS RUN ONLY — there is no persistable equivalent.",
        )
        target.add_argument("--from", dest="from_stage", default=None, help="Re-enter at a named stage")
        target.add_argument("--no-network", action="store_true", help=argparse.SUPPRESS)

    pre = sub.add_parser("preflight", help="Report the repository profile; change nothing")
    pre.add_argument("--target", default=None)
    pre.add_argument("--json", action="store_true")
    pre.add_argument("--no-network", action="store_true")
    pre.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    pre.set_defaults(func=cmd_preflight)

    status = sub.add_parser("status", help="Render recorded ship state; read-only")
    status.add_argument("--json", action="store_true")
    status.add_argument("--limit", type=int, default=5)
    status.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    status.set_defaults(func=cmd_status)

    cfg = sub.add_parser("config", help="Show, set, and validate ship configuration")
    cfg.add_argument("config_action", choices=["show", "validate"], default="show", nargs="?")
    cfg.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    cfg.set_defaults(func=cmd_config)

    return parser


def render_intent(profile: preflight.Profile, *, branch: str, target: str, config: Dict[str, Any]) -> str:
    """The actions this run intends to take, before the first one happens (FR-006).

    Printed unconditionally — not only under ``--dry-run``. A developer should
    never learn what the tool was going to do by watching it do it.
    """
    mode = state_mod.value_of(profile.release_mode)
    lines = [
        "",
        "Intended actions",
        "",
        f"  1. commit        outstanding work on {branch} (skipped if the tree is clean)",
        f"  2. publish       {branch} → {config.get('remote', 'origin')}",
        f"  3. pull_request  {branch} → {target}  (adopting an existing PR if one is open)",
        f"  4. checks        wait up to {config['limits']['checks_wait_seconds']}s for a terminal outcome",
        f"  5. merge         {config['pr']['merge_method']} — REQUIRES YOUR CONFIRMATION",
    ]

    if mode == "observed":
        lines.append(
            f"  6. release       watch this repository's own release path "
            f"(up to {config['limits']['release_wait_seconds']}s) — REQUIRES YOUR CONFIRMATION"
        )
    elif mode == "executed":
        lines.append("  6. release       run the repository's declared release action — REQUIRES YOUR CONFIRMATION")
    elif mode == "none":
        lines.append("  6. release       skipped — this repository has no release path")
    else:
        lines.append("  6. release       UNDETERMINED — the release mode is not established; the run will stop here")

    delete = config.get("cleanup", {}).get("delete_branch", True)
    lines.append(
        f"  7. cleanup       {'delete ' + branch + ' locally and remotely, then ' if delete else ''}"
        f"switch to {target} and update it"
    )
    return "\n".join(lines)


def make_interaction(args: argparse.Namespace) -> pipeline.Interaction:
    """Wire the confirmation seams to the terminal.

    With ``--yes`` the gates are granted for this run only. Without it, and
    without a TTY, the gates return None and the run stops before merging —
    failing closed, because the alternative is an unattended merge nobody asked
    for.
    """
    if args.yes:
        return pipeline.Interaction(
            confirm_commit=lambda text: (print(text), True)[1],
            review_pr=lambda composed: {"title": composed["title"], "body": composed["body"]},
            confirm_gate=lambda stage, prompt: state_mod.make_confirmation(
                granted_by="--yes", prompt=prompt
            ),
            report=lambda message: print(message),
        )

    interactive = sys.stdin.isatty()

    def ask(question: str) -> bool:
        if not interactive:
            return False
        try:
            return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    def confirm_gate(stage: str, prompt: str) -> Optional[Dict[str, Any]]:
        if not interactive:
            print(
                f"\n{stage}: {prompt}\n"
                "  No terminal is attached, so this cannot be confirmed. "
                "Re-run with --yes to authorize this run unattended.",
                file=sys.stderr,
            )
            return None
        if not ask(f"\n{prompt}"):
            return None
        return state_mod.make_confirmation(granted_by="developer", prompt=prompt)

    def review_pr(composed: Dict[str, Any]) -> Optional[Dict[str, str]]:
        print("\nPull request description:")
        print(f"\n  {composed['title']}\n")
        for line in (composed.get("body") or "").splitlines():
            print(f"  {line}")
        if composed.get("note"):
            print(f"\n  note: {composed['note']}")
        if not interactive:
            return {"title": composed["title"], "body": composed["body"]}
        if not ask("\nOpen the pull request with this description?"):
            return None
        return {"title": composed["title"], "body": composed["body"]}

    return pipeline.Interaction(
        confirm_commit=lambda text: (print(f"\n{text}"), ask("\nCommit these changes?"))[1],
        review_pr=review_pr,
        confirm_gate=confirm_gate,
        report=lambda message: print(message),
    )


def cmd_ship(args: argparse.Namespace) -> int:
    repo_root = resolve_root(args.root)
    loaded = config_mod.load(repo_root)
    if loaded.degraded:
        print(f"[ship] {loaded.message}", file=sys.stderr)

    config = loaded.config
    client = None if args.no_network else GhClient(cwd=str(repo_root))

    profile = build_profile(
        repo_root,
        config=config,
        client=client,
        target_override=args.target,
        allow_network=not args.no_network,
    )

    print(render_profile(profile, config=config))

    if profile.blocked:
        print()
        print(render_refusals(profile.refusals))
        return EXIT_REFUSED

    target = state_mod.value_of(profile.integration_branch)
    branch = profile.current_branch

    if not target:
        print()
        print(
            "Refusing to ship: the integration branch could not be determined.\n"
            "  "
            + (profile.integration_branch or {}).get("reason", "")
            + (
                "\n  Candidates found: " + ", ".join(profile.integration_branch_candidates)
                if profile.integration_branch_candidates
                else ""
            )
            + "\n  Set it with: ship config, or pass --target <branch>.\n\nNothing was changed."
        )
        return EXIT_REFUSED

    print(render_intent(profile, branch=branch, target=target, config=config))

    if args.dry_run:
        print("\nDry run — nothing was changed.")
        return EXIT_OK

    print()
    outcome = pipeline.execute(
        repo_root,
        profile=profile,
        config=config,
        client=client,
        interaction=make_interaction(args),
        branch=branch,
        target=target,
        from_stage=args.from_stage,
    )

    if outcome.exit_code == EXIT_OK:
        print()
        print(outcome.message)
    else:
        print(render_halt(outcome, branch=branch, target=target))
    return outcome.exit_code


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "func", None) is not None:
        return args.func(args)

    return cmd_ship(args)


if __name__ == "__main__":
    raise SystemExit(main())
