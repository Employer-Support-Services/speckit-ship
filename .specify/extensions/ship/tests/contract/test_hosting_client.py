"""T036 — one set of expectations, run against both HostingClient implementations.

``GhClient`` shells out to `gh`; ``RecordedClient`` replays captured payloads.
Shared expectations are what stop the two drifting: a normalization that exists
only in the real client would make every test pass against the fake and fail in
production, which is the failure mode recorded fixtures are supposed to prevent.

The `gh`-backed cases are skipped when `gh` is absent or unauthenticated, and
they are read-only — no test here creates, merges, or deletes anything.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from typing import Any, Dict

from scripts.hosting import GhClient, Result, _looks_like_no_pr, _parse_gh_version
from tests.contract.recorded_client import RecordedClient, load_fixture, normalize_pr_view


def gh_available() -> bool:
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True, timeout=30)
    except Exception:
        return False
    return True


requires_gh = unittest.skipUnless(gh_available(), "gh is not installed")


class SharedNormalizationExpectations:
    """Expectations both implementations must satisfy.

    Written against the normalization function each client uses, so the contract
    is asserted rather than each client's plumbing being retested.
    """

    def normalize(self, fixture: str) -> Dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def test_unknown_mergeable_normalizes_to_none_not_conflicting(self) -> None:
        """Invariant 1. The single most consequential normalization in the seam."""
        view = self.normalize("captured-merged-unknown-mergeable")

        self.assertIsNone(view["mergeable"])
        self.assertEqual("UNKNOWN", view["mergeable_raw"])
        self.assertNotEqual("CONFLICTING", view["mergeable"])

    def test_a_real_conflict_is_preserved_as_conflicting(self) -> None:
        view = self.normalize("captured-conflicting-null-rollup")

        self.assertEqual("CONFLICTING", view["mergeable"])

    def test_a_null_rollup_reports_rollup_not_present(self) -> None:
        """Measured on a real payload: the key exists with value null."""
        view = self.normalize("captured-conflicting-null-rollup")

        self.assertFalse(view["rollup_present"])
        self.assertEqual([], view["rollup"])

    def test_an_empty_rollup_reports_present_but_empty(self) -> None:
        """Distinct from the null case: the repository really has no checks."""
        view = self.normalize("synth-empty-rollup")

        self.assertTrue(view["rollup_present"])
        self.assertEqual([], view["rollup"])

    def test_a_populated_rollup_reports_present(self) -> None:
        view = self.normalize("captured-all-success")

        self.assertTrue(view["rollup_present"])
        self.assertEqual(6, len(view["rollup"]))

    def test_the_merge_commit_oid_is_lifted_to_merge_commit_sha(self) -> None:
        view = self.normalize("captured-merged-unknown-mergeable")

        self.assertEqual(40, len(view["merge_commit_sha"]))

    def test_an_absent_merge_commit_is_none_not_empty_string(self) -> None:
        view = self.normalize("captured-all-success")

        self.assertIsNone(view["merge_commit_sha"])


class TestRecordedClientNormalization(SharedNormalizationExpectations, unittest.TestCase):
    def normalize(self, fixture: str) -> Dict[str, Any]:
        return normalize_pr_view(load_fixture(fixture))


class TestGhClientNormalization(SharedNormalizationExpectations, unittest.TestCase):
    """Runs the same expectations through GhClient's own parsing path.

    The subprocess layer is replaced with the fixture bytes, so this exercises
    ``GhClient.pr_view``'s real normalization without a network call.
    """

    def normalize(self, fixture: str) -> Dict[str, Any]:
        payload = load_fixture(fixture)
        client = GhClient()
        client._run_json = lambda *a, **k: Result.of(payload)  # type: ignore[method-assign]
        result = client.pr_view(payload.get("number", 1))
        self.assertTrue(result.ok, result.reason)
        return result.value


class TestResultObjectContract(unittest.TestCase):
    """Every method returns a result; a hiccup is never a False."""

    def test_an_undetermined_result_is_falsy_but_carries_a_reason(self) -> None:
        result = Result.unknown("gh-timeout: the call did not return")

        self.assertFalse(result)
        self.assertTrue(result.undetermined)
        self.assertIn("gh-timeout", result.reason)
        self.assertIsNone(result.value)

    def test_a_determined_false_value_is_still_an_ok_result(self) -> None:
        """`ok` describes the call, not the answer. False is a real answer."""
        result = Result.of(False)

        self.assertTrue(result.ok)
        self.assertIs(False, result.value)


class TestFindPrDistinguishesAbsenceFromFailure(unittest.TestCase):
    def test_no_pr_for_branch_is_an_answer_not_an_error(self) -> None:
        client = RecordedClient(prs={})

        result = client.find_pr("feature/x")

        self.assertTrue(result.ok)
        self.assertIsNone(result.value)

    def test_an_existing_pr_is_returned(self) -> None:
        client = RecordedClient(
            prs={"feature/x": {"number": 7, "url": "u", "state": "OPEN", "base": "trunk", "head": "feature/x"}}
        )

        result = client.find_pr("feature/x")

        self.assertTrue(result.ok)
        self.assertEqual(7, result.value["number"])

    def test_gh_no_pr_messages_are_recognized_as_absence(self) -> None:
        for message in (
            "no pull requests found for branch feature/x",
            "GraphQL: Could not resolve to a PullRequest with the number of 9.",
            "no open pull requests found",
        ):
            self.assertTrue(_looks_like_no_pr(message, ""), message)

    def test_a_transient_failure_is_not_recognized_as_absence(self) -> None:
        """Collapsing these produces the duplicate PR FR-009 forbids."""
        for message in (
            "gh-timeout: `gh pr view` did not return within 120s",
            "error connecting to api.github.com",
            "HTTP 502",
        ):
            self.assertFalse(_looks_like_no_pr(message, ""), message)


class TestProbeReportsCapabilitiesNotVersions(unittest.TestCase):
    def test_the_recorded_probe_reports_the_floor_versions_constraint(self) -> None:
        client = RecordedClient()

        probe = client.probe()

        self.assertTrue(probe.ok)
        self.assertIs(False, probe.value["capabilities"]["pr_checks_json"])
        self.assertIs(True, probe.value["capabilities"]["pr_view_json"])

    def test_an_unauthenticated_probe_still_returns_a_payload_with_a_reason(self) -> None:
        client = RecordedClient(authenticated=False)

        probe = client.probe()

        self.assertTrue(probe.ok)
        self.assertIs(False, probe.value["authenticated"])
        self.assertIn("gh-unauthenticated", probe.reason)

    def test_the_version_string_is_parsed_from_gh_output(self) -> None:
        self.assertEqual(
            "2.4.0+dfsg1",
            _parse_gh_version("gh version 2.4.0+dfsg1 (2022-03-23 Ubuntu 2.4.0+dfsg1-2)"),
        )
        self.assertIsNone(_parse_gh_version("something unexpected"))


class TestRunsForShaFiltersClientSide(unittest.TestCase):
    """gh 2.4.0 has no --branch filter, so the predicate is applied here."""

    def setUp(self) -> None:
        self.runs = load_fixture("captured-run-list")

    def test_only_runs_matching_both_sha_and_branch_are_returned(self) -> None:
        target = self.runs[0]
        client = RecordedClient(run_list=self.runs)

        result = client.runs_for_sha(target["headSha"], target["headBranch"])

        self.assertTrue(result.ok)
        self.assertTrue(all(r["headSha"] == target["headSha"] for r in result.value))

    def test_a_matching_sha_on_a_different_branch_does_not_match(self) -> None:
        target = self.runs[0]
        client = RecordedClient(run_list=self.runs)

        result = client.runs_for_sha(target["headSha"], "some-other-branch")

        self.assertEqual([], result.value)

    def test_the_captured_run_list_uses_name_not_workflowname(self) -> None:
        """Pins the gh 2.4.0 field-name finding from Spike S2."""
        self.assertIn("name", self.runs[0])
        self.assertNotIn("workflowName", self.runs[0])


@requires_gh
class TestGhClientAgainstTheRealBinary(unittest.TestCase):
    """Read-only probes of the installed `gh`. Never mutates anything."""

    def test_probe_reports_a_version_and_a_capability_map(self) -> None:
        probe = GhClient().probe()

        self.assertTrue(probe.ok, probe.reason)
        self.assertIsNotNone(probe.value["gh_version"])
        self.assertIn("pr_view_json", probe.value["capabilities"])

    def test_pr_checks_json_is_absent_on_the_floor_version(self) -> None:
        """research.md R3's load-bearing finding, re-measured rather than trusted."""
        probe = GhClient().probe()
        capability = probe.value["capabilities"]["pr_checks_json"]

        if probe.value["gh_version"] and probe.value["gh_version"].startswith("2.4."):
            self.assertIs(False, capability)

    def test_an_unparseable_json_response_is_undetermined_not_a_crash(self) -> None:
        client = GhClient()
        client._run = lambda *a, **k: Result.of("not json at all")  # type: ignore[method-assign]

        result = client._run_json(["repo", "view"])

        self.assertFalse(result.ok)
        self.assertIn("gh-unparseable", result.reason)


if __name__ == "__main__":
    unittest.main()
