"""T058 — the repair budget (FR-019).

Two properties: attempts never exceed the budget, and `0` disables repair
**entirely** — not "one free attempt", not "unbounded". The spec's stated
assumption is that unbounded automatic repair on a shared branch is unsafe, so
the off switch has to actually be off.
"""

from __future__ import annotations

import unittest

from scripts.repair import RepairLedger
from scripts.state import StateError, make_repair_attempt


def attempt(n: int, *, authority: str = "mechanical", targets: str = "merge_conflict"):
    return make_repair_attempt(
        attempt=n,
        targets=targets,
        authority=authority,
        description=f"attempt {n}",
        pushed_sha=("a" * 40) if authority == "mechanical" else None,
    )


class TestBudgetAccounting(unittest.TestCase):
    def test_a_fresh_ledger_reports_the_full_budget(self) -> None:
        ledger = RepairLedger(2)

        self.assertTrue(ledger.enabled)
        self.assertTrue(ledger.can_attempt())
        self.assertEqual(0, ledger.used)
        self.assertEqual(2, ledger.remaining)
        self.assertFalse(ledger.exhausted)

    def test_attempts_are_counted_and_the_remainder_falls(self) -> None:
        ledger = RepairLedger(2)

        ledger.record(attempt(1))

        self.assertEqual(1, ledger.used)
        self.assertEqual(1, ledger.remaining)
        self.assertTrue(ledger.can_attempt())

    def test_the_budget_is_exhausted_at_exactly_the_configured_count(self) -> None:
        ledger = RepairLedger(2)
        ledger.record(attempt(1))
        ledger.record(attempt(2))

        self.assertTrue(ledger.exhausted)
        self.assertFalse(ledger.can_attempt())
        self.assertEqual(0, ledger.remaining)

    def test_recording_past_the_budget_raises_rather_than_capping(self) -> None:
        """Silently granting one more would make the budget advisory."""
        ledger = RepairLedger(1)
        ledger.record(attempt(1))

        with self.assertRaises(RuntimeError) as ctx:
            ledger.record(attempt(2))

        self.assertIn("exhausted", str(ctx.exception))

    def test_attempt_numbers_are_one_based_and_sequential(self) -> None:
        ledger = RepairLedger(3)

        self.assertEqual(1, ledger.next_attempt_number())
        ledger.record(attempt(1))
        self.assertEqual(2, ledger.next_attempt_number())
        ledger.record(attempt(2))
        self.assertEqual(3, ledger.next_attempt_number())


class TestZeroDisablesRepair(unittest.TestCase):
    def test_a_zero_budget_is_disabled_not_one_free_attempt(self) -> None:
        ledger = RepairLedger(0)

        self.assertFalse(ledger.enabled)
        self.assertFalse(ledger.can_attempt())
        self.assertTrue(ledger.exhausted)

    def test_a_zero_budget_refuses_the_very_first_attempt(self) -> None:
        ledger = RepairLedger(0)

        with self.assertRaises(RuntimeError):
            ledger.record(attempt(1))

    def test_a_zero_budget_says_so_in_the_halt_report(self) -> None:
        ledger = RepairLedger(0)

        self.assertIn("budget is 0", ledger.render())
        self.assertIn("disables repair", ledger.render())

    def test_a_negative_budget_is_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            RepairLedger(-1)


class TestHaltReport(unittest.TestCase):
    def test_every_attempt_is_listed(self) -> None:
        """FR-020: the halt reports every repair attempted."""
        ledger = RepairLedger(2)
        ledger.record(attempt(1))
        ledger.record(attempt(2, authority="proposed", targets="check_failure"))

        rendered = ledger.render()

        self.assertIn("2 of 2 repair attempt(s) made", rendered)
        self.assertIn("attempt 1", rendered)
        self.assertIn("attempt 2", rendered)

    def test_a_mechanical_attempt_is_reported_as_applied(self) -> None:
        ledger = RepairLedger(2)
        ledger.record(attempt(1))

        self.assertIn("applied", ledger.render())

    def test_a_proposed_attempt_is_reported_as_not_applied(self) -> None:
        """Acceptance 2.4 — a proposal must never read as a change that landed."""
        ledger = RepairLedger(2)
        ledger.record(attempt(1, authority="proposed", targets="check_failure"))

        rendered = ledger.render()

        self.assertIn("proposed, not applied", rendered)

    def test_no_attempts_is_reported_explicitly(self) -> None:
        self.assertIn("No repair was attempted", RepairLedger(2).render())


class TestProposedAttemptsCannotClaimToHaveLanded(unittest.TestCase):
    def test_a_proposed_attempt_may_not_carry_a_pushed_sha(self) -> None:
        """Structural: the record writer refuses, so it cannot be recorded wrong."""
        with self.assertRaises(StateError) as ctx:
            make_repair_attempt(
                attempt=1,
                targets="check_failure",
                authority="proposed",
                description="edit the failing assertion",
                pushed_sha="a" * 40,
            )

        self.assertIn("not applied", str(ctx.exception))

    def test_a_mechanical_attempt_may_carry_a_pushed_sha(self) -> None:
        record = make_repair_attempt(
            attempt=1,
            targets="merge_conflict",
            authority="mechanical",
            description="merged origin/trunk",
            pushed_sha="a" * 40,
        )

        self.assertEqual("a" * 40, record["pushed_sha"])


if __name__ == "__main__":
    unittest.main()
