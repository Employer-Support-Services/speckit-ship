"""T056 — SC-008: a resumed run duplicates nothing.

Two failures this guards against, both of which produce visible, outward-facing
mistakes:

**A duplicate pull request.** A re-run must adopt the open PR for the branch, not
open a second one.

**A duplicate merge or release.** If the developer merged in the web UI between
runs — which is common and entirely legitimate — the re-run must notice, adopt
the merge commit, and continue to the release stage rather than attempting a
second merge.

The second is why the journal alone is not enough. Recorded state says what was
true when it was written; the world is the authority on what is true now.
"""

from __future__ import annotations

import unittest

from scripts import config as config_mod
from scripts import engine, pipeline
from scripts import state as state_mod
from tests.contract.recorded_client import RecordedClient, load_fixture
from tests.integration.harness import RepoTestCase, git, requires_git


class FakeProfile:
    """The minimum profile surface the pipeline reads."""

    def __init__(self, *, release_mode=None, integration_branch="trunk"):
        self.integration_branch = state_mod.determined(integration_branch, "git-symbolic-ref")
        self.release_mode = (
            state_mod.determined(release_mode, "workflow-trigger")
            if release_mode
            else state_mod.undetermined("none-determinable: no release path found")
        )
        self.is_repository = state_mod.determined(True, "git-rev-parse")
        self.verified_at = state_mod.now_iso()

    def to_state(self):
        return {
            "is_repository": self.is_repository,
            "integration_branch": self.integration_branch,
            "release_mode": self.release_mode,
            "verified_at": self.verified_at,
        }


@requires_git
class ResumeCase(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.config = config_mod.defaults()
        self.profile = FakeProfile(release_mode="none")

    def run_pipeline(self, client, **kwargs):
        """Drive a run with a fake clock.

        The clock must advance when the pipeline sleeps, or a stage that waits
        for a wall-clock deadline (checks polling, release watching) spins
        against real time. Advancing it here is what lets a 30-minute wait cap
        be exercised in milliseconds.
        """
        now = [1_000_000.0]

        def clock() -> float:
            # Advance a little on every reading too, so a loop that never sleeps
            # still reaches its deadline rather than hanging the suite.
            now[0] += 1.0
            return now[0]

        def sleeper(seconds: float) -> None:
            now[0] += max(seconds, 1.0)

        return pipeline.execute(
            self.repo.path,
            profile=self.profile,
            config=self.config,
            client=client,
            interaction=pipeline.unattended("test"),
            branch="feature/x",
            target="trunk",
            sleeper=sleeper,
            clock=clock,
            **kwargs,
        )

    def runs(self):
        return state_mod.load(self.repo.path).document.get("runs", [])


class TestNoDuplicatePullRequest(ResumeCase):
    def test_a_second_run_adopts_the_open_pull_request(self) -> None:
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)
        first_created = len(client.created_prs)

        self.run_pipeline(client)

        self.assertEqual(1, first_created)
        self.assertEqual(
            1, len(client.created_prs), "a second pull request was created on resume"
        )

    def test_the_adopted_pull_request_keeps_its_number(self) -> None:
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)
        original = client.created_prs[0]["number"]

        self.run_pipeline(client)
        runs = self.runs()

        self.assertTrue(all(r["pr"]["number"] == original for r in runs if r.get("pr")))

    def test_resuming_reuses_the_same_run_id(self) -> None:
        """A resumption continues a run; it does not start a parallel history."""
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)
        self.run_pipeline(client)

        run_ids = {run["run_id"] for run in self.runs()}
        self.assertEqual(1, len(run_ids), f"expected one run, got {run_ids}")

    def test_a_lookup_failure_does_not_produce_a_duplicate(self) -> None:
        """'Could not tell' must never be read as 'no PR exists'."""

        class FlakyClient(RecordedClient):
            def find_pr(self, branch):
                from scripts.hosting import Result

                self.calls.append(("find_pr", branch))
                return Result.unknown("gh-timeout: `gh pr view` did not return")

        client = FlakyClient(pr_views=[load_fixture("synth-pending")])

        outcome = self.run_pipeline(client)

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)
        self.assertEqual([], client.created_prs)


class TestExternalMergeIsAdoptedNotRepeated(ResumeCase):
    def test_a_pr_merged_outside_the_run_is_not_merged_again(self) -> None:
        """The developer merged in the web UI between runs."""
        pending = load_fixture("synth-pending")
        client = RecordedClient(pr_views=[pending])

        self.run_pipeline(client)
        self.assertEqual([], client.merged)

        # The world moves on: the PR is now merged, with a merge commit.
        merged_view = load_fixture("captured-merged-unknown-mergeable")
        merged_view["number"] = client.created_prs[0]["number"]
        client._pr_views = [merged_view]
        client._prs["feature/x"]["state"] = "MERGED"

        self.run_pipeline(client)

        self.assertEqual(
            [], client.merged, "the run merged a pull request that was already merged"
        )

    def test_the_external_merge_commit_is_recorded(self) -> None:
        pending = load_fixture("synth-pending")
        client = RecordedClient(pr_views=[pending])
        self.run_pipeline(client)

        merged_view = load_fixture("captured-merged-unknown-mergeable")
        merged_view["number"] = client.created_prs[0]["number"]
        expected_sha = merged_view["mergeCommit"]["oid"]
        client._pr_views = [merged_view]
        client._prs["feature/x"]["state"] = "MERGED"

        self.run_pipeline(client)

        run = self.runs()[-1]
        self.assertEqual(expected_sha, run["merge_commit_sha"])


class TestResumePoint(ResumeCase):
    def test_a_run_resumes_at_the_first_unsettled_stage(self) -> None:
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])
        self.run_pipeline(client)

        run = self.runs()[-1]

        # Checks were pending, so the run stopped there and that is where it resumes.
        self.assertEqual("checks", engine.next_stage(run))

    def test_an_unpublished_branch_rewinds_to_publish(self) -> None:
        """The recorded publish is re-verified against the remote, not trusted."""
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])
        self.run_pipeline(client)

        # Someone deleted the remote branch between runs.
        git(["push", "origin", "--delete", "feature/x"], cwd=self.repo.path, check=False)

        run = self.runs()[-1]
        reverifier = pipeline.make_reverifier(client, self.repo.path, remote="origin")

        self.assertFalse(reverifier("publish", run))
        self.assertEqual("publish", engine.resume_point(run, reverifier=reverifier))

    def test_an_unreadable_world_does_not_rewind(self) -> None:
        """Rewinding on a network hiccup re-runs a stage that genuinely succeeded."""

        class UnreachableClient(RecordedClient):
            def find_pr(self, branch):
                from scripts.hosting import Result

                return Result.unknown("gh-timeout: unreachable")

            def pr_view(self, number):
                from scripts.hosting import Result

                return Result.unknown("gh-timeout: unreachable")

        client = RecordedClient(pr_views=[load_fixture("synth-pending")])
        self.run_pipeline(client)
        run = self.runs()[-1]

        reverifier = pipeline.make_reverifier(
            UnreachableClient(), self.repo.path, remote="origin"
        )

        self.assertTrue(reverifier("pull_request", run))


class TestConcurrencyRefusal(ResumeCase):
    def test_a_second_run_while_one_holds_the_lock_is_refused(self) -> None:
        from scripts import lock as lock_mod

        lock_mod.acquire(self.repo.path, branch="feature/x", run_id="other-run")
        try:
            client = RecordedClient(pr_views=[load_fixture("synth-pending")])
            outcome = self.run_pipeline(client)

            self.assertEqual(pipeline.EXIT_LOCKED, outcome.exit_code)
            self.assertIn("refused rather than queued", outcome.message)
        finally:
            lock_mod.release(self.repo.path)

    def test_the_lock_is_released_when_a_run_finishes(self) -> None:
        from scripts import lock as lock_mod

        client = RecordedClient(pr_views=[load_fixture("synth-pending")])
        self.run_pipeline(client)

        self.assertFalse(lock_mod.lock_path(self.repo.path).exists())


if __name__ == "__main__":
    unittest.main()
