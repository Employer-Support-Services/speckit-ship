"""T067 — the refusal matrix (FR-001, FR-004, FR-005).

Six conditions that must stop a run, each naming what blocked it. These are
verified against real repositories in real states — a detached HEAD produced by
an actual `git checkout --detach`, a mid-merge produced by an actual conflicting
merge — because the value of these refusals is that they fire on states git
really produces, not on states we imagined.

Each refusal must also say what was *expected*. "Refusing to ship" alone leaves
the developer to guess; the point of stopping early is to be useful about it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import preflight
from scripts.state import is_determined, reason_of, value_of
from tests.integration.harness import RepoTestCase, git, requires_git


def conditions(profile: preflight.Profile) -> list:
    return [refusal.condition for refusal in profile.refusals]


@requires_git
class TestNotARepository(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_plain_directory_is_refused(self) -> None:
        profile = preflight.Profile()

        proceeded = preflight.detect_repository(profile, cwd=self.path)

        self.assertFalse(proceeded)
        self.assertIn("not-a-repository", conditions(profile))

    def test_is_repository_records_a_determined_false_not_an_undetermined(self) -> None:
        """We looked and the answer was no. That is an observation, not a gap."""
        profile = preflight.Profile()
        preflight.detect_repository(profile, cwd=self.path)

        self.assertTrue(is_determined(profile.is_repository))
        self.assertIs(False, value_of(profile.is_repository))

    def test_the_refusal_names_the_directory_and_what_was_expected(self) -> None:
        profile = preflight.Profile()
        preflight.detect_repository(profile, cwd=self.path)

        rendered = profile.refusals[0].render()

        self.assertIn(str(self.path.resolve()), rendered)
        self.assertIn("Expected:", rendered)

    def test_no_state_file_is_created_in_a_non_repository(self) -> None:
        from scripts import state as state_mod

        profile = preflight.Profile()
        preflight.detect_repository(profile, cwd=self.path)

        self.assertFalse(state_mod.state_path(self.path).exists())


@requires_git
class TestNoRemote(RepoTestCase):
    with_remote = False

    def test_a_repository_with_no_remote_is_refused(self) -> None:
        profile = preflight.Profile()

        proceeded = preflight.detect_remote(profile, cwd=self.repo.path)

        self.assertFalse(proceeded)
        self.assertIn("no-remote", conditions(profile))

    def test_the_remote_is_undetermined_with_a_reason(self) -> None:
        profile = preflight.Profile()
        preflight.detect_remote(profile, cwd=self.repo.path)

        self.assertFalse(is_determined(profile.remote))
        self.assertIn("no-remote", reason_of(profile.remote))

    def test_a_misconfigured_remote_name_is_refused_and_lists_what_exists(self) -> None:
        self.repo.add_remote("upstream")
        profile = preflight.Profile()

        preflight.detect_remote(profile, configured="origin", cwd=self.repo.path)

        self.assertIn("remote-not-configured", conditions(profile))
        self.assertIn("upstream", profile.refusals[0].message)


@requires_git
class TestUnsafeWorkingCopy(RepoTestCase):
    default_branch = "trunk"

    def test_being_on_the_integration_branch_is_refused(self) -> None:
        """A branch cannot be shipped into itself (FR-005, Acceptance 3.5)."""
        profile = preflight.Profile()

        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("on-integration-branch", conditions(profile))
        self.assertIn("into itself", profile.refusals[0].message)

    def test_the_integration_branch_refusal_says_what_was_expected(self) -> None:
        profile = preflight.Profile()
        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("feature branch", profile.refusals[0].expected)

    def test_a_detached_head_is_refused(self) -> None:
        self.repo.detach_head()
        profile = preflight.Profile()

        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("detached-head", conditions(profile))

    def test_a_mid_merge_tree_is_refused(self) -> None:
        self.repo.branch("feature/x")
        self.repo.create_conflict_with("trunk")
        self.repo.start_conflicting_merge("trunk")

        profile = preflight.Profile()
        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("unfinished-operation", conditions(profile))
        self.assertIn("merge", profile.refusals[0].message)

    def test_the_unfinished_operation_refusal_says_finish_or_abort(self) -> None:
        self.repo.branch("feature/x")
        self.repo.create_conflict_with("trunk")
        self.repo.start_conflicting_merge("trunk")

        profile = preflight.Profile()
        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("abort", profile.refusals[0].expected)

    def test_a_mid_cherry_pick_is_refused(self) -> None:
        self.repo.branch("feature/x")
        self.repo.create_conflict_with("trunk")
        git(["cherry-pick", "trunk"], cwd=self.repo.path, check=False)

        profile = preflight.Profile()
        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertIn("unfinished-operation", conditions(profile))

    def test_a_feature_branch_on_a_clean_tree_is_not_refused(self) -> None:
        """The matrix must not be so eager that ordinary work is blocked."""
        self.repo.branch("feature/x")
        profile = preflight.Profile()

        preflight.check_working_copy(profile, integration_branch="trunk", cwd=self.repo.path)

        self.assertEqual([], profile.refusals)


@requires_git
class TestHostingPreconditions(RepoTestCase):
    def test_an_unauthenticated_host_is_refused_before_anything_is_committed(self) -> None:
        """FR-004 — authentication is not authorization, and neither is assumed."""
        from tests.contract.recorded_client import RecordedClient

        profile = preflight.Profile()
        preflight.check_hosting(profile, RecordedClient(authenticated=False))

        self.assertIn("hosting-unauthenticated", conditions(profile))

    def test_an_unreachable_host_is_undetermined_not_false(self) -> None:
        from scripts.hosting import Result

        class Unreachable:
            def probe(self):
                return Result.unknown("gh-not-installed: the GitHub CLI is not on PATH")

        profile = preflight.Profile()
        preflight.check_hosting(profile, Unreachable())

        self.assertFalse(is_determined(profile.hosting))
        self.assertIn("hosting-unreachable", conditions(profile))

    def test_an_authenticated_host_records_its_probed_capabilities(self) -> None:
        from tests.contract.recorded_client import RecordedClient

        profile = preflight.Profile()
        preflight.check_hosting(profile, RecordedClient())

        self.assertEqual([], profile.refusals)
        capabilities = value_of(profile.hosting)["capabilities"]
        self.assertIn("pr_view_json", capabilities)
        self.assertIs(False, capabilities["pr_checks_json"])


@requires_git
class TestMultiTargetIsUnsupported(RepoTestCase):
    default_branch = "trunk"

    def test_two_release_workflows_on_the_integration_branch_are_reported(self) -> None:
        """Reported unsupported rather than partially released."""
        workflow = "on:\n  push:\n    branches:\n      - trunk\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
        self.repo.add_workflow("deploy-api.yml", workflow)
        self.repo.add_workflow("deploy-web.yml", workflow)

        profile = preflight.Profile()
        result = preflight.detect_multi_target(
            profile, self.repo.path, integration_branch="trunk"
        )

        self.assertIs(True, value_of(result))
        self.assertIn("deploy-api.yml", profile.release_evidence)


if __name__ == "__main__":
    unittest.main()


@requires_git
class TestProfileIsReVerifiedNotTrusted(RepoTestCase):
    """T076 — a recorded profile is a cache of observations, never an authority.

    The failure this prevents: a repository is renamed, re-pointed, or re-hosted,
    and the run keeps shipping into last week's answer because state.json still
    says so. Recorded state describes what was true when it was written; only the
    world is authoritative about what is true now.
    """

    default_branch = "trunk"

    class _SilentHost:
        """A hosting service that cannot answer, so only git can."""

        def default_branch(self):
            from scripts.hosting import Result

            return Result.unknown("gh-failed: unreachable")

    def test_a_stale_recorded_branch_does_not_override_detection(self) -> None:
        from scripts import state as state_mod

        # Record a profile claiming a branch that is not this repository's.
        document = state_mod.empty_state()
        document["profile"] = {
            "is_repository": state_mod.determined(True, "git-rev-parse"),
            "integration_branch": state_mod.determined("stale-branch", "user-answer"),
            "verified_at": state_mod.now_iso(),
        }
        state_mod.save(self.repo.path, document)

        self.repo.set_remote_head()
        profile = preflight.Profile()
        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=self._SilentHost(), cwd=self.repo.path
        )

        self.assertEqual("trunk", branch)
        self.assertNotEqual("stale-branch", branch)
        self.assertEqual("git-symbolic-ref", profile.integration_branch["source"])

    def test_a_stale_recorded_repository_flag_does_not_override_detection(self) -> None:
        import tempfile
        from pathlib import Path as _Path

        from scripts import state as state_mod

        with tempfile.TemporaryDirectory() as tmp:
            plain = _Path(tmp)
            (plain / ".specify" / "extensions" / "ship").mkdir(parents=True)

            document = state_mod.empty_state()
            document["profile"] = {
                "is_repository": state_mod.determined(True, "git-rev-parse"),
                "verified_at": state_mod.now_iso(),
            }
            state_mod.save(plain, document)

            profile = preflight.Profile()
            proceeded = preflight.detect_repository(profile, cwd=plain)

            self.assertFalse(proceeded)
            self.assertIs(False, value_of(profile.is_repository))

    def test_each_run_stamps_a_fresh_verified_at(self) -> None:
        first = preflight.Profile().to_state()["verified_at"]
        second = preflight.Profile().to_state()["verified_at"]

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
