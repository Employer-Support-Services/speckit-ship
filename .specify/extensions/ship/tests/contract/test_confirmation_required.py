"""T041 — SC-006: no merge or release is recorded succeeded without a confirmation.

Merging and releasing are outward-facing and hard to reverse, so FR-013 requires
an explicit confirmation on **every run**. The spec deliberately excludes a
persistent always-yes setting, and the schema honors that by giving one nowhere
to live: ``confirmation.scope`` is the constant ``"run"``.

That constant is the whole design. A setting like ``always_merge: true`` could
not be stored in this record even if someone added it to the config — the writer
would reject the scope, so the feature could not be half-implemented into
existence by a single well-meaning change.
"""

from __future__ import annotations

import unittest

from scripts.state import (
    StateError,
    make_confirmation,
    make_release_record,
    make_stage,
)

SHA = "f9d87244472f8b7410e097957294fa658706e281"


class TestConfirmationShape(unittest.TestCase):
    def test_a_confirmation_is_always_run_scoped(self) -> None:
        confirmation = make_confirmation(granted_by="tester", prompt="Merge #1?")

        self.assertEqual("run", confirmation["scope"])

    def test_a_confirmation_requires_a_grantor(self) -> None:
        with self.assertRaises(StateError):
            make_confirmation(granted_by="")

    def test_a_confirmation_carries_when_it_was_granted(self) -> None:
        confirmation = make_confirmation(granted_by="tester")

        self.assertTrue(confirmation["granted_at"])


class TestMergeRequiresConfirmation(unittest.TestCase):
    def test_a_succeeded_merge_without_a_confirmation_is_rejected(self) -> None:
        with self.assertRaises(StateError) as ctx:
            make_stage(stage="merge", outcome="succeeded")

        message = str(ctx.exception)
        self.assertIn("FR-013", message)
        self.assertIn("SC-006", message)
        self.assertIn("no unattended path", message)

    def test_a_failed_merge_also_requires_a_confirmation(self) -> None:
        """A merge that was attempted and failed was still authorized to attempt."""
        with self.assertRaises(StateError):
            make_stage(stage="merge", outcome="failed", classification="merge_conflict")

    def test_a_merge_with_a_confirmation_is_accepted(self) -> None:
        stage = make_stage(
            stage="merge", outcome="succeeded", confirmation=make_confirmation(granted_by="tester")
        )

        self.assertEqual("succeeded", stage["outcome"])
        self.assertEqual("run", stage["confirmation"]["scope"])

    def test_a_forged_persistent_scope_is_rejected(self) -> None:
        """There is no always-yes, and the record refuses to pretend otherwise."""
        for forged_scope in ("forever", "always", "repository", "user", "global"):
            with self.assertRaises(StateError, msg=f"scope={forged_scope}"):
                make_stage(
                    stage="merge",
                    outcome="succeeded",
                    confirmation={
                        "granted_by": "config",
                        "granted_at": "2026-08-13T00:00:00Z",
                        "scope": forged_scope,
                    },
                )


class TestReleaseRequiresConfirmation(unittest.TestCase):
    def test_a_succeeded_release_without_a_confirmation_is_rejected(self) -> None:
        record = make_release_record(
            mode="observed", from_merge_sha=SHA, outcome="released", evidence="run 1 succeeded"
        )

        with self.assertRaises(StateError) as ctx:
            make_stage(stage="release", outcome="succeeded", release=record)

        self.assertIn("FR-013", str(ctx.exception))

    def test_the_merge_confirmation_does_not_carry_forward_implicitly(self) -> None:
        """Each gate is confirmed on its own. Two irreversible acts, two answers."""
        merge_stage = make_stage(
            stage="merge",
            outcome="succeeded",
            confirmation=make_confirmation(granted_by="tester", prompt="Merge?"),
        )

        # Building the release stage without passing that confirmation along
        # fails — there is no ambient authorization to inherit.
        with self.assertRaises(StateError):
            make_stage(
                stage="release",
                outcome="succeeded",
                release=make_release_record(
                    mode="observed", from_merge_sha=SHA, outcome="released", evidence="e"
                ),
            )

        self.assertEqual("run", merge_stage["confirmation"]["scope"])


class TestOtherStagesDoNotRequireConfirmation(unittest.TestCase):
    def test_non_outward_facing_stages_need_no_confirmation(self) -> None:
        """The gate is on irreversibility, not on ceremony."""
        for stage in ("preflight", "commit", "publish", "pull_request", "checks", "cleanup"):
            record = make_stage(stage=stage, outcome="succeeded")
            self.assertNotIn("confirmation", record, f"{stage} should not require one")

    def test_an_undetermined_merge_needs_no_confirmation(self) -> None:
        """Nothing outward-facing happened, so there was nothing to authorize."""
        record = make_stage(
            stage="merge",
            outcome="undetermined",
            reason="mergeability-not-computed: the service did not report",
        )

        self.assertEqual("undetermined", record["outcome"])

    def test_a_skipped_release_needs_no_confirmation(self) -> None:
        record = make_stage(
            stage="release",
            outcome="skipped",
            reason="release-mode-none: this repository has no release path",
        )

        self.assertEqual("skipped", record["outcome"])


if __name__ == "__main__":
    unittest.main()
