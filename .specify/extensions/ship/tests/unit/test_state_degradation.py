"""T028 — the four degradation paths (FR-029).

The assertion every test here shares: **no path raises**. A ship run must never
be aborted because its own bookkeeping file is missing, corrupt, or from a
different version. The state file is a record of runs, not a precondition for
them, and treating it as the latter would make the tool fail exactly when the
developer most needs it to work.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import state as state_mod


class DegradationCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".specify" / "extensions" / "ship").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_state(self, content: str) -> Path:
        path = state_mod.state_path(self.root)
        path.write_text(content, encoding="utf-8")
        return path


class TestMissingFile(DegradationCase):
    def test_missing_file_yields_empty_state_without_raising(self) -> None:
        result = state_mod.load(self.root)

        self.assertEqual("missing", result.condition)
        self.assertFalse(result.read_only)
        self.assertEqual([], result.document["runs"])
        self.assertIn("starting fresh", result.message)

    def test_empty_state_records_the_profile_as_undetermined_not_absent(self) -> None:
        """An unrun preflight is 'not yet observed', never a blank that reads as false."""
        result = state_mod.load(self.root)
        is_repo = result.document["profile"]["is_repository"]

        self.assertFalse(is_repo["determined"])
        self.assertIsNone(is_repo["value"])
        self.assertIn("not-yet-observed", is_repo["reason"])


class TestUnparseableFile(DegradationCase):
    def test_corrupt_file_is_moved_aside_and_state_starts_fresh(self) -> None:
        self.write_state("{ this is not json")

        result = state_mod.load(self.root)

        self.assertEqual("unparseable", result.condition)
        self.assertIsNotNone(result.moved_aside)
        self.assertTrue(result.moved_aside.is_file())
        self.assertIn("corrupt-", result.moved_aside.name)
        self.assertFalse(state_mod.state_path(self.root).exists())
        self.assertEqual([], result.document["runs"])

    def test_the_corrupt_content_is_preserved_not_discarded(self) -> None:
        self.write_state("{ truncated mid-write")

        result = state_mod.load(self.root)

        self.assertEqual("{ truncated mid-write", result.moved_aside.read_text(encoding="utf-8"))

    def test_a_json_array_at_top_level_is_treated_as_unparseable(self) -> None:
        self.write_state("[]")

        result = state_mod.load(self.root)

        self.assertEqual("unparseable", result.condition)


class TestVersionSkew(DegradationCase):
    def test_newer_schema_degrades_to_read_only_and_does_not_abort(self) -> None:
        self.write_state(json.dumps({"schema_version": 99, "profile": {}, "runs": []}))

        result = state_mod.load(self.root)

        self.assertEqual("newer", result.condition)
        self.assertTrue(result.read_only)
        self.assertIn("read-only", result.message)

    def test_a_read_only_load_skips_the_mutation_rather_than_raising(self) -> None:
        """The run continues; it simply records nothing this time."""
        original = json.dumps({"schema_version": 99, "profile": {}, "runs": []})
        path = self.write_state(original)

        def mutate(document: dict) -> None:
            document["runs"].append({"run_id": "should-not-be-written"})

        result = state_mod.update(self.root, mutate)

        self.assertTrue(result.read_only)
        self.assertEqual(original, path.read_text(encoding="utf-8"))

    def test_older_schema_migrates_forward_on_write(self) -> None:
        self.write_state(
            json.dumps({"schema_version": 0, "profile": {}, "runs": [], "legacy_key": "kept"})
        )

        result = state_mod.load(self.root)
        self.assertEqual("older", result.condition)
        self.assertFalse(result.read_only)

        state_mod.update(self.root, lambda doc: None)

        written = json.loads(state_mod.state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(state_mod.SCHEMA_VERSION, written["schema_version"])
        self.assertEqual("kept", written["legacy_key"])

    def test_missing_schema_version_is_treated_as_older_not_fatal(self) -> None:
        self.write_state(json.dumps({"profile": {}, "runs": []}))

        result = state_mod.load(self.root)

        self.assertEqual("older", result.condition)
        self.assertFalse(result.read_only)


class TestUnknownKeyPreservation(DegradationCase):
    def test_unknown_top_level_keys_survive_read_modify_write(self) -> None:
        """A newer writer's fields are carried through, not rebuilt away."""
        self.write_state(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": {"is_repository": state_mod.determined(True, "git-rev-parse"),
                                "verified_at": state_mod.now_iso()},
                    "runs": [],
                    "future_field": {"written_by": "a later version"},
                }
            )
        )

        state_mod.update(
            self.root,
            lambda doc: doc["runs"].append(
                state_mod.new_run(branch="feature/x", target_branch="trunk")
            ),
        )

        written = json.loads(state_mod.state_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual({"written_by": "a later version"}, written["future_field"])
        self.assertEqual(1, len(written["runs"]))


class TestAtomicWrite(DegradationCase):
    def test_no_temp_file_is_left_behind(self) -> None:
        state_mod.save(self.root, state_mod.empty_state())

        directory = state_mod.state_path(self.root).parent
        leftovers = [p.name for p in directory.iterdir() if ".tmp" in p.name]

        self.assertEqual([], leftovers)

    def test_save_validates_before_writing(self) -> None:
        """A forged wrapper is refused at the write, not caught in review later."""
        document = state_mod.empty_state()
        document["profile"]["integration_branch"] = {
            "determined": False,
            "value": "main",
            "captured_at": state_mod.now_iso(),
            "reason": "guessed",
        }

        with self.assertRaises(state_mod.StateError):
            state_mod.save(self.root, document)

        self.assertFalse(state_mod.state_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
