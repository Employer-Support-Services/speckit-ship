"""T027 — Determined[T] and its validator.

The central assertion here is ``test_undetermined_with_a_value_is_rejected``.
Every other guarantee in this feature is downstream of it: if a value can be
recorded as "we could not determine this" while carrying a plausible-looking
stand-in, then every consumer reads that stand-in as an observation and SC-007
becomes unverifiable by anything but human inspection.
"""

from __future__ import annotations

import unittest

from scripts.state import (
    StateError,
    determined,
    is_determined,
    now_iso,
    parse_iso,
    reason_of,
    undetermined,
    validate_determined,
    validate_tree,
    value_of,
)


class TestDeterminedConstructors(unittest.TestCase):
    def test_determined_carries_value_source_and_capture_time(self) -> None:
        wrapped = determined("trunk", "git-symbolic-ref")

        self.assertTrue(wrapped["determined"])
        self.assertEqual("trunk", wrapped["value"])
        self.assertEqual("git-symbolic-ref", wrapped["source"])
        self.assertIsNotNone(parse_iso(wrapped["captured_at"]))

    def test_determined_refuses_an_empty_source(self) -> None:
        """A value with no stated provenance is indistinguishable from a guess."""
        with self.assertRaises(StateError) as ctx:
            determined("trunk", "")
        self.assertIn("source", str(ctx.exception))

    def test_undetermined_carries_null_value_and_a_reason(self) -> None:
        wrapped = undetermined("no-remote: this repository has no configured remote")

        self.assertFalse(wrapped["determined"])
        self.assertIsNone(wrapped["value"])
        self.assertIn("no-remote", wrapped["reason"])
        self.assertNotIn("source", wrapped)

    def test_undetermined_refuses_an_empty_reason(self) -> None:
        with self.assertRaises(StateError):
            undetermined("")

    def test_false_is_a_determined_value_not_an_absence(self) -> None:
        """`has_checks: false` is a real answer, distinct from 'we could not tell'."""
        wrapped = determined(False, "workflow-trigger")

        self.assertTrue(is_determined(wrapped))
        self.assertIs(False, value_of(wrapped))
        self.assertIsNone(reason_of(wrapped))

    def test_zero_is_a_determined_value_not_an_absence(self) -> None:
        wrapped = determined(0, "git-rev-list")

        self.assertTrue(is_determined(wrapped))
        self.assertEqual(0, value_of(wrapped))


class TestValidateDetermined(unittest.TestCase):
    def test_accepts_a_well_formed_determined_value(self) -> None:
        validate_determined(determined("main", "config"))

    def test_accepts_a_well_formed_undetermined_value(self) -> None:
        validate_determined(undetermined("checks-wait-exceeded: the cap was reached"))

    def test_undetermined_with_a_value_is_rejected(self) -> None:
        """The one pairing FR-028 forbids.

        This is the shape a well-meaning refactor produces: someone keeps the
        last known value "for context" while marking the field undetermined. To
        every consumer downstream that reads as an observation.
        """
        forged = {
            "determined": False,
            "value": "main",
            "captured_at": now_iso(),
            "reason": "could-not-detect: guessing",
        }

        with self.assertRaises(StateError) as ctx:
            validate_determined(forged)

        message = str(ctx.exception)
        self.assertIn("FR-028", message)
        self.assertIn("main", message)

    def test_undetermined_without_a_reason_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            validate_determined(
                {"determined": False, "value": None, "captured_at": now_iso()}
            )

    def test_determined_without_a_source_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            validate_determined(
                {"determined": True, "value": "main", "captured_at": now_iso()}
            )

    def test_missing_capture_time_is_rejected(self) -> None:
        """FR-027 — every recorded value carries when it was captured."""
        with self.assertRaises(StateError) as ctx:
            validate_determined({"determined": True, "value": "x", "source": "config"})
        self.assertIn("captured_at", str(ctx.exception))

    def test_non_iso_capture_time_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            validate_determined(
                {
                    "determined": True,
                    "value": "x",
                    "source": "config",
                    "captured_at": "last tuesday",
                }
            )

    def test_non_boolean_determined_flag_is_rejected(self) -> None:
        with self.assertRaises(StateError):
            validate_determined(
                {
                    "determined": "yes",
                    "value": "x",
                    "source": "config",
                    "captured_at": now_iso(),
                }
            )

    def test_extra_keys_are_rejected(self) -> None:
        """Keeps a second, unvalidated channel from growing on the wrapper."""
        wrapped = determined("main", "config")
        wrapped["fallback"] = "master"

        with self.assertRaises(StateError):
            validate_determined(wrapped)


class TestValidateTree(unittest.TestCase):
    def test_walks_nested_structures(self) -> None:
        document = {
            "profile": {
                "integration_branch": determined("trunk", "git-symbolic-ref"),
                "nested": {"has_checks": undetermined("no-checks-configured: none")},
            },
            "runs": [{"stages": [{"detail": {"x": determined(1, "config")}}]}],
        }
        validate_tree(document)

    def test_reports_the_path_to_the_offending_wrapper(self) -> None:
        document = {
            "profile": {
                "integration_branch": {
                    "determined": False,
                    "value": "main",
                    "captured_at": now_iso(),
                    "reason": "guessed",
                }
            }
        }

        with self.assertRaises(StateError) as ctx:
            validate_tree(document)

        self.assertIn("profile.integration_branch", str(ctx.exception))

    def test_ignores_plain_objects(self) -> None:
        validate_tree({"pr": {"number": 7, "state": "OPEN"}, "limits": {"a": 1}})


if __name__ == "__main__":
    unittest.main()
