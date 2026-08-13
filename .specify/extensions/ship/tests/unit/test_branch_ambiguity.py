"""T069 — ask once, record, never ask again (FR-003, FR-043).

The requirement has three halves and all three are load-bearing:

1. **Candidates are presented** when detection could not decide — not a guess,
   and not a bare refusal either.
2. **The choice is recorded** with `source: user-answer`, so an audit later can
   tell a human's decision from a heuristic's.
3. **The next run does not ask.** This is the half that was missing until now:
   an answer held only in memory is not an answer, and re-asking every run
   would train the developer to answer without reading.

The answer lands in `config.json` rather than `state.json`, and the tests below
assert that specifically. Configuration is committed, so the answer travels with
the repository; recorded state is gitignored and re-verified every run, so an
answer stored there would be lost to a colleague and to the first corrupt-file
recovery.
"""

from __future__ import annotations

import json
import unittest

from scripts import config as config_mod
from scripts import preflight
from scripts.hosting import Result
from scripts.state import is_determined, reason_of, value_of
from tests.integration.harness import RepoTestCase, requires_git


class SilentClient:
    """A hosting service that cannot answer — forcing the ambiguity path."""

    def default_branch(self) -> Result:
        return Result.unknown("gh-failed: unreachable")


@requires_git
class TestCandidatesArePresented(RepoTestCase):
    default_branch = "trunk"

    def test_candidates_come_from_the_remote_s_actual_branches(self) -> None:
        self.repo.branch("release-line")
        self.repo.write_file("a.txt", "a")
        self.repo.commit("work")
        self.repo.push()
        self.repo.checkout("trunk")

        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile,
            remote="origin",
            client=SilentClient(),
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertIn("trunk", profile.integration_branch_candidates)
        self.assertIn("release-line", profile.integration_branch_candidates)

    def test_nothing_is_determined_while_candidates_are_outstanding(self) -> None:
        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile,
            remote="origin",
            client=SilentClient(),
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertFalse(is_determined(profile.integration_branch))
        self.assertIsNone(profile.integration_branch["value"])

    def test_no_hardcoded_name_appears_when_detection_fails(self) -> None:
        """FR-002 — not main, not master, not the first candidate."""
        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile,
            remote="origin",
            client=SilentClient(),
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertIsNone(value_of(profile.integration_branch))


@requires_git
class TestTheAnswerIsRecorded(RepoTestCase):
    default_branch = "trunk"

    def test_the_answer_is_marked_as_a_user_answer(self) -> None:
        profile = preflight.Profile()

        preflight.record_branch_answer(profile, "trunk")

        self.assertEqual("trunk", value_of(profile.integration_branch))
        self.assertEqual("user-answer", profile.integration_branch["source"])

    def test_answering_clears_the_outstanding_candidates(self) -> None:
        profile = preflight.Profile()
        profile.integration_branch_candidates = ["trunk", "production"]

        preflight.record_branch_answer(profile, "trunk")

        self.assertEqual([], profile.integration_branch_candidates)


@requires_git
class TestTheAnswerPersists(RepoTestCase):
    default_branch = "trunk"

    def test_the_answer_is_written_to_committed_configuration(self) -> None:
        result = preflight.persist_branch_answer(self.repo.path, "trunk")

        self.assertTrue(result["saved"], result["problem"])

        saved = json.loads(config_mod.config_path(self.repo.path).read_text(encoding="utf-8"))
        self.assertEqual("trunk", saved["target_branch"])

    def test_the_answer_is_not_written_to_recorded_state(self) -> None:
        """state.json is a cache of observations; an answer stored there is lost."""
        from scripts import state as state_mod

        preflight.persist_branch_answer(self.repo.path, "trunk")

        self.assertFalse(state_mod.state_path(self.repo.path).exists())

    def test_a_second_run_does_not_ask_because_config_answers_first(self) -> None:
        """The half that was missing. Step 1 of the R4 precedence is configuration."""
        preflight.persist_branch_answer(self.repo.path, "trunk")

        loaded = config_mod.load(self.repo.path)
        profile = preflight.Profile()
        client = SilentClient()

        branch = preflight.detect_integration_branch(
            profile,
            remote="origin",
            configured=loaded.config["target_branch"],
            client=client,
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertEqual("trunk", branch)
        self.assertTrue(is_determined(profile.integration_branch))
        self.assertEqual("config", profile.integration_branch["source"])
        self.assertEqual([], profile.integration_branch_candidates)

    def test_the_recorded_answer_survives_a_deleted_state_file(self) -> None:
        """Configuration is committed; recorded state is disposable."""
        from scripts import state as state_mod

        preflight.persist_branch_answer(self.repo.path, "trunk")
        state_mod.save(self.repo.path, state_mod.empty_state())
        state_mod.state_path(self.repo.path).unlink()

        self.assertEqual("trunk", config_mod.load(self.repo.path).config["target_branch"])

    def test_an_unsaveable_answer_reports_the_problem_rather_than_raising(self) -> None:
        """A run must not abort because the answer could not be written."""
        result = preflight.persist_branch_answer(self.repo.path, "trunk")
        self.assertTrue(result["saved"])

        # An invalid pairing: shipping a branch into itself.
        config = config_mod.load(self.repo.path).config
        config["source_branch"] = "trunk"
        config_path = config_mod.config_path(self.repo.path)
        config_path.write_text(json.dumps(config), encoding="utf-8")

        result = preflight.persist_branch_answer(self.repo.path, "trunk")

        self.assertFalse(result["saved"])
        self.assertIn("cannot be shipped into itself", result["problem"])


@requires_git
class TestReleaseModeAskOnce(RepoTestCase):
    default_branch = "trunk"

    def test_the_mode_answer_is_marked_as_a_user_answer(self) -> None:
        profile = preflight.Profile()

        preflight.record_release_mode_answer(profile, "none")

        self.assertEqual("none", value_of(profile.release_mode))
        self.assertEqual("user-answer", profile.release_mode["source"])

    def test_the_mode_answer_persists_to_configuration(self) -> None:
        result = preflight.persist_release_mode_answer(self.repo.path, "observed")

        self.assertTrue(result["saved"], result["problem"])
        self.assertEqual("observed", config_mod.load(self.repo.path).config["release"]["mode"])

    def test_a_recorded_mode_short_circuits_detection_on_the_next_run(self) -> None:
        preflight.persist_release_mode_answer(self.repo.path, "none")
        configured = config_mod.load(self.repo.path).config["release"]["mode"]

        profile = preflight.Profile()
        result = preflight.detect_release_mode(
            profile, self.repo.path, integration_branch="trunk", configured_mode=configured
        )

        self.assertEqual("none", value_of(result))
        self.assertEqual("config", result["source"])

    def test_executed_without_a_declared_action_cannot_be_saved(self) -> None:
        """Recording it would fail the release stage on every future run."""
        result = preflight.persist_release_mode_answer(self.repo.path, "executed")

        self.assertFalse(result["saved"])
        self.assertIn("release.action", result["problem"])

    def test_executed_with_a_declared_action_saves(self) -> None:
        result = preflight.persist_release_mode_answer(
            self.repo.path, "executed", action={"workflow": "release.yml"}
        )

        self.assertTrue(result["saved"], result["problem"])

    def test_an_undetermined_mode_carries_the_none_determinable_token(self) -> None:
        profile = preflight.Profile()

        result = preflight.detect_release_mode(
            profile, self.repo.path, integration_branch="trunk"
        )

        self.assertFalse(is_determined(result))
        self.assertIn("none-determinable", reason_of(result))


if __name__ == "__main__":
    unittest.main()


@requires_git
class TestTheAskOnceLoopEndToEnd(RepoTestCase):
    """The three halves of FR-003 exercised together through ship.py's own path.

    The choice prompt itself is stubbed — a terminal is not available in the
    suite — but everything after it is real: the same recording, the same
    persistence, and the same detection on the following run.
    """

    default_branch = "trunk"

    def setUp(self) -> None:
        super().setUp()
        from scripts import ship

        self.ship = ship
        self._real_choose = ship._choose

    def tearDown(self) -> None:
        self.ship._choose = self._real_choose
        super().tearDown()

    def stub_choice(self, answer):
        seen = {}

        def _choose(prompt, options):
            seen["prompt"] = prompt
            seen["options"] = list(options)
            return answer

        self.ship._choose = _choose
        return seen

    def test_the_prompt_offers_the_detected_candidates(self) -> None:
        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile, remote="origin", client=SilentClient(),
            cwd=self.repo.path, allow_network=False,
        )
        seen = self.stub_choice("trunk")

        self.ship.ask_integration_branch(self.repo.path, profile)

        self.assertIn("trunk", seen["options"])
        self.assertIn("not be asked again", seen["prompt"])

    def test_answering_records_persists_and_settles_the_question(self) -> None:
        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile, remote="origin", client=SilentClient(),
            cwd=self.repo.path, allow_network=False,
        )
        self.stub_choice("trunk")

        chosen = self.ship.ask_integration_branch(self.repo.path, profile)

        self.assertEqual("trunk", chosen)
        self.assertEqual("user-answer", profile.integration_branch["source"])
        self.assertEqual("trunk", config_mod.load(self.repo.path).config["target_branch"])

    def test_the_next_run_detects_from_configuration_without_asking(self) -> None:
        first = preflight.Profile()
        preflight.detect_integration_branch(
            first, remote="origin", client=SilentClient(),
            cwd=self.repo.path, allow_network=False,
        )
        self.stub_choice("trunk")
        self.ship.ask_integration_branch(self.repo.path, first)

        # Second run: any prompt at all is a failure.
        def _must_not_ask(prompt, options):
            raise AssertionError("the developer was asked a second time")

        self.ship._choose = _must_not_ask

        second = preflight.Profile()
        branch = preflight.detect_integration_branch(
            second,
            remote="origin",
            configured=config_mod.load(self.repo.path).config["target_branch"],
            client=SilentClient(),
            cwd=self.repo.path,
            allow_network=False,
        )

        self.assertEqual("trunk", branch)
        self.assertEqual("config", second.integration_branch["source"])

    def test_declining_records_nothing(self) -> None:
        profile = preflight.Profile()
        preflight.detect_integration_branch(
            profile, remote="origin", client=SilentClient(),
            cwd=self.repo.path, allow_network=False,
        )
        self.stub_choice(None)

        chosen = self.ship.ask_integration_branch(self.repo.path, profile)

        self.assertIsNone(chosen)
        self.assertFalse(is_determined(profile.integration_branch))
        self.assertFalse(config_mod.config_path(self.repo.path).exists())
