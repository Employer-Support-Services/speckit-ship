"""T064 — the repair loop end to end through the pipeline (FR-016, FR-019, FR-020).

The unit tests prove the ledger counts and the conflict repair resolves. This
module proves the pipeline actually *uses* them: that a classified failure is
reported before repair is attempted, that the budget is honored by the run and
not just by the ledger object, and that exhaustion halts leaving the branch and
pull request intact.

Acceptance 2.4 gets its own class. A proposed repair must be **described and
awaited**, never applied — and the run must not report progress it did not make.
"""

from __future__ import annotations

import unittest

from scripts import config as config_mod
from scripts import pipeline
from scripts import state as state_mod
from tests.contract.recorded_client import RecordedClient, load_fixture
from tests.integration.harness import RepoTestCase, requires_git

REQUIRED = ["build", "unit-tests", "lint"]


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


@requires_git
class RepairLoopCase(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo.branch("feature/x")
        self.repo.write_file("a.txt", "content\n")
        self.repo.commit("work")
        self.config = config_mod.defaults()
        self.profile = FakeProfile()
        self.reported: list = []

    def interaction(self, *, proposer=None) -> pipeline.Interaction:
        return pipeline.Interaction(
            confirm_commit=lambda _t: True,
            review_pr=lambda c: {"title": c["title"], "body": c["body"]},
            confirm_gate=lambda stage, prompt: state_mod.make_confirmation(
                granted_by="test", prompt=prompt
            ),
            propose_repair=proposer,
            report=self.reported.append,
        )

    def run_pipeline(self, client, *, proposer=None):
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
            interaction=self.interaction(proposer=proposer),
            branch="feature/x",
            target="trunk",
            sleeper=sleeper,
            clock=clock,
        )

    def failing_client(self, **kwargs) -> RecordedClient:
        return RecordedClient(
            pr_views=[load_fixture("synth-required-failure")],
            required_check_names=REQUIRED,
            **kwargs,
        )

    def output(self) -> str:
        return "\n".join(self.reported)

    def last_run(self):
        return state_mod.load(self.repo.path).document["runs"][-1]


class TestClassificationIsReportedBeforeRepair(RepairLoopCase):
    def test_a_failing_check_is_classified_as_check_failure(self) -> None:
        self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        self.assertIn("classified as check_failure", self.output())

    def test_the_classification_precedes_the_repair_attempt_in_the_report(self) -> None:
        """Acceptance 2.1 — the developer learns what broke before we change things."""
        self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "fix the assertion"}
        )

        text = self.output()
        self.assertLess(
            text.index("classified as check_failure"),
            text.index("Attempting repair"),
            "the repair attempt was reported before the classification",
        )

    def test_the_classification_is_explained_not_just_named(self) -> None:
        self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        self.assertIn("required checks reported a failure", self.output())


class TestProposedRepairsAreNeverApplied(RepairLoopCase):
    def test_a_proposal_is_recorded_with_proposed_authority(self) -> None:
        self.run_pipeline(
            self.failing_client(),
            proposer=lambda e: {"description": "widen the timeout in test_slow"},
        )

        repairs = self.last_run()["repairs"]

        self.assertEqual(1, len(repairs))
        self.assertEqual("proposed", repairs[0]["authority"])
        self.assertEqual("check_failure", repairs[0]["targets"])

    def test_a_proposal_carries_no_pushed_sha(self) -> None:
        """Structural: it cannot claim to have landed."""
        self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "change x"}
        )

        self.assertIsNone(self.last_run()["repairs"][0]["pushed_sha"])

    def test_the_run_says_plainly_that_nothing_was_applied(self) -> None:
        self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "change x"}
        )

        self.assertIn("NOT been applied", self.output())

    def test_the_proposer_receives_the_failing_check_names(self) -> None:
        seen = {}

        def proposer(evidence):
            seen.update(evidence)
            return None

        self.run_pipeline(self.failing_client(), proposer=proposer)

        names = [c["name"] for c in seen["failing_checks"]]
        self.assertIn("unit-tests", names)

    def test_a_proposal_halts_rather_than_continuing_to_merge(self) -> None:
        outcome = self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "change x"}
        )

        self.assertEqual(pipeline.EXIT_FAILED, outcome.exit_code)

    def test_nothing_is_merged_when_a_repair_is_only_proposed(self) -> None:
        client = self.failing_client()

        self.run_pipeline(client, proposer=lambda e: {"description": "change x"})

        self.assertEqual([], client.merged)


class TestBudgetIsHonoredByTheRun(RepairLoopCase):
    def test_a_zero_budget_attempts_no_repair(self) -> None:
        self.config["limits"]["repair_budget"] = 0
        proposed = []

        self.run_pipeline(
            self.failing_client(),
            proposer=lambda e: proposed.append(e) or {"description": "x"},
        )

        self.assertEqual([], proposed)
        self.assertEqual([], self.last_run().get("repairs", []))

    def test_a_zero_budget_says_repair_is_disabled(self) -> None:
        self.config["limits"]["repair_budget"] = 0

        self.run_pipeline(self.failing_client(), proposer=lambda e: {"description": "x"})

        self.assertIn("Repair is disabled", self.output())

    def test_attempts_never_exceed_the_budget(self) -> None:
        self.config["limits"]["repair_budget"] = 1

        self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "attempt"}
        )

        self.assertLessEqual(len(self.last_run()["repairs"]), 1)


class TestHaltLeavesEverythingIntact(RepairLoopCase):
    def test_the_pull_request_is_not_closed_on_halt(self) -> None:
        """FR-020 — the run halts; it does not clean up after itself."""
        client = self.failing_client()

        self.run_pipeline(client, proposer=lambda e: None)

        pr = self.last_run()["pr"]
        self.assertIsNotNone(pr)
        self.assertEqual("OPEN", client._prs["feature/x"]["state"])

    def test_the_branch_still_exists_locally_and_remotely(self) -> None:
        self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        self.assertEqual("feature/x", self.repo.current_branch())
        self.assertIn("feature/x", self.repo.remote_branches())

    def test_the_halt_report_lists_every_attempt(self) -> None:
        outcome = self.run_pipeline(
            self.failing_client(), proposer=lambda e: {"description": "widen the timeout"}
        )

        self.assertIn("repair attempt(s) made", outcome.message)
        self.assertIn("widen the timeout", outcome.message)
        self.assertIn("proposed, not applied", outcome.message)

    def test_the_halt_report_names_the_stage_and_the_cause(self) -> None:
        """SC-004 — nameable from the report alone, without opening github.com."""
        outcome = self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        self.assertIn("Halted at checks", outcome.message)
        self.assertIn("check_failure", outcome.message)

    def test_the_run_is_recorded_as_halted_with_its_classification(self) -> None:
        self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        run = self.last_run()

        self.assertEqual("halted", run["status"])
        self.assertEqual("check_failure", run["halt_reason"]["classification"])
        self.assertEqual("checks", run["halt_reason"]["stage"])

    def test_a_halted_run_is_resumable_rather_than_orphaned(self) -> None:
        self.run_pipeline(self.failing_client(), proposer=lambda e: None)
        first = self.last_run()["run_id"]

        self.run_pipeline(self.failing_client(), proposer=lambda e: None)

        run_ids = {r["run_id"] for r in state_mod.load(self.repo.path).document["runs"]}
        self.assertEqual({first}, run_ids)


class TestUndeterminedIsNotRepaired(RepairLoopCase):
    def test_an_unresolved_checks_outcome_attempts_no_repair(self) -> None:
        """There is nothing to repair — we do not know that anything is broken."""
        self.config["limits"]["checks_wait_seconds"] = 60
        proposed = []

        self.run_pipeline(
            RecordedClient(
                pr_views=[load_fixture("synth-pending")], required_check_names=REQUIRED
            ),
            proposer=lambda e: proposed.append(e) or {"description": "x"},
        )

        self.assertEqual([], proposed)
        self.assertEqual([], self.last_run().get("repairs", []))

    def test_an_unresolved_outcome_still_exits_30(self) -> None:
        self.config["limits"]["checks_wait_seconds"] = 60

        outcome = self.run_pipeline(
            RecordedClient(
                pr_views=[load_fixture("synth-pending")], required_check_names=REQUIRED
            )
        )

        self.assertEqual(pipeline.EXIT_UNDETERMINED, outcome.exit_code)


if __name__ == "__main__":
    unittest.main()
