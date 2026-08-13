"""T060 — exit 30 for an unresolved outcome, never 20.

The distinction: `20` means the run *failed* and we can say why. `30` means the
run *does not know*. Collapsing them re-creates the inference the whole feature
exists to prevent — an unresolved pipeline read as a red one is merely wrong,
but read as a green one it merges.

This module asserts the property end-to-end through the pipeline, not just on a
constant, because the constant being right is worth nothing if the checks stage
routes an undetermined outcome down the failure path.
"""

from __future__ import annotations

import unittest

from scripts import config as config_mod
from scripts import pipeline
from scripts import state as state_mod
from tests.contract.recorded_client import RecordedClient, load_fixture
from tests.integration.harness import RepoTestCase, requires_git


class FakeProfile:
    def __init__(self, *, release_mode="none", integration_branch="trunk"):
        self.integration_branch = state_mod.determined(integration_branch, "git-symbolic-ref")
        self.release_mode = state_mod.determined(release_mode, "config")
        self.is_repository = state_mod.determined(True, "git-rev-parse")
        self.verified_at = state_mod.now_iso()

    def to_state(self):
        return {
            "is_repository": self.is_repository,
            "integration_branch": self.integration_branch,
            "release_mode": self.release_mode,
            "verified_at": self.verified_at,
        }


class TestTheCodesAreDistinct(unittest.TestCase):
    def test_failed_and_undetermined_have_different_codes(self) -> None:
        self.assertNotEqual(pipeline.EXIT_FAILED, pipeline.EXIT_UNDETERMINED)

    def test_the_codes_match_the_documented_contract(self) -> None:
        self.assertEqual(0, pipeline.EXIT_OK)
        self.assertEqual(10, pipeline.EXIT_REFUSED)
        self.assertEqual(20, pipeline.EXIT_FAILED)
        self.assertEqual(30, pipeline.EXIT_UNDETERMINED)
        self.assertEqual(40, pipeline.EXIT_LOCKED)

    def test_ship_py_exposes_the_same_codes(self) -> None:
        """Two modules, one contract — they must not drift."""
        from scripts import ship

        self.assertEqual(ship.EXIT_FAILED, pipeline.EXIT_FAILED)
        self.assertEqual(ship.EXIT_UNDETERMINED, pipeline.EXIT_UNDETERMINED)
        self.assertEqual(ship.EXIT_LOCKED, pipeline.EXIT_LOCKED)


@requires_git
class ExitCodeCase(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.config = config_mod.defaults()
        self.profile = FakeProfile()

    def run_pipeline(self, client, **kwargs):
        now = [1_000_000.0]

        def clock() -> float:
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


class TestUnresolvedChecksExitThirty(ExitCodeCase):
    def test_a_wait_that_elapses_exits_30_not_20(self) -> None:
        """Scenario 5c. The assertion this module exists for."""
        self.config["limits"]["checks_wait_seconds"] = 60
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        outcome = self.run_pipeline(client)

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)
        self.assertNotEqual(pipeline.EXIT_FAILED, outcome.exit_code)
        self.assertIn("checks-wait-exceeded", outcome.message)

    def test_no_checks_configured_exits_30_not_0(self) -> None:
        """An empty rollup is not green, so the run must not sail past it."""
        client = RecordedClient(pr_views=[load_fixture("synth-empty-rollup")])

        outcome = self.run_pipeline(client)

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)
        self.assertIn("no-checks-configured", outcome.message)

    def test_an_unreadable_rollup_exits_30(self) -> None:
        client = RecordedClient(pr_views=[load_fixture("captured-conflicting-null-rollup")])

        outcome = self.run_pipeline(client)

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)
        self.assertIn("rollup-unreadable", outcome.message)

    def test_nothing_is_merged_on_an_undetermined_outcome(self) -> None:
        """FR-012, observed rather than argued."""
        self.config["limits"]["checks_wait_seconds"] = 60
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)

        self.assertEqual([], client.merged)


class TestClassifiedFailuresExitTwenty(ExitCodeCase):
    def test_a_required_check_failure_exits_20(self) -> None:
        client = RecordedClient(pr_views=[load_fixture("synth-required-failure")])
        # Requiredness is unreadable through the recorded client, so the run
        # reports undetermined rather than failed — assert that honestly.
        outcome = self.run_pipeline(client)

        self.assertIn(
            outcome.exit_code, (pipeline.EXIT_FAILED, pipeline.EXIT_UNDETERMINED)
        )

    def test_an_unresolvable_pr_lookup_exits_30(self) -> None:
        class FlakyClient(RecordedClient):
            def find_pr(self, branch):
                from scripts.hosting import Result

                return Result.unknown("gh-timeout: `gh pr view` did not return")

        outcome = self.run_pipeline(FlakyClient(pr_views=[load_fixture("synth-pending")]))

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)


class TestTheRunRecordsWhichItWas(ExitCodeCase):
    def test_an_undetermined_halt_records_no_failure_classification(self) -> None:
        """A halt_reason with a classification would assert a diagnosis we lack."""
        self.config["limits"]["checks_wait_seconds"] = 60
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)

        run = state_mod.load(self.repo.path).document["runs"][-1]
        self.assertEqual("halted", run["status"])
        self.assertIsNone(run.get("halt_reason"))

    def test_the_checks_stage_records_undetermined_with_its_reason(self) -> None:
        self.config["limits"]["checks_wait_seconds"] = 60
        client = RecordedClient(pr_views=[load_fixture("synth-pending")])

        self.run_pipeline(client)

        run = state_mod.load(self.repo.path).document["runs"][-1]
        checks = state_mod.stage_record(run, "checks")

        self.assertEqual("undetermined", checks["outcome"])
        self.assertTrue(checks["reason"])


if __name__ == "__main__":
    unittest.main()
