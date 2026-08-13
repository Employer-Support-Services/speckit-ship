"""T059 — mechanical conflict repair against a real bare remote (FR-018, SC-005).

Two cases, and the difference between them is the whole design:

**A stale branch** — the target moved, the branch did not, and the two touched
different files. git resolves this alone. This is the class SC-005 measures, and
it is most of what GitHub reports as "conflicting".

**A genuine conflict** — both branches edited the same lines. Resolving it means
choosing between two people's work. The repair is refused, the working copy is
restored to exactly where it was, and the conflicted paths are handed back.

The restoration assertion matters as much as the repair one. A repair that fails
halfway and leaves a half-merged tree is worse than one that never ran.
"""

from __future__ import annotations

import unittest

from scripts import repair
from tests.integration.harness import RepoTestCase, git, requires_git


@requires_git
class ConflictCase(RepoTestCase):
    def move_target_ahead(self, *, path: str = "other.txt", content: str = "from trunk\n") -> None:
        """Advance trunk on the remote so the feature branch is behind."""
        self.repo.checkout("trunk")
        self.repo.write_file(path, content)
        self.repo.commit(f"trunk: add {path}")
        self.repo.push("trunk")


class TestStaleBranchIsRepairedMechanically(ConflictCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.branch("feature/x")
        self.repo.write_file("feature.txt", "from the feature branch\n")
        self.repo.commit("feature work")
        self.repo.push("feature/x", set_upstream=True)
        self.move_target_ahead()
        self.repo.checkout("feature/x")

    def test_a_behind_branch_is_brought_up_to_date_and_pushed(self) -> None:
        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertTrue(result.repaired, result.message)
        self.assertFalse(result.handed_back)
        self.assertIsNotNone(result.attempt)
        self.assertEqual("mechanical", result.attempt["authority"])
        self.assertIsNotNone(result.attempt["pushed_sha"])

    def test_the_target_s_work_is_present_after_the_repair(self) -> None:
        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertTrue((self.repo.path / "other.txt").is_file())
        self.assertTrue((self.repo.path / "feature.txt").is_file())

    def test_the_repair_is_visible_on_the_remote(self) -> None:
        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        local = self.repo.head_sha()
        remote = git(
            ["ls-remote", "origin", "refs/heads/feature/x"], cwd=self.repo.path
        ).stdout.split()[0]

        self.assertEqual(local, remote)

    def test_rebase_mode_replays_the_branch_onto_the_target(self) -> None:
        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="rebase"
        )

        self.assertTrue(result.repaired, result.message)
        self.assertIn("rebased onto", result.attempt["description"])

    def test_an_already_current_branch_is_handed_back_not_no_op_repaired(self) -> None:
        """A no-op merge reported as a repair would burn budget and prove nothing."""
        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        second = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertFalse(second.repaired)
        self.assertTrue(second.handed_back)
        self.assertIn("already up to date", second.message)


class TestGenuineConflictIsHandedBack(ConflictCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.branch("feature/x")
        self.repo.write_file("shared.txt", "the feature branch's version\n")
        self.repo.commit("feature edits shared.txt")
        self.repo.push("feature/x", set_upstream=True)

        self.repo.checkout("trunk")
        self.repo.write_file("shared.txt", "the integration branch's version\n")
        self.repo.commit("trunk edits shared.txt")
        self.repo.push("trunk")
        self.repo.checkout("feature/x")

    def test_a_semantic_conflict_is_not_repaired(self) -> None:
        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertFalse(result.repaired)
        self.assertTrue(result.handed_back)
        self.assertIsNone(result.attempt)

    def test_the_conflicted_paths_are_reported(self) -> None:
        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertIn("shared.txt", result.conflicted_paths)
        self.assertIn("shared.txt", result.message)

    def test_the_message_says_a_human_must_choose(self) -> None:
        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertIn("two people's changes", result.message)

    def test_the_working_copy_is_restored_exactly(self) -> None:
        """A half-merged tree left behind is worse than no repair at all."""
        before = self.fingerprint()

        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertWorkingCopyUnchanged(before)

    def test_no_merge_is_left_in_progress(self) -> None:
        from scripts import gitops

        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertIsNone(gitops.in_progress_rebase_or_merge(cwd=self.repo.path))

    def test_the_branch_content_is_the_feature_s_own_after_the_refusal(self) -> None:
        repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertEqual(
            "the feature branch's version\n",
            (self.repo.path / "shared.txt").read_text(encoding="utf-8"),
        )

    def test_a_rebase_conflict_is_also_restored(self) -> None:
        before = self.fingerprint()

        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="rebase"
        )

        self.assertFalse(result.repaired)
        self.assertWorkingCopyUnchanged(before)


class TestDetectUnmergeableBeforeMerging(ConflictCase):
    def test_a_clean_branch_is_reported_mergeable(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("feature.txt", "content\n")
        self.repo.commit("feature work")
        self.move_target_ahead()
        self.repo.checkout("feature/x")

        result = repair.detect_unmergeable(
            self.repo.path, branch="feature/x", target="trunk"
        )

        self.assertIs(True, result["mergeable"])

    def test_a_conflicting_branch_is_detected_without_touching_the_tree(self) -> None:
        """FR-018 — detected *before* a merge is attempted."""
        self.repo.branch("feature/x")
        self.repo.write_file("shared.txt", "feature version\n")
        self.repo.commit("feature edits shared")
        self.repo.checkout("trunk")
        self.repo.write_file("shared.txt", "trunk version\n")
        self.repo.commit("trunk edits shared")
        self.repo.push("trunk")
        self.repo.checkout("feature/x")

        before = self.fingerprint()
        result = repair.detect_unmergeable(
            self.repo.path, branch="feature/x", target="trunk"
        )

        self.assertIs(False, result["mergeable"])
        self.assertWorkingCopyUnchanged(before)


class TestDirtyTreeIsRefused(ConflictCase):
    def test_uncommitted_changes_block_a_repair_that_could_not_be_rolled_back(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("feature.txt", "content\n")
        self.repo.commit("feature work")
        self.move_target_ahead()
        self.repo.checkout("feature/x")
        self.repo.write_file("uncommitted.txt", "work in progress\n")

        result = repair.repair_conflict(
            self.repo.path, branch="feature/x", target="trunk", merge_method="merge"
        )

        self.assertFalse(result.repaired)
        self.assertTrue(result.handed_back)
        self.assertIn("uncommitted changes", result.message)
        self.assertTrue((self.repo.path / "uncommitted.txt").is_file())


if __name__ == "__main__":
    unittest.main()
