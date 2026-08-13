"""T032 — every forbidden transition raises rather than warns.

A warning in an unattended pipeline is a log line nobody reads. These are the
requirements that have to hold when no human is watching the terminal, so the
assertion throughout is ``assertRaises``, never "records a warning".

The four that matter most:

* an **undetermined** checks outcome is not a pass (FR-012)
* release requires a merge *and* its commit SHA (FR-014)
* cleanup requires a confirmed merge (FR-023)
* merge and release each require a confirmation for **this run** (FR-013)
"""

from __future__ import annotations

import unittest

from scripts import state as state_mod
from scripts.engine import (
    GuardError,
    NothingToShip,
    assert_can_enter,
    assert_something_to_ship,
    is_complete,
    next_stage,
    resume_point,
)


def run_with(*stages, merge_commit_sha=None):
    """Build a run whose stages are ``(name, outcome)`` pairs, in order."""
    run = state_mod.new_run(branch="feature/x", target_branch="trunk")
    run["merge_commit_sha"] = merge_commit_sha
    for name, outcome in stages:
        record = {
            "stage": name,
            "outcome": outcome,
            "started_at": state_mod.now_iso(),
            "ended_at": state_mod.now_iso(),
        }
        if outcome in ("undetermined", "skipped"):
            record["reason"] = f"{name}-reason: recorded for the test"
        if outcome == "failed":
            record["classification"] = "precondition"
        run["stages"].append(record)
    return run


def confirmation():
    return state_mod.make_confirmation(granted_by="tester", prompt="proceed?")


UP_TO_CHECKS = (
    ("preflight", "succeeded"),
    ("commit", "succeeded"),
    ("publish", "succeeded"),
    ("pull_request", "succeeded"),
)


class TestOrdering(unittest.TestCase):
    def test_a_stage_cannot_start_before_its_predecessors_ran(self) -> None:
        run = run_with(("preflight", "succeeded"))

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "publish")

        self.assertIn("commit", str(ctx.exception))
        self.assertIn("no recorded outcome", str(ctx.exception))

    def test_a_stage_cannot_start_while_a_predecessor_is_in_progress(self) -> None:
        run = run_with(("preflight", "succeeded"), ("commit", "in_progress"))

        with self.assertRaises(GuardError):
            assert_can_enter(run, "publish")

    def test_a_stage_cannot_start_after_a_predecessor_failed(self) -> None:
        run = run_with(("preflight", "succeeded"), ("commit", "failed"))

        with self.assertRaises(GuardError):
            assert_can_enter(run, "publish")

    def test_a_skipped_predecessor_is_settled_and_allows_progress(self) -> None:
        """Skip-with-reason is a legitimate outcome; only reordering is forbidden."""
        run = run_with(
            ("preflight", "succeeded"),
            ("commit", "skipped"),
            ("publish", "succeeded"),
        )

        assert_can_enter(run, "pull_request")


class TestMergeGuard(unittest.TestCase):
    def test_merge_is_refused_when_checks_are_undetermined(self) -> None:
        """FR-012. The single most important refusal in the feature."""
        run = run_with(*UP_TO_CHECKS, ("checks", "undetermined"))

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "merge", confirmation=confirmation())

        message = str(ctx.exception)
        self.assertIn("undetermined", message)
        self.assertIn("not a pass", message)
        self.assertIn("FR-012", message)

    def test_merge_is_refused_when_checks_failed(self) -> None:
        run = run_with(*UP_TO_CHECKS, ("checks", "failed"))

        with self.assertRaises(GuardError):
            assert_can_enter(run, "merge", confirmation=confirmation())

    def test_merge_is_refused_when_checks_were_skipped(self) -> None:
        """A skipped checks stage is settled for ordering, but it is not a pass."""
        run = run_with(*UP_TO_CHECKS, ("checks", "skipped"))

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "merge", confirmation=confirmation())

        self.assertIn("not 'succeeded'", str(ctx.exception))

    def test_merge_is_refused_without_a_confirmation(self) -> None:
        run = run_with(*UP_TO_CHECKS, ("checks", "succeeded"))

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "merge")

        self.assertIn("FR-013", str(ctx.exception))

    def test_merge_is_refused_with_a_confirmation_that_is_not_run_scoped(self) -> None:
        """There is no persistable always-yes value; a forged one is still refused."""
        run = run_with(*UP_TO_CHECKS, ("checks", "succeeded"))
        forged = {"granted_by": "x", "granted_at": state_mod.now_iso(), "scope": "forever"}

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "merge", confirmation=forged)

        self.assertIn("scope", str(ctx.exception))

    def test_merge_is_permitted_on_green_checks_with_a_confirmation(self) -> None:
        run = run_with(*UP_TO_CHECKS, ("checks", "succeeded"))

        assert_can_enter(run, "merge", confirmation=confirmation())


class TestReleaseGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.through_checks = (*UP_TO_CHECKS, ("checks", "succeeded"))

    def test_release_is_refused_when_the_merge_did_not_succeed(self) -> None:
        run = run_with(*self.through_checks, ("merge", "failed"))

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "release", confirmation=confirmation())

        self.assertIn("FR-014", str(ctx.exception))

    def test_release_is_refused_without_a_merge_commit_sha(self) -> None:
        """The correlation key. Without it a release cannot be attributed to this merge."""
        run = run_with(*self.through_checks, ("merge", "succeeded"), merge_commit_sha=None)

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "release", confirmation=confirmation())

        message = str(ctx.exception)
        self.assertIn("merge_commit_sha", message)
        self.assertIn("time proximity", message)

    def test_release_is_refused_without_its_own_confirmation(self) -> None:
        """The merge's confirmation does not carry forward to the release."""
        run = run_with(
            *self.through_checks, ("merge", "succeeded"), merge_commit_sha="a" * 40
        )

        with self.assertRaises(GuardError):
            assert_can_enter(run, "release")

    def test_release_is_permitted_after_a_confirmed_merge_with_a_sha(self) -> None:
        run = run_with(
            *self.through_checks, ("merge", "succeeded"), merge_commit_sha="a" * 40
        )

        assert_can_enter(run, "release", confirmation=confirmation())


class TestCleanupGuard(unittest.TestCase):
    def test_cleanup_is_refused_when_the_merge_did_not_succeed(self) -> None:
        run = run_with(
            *UP_TO_CHECKS,
            ("checks", "succeeded"),
            ("merge", "failed"),
            ("release", "skipped"),
        )

        with self.assertRaises(GuardError) as ctx:
            assert_can_enter(run, "cleanup")

        self.assertIn("FR-023", str(ctx.exception))

    def test_cleanup_is_permitted_after_a_confirmed_merge(self) -> None:
        run = run_with(
            *UP_TO_CHECKS,
            ("checks", "succeeded"),
            ("merge", "succeeded"),
            ("release", "succeeded"),
            merge_commit_sha="a" * 40,
        )

        assert_can_enter(run, "cleanup")

    def test_cleanup_is_permitted_when_release_was_skipped_with_a_reason(self) -> None:
        """A repository with release mode 'none' still cleans up."""
        run = run_with(
            *UP_TO_CHECKS,
            ("checks", "succeeded"),
            ("merge", "succeeded"),
            ("release", "skipped"),
            merge_commit_sha="a" * 40,
        )

        assert_can_enter(run, "cleanup")


class TestNextStageAndResume(unittest.TestCase):
    def test_next_stage_is_the_first_unsettled_one(self) -> None:
        run = run_with(("preflight", "succeeded"), ("commit", "skipped"))

        self.assertEqual("publish", next_stage(run))

    def test_a_fully_settled_run_is_complete(self) -> None:
        run = run_with(*[(stage, "succeeded") for stage in state_mod.STAGES])

        self.assertIsNone(next_stage(run))
        self.assertTrue(is_complete(run))

    def test_resume_rewinds_when_reverification_fails(self) -> None:
        """The developer may have merged in the web UI — or closed the PR."""
        run = run_with(
            ("preflight", "succeeded"),
            ("commit", "succeeded"),
            ("publish", "succeeded"),
            ("pull_request", "succeeded"),
        )

        def reverifier(stage, _run):
            # The recorded PR is gone from the world.
            return stage != "pull_request"

        self.assertEqual("pull_request", resume_point(run, reverifier=reverifier))

    def test_resume_continues_forward_when_everything_reverifies(self) -> None:
        run = run_with(
            ("preflight", "succeeded"),
            ("commit", "succeeded"),
            ("publish", "succeeded"),
            ("pull_request", "succeeded"),
        )

        self.assertEqual("checks", resume_point(run, reverifier=lambda *_: True))


class TestNothingToShip(unittest.TestCase):
    def test_an_identical_branch_stops_before_a_pull_request(self) -> None:
        with self.assertRaises(NothingToShip):
            assert_something_to_ship(ahead=0, branch="feature/x", target="trunk", tree_dirty=False)

    def test_uncommitted_changes_mean_there_is_something_to_ship(self) -> None:
        assert_something_to_ship(ahead=0, branch="feature/x", target="trunk", tree_dirty=True)

    def test_commits_ahead_mean_there_is_something_to_ship(self) -> None:
        assert_something_to_ship(ahead=3, branch="feature/x", target="trunk", tree_dirty=False)

    def test_an_unknown_comparison_is_not_nothing_to_ship(self) -> None:
        """'We could not compare' must not be resolved into 'there is nothing here'."""
        assert_something_to_ship(ahead=None, branch="feature/x", target="trunk", tree_dirty=False)


if __name__ == "__main__":
    unittest.main()
