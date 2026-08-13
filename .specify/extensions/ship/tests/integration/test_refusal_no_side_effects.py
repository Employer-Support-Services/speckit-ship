"""T068 — SC-003: a refusal changes nothing, in 100% of cases.

The other refusal tests assert that the run *stops* and says why. This one
asserts the harder property: that stopping left no trace. Every case captures a
fingerprint of the working copy before and after and requires them byte-identical
— HEAD, the porcelain status, the index tree, and the stash list, so a staged
change or a stashed one cannot slip through unnoticed.

Two things this deliberately also checks, because they are the ways a "harmless"
refusal actually does leave a trace:

* **No state file appears.** A run that refuses must not create
  `.specify/extensions/ship/state.json`, nor the directories leading to it. A
  developer inspecting an unfamiliar repository should be able to run preflight
  without it writing anything into their checkout.
* **No lock is left behind.** A refusal before the lock is taken must not create
  one, and a refusal after must release it — otherwise the next run reports a
  concurrent run that does not exist.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts import lock as lock_mod
from scripts import preflight
from scripts import state as state_mod
from tests.integration.harness import RepoTestCase, git, requires_git

SHIP_PY = Path(__file__).resolve().parents[2] / "scripts" / "ship.py"


@requires_git
class RefusalSideEffectCase(RepoTestCase):
    default_branch = "trunk"

    def run_ship(self, *args) -> subprocess.CompletedProcess:
        """Invoke the real CLI, non-interactively, and never let it prompt."""
        return subprocess.run(
            [sys.executable, str(SHIP_PY), "--root", str(self.repo.path), "--no-network", *args],
            cwd=str(self.repo.path),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )

    def assertNoTrace(self, before: str) -> None:
        self.assertWorkingCopyUnchanged(before)
        self.assertFalse(
            state_mod.state_path(self.repo.path).exists(),
            "a refusal created a state file",
        )
        self.assertFalse(
            lock_mod.lock_path(self.repo.path).exists(),
            "a refusal left a lock behind",
        )


class TestPreflightRefusalsLeaveNothing(RefusalSideEffectCase):
    def test_on_the_integration_branch(self) -> None:
        self.repo.checkout("trunk")
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertNoTrace(before)

    def test_detached_head(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work")
        self.repo.detach_head()
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertNoTrace(before)

    def test_mid_merge(self) -> None:
        self.repo.branch("feature/x")
        self.repo.create_conflict_with("trunk")
        self.repo.start_conflicting_merge("trunk")
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertNoTrace(before)

    def test_uncommitted_work_survives_a_refusal_untouched(self) -> None:
        """The case a developer would actually notice."""
        self.repo.checkout("trunk")
        self.repo.write_file("work-in-progress.txt", "half-finished\n")
        git(["add", "work-in-progress.txt"], cwd=self.repo.path)
        self.repo.write_file("also-unstaged.txt", "not staged\n")
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode)
        self.assertNoTrace(before)
        self.assertEqual(
            "half-finished\n",
            (self.repo.path / "work-in-progress.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            "not staged\n",
            (self.repo.path / "also-unstaged.txt").read_text(encoding="utf-8"),
        )

    def test_a_stashed_change_is_not_disturbed(self) -> None:
        self.repo.checkout("trunk")
        self.repo.write_file("stashed.txt", "stashed content\n")
        git(["add", "-A"], cwd=self.repo.path)
        git(["stash", "push", "-m", "wip"], cwd=self.repo.path)
        before = self.fingerprint()

        self.run_ship()

        self.assertNoTrace(before)
        self.assertIn("wip", git(["stash", "list"], cwd=self.repo.path).stdout)


class TestNoRemoteRefusalLeavesNothing(RefusalSideEffectCase):
    with_remote = False

    def test_a_repository_with_no_remote(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work")
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertNoTrace(before)


class TestPreflightCommandIsReadOnly(RefusalSideEffectCase):
    def test_a_successful_preflight_writes_nothing(self) -> None:
        """contracts/commands.md specifies preflight as 'changes nothing'.

        Not merely "changes nothing important" — a state file appearing because
        someone asked a read-only question is a change, and it creates
        directories in a checkout the developer may only be inspecting.
        """
        self.repo.set_remote_head()
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work")
        before = self.fingerprint()

        result = self.run_ship("preflight")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNoTrace(before)

    def test_a_refused_preflight_writes_nothing(self) -> None:
        self.repo.set_remote_head()
        self.repo.checkout("trunk")
        before = self.fingerprint()

        result = self.run_ship("preflight")

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertIn("integration branch itself", result.stdout)
        self.assertNoTrace(before)

    def test_an_undetermined_integration_branch_exits_10_not_0(self) -> None:
        """Preflight answers "can I ship from here, and where would it go?".

        When it cannot say where, the answer is no — and exit 0 would tell a
        script that shipping is fine.
        """
        self.repo.branch("feature/x")
        before = self.fingerprint()

        result = self.run_ship("preflight")

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertIn("integration branch is undetermined", result.stdout)
        self.assertNoTrace(before)

    def test_preflight_creates_no_extension_directories(self) -> None:
        self.repo.set_remote_head()
        self.repo.branch("feature/x")

        # Remove the directory the harness pre-creates, so its absence is real.
        target = self.repo.path / ".specify" / "extensions" / "ship"
        for child in sorted(target.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        target.rmdir()

        self.run_ship("preflight")

        self.assertFalse(target.exists(), "preflight created the extension directory")


class TestDryRunChangesNothing(RefusalSideEffectCase):
    def test_a_dry_run_on_a_shippable_branch_leaves_the_tree_alone(self) -> None:
        self.repo.set_remote_head()
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.write_file("uncommitted.txt", "pending\n")
        before = self.fingerprint()

        result = self.run_ship("--dry-run")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertWorkingCopyUnchanged(before)
        self.assertNotIn("feature/x", self.repo.remote_branches())


class TestNonInteractiveNeverGuesses(RefusalSideEffectCase):
    def test_an_undetermined_branch_refuses_rather_than_choosing(self) -> None:
        """FR-002 — a prompt nobody saw is still a guess."""
        # No refs/remotes/origin/HEAD, and --no-network blocks `git remote show`.
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work")
        before = self.fingerprint()

        result = self.run_ship()

        self.assertEqual(10, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("main", result.stdout.split("integration branch")[-1][:80])
        self.assertNoTrace(before)


if __name__ == "__main__":
    unittest.main()
