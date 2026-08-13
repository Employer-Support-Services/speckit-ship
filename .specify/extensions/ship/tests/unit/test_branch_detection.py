"""T031 — the four-step integration-branch precedence (FR-002, research R4).

**Every fixture here uses a non-`main` default branch.** That is the whole point.
A test whose fixture repository is called ``main`` passes identically whether the
implementation reads ``git symbolic-ref`` or simply returns the string
``"main"`` — it proves nothing about detection. ``trunk`` fails loudly against a
hardcoded fallback.

The precedence under test:

  1. saved configuration      (a recorded human answer)
  2. git symbolic-ref         (the local mirror of the remote default)
  3. gh repo view             (the hosting service's own record)
  4. git remote show          (a network re-derivation)

and, when none answers or two disagree: candidates recorded, nothing guessed.
"""

from __future__ import annotations

import unittest

from scripts import preflight
from scripts.hosting import Result
from scripts.state import is_determined, reason_of, value_of
from tests.integration.harness import RepoTestCase, requires_git


class FakeDefaultBranchClient:
    """Only ``default_branch`` matters to this module; the rest is unused here."""

    def __init__(self, name=None, *, ok=True, reason="gh-failed: unreachable"):
        self.name = name
        self.ok = ok
        self.reason = reason
        self.calls = 0

    def default_branch(self) -> Result:
        self.calls += 1
        if not self.ok:
            return Result.unknown(self.reason)
        return Result.of(self.name)


@requires_git
class TestPrecedence(RepoTestCase):
    default_branch = "trunk"

    def test_configuration_wins_and_is_recorded_as_such(self) -> None:
        profile = preflight.Profile()
        client = FakeDefaultBranchClient("trunk")

        branch = preflight.detect_integration_branch(
            profile, remote="origin", configured="release-line", client=client, cwd=self.repo.path
        )

        self.assertEqual("release-line", branch)
        self.assertEqual("config", profile.integration_branch["source"])
        # Configuration short-circuits: nothing else is consulted.
        self.assertEqual(0, client.calls)

    def test_symbolic_ref_answers_when_present(self) -> None:
        self.repo.set_remote_head()
        profile = preflight.Profile()

        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=None, cwd=self.repo.path
        )

        self.assertEqual("trunk", branch)
        self.assertEqual("git-symbolic-ref", profile.integration_branch["source"])

    def test_hosting_service_answers_when_symbolic_ref_is_absent(self) -> None:
        # No set_remote_head() — a fresh `git init` + `remote add` has no
        # refs/remotes/origin/HEAD, which is a realistic and common state.
        profile = preflight.Profile()
        client = FakeDefaultBranchClient("trunk")

        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=client, cwd=self.repo.path
        )

        self.assertEqual("trunk", branch)
        self.assertEqual("gh-repo-view", profile.integration_branch["source"])

    def test_a_non_main_branch_is_detected_not_assumed(self) -> None:
        """The test this whole module exists for."""
        self.repo.set_remote_head()
        profile = preflight.Profile()

        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=None, cwd=self.repo.path
        )

        self.assertEqual("trunk", branch)
        self.assertNotEqual("main", branch)
        self.assertNotEqual("master", branch)


@requires_git
class TestUnusualBranchNames(RepoTestCase):
    default_branch = "release/2026.08"

    def test_a_branch_name_containing_a_slash_is_detected(self) -> None:
        self.repo.set_remote_head()
        profile = preflight.Profile()

        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=None, cwd=self.repo.path
        )

        self.assertEqual("release/2026.08", branch)


@requires_git
class TestAmbiguityAndSilence(RepoTestCase):
    default_branch = "trunk"

    def test_disagreement_records_candidates_and_determines_nothing(self) -> None:
        """Two systems of record disagreeing is not a tiebreak opportunity."""
        self.repo.set_remote_head()  # says 'trunk'
        profile = preflight.Profile()
        client = FakeDefaultBranchClient("production")  # says 'production'

        branch = preflight.detect_integration_branch(
            profile, remote="origin", client=client, cwd=self.repo.path
        )

        self.assertIsNone(branch)
        self.assertFalse(is_determined(profile.integration_branch))
        self.assertIn("integration-branch-ambiguous", reason_of(profile.integration_branch))
        self.assertCountEqual(["trunk", "production"], profile.integration_branch_candidates)

    def test_the_ambiguity_reason_names_both_sources(self) -> None:
        self.repo.set_remote_head()
        profile = preflight.Profile()
        client = FakeDefaultBranchClient("production")

        preflight.detect_integration_branch(
            profile, remote="origin", client=client, cwd=self.repo.path
        )

        reason = reason_of(profile.integration_branch)
        self.assertIn("git-symbolic-ref", reason)
        self.assertIn("gh-repo-view", reason)

    def test_no_source_answering_yields_undetermined_never_a_default(self) -> None:
        profile = preflight.Profile()
        client = FakeDefaultBranchClient(ok=False)

        branch = preflight.detect_integration_branch(
            profile,
            remote="origin",
            client=client,
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertIsNone(branch)
        self.assertFalse(is_determined(profile.integration_branch))
        self.assertIsNone(profile.integration_branch["value"])
        self.assertIn("integration-branch-undetermined", reason_of(profile.integration_branch))

    def test_candidates_are_offered_from_the_remotes_actual_branches(self) -> None:
        self.repo.branch("feature/one")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work on feature/one")
        self.repo.push()
        self.repo.checkout("trunk")

        profile = preflight.Profile()
        client = FakeDefaultBranchClient(ok=False)

        preflight.detect_integration_branch(
            profile,
            remote="origin",
            client=client,
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertIn("trunk", profile.integration_branch_candidates)
        self.assertIn("feature/one", profile.integration_branch_candidates)

    def test_a_recorded_answer_is_marked_as_a_user_answer(self) -> None:
        """FR-003 — and 'user-answer' is what makes it auditable later."""
        profile = preflight.Profile()

        preflight.record_branch_answer(profile, "trunk")

        self.assertEqual("trunk", value_of(profile.integration_branch))
        self.assertEqual("user-answer", profile.integration_branch["source"])
        self.assertEqual([], profile.integration_branch_candidates)


class TestParsers(unittest.TestCase):
    def test_symbolic_ref_output_is_stripped_to_a_branch_name(self) -> None:
        from scripts.gitops import parse_symbolic_ref

        self.assertEqual(
            "trunk", parse_symbolic_ref("refs/remotes/origin/trunk", "origin")
        )
        self.assertEqual(
            "release/2026.08",
            parse_symbolic_ref("refs/remotes/origin/release/2026.08", "origin"),
        )
        self.assertIsNone(parse_symbolic_ref("refs/heads/trunk", "origin"))

    def test_remote_show_head_branch_is_parsed(self) -> None:
        from scripts.gitops import parse_remote_show_head

        output = (
            "* remote origin\n"
            "  Fetch URL: git@example.invalid:acme/thing.git\n"
            "  HEAD branch: trunk\n"
            "  Remote branches:\n"
        )
        self.assertEqual("trunk", parse_remote_show_head(output))

    def test_unknown_head_branch_is_not_a_name(self) -> None:
        """git prints '(unknown)' when it cannot tell. That is not an answer."""
        from scripts.gitops import parse_remote_show_head

        self.assertIsNone(parse_remote_show_head("  HEAD branch: (unknown)\n"))


if __name__ == "__main__":
    unittest.main()
