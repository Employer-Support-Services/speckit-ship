"""git plumbing, wrapped so that expected conditions are values, not exceptions.

Every function here returns a result object. "Not a repository", "no remote",
"no upstream" and "detached HEAD" are all *answers* this tool asks for
deliberately — raising on them would force the caller into try/except as control
flow and, worse, would make it easy to catch broadly and treat a genuine failure
as one of the expected states.

Nothing in this module writes ship state; it observes and returns. The engine
records.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

# Nothing here should ever block a run indefinitely. Local git operations are
# fast; a hang means something pathological (a hung credential helper, a dead
# network mount) and a bounded failure is more useful than a stalled pipeline.
DEFAULT_TIMEOUT = 60
NETWORK_TIMEOUT = 300


class GitResult:
    """One git invocation's outcome."""

    def __init__(
        self,
        *,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        argv: Sequence[str] = (),
        timed_out: bool = False,
        git_missing: bool = False,
    ) -> None:
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.argv = list(argv)
        self.timed_out = timed_out
        self.git_missing = git_missing

    @property
    def text(self) -> str:
        return self.stdout.strip()

    @property
    def lines(self) -> List[str]:
        return [line for line in self.stdout.splitlines() if line.strip()]

    @property
    def error(self) -> str:
        """A single sentence naming what went wrong, for a refusal report."""
        if self.git_missing:
            return "git is not installed or not on PATH"
        if self.timed_out:
            return f"git {' '.join(self.argv[:3])} timed out"
        return self.stderr.strip() or f"git exited {self.returncode}"

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<GitResult ok={self.ok} rc={self.returncode} {' '.join(self.argv[:4])}>"


def run(
    args: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    check_stdin: bool = True,
) -> GitResult:
    """Invoke git. Never raises for a non-zero exit."""
    argv = ["git", *args]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL if check_stdin else None,
        )
    except FileNotFoundError:
        return GitResult(ok=False, argv=argv, git_missing=True, returncode=127)
    except subprocess.TimeoutExpired:
        return GitResult(ok=False, argv=argv, timed_out=True, returncode=124)

    return GitResult(
        ok=proc.returncode == 0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
        argv=argv,
    )


# --------------------------------------------------------------------------
# Repository shape
# --------------------------------------------------------------------------


def is_inside_work_tree(cwd: Optional[Path] = None) -> GitResult:
    """FR-001's first question. ``result.ok and result.text == 'true'``."""
    return run(["rev-parse", "--is-inside-work-tree"], cwd=cwd)


def root(cwd: Optional[Path] = None) -> GitResult:
    return run(["rev-parse", "--show-toplevel"], cwd=cwd)


def current_branch(cwd: Optional[Path] = None) -> GitResult:
    """The checked-out branch name.

    On a detached HEAD this succeeds with the literal text ``HEAD`` — which is
    why ``is_detached`` exists as a separate question rather than being inferred
    from a parse failure here.
    """
    return run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)


def is_detached(cwd: Optional[Path] = None) -> bool:
    result = run(["symbolic-ref", "-q", "HEAD"], cwd=cwd)
    return not result.ok


def head_sha(cwd: Optional[Path] = None) -> GitResult:
    return run(["rev-parse", "HEAD"], cwd=cwd)


def git_dir(cwd: Optional[Path] = None) -> GitResult:
    return run(["rev-parse", "--absolute-git-dir"], cwd=cwd)


def in_progress_rebase_or_merge(cwd: Optional[Path] = None) -> Optional[str]:
    """Name the unfinished operation, or None.

    Detected from the marker paths git itself maintains, which is the system of
    record for this: ``rebase-merge``/``rebase-apply`` for a rebase,
    ``MERGE_HEAD`` for a merge, and the two cherry-pick/revert sequencer files
    that leave the tree in the same "finish or abort me first" state.
    """
    result = git_dir(cwd=cwd)
    if not result.ok:
        return None

    gd = Path(result.text)
    markers = [
        ("rebase-merge", "an interactive or merge-based rebase"),
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
        ("REVERT_HEAD", "a revert"),
        ("BISECT_LOG", "a bisect"),
    ]
    for name, description in markers:
        if (gd / name).exists():
            return description
    return None


def porcelain_status(cwd: Optional[Path] = None) -> GitResult:
    """``git status --porcelain`` — the working-tree diff in one line per path."""
    return run(["status", "--porcelain"], cwd=cwd)


def is_clean(cwd: Optional[Path] = None) -> bool:
    result = porcelain_status(cwd=cwd)
    return result.ok and result.text == ""


def working_tree_fingerprint(cwd: Optional[Path] = None) -> str:
    """A cheap identity for "the working copy did not change".

    Used by the refusal tests (SC-003) to assert byte-identical state across a
    refusal. Combines HEAD, the porcelain status, and the index hash so that a
    staged-but-uncommitted change is not invisible to it.
    """
    parts = [
        head_sha(cwd=cwd).text,
        porcelain_status(cwd=cwd).stdout,
        run(["rev-parse", "HEAD:"], cwd=cwd).text,
        run(["stash", "list"], cwd=cwd).stdout,
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Remotes
# --------------------------------------------------------------------------


def remotes(cwd: Optional[Path] = None) -> List[str]:
    result = run(["remote"], cwd=cwd)
    return result.lines if result.ok else []


def remote_url(remote: str, cwd: Optional[Path] = None) -> GitResult:
    return run(["remote", "get-url", remote], cwd=cwd)


def symbolic_ref(remote: str, cwd: Optional[Path] = None) -> GitResult:
    """``refs/remotes/<remote>/HEAD`` — step 2 of the R4 precedence.

    This is the local mirror of the remote's default branch. It is absent in
    plenty of clones (notably shallow ones and some CI checkouts), which is why
    the precedence has two more steps behind it rather than stopping here.
    """
    return run(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd=cwd)


def remote_show(remote: str, cwd: Optional[Path] = None) -> GitResult:
    """``git remote show <remote>`` — step 4, a network re-derivation."""
    return run(["remote", "show", remote], cwd=cwd, timeout=NETWORK_TIMEOUT)


def parse_remote_show_head(output: str) -> Optional[str]:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("HEAD branch:"):
            value = stripped.split(":", 1)[1].strip()
            # git prints "(unknown)" when it cannot tell. That is not a name.
            if value and value != "(unknown)":
                return value
    return None


def parse_symbolic_ref(output: str, remote: str) -> Optional[str]:
    """``refs/remotes/origin/trunk`` -> ``trunk``."""
    text = output.strip()
    prefix = f"refs/remotes/{remote}/"
    if text.startswith(prefix):
        name = text[len(prefix) :]
        return name or None
    return None


def fetch(
    remote: str, refspec: Optional[str] = None, *, cwd: Optional[Path] = None
) -> GitResult:
    args = ["fetch", remote]
    if refspec:
        args.append(refspec)
    return run(args, cwd=cwd, timeout=NETWORK_TIMEOUT)


def remote_branch_exists(
    remote: str, branch: str, *, cwd: Optional[Path] = None
) -> Optional[bool]:
    """True, False, or **None for "could not check"**.

    The None case is the point. An offline developer, or one whose credentials
    have expired, must not have a target branch reported as nonexistent — that
    would turn a connectivity problem into a false claim about the repository.
    """
    result = run(
        ["ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        cwd=cwd,
        timeout=NETWORK_TIMEOUT,
    )
    if not result.ok:
        return None
    return bool(result.text)


def local_branch_exists(branch: str, *, cwd: Optional[Path] = None) -> bool:
    return run(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=cwd
    ).ok


def tracking_branch(branch: str, *, cwd: Optional[Path] = None) -> Optional[str]:
    result = run(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"],
        cwd=cwd,
    )
    return result.text if result.ok and result.text else None


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


class AheadBehind:
    def __init__(self, ahead: Optional[int], behind: Optional[int], *, reason: str = ""):
        self.ahead = ahead
        self.behind = behind
        self.reason = reason

    @property
    def known(self) -> bool:
        return self.ahead is not None and self.behind is not None


def ahead_behind(left: str, right: str, *, cwd: Optional[Path] = None) -> AheadBehind:
    """How far ``left`` is ahead of and behind ``right``.

    Returns an ``AheadBehind`` whose counts are None when the comparison could
    not be made (an unknown ref, unrelated histories). A zero and an unknown are
    different answers, and the caller must be able to tell them apart — this is
    FR-028 applied to a number.
    """
    result = run(["rev-list", "--left-right", "--count", f"{left}...{right}"], cwd=cwd)
    if not result.ok:
        return AheadBehind(None, None, reason=result.error)
    parts = result.text.split()
    if len(parts) != 2:
        return AheadBehind(None, None, reason=f"unexpected rev-list output: {result.text!r}")
    try:
        return AheadBehind(int(parts[0]), int(parts[1]))
    except ValueError:
        return AheadBehind(None, None, reason=f"unparseable rev-list output: {result.text!r}")


def unmerged_commits(branch: str, target: str, *, cwd: Optional[Path] = None) -> GitResult:
    """Commits on ``branch`` that are not reachable from ``target``.

    FR-025's evidence: a branch with any output here must not be deleted, and
    the commits themselves are what gets reported instead of a bare refusal.
    """
    return run(
        ["log", "--oneline", "--no-decorate", f"{target}..{branch}"], cwd=cwd
    )


def commits_between(base: str, head: str, *, cwd: Optional[Path] = None) -> GitResult:
    """Commit subjects for PR body composition (``pr.composition == 'commits'``)."""
    return run(["log", "--reverse", "--pretty=format:%s", f"{base}..{head}"], cwd=cwd)


def diff_stat(*, cwd: Optional[Path] = None, staged: bool = False) -> GitResult:
    args = ["diff", "--stat"]
    if staged:
        args.insert(1, "--cached")
    return run(args, cwd=cwd)


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------


def stage_all(cwd: Optional[Path] = None) -> GitResult:
    return run(["add", "-A"], cwd=cwd)


def commit(message: str, *, cwd: Optional[Path] = None) -> GitResult:
    return run(["commit", "-m", message], cwd=cwd)


def push(
    remote: str, branch: str, *, set_upstream: bool = False, cwd: Optional[Path] = None
) -> GitResult:
    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    args.extend([remote, branch])
    return run(args, cwd=cwd, timeout=NETWORK_TIMEOUT)


def checkout(branch: str, *, cwd: Optional[Path] = None) -> GitResult:
    return run(["checkout", branch], cwd=cwd)


def pull(remote: str, branch: str, *, cwd: Optional[Path] = None) -> GitResult:
    return run(["pull", "--ff-only", remote, branch], cwd=cwd, timeout=NETWORK_TIMEOUT)


def delete_local_branch(branch: str, *, force: bool = False, cwd: Optional[Path] = None) -> GitResult:
    return run(["branch", "-D" if force else "-d", branch], cwd=cwd)


def delete_remote_branch(remote: str, branch: str, *, cwd: Optional[Path] = None) -> GitResult:
    return run(["push", remote, "--delete", branch], cwd=cwd, timeout=NETWORK_TIMEOUT)


def merge(ref: str, *, cwd: Optional[Path] = None, message: Optional[str] = None) -> GitResult:
    args = ["merge", "--no-edit"]
    if message:
        args.extend(["-m", message])
    args.append(ref)
    return run(args, cwd=cwd)


def rebase(ref: str, *, cwd: Optional[Path] = None) -> GitResult:
    return run(["rebase", ref], cwd=cwd)


def abort_merge(cwd: Optional[Path] = None) -> GitResult:
    return run(["merge", "--abort"], cwd=cwd)


def abort_rebase(cwd: Optional[Path] = None) -> GitResult:
    return run(["rebase", "--abort"], cwd=cwd)


def conflicted_paths(cwd: Optional[Path] = None) -> List[str]:
    result = run(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
    return result.lines if result.ok else []
