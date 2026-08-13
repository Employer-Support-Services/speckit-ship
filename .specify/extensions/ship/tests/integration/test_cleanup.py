"""T039 — cleanup against a real remote (FR-023, FR-024, FR-025).

Two behaviors carry the weight:

**A branch with unmerged commits is never deleted** (FR-025). Deletion is the one
irreversible act in this stage, and the commits themselves are reported rather
than a bare refusal, so the developer can see what would have been lost.

**A refused remote deletion is reported, not failed** (FR-023). The work shipped;
a protected branch blocking the tidy-up does not make the run unsuccessful.
"""

from __future__ import annotations

import unittest

from scripts.stages import cleanup as cleanup_stage
from tests.integration.harness import RepoTestCase, git, requires_git


@requires_git
class TestCleanupAfterAMerge(RepoTestCase):
    def merge_feature_into_trunk(self, branch: str = "feature/x") -> None:
        """Simulate what the merge stage's outward effect looks like locally."""
        self.repo.checkout("trunk")
        git(["merge", "--no-ff", "--no-edit", branch], cwd=self.repo.path)
        self.repo.push("trunk")
        self.repo.checkout(branch)

    def test_a_merged_branch_is_deleted_locally_and_remotely(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.push("feature/x", set_upstream=True)
        self.merge_feature_into_trunk()

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertEqual("succeeded", result.outcome)
        self.assertNotIn("feature/x", self.repo.remote_branches())
        self.assertNotIn(
            "feature/x",
            git(["branch", "--format=%(refname:short)"], cwd=self.repo.path).stdout.split(),
        )

    def test_the_working_copy_ends_on_the_integration_branch(self) -> None:
        """FR-024 — the developer is left somewhere sensible."""
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.push("feature/x", set_upstream=True)
        self.merge_feature_into_trunk()

        cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertEqual("trunk", self.repo.current_branch())

    def test_the_integration_branch_is_updated_from_the_remote(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.push("feature/x", set_upstream=True)
        self.merge_feature_into_trunk()

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertTrue(
            any("updated trunk" in action for action in result.detail["actions"]),
            result.detail["actions"],
        )

    def test_delete_branch_false_keeps_the_branch_but_still_returns_home(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.push("feature/x", set_upstream=True)
        self.merge_feature_into_trunk()

        result = cleanup_stage.run(
            self.repo.path, branch="feature/x", target="trunk", delete_branch=False
        )

        self.assertEqual("succeeded", result.outcome)
        self.assertIn("feature/x", self.repo.remote_branches())
        self.assertEqual("trunk", self.repo.current_branch())


@requires_git
class TestUnmergedCommitsAreNeverDeleted(RepoTestCase):
    def test_a_branch_with_unmerged_commits_is_refused(self) -> None:
        """FR-025. The commits have not landed; deleting would discard them."""
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("unmerged work")
        self.repo.push("feature/x", set_upstream=True)

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertEqual("undetermined", result.outcome)
        self.assertIn("unmerged-commits", result.reason)
        self.assertIn("feature/x", self.repo.remote_branches())

    def test_the_unmerged_commits_are_reported_not_merely_counted(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("first unmerged thing")
        self.repo.write_file("b.txt", "content\n")
        self.repo.commit("second unmerged thing")

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertEqual("undetermined", result.outcome)
        self.assertIn("first unmerged thing", result.message)
        self.assertIn("second unmerged thing", result.message)

    def test_the_branch_survives_the_refusal(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("unmerged work")

        cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        branches = git(
            ["branch", "--format=%(refname:short)"], cwd=self.repo.path
        ).stdout.split()
        self.assertIn("feature/x", branches)


@requires_git
class TestRemoteRefusalIsReportedNotFailed(RepoTestCase):
    def test_a_branch_never_pushed_still_cleans_up_locally(self) -> None:
        """The remote deletion cannot succeed; that is a note, not a failure."""
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.checkout("trunk")
        git(["merge", "--no-ff", "--no-edit", "feature/x"], cwd=self.repo.path)
        self.repo.push("trunk")
        self.repo.checkout("feature/x")

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertEqual("succeeded", result.outcome)
        self.assertTrue(result.detail["reported"], "the refused remote delete should be reported")
        self.assertTrue(
            any("remote deletion" in note for note in result.detail["reported"]),
            result.detail["reported"],
        )

    def test_the_report_names_what_was_refused(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.checkout("trunk")
        git(["merge", "--no-ff", "--no-edit", "feature/x"], cwd=self.repo.path)
        self.repo.push("trunk")
        self.repo.checkout("feature/x")

        result = cleanup_stage.run(self.repo.path, branch="feature/x", target="trunk")

        self.assertIn("note:", result.message)


@requires_git
class TestDryRun(RepoTestCase):
    def test_a_dry_run_deletes_nothing_and_stays_put(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.repo.push("feature/x", set_upstream=True)

        result = cleanup_stage.run(
            self.repo.path, branch="feature/x", target="trunk", dry_run=True
        )

        self.assertEqual("skipped", result.outcome)
        self.assertIn("feature/x", self.repo.remote_branches())
        self.assertEqual("feature/x", self.repo.current_branch())


if __name__ == "__main__":
    unittest.main()
