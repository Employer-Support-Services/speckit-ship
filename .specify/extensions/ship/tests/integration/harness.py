"""Throwaway git repositories for integration tests.

Real git, real refs, real remote — just local. A ``git init --bare`` directory
serves as the remote, so push, fetch, ls-remote, and branch deletion all behave
exactly as they will in production without a network or a credential in sight
(research.md R12, layer 2).

**The default branch in these fixtures is deliberately not ``main``.** A fixture
named ``main`` cannot distinguish detection from a hardcoded guess: the code
under test would pass whether it read ``symbolic-ref`` or simply returned the
string. ``trunk`` is used throughout for exactly that reason.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List, Optional, Sequence

# Not 'main'. See the module docstring — this is load-bearing.
DEFAULT_BRANCH = "trunk"


def git(args: Sequence[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed in {cwd}:\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


class TempRepo:
    """A work tree plus its bare remote."""

    def __init__(self, base: Path, *, default_branch: str = DEFAULT_BRANCH) -> None:
        self.base = base
        self.default_branch = default_branch
        self.remote_path = base / "remote.git"
        self.path = base / "work"

    # -- construction ----------------------------------------------------

    def create(self, *, with_remote: bool = True, initial_commit: bool = True) -> "TempRepo":
        self.path.mkdir(parents=True, exist_ok=True)
        git(["init", "-q"], cwd=self.path)
        self._configure(self.path)

        # `git init -b` is not available on every supported git, so the branch
        # is renamed explicitly instead.
        git(["checkout", "-q", "-B", self.default_branch], cwd=self.path)

        if initial_commit:
            self.write_file("README.md", "# fixture repository\n")
            self.commit("Initial commit")

        if with_remote:
            self.add_remote()

        return self

    def _configure(self, path: Path) -> None:
        git(["config", "user.email", "ship-tests@example.invalid"], cwd=path)
        git(["config", "user.name", "Ship Tests"], cwd=path)
        git(["config", "commit.gpgsign", "false"], cwd=path)
        # Keep the fixture's behavior independent of the developer's own config.
        git(["config", "core.hooksPath", "/dev/null"], cwd=path)

    def add_remote(self, name: str = "origin") -> None:
        self.remote_path.mkdir(parents=True, exist_ok=True)
        git(["init", "-q", "--bare", str(self.remote_path)], cwd=self.base)
        git(["remote", "add", name, str(self.remote_path)], cwd=self.path)
        git(["push", "-q", "-u", name, self.default_branch], cwd=self.path)

    def set_remote_head(self, name: str = "origin") -> None:
        """Create ``refs/remotes/<name>/HEAD``, which a fresh clone would have.

        ``git init`` + ``git remote add`` does *not* create it, which is itself
        realistic — plenty of checkouts lack it, which is why the R4 precedence
        has three more steps behind ``symbolic-ref``.
        """
        git(
            ["symbolic-ref", f"refs/remotes/{name}/HEAD",
             f"refs/remotes/{name}/{self.default_branch}"],
            cwd=self.path,
        )

    # -- operations ------------------------------------------------------

    def write_file(self, relative: str, content: str) -> Path:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def commit(self, message: str, *, add_all: bool = True) -> str:
        if add_all:
            git(["add", "-A"], cwd=self.path)
        git(["commit", "-q", "-m", message], cwd=self.path)
        return self.head_sha()

    def branch(self, name: str, *, checkout: bool = True) -> None:
        git(["checkout", "-q", "-b" if checkout else "-B", name], cwd=self.path)

    def checkout(self, name: str) -> None:
        git(["checkout", "-q", name], cwd=self.path)

    def push(self, branch: Optional[str] = None, *, set_upstream: bool = False) -> None:
        args = ["push", "-q"]
        if set_upstream:
            args.append("-u")
        args.extend(["origin", branch or self.current_branch()])
        git(args, cwd=self.path)

    def head_sha(self) -> str:
        return git(["rev-parse", "HEAD"], cwd=self.path).stdout.strip()

    def current_branch(self) -> str:
        return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=self.path).stdout.strip()

    def remote_branches(self) -> List[str]:
        proc = git(["ls-remote", "--heads", "origin"], cwd=self.path)
        return [
            line.split("refs/heads/", 1)[1]
            for line in proc.stdout.splitlines()
            if "refs/heads/" in line
        ]

    def porcelain(self) -> str:
        return git(["status", "--porcelain"], cwd=self.path).stdout

    # -- states worth reproducing ----------------------------------------

    def detach_head(self) -> None:
        git(["checkout", "-q", "--detach", "HEAD"], cwd=self.path)

    def start_conflicting_merge(self, other_branch: str) -> None:
        """Leave the tree mid-merge with a real conflict.

        ``check=False`` because the merge is *expected* to fail — that failure
        is the state being constructed.
        """
        git(["merge", "--no-commit", "--no-ff", other_branch], cwd=self.path, check=False)

    def create_conflict_with(self, other_branch: str, *, path: str = "conflict.txt") -> None:
        """Two branches editing the same line, so a merge cannot be mechanical."""
        base = self.current_branch()

        self.write_file(path, "from the feature branch\n")
        self.commit(f"Edit {path} on {base}")

        self.checkout(other_branch)
        self.write_file(path, "from the integration branch\n")
        self.commit(f"Edit {path} on {other_branch}")

        self.checkout(base)

    def add_workflow(self, name: str, content: str) -> Path:
        return self.write_file(f".github/workflows/{name}", content)

    def install_specify(self) -> Path:
        """Create the ``.specify/extensions/ship`` directory the engine writes into."""
        target = self.path / ".specify" / "extensions" / "ship"
        target.mkdir(parents=True, exist_ok=True)
        return target


class RepoTestCase(unittest.TestCase):
    """Base class handing each test a fresh repository and cleaning up after."""

    default_branch = DEFAULT_BRANCH
    with_remote = True
    initial_commit = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = TempRepo(self.base, default_branch=self.default_branch).create(
            with_remote=self.with_remote, initial_commit=self.initial_commit
        )
        self.repo.install_specify()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- assertions ------------------------------------------------------

    def assertWorkingCopyUnchanged(self, before: str, message: str = "") -> None:
        """SC-003: a refusal leaves the working copy byte-identical."""
        from scripts import gitops

        after = gitops.working_tree_fingerprint(cwd=self.repo.path)
        self.assertEqual(
            before,
            after,
            message or "The working copy changed across an operation that must change nothing.",
        )

    def fingerprint(self) -> str:
        from scripts import gitops

        return gitops.working_tree_fingerprint(cwd=self.repo.path)


def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


requires_git = unittest.skipUnless(git_available(), "git is not available")
