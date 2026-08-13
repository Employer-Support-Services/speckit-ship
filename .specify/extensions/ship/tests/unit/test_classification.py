"""T057 — failure classification into four classes (FR-016).

The assertion that carries the most weight is
``test_an_undetermined_outcome_gets_no_classification``. An undetermined outcome
is not a failure whose cause we happen not to know — it is a different answer.
Giving it a classification would let a caller treat "we do not know" as "it
broke, somehow", which is the collapse exit codes 20 and 30 exist to prevent.
"""

from __future__ import annotations

import unittest

from scripts.repair import classify, describe_classification
from scripts.state import FAILURE_CLASSIFICATIONS


class TestTheFourClasses(unittest.TestCase):
    def test_a_conflicting_merge_is_a_merge_conflict(self) -> None:
        result = classify(
            stage="merge",
            outcome="failed",
            message="#12 cannot merge cleanly into trunk — the branch conflicts with the target.",
        )

        self.assertEqual("merge_conflict", result)

    def test_a_failing_check_is_a_check_failure(self) -> None:
        result = classify(stage="checks", outcome="failed", message="1 failure")

        self.assertEqual("check_failure", result)

    def test_a_protected_branch_is_a_permission_failure(self) -> None:
        result = classify(
            stage="publish",
            outcome="failed",
            message="remote: error: GH006: Protected branch update failed",
        )

        self.assertEqual("permission", result)

    def test_a_required_review_is_a_permission_failure(self) -> None:
        """The repository's rules are respected as given, never bypassed."""
        result = classify(
            stage="merge",
            outcome="failed",
            message="At least 1 approving review is required by reviewers with write access.",
        )

        self.assertEqual("permission", result)

    def test_an_unmet_stage_precondition_is_a_precondition_failure(self) -> None:
        result = classify(
            stage="commit", outcome="failed", message="git commit failed: nothing to commit"
        )

        self.assertEqual("precondition", result)

    def test_every_class_is_one_the_schema_accepts(self) -> None:
        for stage, message in (
            ("merge", "conflict"),
            ("checks", "failed"),
            ("publish", "permission denied"),
            ("commit", "something else"),
        ):
            result = classify(stage=stage, outcome="failed", message=message)
            self.assertIn(result, FAILURE_CLASSIFICATIONS)


class TestUndeterminedIsNotAClassification(unittest.TestCase):
    def test_an_undetermined_outcome_gets_no_classification(self) -> None:
        """The assertion this module exists for."""
        result = classify(
            stage="checks",
            outcome="undetermined",
            message="checks-wait-exceeded: the configured wait elapsed",
        )

        self.assertIsNone(result)

    def test_an_undetermined_merge_gets_no_classification(self) -> None:
        result = classify(
            stage="merge",
            outcome="undetermined",
            message="mergeability-not-computed: the service did not report",
        )

        self.assertIsNone(result)

    def test_an_undetermined_outcome_mentioning_conflict_is_still_unclassified(self) -> None:
        """Wording must not promote an unresolved outcome into a diagnosis."""
        result = classify(
            stage="merge",
            outcome="undetermined",
            message="mergeability-not-computed — this is NOT a conflict",
        )

        self.assertIsNone(result)

    def test_a_succeeded_or_skipped_outcome_gets_no_classification(self) -> None:
        for outcome in ("succeeded", "skipped", "in_progress"):
            self.assertIsNone(classify(stage="checks", outcome=outcome, message=""))


class TestPermissionWinsOverOtherReadings(unittest.TestCase):
    def test_a_permission_refusal_during_merge_is_not_read_as_a_conflict(self) -> None:
        """The remedies differ entirely: one needs rights, the other needs a rebase."""
        result = classify(
            stage="merge",
            outcome="failed",
            message="403 Resource not accessible by integration (merge conflict resolution required)",
        )

        self.assertEqual("permission", result)


class TestDescriptions(unittest.TestCase):
    def test_every_classification_has_an_actionable_description(self) -> None:
        for classification in FAILURE_CLASSIFICATIONS:
            description = describe_classification(classification)
            self.assertTrue(description)
            self.assertNotEqual("unclassified", description)

    def test_the_undetermined_description_says_no_repair_is_attempted(self) -> None:
        description = describe_classification(None)

        self.assertIn("separate answer", description)
        self.assertIn("no repair", description)

    def test_the_permission_description_says_rules_are_not_bypassed(self) -> None:
        description = describe_classification("permission")

        self.assertIn("never attempts to bypass", description)


if __name__ == "__main__":
    unittest.main()
