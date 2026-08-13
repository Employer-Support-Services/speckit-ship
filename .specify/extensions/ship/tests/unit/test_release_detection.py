"""Release-mode and checks detection from workflow triggers (T022, research R6).

The repository's own workflow declarations are the system of record for how it
releases. This module reads them; it does not guess from repository shape.

``TestTriggerParsing`` carries a regression test for a real bug: the branch-list
parser used ``\\s*`` on a line-anchored pattern, and since ``\\s`` matches
newlines it swallowed the line break and consumed the first list item. Every
branch-filtered workflow therefore looked like it had no filters, and a
repository that plainly releases on push to its integration branch was reported
``none-determinable``. The failure was silent and in the safe direction — it
asked instead of guessing — which is exactly why it could have survived a long
time unnoticed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import preflight
from scripts.state import is_determined, reason_of, value_of

PUSH_TO_BRANCH = """\
name: "[DEV] Build, Test, & Deploy"

on:
  push:
    branches:
      - "main"

jobs:
  build:
    runs-on: ubuntu-latest
"""

PUSH_TO_TRUNK_UNQUOTED = """\
name: deploy

on:
  push:
    branches:
      - trunk
      - hotfix/*

jobs:
  deploy:
    runs-on: ubuntu-latest
"""

PUSH_INLINE_LIST = """\
name: deploy
on:
  push:
    branches: [trunk, "release/*"]
jobs:
  deploy:
    runs-on: ubuntu-latest
"""

WORKFLOW_DISPATCH_ONLY = """\
name: "[PROD] Deploy Workflow"

on:
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest
"""

PULL_REQUEST_ONLY = """\
name: validate
on:
  pull_request:
    branches:
      - trunk
jobs:
  test:
    runs-on: ubuntu-latest
"""

RELEASE_EVENT = """\
name: publish
on:
  release:
    types: [published]
jobs:
  publish:
    runs-on: ubuntu-latest
"""

PUSH_TAGS_ONLY = """\
name: tag build
on:
  push:
    tags:
      - "v*"
jobs:
  build:
    runs-on: ubuntu-latest
"""


class TestTriggerParsing(unittest.TestCase):
    def test_a_quoted_branch_list_is_read(self) -> None:
        """The regression. This returned [] before the \\s -> [ \\t] fix."""
        self.assertEqual(["main"], preflight._trigger_branches(PUSH_TO_BRANCH, "push"))

    def test_an_unquoted_multi_entry_list_is_read(self) -> None:
        self.assertEqual(
            ["trunk", "hotfix/*"], preflight._trigger_branches(PUSH_TO_TRUNK_UNQUOTED, "push")
        )

    def test_an_inline_list_is_read(self) -> None:
        self.assertEqual(
            ["trunk", "release/*"], preflight._trigger_branches(PUSH_INLINE_LIST, "push")
        )

    def test_a_workflow_with_no_push_trigger_yields_no_branches(self) -> None:
        self.assertEqual([], preflight._trigger_branches(WORKFLOW_DISPATCH_ONLY, "push"))

    def test_tags_are_not_branches(self) -> None:
        self.assertEqual([], preflight._trigger_branches(PUSH_TAGS_ONLY, "push"))

    def test_a_pull_request_filter_is_not_read_as_a_push_filter(self) -> None:
        self.assertEqual([], preflight._trigger_branches(PULL_REQUEST_ONLY, "push"))


class TestBranchMatching(unittest.TestCase):
    def test_exact_names_match(self) -> None:
        self.assertTrue(preflight._branch_matches("trunk", "trunk"))
        self.assertFalse(preflight._branch_matches("trunk", "main"))

    def test_single_star_does_not_cross_a_slash(self) -> None:
        self.assertTrue(preflight._branch_matches("hotfix/*", "hotfix/urgent"))
        self.assertFalse(preflight._branch_matches("hotfix/*", "hotfix/a/b"))

    def test_double_star_crosses_slashes(self) -> None:
        self.assertTrue(preflight._branch_matches("release/**", "release/2026/08"))


class WorkflowCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".github" / "workflows").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add(self, name: str, content: str) -> None:
        (self.root / ".github" / "workflows" / name).write_text(content, encoding="utf-8")


class TestReleaseModeDetection(WorkflowCase):
    def test_push_to_the_integration_branch_is_observed(self) -> None:
        self.add("deploy.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="main")

        self.assertEqual("observed", value_of(result))
        self.assertEqual("workflow-trigger", result["source"])
        self.assertIn("deploy.yml", profile.release_evidence)

    def test_the_evidence_names_what_produced_the_verdict(self) -> None:
        self.add("deploy.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        preflight.detect_release_mode(profile, self.root, integration_branch="main")

        self.assertIn("triggers on push to main", profile.release_evidence)

    def test_a_release_event_is_observed(self) -> None:
        self.add("publish.yml", RELEASE_EVENT)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="trunk")

        self.assertEqual("observed", value_of(result))

    def test_a_push_filter_for_a_different_branch_is_not_observed(self) -> None:
        """A workflow that fires on `main` says nothing about a `trunk` repository."""
        self.add("deploy.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="trunk")

        self.assertFalse(is_determined(result))
        self.assertIn("none-determinable", reason_of(result))

    def test_manual_dispatch_alone_is_none_determinable(self) -> None:
        """workflow_dispatch is a human pressing a button, not an observed release."""
        self.add("prod.yml", WORKFLOW_DISPATCH_ONLY)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="main")

        self.assertFalse(is_determined(result))
        self.assertIn("none-determinable", reason_of(result))

    def test_a_repository_with_no_workflows_is_none_determinable(self) -> None:
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="trunk")

        self.assertFalse(is_determined(result))
        self.assertIsNone(result["value"])
        self.assertIn("none-determinable", reason_of(result))

    def test_ci_only_repositories_are_none_determinable_not_observed(self) -> None:
        self.add("validate.yml", PULL_REQUEST_ONLY)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(profile, self.root, integration_branch="trunk")

        self.assertFalse(is_determined(result))

    def test_configuration_overrides_detection_and_says_so(self) -> None:
        self.add("validate.yml", PULL_REQUEST_ONLY)
        profile = preflight.Profile()

        result = preflight.detect_release_mode(
            profile, self.root, integration_branch="trunk", configured_mode="executed"
        )

        self.assertEqual("executed", value_of(result))
        self.assertEqual("config", result["source"])

    def test_a_recorded_answer_is_marked_as_a_user_answer(self) -> None:
        profile = preflight.Profile()

        preflight.record_release_mode_answer(profile, "none")

        self.assertEqual("none", value_of(profile.release_mode))
        self.assertEqual("user-answer", profile.release_mode["source"])


class TestChecksDetection(WorkflowCase):
    def test_a_pull_request_workflow_means_checks_exist(self) -> None:
        self.add("validate.yml", PULL_REQUEST_ONLY)
        profile = preflight.Profile()

        result = preflight.detect_has_checks(profile, self.root)

        self.assertIs(True, value_of(result))

    def test_no_workflows_means_no_checks_as_a_real_answer(self) -> None:
        """`false` here is determined, not undetermined — the spec names this case."""
        profile = preflight.Profile()

        result = preflight.detect_has_checks(profile, self.root)

        self.assertTrue(is_determined(result))
        self.assertIs(False, value_of(result))

    def test_deploy_only_workflows_mean_no_pr_checks(self) -> None:
        self.add("deploy.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        result = preflight.detect_has_checks(profile, self.root)

        self.assertIs(False, value_of(result))


class TestMultiTarget(WorkflowCase):
    def test_one_release_workflow_is_not_multi_target(self) -> None:
        self.add("deploy.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        result = preflight.detect_multi_target(profile, self.root, integration_branch="main")

        self.assertIs(False, value_of(result))

    def test_two_release_workflows_on_the_same_branch_is_multi_target(self) -> None:
        """Reported unsupported rather than partially released (spec Assumptions)."""
        self.add("deploy-api.yml", PUSH_TO_BRANCH)
        self.add("deploy-web.yml", PUSH_TO_BRANCH)
        profile = preflight.Profile()

        result = preflight.detect_multi_target(profile, self.root, integration_branch="main")

        self.assertIs(True, value_of(result))
        self.assertIn("deploy-api.yml", profile.release_evidence)
        self.assertIn("deploy-web.yml", profile.release_evidence)


if __name__ == "__main__":
    unittest.main()
