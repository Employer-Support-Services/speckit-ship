"""T038 — commit and publish against a real bare local remote.

Real git, real refs, real push. No network: the remote is a `git init --bare`
directory in a temp folder, which behaves exactly like a remote for everything
these stages do.
"""

from __future__ import annotations

import unittest

from scripts.stages import commit as commit_stage
from scripts.stages import publish as publish_stage
from tests.integration.harness import RepoTestCase, git, requires_git


@requires_git
class TestCommitStage(RepoTestCase):
    def test_a_clean_tree_is_skipped_with_a_reason_not_failed(self) -> None:
        """Re-running ship after a successful commit must not error."""
        self.repo.branch("feature/x")

        result = commit_stage.run(self.repo.path, message="ship: x")

        self.assertEqual("skipped", result.outcome)
        self.assertIn("clean-tree", result.reason)

    def test_pending_changes_are_summarized_before_being_committed(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")

        summary = commit_stage.summarize_pending(self.repo.path)

        self.assertTrue(summary["readable"])
        self.assertEqual(1, summary["count"])
        self.assertIn("a.txt", summary["untracked"])

    def test_the_summary_names_untracked_files_explicitly(self) -> None:
        """'Would ship more than intended' is the edge case this guards."""
        self.repo.branch("feature/x")
        self.repo.write_file("intended.txt", "x\n")
        self.repo.write_file("unrelated.log", "y\n")

        rendered = commit_stage.render_pending(
            commit_stage.summarize_pending(self.repo.path)
        )

        self.assertIn("intended.txt", rendered)
        self.assertIn("unrelated.log", rendered)
        self.assertIn("untracked", rendered)

    def test_the_developer_sees_the_summary_before_anything_is_staged(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        seen = {}

        def confirm(text: str) -> bool:
            # Nothing may be staged at the moment the developer is asked.
            seen["text"] = text
            seen["staged_at_prompt"] = git(
                ["diff", "--cached", "--name-only"], cwd=self.repo.path
            ).stdout.strip()
            return True

        commit_stage.run(self.repo.path, message="ship: x", confirm=confirm)

        self.assertIn("a.txt", seen["text"])
        self.assertEqual("", seen["staged_at_prompt"])

    def test_declining_is_a_skip_and_leaves_the_tree_untouched(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        before = self.fingerprint()

        result = commit_stage.run(
            self.repo.path, message="ship: x", confirm=lambda _text: False
        )

        self.assertEqual("skipped", result.outcome)
        self.assertIn("declined", result.reason)
        self.assertWorkingCopyUnchanged(before)

    def test_a_confirmed_commit_records_the_resulting_sha(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")

        result = commit_stage.run(
            self.repo.path, message="ship: x", confirm=lambda _text: True
        )

        self.assertEqual("succeeded", result.outcome)
        self.assertEqual(self.repo.head_sha(), result.detail["sha"])
        self.assertEqual("", self.repo.porcelain())

    def test_a_dry_run_changes_nothing(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        before = self.fingerprint()

        result = commit_stage.run(self.repo.path, message="ship: x", dry_run=True)

        self.assertEqual("skipped", result.outcome)
        self.assertIn("dry-run", result.reason)
        self.assertWorkingCopyUnchanged(before)


@requires_git
class TestPublishStage(RepoTestCase):
    def test_a_first_publish_sets_upstream_tracking(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")

        result = publish_stage.run(self.repo.path, remote="origin", branch="feature/x")

        self.assertEqual("succeeded", result.outcome)
        self.assertTrue(result.detail["set_upstream"])
        self.assertIn("feature/x", self.repo.remote_branches())

    def test_a_second_publish_does_not_repoint_upstream(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "1\n")
        self.repo.commit("work")
        publish_stage.run(self.repo.path, remote="origin", branch="feature/x")

        self.repo.write_file("a.txt", "2\n")
        self.repo.commit("more work")
        result = publish_stage.run(self.repo.path, remote="origin", branch="feature/x")

        self.assertEqual("succeeded", result.outcome)
        self.assertFalse(result.detail["set_upstream"])

    def test_the_published_head_matches_the_local_head(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        expected = self.repo.commit("work")

        result = publish_stage.run(self.repo.path, remote="origin", branch="feature/x")

        self.assertEqual(expected, result.detail["head_sha"])

    def test_pushing_to_a_missing_remote_fails_with_a_named_cause(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")

        result = publish_stage.run(self.repo.path, remote="nowhere", branch="feature/x")

        self.assertEqual("failed", result.outcome)
        self.assertIn(result.classification, ("precondition", "permission"))
        self.assertTrue(result.detail["error"])

    def test_a_dry_run_publishes_nothing(self) -> None:
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")

        result = publish_stage.run(
            self.repo.path, remote="origin", branch="feature/x", dry_run=True
        )

        self.assertEqual("skipped", result.outcome)
        self.assertNotIn("feature/x", self.repo.remote_branches())


if __name__ == "__main__":
    unittest.main()
