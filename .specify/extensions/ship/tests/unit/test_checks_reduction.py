"""T037 — rollup reduction, the four outcomes, and the undetermined shapes.

The assertion this module exists for is
``test_an_empty_rollup_reduces_to_undetermined_never_passed``. A repository with
no checks configured has no green to wait for, and the tempting reduction —
"nothing failed, therefore pass" — is precisely the inference FR-012 forbids.
It would merge unattended on a repository that has no CI at all.

Fixtures are the recorded payloads in ``tests/fixtures/gh/``, so the field names
and the null-vs-empty distinctions are GitHub's rather than ours.
"""

from __future__ import annotations

import unittest

from scripts.stages.checks import (
    ChecksOutcome,
    normalize_entry,
    reduce_rollup,
)
from scripts.state import is_determined, reason_of, value_of
from tests.contract.recorded_client import load_fixture, normalize_pr_view


def view(fixture: str):
    return normalize_pr_view(load_fixture(fixture))


class TestNormalizeEntry(unittest.TestCase):
    def test_a_completed_successful_check_run(self) -> None:
        name, outcome, url = normalize_entry(
            {"__typename": "CheckRun", "name": "build", "status": "COMPLETED",
             "conclusion": "SUCCESS", "detailsUrl": "https://example.invalid/1"}
        )
        self.assertEqual(("build", "success", "https://example.invalid/1"), (name, outcome, url))

    def test_an_in_progress_check_run_is_pending(self) -> None:
        _, outcome, _ = normalize_entry(
            {"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS", "conclusion": None}
        )
        self.assertEqual("pending", outcome)

    def test_a_completed_run_with_no_conclusion_is_pending_not_success(self) -> None:
        """An unreadable result is not a passing one."""
        _, outcome, _ = normalize_entry(
            {"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": None}
        )
        self.assertEqual("pending", outcome)

    def test_status_context_entries_are_handled(self) -> None:
        """The rollup mixes CheckRun and StatusContext shapes in one array."""
        name, outcome, url = normalize_entry(
            {"__typename": "StatusContext", "context": "ci/legacy", "state": "SUCCESS",
             "targetUrl": "https://example.invalid/2"}
        )
        self.assertEqual(("ci/legacy", "success", "https://example.invalid/2"), (name, outcome, url))

    def test_action_required_is_a_failure(self) -> None:
        _, outcome, _ = normalize_entry(
            {"__typename": "CheckRun", "name": "x", "status": "COMPLETED",
             "conclusion": "ACTION_REQUIRED"}
        )
        self.assertEqual("failure", outcome)

    def test_an_unrecognized_conclusion_fails_closed(self) -> None:
        """A conclusion we do not know is not assumed benign."""
        _, outcome, _ = normalize_entry(
            {"__typename": "CheckRun", "name": "x", "status": "COMPLETED",
             "conclusion": "SOMETHING_NEW"}
        )
        self.assertEqual("failure", outcome)


class TestTheFourOutcomes(unittest.TestCase):
    def test_all_success_reduces_to_passed(self) -> None:
        result = reduce_rollup(view("captured-all-success"))

        self.assertEqual("passed", result.outcome)
        self.assertEqual(6, len(result.checks))
        self.assertEqual([], result.required_failures)

    def test_a_required_failure_reduces_to_failed(self) -> None:
        result = reduce_rollup(
            view("synth-required-failure"),
            required_names=["build", "unit-tests", "lint"],
            requiredness_known=True,
        )

        self.assertEqual("failed", result.outcome)
        self.assertEqual(["unit-tests"], result.required_failures)

    def test_a_running_check_reduces_to_pending(self) -> None:
        result = reduce_rollup(
            view("synth-pending"), required_names=["build", "unit-tests"], requiredness_known=True
        )

        self.assertEqual("pending", result.outcome)
        self.assertFalse(result.terminal)

    def test_pending_is_not_terminal_but_the_others_are(self) -> None:
        self.assertFalse(ChecksOutcome("pending").terminal)
        for outcome in ("passed", "failed", "undetermined"):
            self.assertTrue(ChecksOutcome(outcome, reason="r").terminal)


class TestEmptyRollup(unittest.TestCase):
    def test_an_empty_rollup_reduces_to_undetermined_never_passed(self) -> None:
        """The test this module exists for.

        'Nothing failed, therefore pass' would merge unattended on a repository
        with no CI whatsoever.
        """
        result = reduce_rollup(view("synth-empty-rollup"))

        self.assertEqual("undetermined", result.outcome)
        self.assertNotEqual("passed", result.outcome)
        self.assertIn("no-checks-configured", result.reason)

    def test_the_reason_explains_there_is_no_green_to_wait_for(self) -> None:
        result = reduce_rollup(view("synth-empty-rollup"))

        self.assertIn("no green to wait for", result.reason)


class TestUnreadableRollup(unittest.TestCase):
    def test_a_null_rollup_is_unreadable_not_empty(self) -> None:
        """Measured on a real CONFLICTING PR: the key is present with value null.

        Reducing that to 'no checks configured' turns "GitHub did not compute a
        rollup" into a claim about the repository.
        """
        result = reduce_rollup(view("captured-conflicting-null-rollup"))

        self.assertEqual("undetermined", result.outcome)
        self.assertIn("rollup-unreadable", result.reason)
        self.assertNotIn("no-checks-configured", result.reason)

    def test_the_two_undetermined_shapes_carry_different_reasons(self) -> None:
        """'We stopped waiting' and 'there was nothing to wait for' are different."""
        empty = reduce_rollup(view("synth-empty-rollup"))
        unreadable = reduce_rollup(view("captured-conflicting-null-rollup"))

        self.assertNotEqual(empty.reason, unreadable.reason)


class TestRequiredness(unittest.TestCase):
    def test_optional_failures_do_not_gate_when_requiredness_is_known(self) -> None:
        """Only required checks gate; optional failures are reported alongside."""
        result = reduce_rollup(
            view("synth-mixed-required-optional"),
            required_names=["build", "unit-tests"],
            requiredness_known=True,
        )

        self.assertEqual("passed", result.outcome)
        self.assertCountEqual(
            ["optional-coverage-upload", "optional-preview-deploy"], result.optional_failures
        )

    def test_a_failure_with_unknown_requiredness_is_undetermined_not_failed(self) -> None:
        """An optional-looking failure may in fact be required.

        Neither 'failed' (over-claims) nor 'passed' (merges on a possibly-gating
        failure) is honest here.
        """
        result = reduce_rollup(view("synth-required-failure"), requiredness_known=False)

        self.assertEqual("undetermined", result.outcome)
        self.assertIn("check-requiredness-unknown", result.reason)
        self.assertIn("unit-tests", result.reason)

    def test_green_checks_pass_even_when_requiredness_is_unknown(self) -> None:
        """Requiredness only matters when something failed."""
        result = reduce_rollup(view("captured-all-success"), requiredness_known=False)

        self.assertEqual("passed", result.outcome)

    def test_requiredness_is_recorded_as_undetermined_when_unreadable(self) -> None:
        result = reduce_rollup(view("captured-all-success"), requiredness_known=False)

        for check in result.checks:
            self.assertFalse(is_determined(check["required"]))
            self.assertIn("requiredness-unknown", reason_of(check["required"]))

    def test_requiredness_is_recorded_as_determined_when_readable(self) -> None:
        result = reduce_rollup(
            view("captured-all-success"),
            required_names=["Validate Migration Timestamps"],
            requiredness_known=True,
        )

        by_name = {c["name"]: c for c in result.checks}
        self.assertIs(True, value_of(by_name["Validate Migration Timestamps"]["required"]))
        for name, check in by_name.items():
            self.assertTrue(is_determined(check["required"]))
            if name != "Validate Migration Timestamps":
                self.assertIs(False, value_of(check["required"]))

    def test_a_cancelled_required_check_fails(self) -> None:
        result = reduce_rollup(
            view("synth-cancelled"),
            required_names=["build", "unit-tests"],
            requiredness_known=True,
        )

        self.assertEqual("failed", result.outcome)
        self.assertEqual(["build"], result.required_failures)


class TestEveryCheckCarriesACaptureTime(unittest.TestCase):
    def test_fr_027_holds_for_every_check_result(self) -> None:
        result = reduce_rollup(view("captured-all-success"))

        for check in result.checks:
            self.assertIn("captured_at", check)
            self.assertTrue(check["captured_at"])


if __name__ == "__main__":
    unittest.main()
