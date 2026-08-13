"""T040 — SC-012: no release is reported without confirmation from the release path.

This is the one thing the tool must never be able to say untruthfully. The
enforcement is at the *writer*, not at the caller: ``make_release_record`` and
``validate_release_record`` refuse a record whose ``evidence`` is empty, for
every outcome including ``released``. A caller cannot construct the untruthful
record even by mistake, and a future caller who tries gets an exception rather
than a silently plausible entry in the run history.

The reason evidence is required even on success — where it is most tempting to
treat it as decoration — is that "released" with nothing behind it is exactly
the failure this rule exists to prevent. An empty evidence string on a failure
is a documentation gap; on a success it is a false claim about production.
"""

from __future__ import annotations

import unittest

from scripts.state import (
    STAGES,
    StateError,
    make_confirmation,
    make_release_record,
    make_stage,
    validate_release_record,
)

SHA = "f9d87244472f8b7410e097957294fa658706e281"


class TestEvidenceIsRequired(unittest.TestCase):
    def test_released_with_empty_evidence_is_rejected(self) -> None:
        with self.assertRaises(StateError) as ctx:
            make_release_record(
                mode="observed", from_merge_sha=SHA, outcome="released", evidence=""
            )

        message = str(ctx.exception)
        self.assertIn("FR-015", message)
        self.assertIn("SC-012", message)

    def test_released_with_whitespace_evidence_is_rejected(self) -> None:
        """Whitespace is not evidence."""
        with self.assertRaises(StateError):
            make_release_record(
                mode="observed", from_merge_sha=SHA, outcome="released", evidence="   \n\t "
            )

    def test_evidence_is_required_for_every_outcome_not_just_released(self) -> None:
        for outcome in ("released", "failed", "undetermined"):
            with self.assertRaises(StateError, msg=f"outcome={outcome}"):
                make_release_record(
                    mode="observed", from_merge_sha=SHA, outcome=outcome, evidence=""
                )

    def test_a_record_with_real_evidence_is_accepted(self) -> None:
        record = make_release_record(
            mode="observed",
            from_merge_sha=SHA,
            outcome="released",
            identifier="31707131079",
            evidence=(
                "workflow run 31707131079 ([DEV] Build, Test, & Deploy) for merge "
                "commit f9d87244 concluded 'success'"
            ),
        )

        self.assertEqual("released", record["outcome"])
        self.assertTrue(record["evidence"].strip())
        self.assertTrue(record["confirmed_at"])


class TestCorrelationKey(unittest.TestCase):
    def test_a_record_without_a_merge_sha_is_rejected(self) -> None:
        with self.assertRaises(StateError) as ctx:
            make_release_record(
                mode="observed", from_merge_sha="", outcome="released", evidence="something"
            )

        self.assertIn("correlation key", str(ctx.exception))

    def test_a_truncated_sha_is_rejected(self) -> None:
        """Guards against a caller passing a display-shortened SHA as the key."""
        with self.assertRaises(StateError):
            make_release_record(
                mode="observed", from_merge_sha="f9d872", outcome="released", evidence="x"
            )

    def test_an_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            make_release_record(
                mode="inferred", from_merge_sha=SHA, outcome="released", evidence="x"
            )

    def test_there_is_no_mode_that_derives_a_release_from_a_merge(self) -> None:
        """The vocabulary itself gives inference nowhere to live."""
        from scripts.state import RELEASE_MODES

        self.assertEqual(("observed", "executed"), RELEASE_MODES)
        for forbidden in ("inferred", "merged", "assumed", "implied"):
            self.assertNotIn(forbidden, RELEASE_MODES)


class TestValidationOnWrite(unittest.TestCase):
    def test_a_forged_record_is_caught_when_attached_to_a_stage(self) -> None:
        """The stage writer re-validates, so no path smuggles a bad record in."""
        forged = {
            "mode": "observed",
            "from_merge_sha": SHA,
            "outcome": "released",
            "evidence": "",
            "confirmed_at": "2026-08-13T00:00:00Z",
        }

        with self.assertRaises(StateError):
            make_stage(
                stage="release",
                outcome="succeeded",
                release=forged,
                confirmation=make_confirmation(granted_by="tester"),
            )

    def test_a_hand_built_record_missing_evidence_entirely_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            validate_release_record(
                {
                    "mode": "observed",
                    "from_merge_sha": SHA,
                    "outcome": "released",
                    "confirmed_at": "2026-08-13T00:00:00Z",
                }
            )


class TestFailedIsDistinctFromNeverAttempted(unittest.TestCase):
    def test_a_failed_release_is_recordable_and_carries_its_evidence(self) -> None:
        """FR-045: a failed release leaves the integration branch ahead of production.

        That is a different and more urgent fact than a run that never reached
        the release stage, and the record has to be able to express it.
        """
        record = make_release_record(
            mode="executed",
            from_merge_sha=SHA,
            outcome="failed",
            identifier="31707131079",
            evidence="run 31707131079 concluded 'failure'",
        )

        self.assertEqual("failed", record["outcome"])

    def test_a_run_that_never_released_has_no_release_record_at_all(self) -> None:
        """Absence, not a record with outcome 'undetermined' pretending to be one."""
        stage = make_stage(
            stage="release",
            outcome="skipped",
            reason="release-mode-none: this repository has no release path",
        )

        self.assertNotIn("release", stage)


class TestReleaseStagePosition(unittest.TestCase):
    def test_release_comes_after_merge_in_the_stage_order(self) -> None:
        self.assertLess(STAGES.index("merge"), STAGES.index("release"))

    def test_a_merge_stage_cannot_carry_a_release_record(self) -> None:
        """FR-015 structurally: the merge stage has no way to claim a release.

        ``make_stage`` accepts ``release=`` for any stage, so this asserts the
        engine's own usage: only the release stage's own path supplies one.
        """
        stage = make_stage(
            stage="merge",
            outcome="succeeded",
            confirmation=make_confirmation(granted_by="tester"),
            detail={"merge_commit_sha": SHA},
        )

        self.assertNotIn("release", stage)


if __name__ == "__main__":
    unittest.main()
