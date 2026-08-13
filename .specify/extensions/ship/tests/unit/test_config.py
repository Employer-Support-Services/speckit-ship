"""T029 — configuration loading, validation, and rejected saves (FR-040).

The requirement with teeth here is that a rejected save leaves the previous
configuration **byte-identical**. Not "mostly intact", not "restored on the next
load" — byte-identical, because a developer who saves an invalid config and gets
an error must be able to trust that the file on disk is still the one that was
working five seconds ago.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import config as config_mod


class ConfigCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".specify" / "extensions" / "ship").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_config(self, payload: dict) -> Path:
        path = config_mod.config_path(self.root)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


class TestLoadWithDefaults(ConfigCase):
    def test_absent_file_returns_every_documented_default(self) -> None:
        result = config_mod.load(self.root)

        self.assertEqual("missing", result.condition)
        self.assertIsNone(result.config["target_branch"])
        self.assertEqual("origin", result.config["remote"])
        self.assertEqual("commits", result.config["pr"]["composition"])
        self.assertEqual("squash", result.config["pr"]["merge_method"])
        self.assertEqual(1800, result.config["limits"]["checks_wait_seconds"])
        self.assertEqual(2, result.config["limits"]["repair_budget"])
        self.assertEqual(900, result.config["limits"]["freshness_seconds"])
        self.assertTrue(result.config["cleanup"]["delete_branch"])

    def test_target_branch_never_defaults_to_main(self) -> None:
        """FR-002. The default is 'detect', and detect can answer 'I could not'."""
        result = config_mod.load(self.root)

        self.assertIsNone(result.config["target_branch"])
        self.assertNotEqual("main", result.config["target_branch"])
        self.assertNotEqual("master", result.config["target_branch"])

    def test_partial_file_is_merged_over_defaults(self) -> None:
        self.write_config({"schema_version": 1, "pr": {"composition": "drafted"}})

        result = config_mod.load(self.root)

        self.assertEqual("drafted", result.config["pr"]["composition"])
        # Sibling keys in the same object keep their defaults.
        self.assertEqual("squash", result.config["pr"]["merge_method"])

    def test_unparseable_file_degrades_to_defaults_and_leaves_the_file_alone(self) -> None:
        path = config_mod.config_path(self.root)
        path.write_text("{ not json", encoding="utf-8")

        result = config_mod.load(self.root)

        self.assertEqual("unparseable", result.condition)
        self.assertEqual("origin", result.config["remote"])
        self.assertEqual("{ not json", path.read_text(encoding="utf-8"))


class TestValidation(ConfigCase):
    def test_a_default_configuration_is_valid(self) -> None:
        self.assertEqual([], config_mod.validate_config(config_mod.defaults()))

    def test_source_and_target_may_not_be_the_same_branch(self) -> None:
        config = config_mod.defaults()
        config["source_branch"] = "trunk"
        config["target_branch"] = "trunk"

        problems = config_mod.validate_config(config)

        self.assertTrue(any("cannot be shipped into itself" in p for p in problems))

    def test_executed_mode_without_an_action_is_rejected(self) -> None:
        """The tool never composes a release action (spec Assumptions)."""
        config = config_mod.defaults()
        config["release"]["mode"] = "executed"
        config["release"]["action"] = None

        problems = config_mod.validate_config(config)

        self.assertTrue(any("release.action" in p for p in problems))
        self.assertTrue(any("never composes one" in p for p in problems))

    def test_executed_mode_with_a_declared_workflow_is_valid(self) -> None:
        config = config_mod.defaults()
        config["release"]["mode"] = "executed"
        config["release"]["action"] = {"workflow": "release.yml", "ref": "trunk"}

        self.assertEqual([], config_mod.validate_config(config))

    def test_release_action_may_declare_only_one_shape(self) -> None:
        config = config_mod.defaults()
        config["release"]["mode"] = "executed"
        config["release"]["action"] = {"workflow": "release.yml", "script": "./release.sh"}

        problems = config_mod.validate_config(config)

        self.assertTrue(any("exactly one" in p for p in problems))

    def test_every_limit_is_range_checked(self) -> None:
        for key, (low, high) in config_mod.LIMIT_RANGES.items():
            for bad in (low - 1, high + 1):
                config = config_mod.defaults()
                config["limits"][key] = bad
                problems = config_mod.validate_config(config)
                self.assertTrue(
                    any(key in p for p in problems),
                    f"limits.{key}={bad} should have been rejected",
                )

    def test_limit_boundaries_are_inclusive(self) -> None:
        for key, (low, high) in config_mod.LIMIT_RANGES.items():
            for good in (low, high):
                config = config_mod.defaults()
                config["limits"][key] = good
                self.assertEqual(
                    [],
                    config_mod.validate_config(config),
                    f"limits.{key}={good} should be accepted",
                )

    def test_repair_budget_zero_is_valid_and_disables_repair(self) -> None:
        config = config_mod.defaults()
        config["limits"]["repair_budget"] = 0

        self.assertEqual([], config_mod.validate_config(config))

    def test_unknown_merge_method_is_rejected(self) -> None:
        config = config_mod.defaults()
        config["pr"]["merge_method"] = "fast-forward"

        problems = config_mod.validate_config(config)

        self.assertTrue(any("merge_method" in p for p in problems))

    def test_merge_method_not_permitted_by_the_repository_is_rejected(self) -> None:
        config = config_mod.defaults()
        config["pr"]["merge_method"] = "rebase"

        problems = config_mod.validate_config(
            config, permitted_merge_methods=["squash", "merge"]
        )

        self.assertTrue(any("not enabled on this repository" in p for p in problems))

    def test_merge_method_is_not_checked_when_permissions_are_unknown(self) -> None:
        """FR-036: the control renders disabled with the reason, not saved optimistically."""
        config = config_mod.defaults()
        config["pr"]["merge_method"] = "rebase"

        self.assertEqual([], config_mod.validate_config(config, permitted_merge_methods=None))

    def test_a_target_branch_that_does_not_resolve_is_rejected(self) -> None:
        config = config_mod.defaults()
        config["target_branch"] = "no-such-branch"

        problems = config_mod.validate_config(
            config, remote_branch_exists=lambda remote, branch: False
        )

        self.assertTrue(any("does not resolve on remote" in p for p in problems))

    def test_an_unverifiable_target_branch_is_not_a_failure(self) -> None:
        """Offline is not the same as wrong.

        A connectivity problem must never be reported as a claim about the
        repository's branches.
        """
        config = config_mod.defaults()
        config["target_branch"] = "trunk"

        problems = config_mod.validate_config(
            config, remote_branch_exists=lambda remote, branch: None
        )

        self.assertEqual([], problems)

    def test_unknown_remote_is_rejected_when_the_remote_list_is_known(self) -> None:
        config = config_mod.defaults()
        config["remote"] = "upstream"

        problems = config_mod.validate_config(config, known_remotes=["origin"])

        self.assertTrue(any("not configured in this repository" in p for p in problems))

    def test_unknown_keys_are_rejected(self) -> None:
        config = config_mod.defaults()
        config["auto_merge_always"] = True

        problems = config_mod.validate_config(config)

        self.assertTrue(any("auto_merge_always" in p for p in problems))


class TestRejectedSave(ConfigCase):
    def test_a_rejected_save_leaves_the_previous_file_byte_identical(self) -> None:
        good = config_mod.defaults()
        good["target_branch"] = "trunk"
        path = config_mod.save(self.root, good)
        before = path.read_bytes()

        bad = config_mod.defaults()
        bad["limits"]["repair_budget"] = 99

        with self.assertRaises(config_mod.ConfigError):
            config_mod.save(self.root, bad)

        self.assertEqual(before, path.read_bytes())

    def test_the_rejection_names_the_specific_problem(self) -> None:
        bad = config_mod.defaults()
        bad["limits"]["checks_wait_seconds"] = 5

        with self.assertRaises(config_mod.ConfigError) as ctx:
            config_mod.save(self.root, bad)

        message = str(ctx.exception)
        self.assertIn("checks_wait_seconds", message)
        self.assertIn("60", message)
        self.assertIn("previous configuration was retained", message)

    def test_a_rejected_first_save_writes_no_file_at_all(self) -> None:
        bad = config_mod.defaults()
        bad["release"]["mode"] = "executed"

        with self.assertRaises(config_mod.ConfigError):
            config_mod.save(self.root, bad)

        self.assertFalse(config_mod.config_path(self.root).exists())

    def test_a_successful_save_round_trips(self) -> None:
        config = config_mod.defaults()
        config["target_branch"] = "trunk"
        config["pr"]["composition"] = "drafted"
        config_mod.save(self.root, config)

        reloaded = config_mod.load(self.root)

        self.assertEqual("ok", reloaded.condition)
        self.assertEqual("trunk", reloaded.config["target_branch"])
        self.assertEqual("drafted", reloaded.config["pr"]["composition"])

    def test_no_temp_file_is_left_behind(self) -> None:
        config_mod.save(self.root, config_mod.defaults())

        directory = config_mod.config_path(self.root).parent
        self.assertEqual([], [p.name for p in directory.iterdir() if ".tmp" in p.name])


if __name__ == "__main__":
    unittest.main()
