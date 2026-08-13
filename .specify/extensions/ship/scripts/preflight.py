"""Establish ground truth before anything is touched.

This module answers "can I ship from here, and where would it go?" and it is
the most consequential code in the feature: the riskiest wrong answer the tool
can give is a wrong integration branch, and it is decided here.

The rule that shapes everything below: **no hardcoded ``main``/``master``
fallback anywhere** (FR-002). Every answer either comes from a system of record
and says which one, or is recorded as undetermined and asked about once.

This repository's own history is the argument for that. When this feature was
specified, ``git rev-parse --is-inside-work-tree`` here returned *fatal: not a
git repository* while ``.spec-context.json`` recorded ``"branch": "main"`` — a
value that looked right and had never been observed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts import gitops
from scripts.state import determined, now_iso, undetermined

# Workflow trigger shapes that mean "this repository releases when the
# integration branch moves" (research.md R6).
RELEASE_WORKFLOW_HINTS = ("release", "deploy", "publish", "cd", "ship")


class Refusal:
    """A named blocking condition. Exit 10, before any state changes."""

    def __init__(self, condition: str, message: str, *, expected: str = "") -> None:
        self.condition = condition
        self.message = message
        self.expected = expected

    def render(self) -> str:
        text = f"Refusing to ship: {self.message}"
        if self.expected:
            text += f"\n  Expected: {self.expected}"
        return text

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Refusal {self.condition}>"


class Profile:
    """What preflight established. A cache of observations, never an authority.

    Re-verified on every run (T076): a recorded profile tells you what was true
    last time, and the developer may have renamed a branch since.
    """

    def __init__(self) -> None:
        self.is_repository: Dict[str, Any] = undetermined("not-yet-observed: preflight has not run")
        self.root: Optional[Dict[str, Any]] = None
        self.remote: Optional[Dict[str, Any]] = None
        self.integration_branch: Optional[Dict[str, Any]] = None
        self.integration_branch_candidates: List[str] = []
        self.hosting: Optional[Dict[str, Any]] = None
        self.has_checks: Optional[Dict[str, Any]] = None
        self.release_mode: Optional[Dict[str, Any]] = None
        self.release_evidence: Optional[str] = None
        self.multi_target: Optional[Dict[str, Any]] = None
        self.current_branch: Optional[str] = None
        self.refusals: List[Refusal] = []
        self.verified_at: str = now_iso()

    def to_state(self) -> Dict[str, Any]:
        """The ``profile`` object as it is recorded in ``state.json``."""
        payload: Dict[str, Any] = {
            "is_repository": self.is_repository,
            "verified_at": self.verified_at,
        }
        for key in (
            "root",
            "remote",
            "integration_branch",
            "hosting",
            "has_checks",
            "release_mode",
            "multi_target",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.integration_branch_candidates:
            payload["integration_branch_candidates"] = self.integration_branch_candidates
        if self.release_evidence is not None:
            payload["release_evidence"] = self.release_evidence
        return payload

    @property
    def blocked(self) -> bool:
        return bool(self.refusals)


# --------------------------------------------------------------------------
# T020 — repository and remote
# --------------------------------------------------------------------------


def detect_repository(profile: Profile, cwd: Optional[Path] = None) -> bool:
    """FR-001. Returns False when the run must stop here."""
    inside = gitops.is_inside_work_tree(cwd=cwd)

    if inside.git_missing:
        profile.is_repository = undetermined(
            "git-not-installed: git is not installed or not on PATH, so whether "
            "this is a repository could not be established"
        )
        profile.refusals.append(
            Refusal(
                "git-missing",
                "git is not installed or not on PATH.",
                expected="git 2.20 or newer available as `git`",
            )
        )
        return False

    if not inside.ok or inside.text != "true":
        profile.is_repository = determined(False, "git-rev-parse")
        profile.refusals.append(
            Refusal(
                "not-a-repository",
                f"{Path(cwd or '.').resolve()} is not inside a git repository.",
                expected="a directory inside a git work tree",
            )
        )
        return False

    profile.is_repository = determined(True, "git-rev-parse")

    root_result = gitops.root(cwd=cwd)
    if root_result.ok:
        profile.root = determined(root_result.text, "git-rev-parse")
    else:
        profile.root = undetermined(
            f"root-unresolved: git could not report the work-tree root ({root_result.error})"
        )
    return True


def detect_remote(
    profile: Profile, *, configured: str = "origin", cwd: Optional[Path] = None
) -> bool:
    """FR-004's first half: a usable remote exists and we know its identity."""
    names = gitops.remotes(cwd=cwd)

    if not names:
        profile.remote = undetermined(
            "no-remote: this repository has no configured remote, so there is "
            "nowhere to publish the branch"
        )
        profile.refusals.append(
            Refusal(
                "no-remote",
                "this repository has no configured remote.",
                expected="a remote such as `origin` (`git remote add origin <url>`)",
            )
        )
        return False

    name = configured if configured in names else names[0]
    if configured not in names:
        profile.remote = undetermined(
            f"remote-not-configured: the configured remote {configured!r} does not "
            f"exist in this repository (found: {', '.join(names)})"
        )
        profile.refusals.append(
            Refusal(
                "remote-not-configured",
                f"the configured remote {configured!r} does not exist "
                f"(found: {', '.join(names)}).",
                expected=f"`{configured}` configured, or ship.remote set to one of the above",
            )
        )
        return False

    url_result = gitops.remote_url(name, cwd=cwd)
    url = url_result.text if url_result.ok else None
    host = _host_from_url(url) if url else None

    if url is None:
        profile.remote = undetermined(
            f"remote-url-unreadable: git could not report a URL for remote {name!r}"
        )
        return False

    profile.remote = determined({"name": name, "url": url, "host": host}, "git-remote-get-url")
    return True


def _host_from_url(url: str) -> Optional[str]:
    """Host from either SSH (``git@host:owner/repo``) or HTTPS form."""
    match = re.match(r"^[a-zA-Z0-9._-]+@([^:]+):", url)
    if match:
        return match.group(1)
    match = re.match(r"^[a-z]+://(?:[^@/]+@)?([^/:]+)", url)
    if match:
        return match.group(1)
    return None


# --------------------------------------------------------------------------
# T021 — the four-step integration-branch precedence (FR-002, FR-003)
# --------------------------------------------------------------------------


def detect_integration_branch(
    profile: Profile,
    *,
    remote: str,
    configured: Optional[str] = None,
    client=None,
    cwd: Optional[Path] = None,
    allow_network: bool = True,
) -> Optional[str]:
    """Resolve the integration branch, recording which source answered.

    Precedence, stopping at the first source that answers unambiguously:

    1. Saved ship configuration — a recorded human answer (FR-037/FR-041).
    2. ``git symbolic-ref refs/remotes/<remote>/HEAD`` — the local mirror.
    3. ``gh repo view --json defaultBranchRef`` — the service's own record.
    4. ``git remote show <remote>`` — a network re-derivation.

    Sources 2–4 are systems of record. A *name* is not, which is why there is no
    fifth step trying ``main`` and then ``master``: that step would answer
    confidently and wrongly in exactly the repositories this tool most needs to
    get right.

    When sources disagree or none answers, every candidate found is recorded in
    ``integration_branch_candidates`` and the caller asks once (FR-003).
    """
    candidates: List[tuple] = []  # (branch, source)

    if configured:
        profile.integration_branch = determined(configured, "config")
        return configured

    # 2 — local mirror of the remote's default.
    sym = gitops.symbolic_ref(remote, cwd=cwd)
    if sym.ok:
        name = gitops.parse_symbolic_ref(sym.text, remote)
        if name:
            candidates.append((name, "git-symbolic-ref"))

    # 3 — the hosting service's own record.
    if client is not None:
        default = client.default_branch()
        if default.ok and default.value:
            candidates.append((default.value, "gh-repo-view"))

    # 4 — network re-derivation, only when the cheaper sources were silent.
    if not candidates and allow_network:
        show = gitops.remote_show(remote, cwd=cwd)
        if show.ok:
            name = gitops.parse_remote_show_head(show.stdout)
            if name:
                candidates.append((name, "git-remote-show"))

    distinct = []
    for name, source in candidates:
        if name not in [c[0] for c in distinct]:
            distinct.append((name, source))

    if len(distinct) == 1:
        name, source = distinct[0]
        profile.integration_branch = determined(name, source)
        profile.integration_branch_candidates = []
        return name

    if len(distinct) > 1:
        # Disagreement between two systems of record is exactly the case where
        # picking one would be a guess wearing a source label.
        profile.integration_branch_candidates = [name for name, _ in distinct]
        profile.integration_branch = undetermined(
            "integration-branch-ambiguous: sources disagree on the integration "
            "branch ("
            + ", ".join(f"{name} per {source}" for name, source in distinct)
            + ")"
        )
        return None

    profile.integration_branch_candidates = _candidate_branches(remote, cwd=cwd)
    profile.integration_branch = undetermined(
        "integration-branch-undetermined: no source reported a default branch "
        f"for remote {remote!r}. Checked: git symbolic-ref, gh repo view, "
        "git remote show."
    )
    return None


def _candidate_branches(remote: str, *, cwd: Optional[Path] = None) -> List[str]:
    """Remote branches, offered as choices when detection could not decide.

    Deliberately not filtered down to likely-looking names: presenting a short
    list built from a guess about naming is the same error as guessing outright,
    just harder to notice.
    """
    result = gitops.run(
        ["for-each-ref", "--format=%(refname:strip=3)", f"refs/remotes/{remote}"], cwd=cwd
    )
    if not result.ok:
        return []
    return [line for line in result.lines if line and line != "HEAD"]


def record_branch_answer(profile: Profile, branch: str) -> Dict[str, Any]:
    """Record a developer's one-time answer (FR-003).

    ``source: user-answer`` is what makes it auditable later: the profile says a
    human decided this, not a heuristic.

    This updates the **in-memory** profile only. Durability is
    ``persist_branch_answer`` — see its docstring for why the two are separate.
    """
    profile.integration_branch = determined(branch, "user-answer")
    profile.integration_branch_candidates = []
    return profile.integration_branch


def persist_branch_answer(repo_root: Path, branch: str) -> Dict[str, Any]:
    """Save the answer so no later run asks again. (FR-003)

    The answer goes to **config.json**, not to the recorded profile, and the
    distinction is the whole point:

    * ``state.json`` holds *observations*. It is gitignored, and it is a cache —
      re-verified on every run, never an authority. An answer stored there would
      be re-asked by a colleague, and lost on the first corrupt-file recovery.
    * ``config.json`` holds *stated intent*. It is committed, so the answer
      travels with the repository (FR-041), and step 1 of the R4 precedence
      already reads it before any detection runs.

    So persisting an answer is simply writing the configuration the developer
    just supplied. There is no second mechanism, and no way for the two to
    disagree.

    Returns ``{"saved": bool, "path": …, "problem": …}`` rather than raising: an
    answer that cannot be saved must not abort a run that is otherwise fine. The
    run proceeds with the answer in memory and says it will ask again.
    """
    from scripts import config as config_mod

    loaded = config_mod.load(repo_root)
    updated = dict(loaded.config)
    updated["target_branch"] = branch

    try:
        path = config_mod.save(repo_root, updated)
    except config_mod.ConfigError as exc:
        return {"saved": False, "path": None, "problem": str(exc)}
    except OSError as exc:
        return {"saved": False, "path": None, "problem": f"could not write configuration: {exc}"}

    return {"saved": True, "path": path, "problem": None}


# --------------------------------------------------------------------------
# T022 — checks, release mode, multi-target (FR-043, research R6)
# --------------------------------------------------------------------------


def workflow_files(repo_root: Path) -> List[Path]:
    directory = Path(repo_root) / ".github" / "workflows"
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def detect_has_checks(
    profile: Profile, repo_root: Path, *, client=None, pr_number: Optional[int] = None
) -> Dict[str, Any]:
    """Whether this repository reports checks against a pull request.

    ``False`` here is a real answer, distinct from undetermined. A repository
    with no CI at all is a case the spec names explicitly, and it must not be
    silently treated as green (FR-012) nor as broken.
    """
    if client is not None and pr_number is not None:
        view = client.pr_view(pr_number)
        if view.ok:
            rollup = view.value.get("rollup") or []
            profile.has_checks = determined(bool(rollup), "gh-pr-view")
            return profile.has_checks

    files = workflow_files(repo_root)
    pr_triggered = [f for f in files if _triggers_on_pull_request(f.read_text(encoding="utf-8", errors="replace"))]

    if files:
        profile.has_checks = determined(bool(pr_triggered), "workflow-trigger")
    else:
        profile.has_checks = determined(False, "workflow-trigger")
    return profile.has_checks


def _triggers_on_pull_request(text: str) -> bool:
    return bool(re.search(r"^[ \t]*(pull_request|pull_request_target)[ \t]*:", text, re.M))


def _trigger_branches(text: str, event: str) -> List[str]:
    """Branch filters under ``on.<event>.branches``, as written.

    A small, deliberately shallow YAML read: this looks at the repository's own
    declaration, which is the system of record for how it releases, and does not
    try to evaluate the workflow.

    Note the ``[ \\t]`` rather than ``\\s`` throughout. ``\\s`` matches newlines,
    so a trailing ``\\s*`` on a line-anchored pattern happily swallows the line
    break and the indentation of the *next* line — which silently consumed the
    first list item and made every branch-filtered workflow look like it had no
    filters at all.
    """
    match = re.search(rf"^[ \t]*{event}[ \t]*:[ \t]*$", text, re.M)
    if not match:
        return []

    tail = text[match.end():]
    branches_match = re.search(r"^[ \t]*branches[ \t]*:[ \t]*(.*)$", tail, re.M)
    if not branches_match:
        return []

    inline = branches_match.group(1).strip()
    if inline.startswith("["):
        return [b.strip().strip("'\"") for b in inline.strip("[]").split(",") if b.strip()]

    listing = tail[branches_match.end():]
    names: List[str] = []
    for line in listing.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            names.append(stripped[2:].strip().strip("'\""))
        elif stripped and not stripped.startswith("#"):
            break
    return names


def detect_release_mode(
    profile: Profile,
    repo_root: Path,
    *,
    integration_branch: Optional[str],
    configured_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify how this repository releases. (FR-043, research R6)

    Three verdicts, and the third is the important one:

    ``observed``
        a workflow triggers on push to the integration branch, or on
        ``release``/``deployment``. The run watches it after the merge.
    ``executed``
        the repository declares an explicit release action in its ship config.
    ``none-determinable``
        recorded undetermined, asked about once (FR-043).

    Nothing here infers a release from a merge. That inference is precisely what
    FR-015 and SC-012 forbid, and it is the single hardest line in the spec.
    """
    if configured_mode:
        profile.release_mode = determined(configured_mode, "config")
        profile.release_evidence = "ship configuration release.mode"
        return profile.release_mode

    files = workflow_files(repo_root)
    if not files:
        profile.release_mode = undetermined(
            "none-determinable: this repository declares no workflows, so no "
            "release path could be classified as observed or executed"
        )
        profile.release_evidence = None
        return profile.release_mode

    evidence: List[str] = []

    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")

        if re.search(r"^[ \t]*(release|deployment|deployment_status)[ \t]*:", text, re.M):
            evidence.append(f"{path.name} triggers on a release/deployment event")
            continue

        push_branches = _trigger_branches(text, "push")
        if integration_branch and push_branches:
            if any(_branch_matches(pattern, integration_branch) for pattern in push_branches):
                evidence.append(
                    f"{path.name} triggers on push to {integration_branch}"
                )
                continue

        if not push_branches and re.search(r"^[ \t]*push[ \t]*:", text, re.M):
            # `on: push` with no branch filter fires for the integration branch too.
            if any(hint in path.name.lower() for hint in RELEASE_WORKFLOW_HINTS):
                evidence.append(f"{path.name} triggers on every push")

    if evidence:
        profile.release_mode = determined("observed", "workflow-trigger")
        profile.release_evidence = "; ".join(evidence)
        return profile.release_mode

    profile.release_mode = undetermined(
        "none-determinable: no workflow triggers on the integration branch or on "
        "a release event, and no release action is configured"
    )
    profile.release_evidence = (
        f"inspected {len(files)} workflow file(s): "
        + ", ".join(p.name for p in files)
    )
    return profile.release_mode


def _branch_matches(pattern: str, branch: str) -> bool:
    """GitHub branch filters allow ``*`` and ``**`` globs."""
    if pattern == branch:
        return True
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return bool(re.fullmatch(regex, branch))


def record_release_mode_answer(profile: Profile, mode: str) -> Dict[str, Any]:
    """FR-043's ask-once path. In-memory; see ``persist_release_mode_answer``."""
    profile.release_mode = determined(mode, "user-answer")
    profile.release_evidence = "developer answered at preflight"
    return profile.release_mode


def persist_release_mode_answer(
    repo_root: Path, mode: str, *, action: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Save the release-mode answer so no later run asks again. (FR-043)

    Same reasoning as ``persist_branch_answer``: this is stated intent, so it
    belongs in the committed configuration.

    ``executed`` requires a declared ``release.action``, and validation refuses
    the save without one. That refusal is correct rather than inconvenient — the
    tool never composes a release action, so recording "executed" with nothing
    to execute would produce a repository that fails at the release stage on
    every future run, with the failure attributed to the wrong place.
    """
    from scripts import config as config_mod

    loaded = config_mod.load(repo_root)
    updated = dict(loaded.config)
    release = dict(updated.get("release") or {})
    release["mode"] = mode
    if action is not None:
        release["action"] = action
    updated["release"] = release

    try:
        path = config_mod.save(repo_root, updated)
    except config_mod.ConfigError as exc:
        return {"saved": False, "path": None, "problem": str(exc)}
    except OSError as exc:
        return {"saved": False, "path": None, "problem": f"could not write configuration: {exc}"}

    return {"saved": True, "path": path, "problem": None}


def detect_multi_target(
    profile: Profile, repo_root: Path, *, integration_branch: Optional[str]
) -> Dict[str, Any]:
    """Does one integration branch feed several independent release targets?

    Such a repository is reported **unsupported** rather than partially released
    (spec Assumptions). Releasing one of three targets and reporting success
    would be the worst possible failure mode here.
    """
    files = workflow_files(repo_root)
    if not files or not integration_branch:
        profile.multi_target = determined(False, "workflow-trigger")
        return profile.multi_target

    releasing: List[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        push_branches = _trigger_branches(text, "push")
        triggers_release = bool(
            re.search(r"^[ \t]*(release|deployment)[ \t]*:", text, re.M)
        ) or any(_branch_matches(p, integration_branch) for p in push_branches)

        if triggers_release and any(
            hint in path.name.lower() for hint in RELEASE_WORKFLOW_HINTS
        ):
            releasing.append(path.name)

    profile.multi_target = determined(len(releasing) > 1, "workflow-trigger")
    if len(releasing) > 1:
        profile.release_evidence = (
            f"{len(releasing)} release workflows fire on {integration_branch}: "
            + ", ".join(releasing)
        )
    return profile.multi_target


# --------------------------------------------------------------------------
# The refusal matrix (FR-005)
# --------------------------------------------------------------------------


def check_working_copy(
    profile: Profile, *, integration_branch: Optional[str], cwd: Optional[Path] = None
) -> None:
    """Refuse on any working-copy state that makes the run unsafe.

    Each refusal names the blocking condition. None of them changes anything —
    that is the whole point, and SC-003 measures it at 100%.
    """
    if gitops.is_detached(cwd=cwd):
        profile.refusals.append(
            Refusal(
                "detached-head",
                "the working copy is in a detached-HEAD state, so there is no "
                "branch to ship.",
                expected="a checked-out branch (`git switch <branch>`)",
            )
        )
        return

    branch_result = gitops.current_branch(cwd=cwd)
    if not branch_result.ok:
        profile.refusals.append(
            Refusal(
                "branch-unresolved",
                f"the current branch could not be determined ({branch_result.error}).",
            )
        )
        return

    branch = branch_result.text
    profile.current_branch = branch

    unfinished = gitops.in_progress_rebase_or_merge(cwd=cwd)
    if unfinished:
        profile.refusals.append(
            Refusal(
                "unfinished-operation",
                f"the working copy is in the middle of {unfinished}.",
                expected="finish it, or abort it, before shipping",
            )
        )

    if integration_branch and branch == integration_branch:
        profile.refusals.append(
            Refusal(
                "on-integration-branch",
                f"the working copy is on {branch!r}, which is the integration "
                "branch itself — a branch cannot be shipped into itself.",
                expected=f"a feature branch that targets {integration_branch}",
            )
        )


def check_hosting(profile: Profile, client) -> None:
    """FR-004's second half: reachable, authenticated, and capable.

    Authentication is not authorization, so this records the probed capability
    map alongside the auth verdict and lets the caller name *which* precondition
    failed rather than reporting a generic "gh problem".
    """
    probe = client.probe()

    if not probe.ok:
        profile.hosting = undetermined(f"hosting-unreachable: {probe.reason}")
        profile.refusals.append(
            Refusal("hosting-unreachable", probe.reason or "the hosting service could not be reached.")
        )
        return

    payload = probe.value
    profile.hosting = determined(
        {
            "service": "github",
            "reachable": payload.get("reachable"),
            "authenticated": payload.get("authenticated"),
            "host": payload.get("host"),
            "gh_version": payload.get("gh_version"),
            "capabilities": payload.get("capabilities", {}),
        },
        "gh-auth-status",
    )

    if not payload.get("authenticated"):
        profile.refusals.append(
            Refusal(
                "hosting-unauthenticated",
                probe.reason
                or "the GitHub CLI reports no usable credential for this host.",
                expected="`gh auth login`",
            )
        )
