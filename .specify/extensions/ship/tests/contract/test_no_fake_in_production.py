"""Invariant 5 of contracts/hosting-client.md, enforced mechanically.

``RecordedClient`` replays captured ``gh --json`` payloads. It exists so the
suite can be hermetic. It must never be reachable from a production path.

The reason this is a test and not a convention: the whole feature is a promise
that reported state is real. A fake wired into a production path would not fail
loudly — it would report a plausible green and be believed. Convention catches
that only if someone happens to look at the right diff; an import walk catches
it every run.

The check is deliberately crude (it reads source, it does not import) so that it
cannot itself be defeated by an import side effect.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = EXTENSION_ROOT / "scripts"

# Names that may only ever appear under tests/.
FORBIDDEN_NAMES = {"RecordedClient", "recorded_client"}


def python_sources(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


class TestNoFakeInProduction(unittest.TestCase):
    def test_scripts_directory_exists(self) -> None:
        """Guards the guard: an empty walk must not read as a pass."""
        self.assertTrue(
            SCRIPTS.is_dir(),
            f"{SCRIPTS} does not exist — this test would otherwise vacuously pass",
        )
        self.assertTrue(
            python_sources(SCRIPTS),
            f"No Python sources found under {SCRIPTS} — refusing to report a pass "
            "over an empty set",
        )

    def test_no_production_module_imports_the_recorded_client(self) -> None:
        offenders: list[str] = []

        for source in python_sources(SCRIPTS):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            rel = source.relative_to(EXTENSION_ROOT)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(part in FORBIDDEN_NAMES for part in alias.name.split(".")):
                            offenders.append(f"{rel}:{node.lineno} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module_parts = (node.module or "").split(".")
                    if any(part in FORBIDDEN_NAMES for part in module_parts):
                        offenders.append(
                            f"{rel}:{node.lineno} imports from {node.module}"
                        )
                    for alias in node.names:
                        if alias.name in FORBIDDEN_NAMES:
                            offenders.append(
                                f"{rel}:{node.lineno} imports {alias.name} "
                                f"from {node.module}"
                            )

        self.assertEqual(
            [],
            offenders,
            "A production module reaches the recorded test double. "
            "Invariant 5 of contracts/hosting-client.md forbids this:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_production_module_references_the_tests_package(self) -> None:
        """A subtler route to the same failure than importing the name directly."""
        offenders: list[str] = []

        for source in python_sources(SCRIPTS):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            rel = source.relative_to(EXTENSION_ROOT)

            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]

                for name in names:
                    if name == "tests" or name.startswith("tests."):
                        offenders.append(f"{rel}:{node.lineno} imports {name}")

        self.assertEqual(
            [],
            offenders,
            "A production module imports from the tests package:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
