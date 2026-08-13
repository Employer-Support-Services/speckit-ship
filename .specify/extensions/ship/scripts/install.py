#!/usr/bin/env python3
"""Install the ship extension into a repository's host integration.

Three jobs, all idempotent — running this twice changes nothing the second time:

  1. Generate the ``.claude/skills/speckit-ship*/SKILL.md`` wrappers from the
     ``provides.commands`` entries in ``extension.yml``, carrying the
     ``source: ship:commands/<file>`` frontmatter the Companion's own generated
     skills use.
  2. Append the run-state ignore entries to ``.specify/.gitignore``.
  3. Report what it did, so a caller can tell an install from a no-op.

Stdlib only, by design (research.md R2): this has to run in any repository with
no install step of its own. That rules out PyYAML, so the manifest is read by a
small reader that understands the exact subset of YAML *we* write in
``extension.yml`` — see ``read_commands``. It is deliberately strict: an
unexpected shape raises rather than silently yielding no commands, because an
install that quietly registers nothing is worse than one that fails.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, NamedTuple

# The ignore entries this installer maintains. Kept here rather than in a
# template file so the installer is self-contained.
GITIGNORE_BLOCK = [
    "extensions/ship/state.json",
    "extensions/ship/state.json.corrupt-*",
    "extensions/ship/ship.lock",
]

GITIGNORE_HEADER = """
# Ship run history and its concurrency lock. Deliberately NOT shared: state.json
# is rewritten on every ship, so committing it would conflict on every merge into
# the integration branch — a tool that gets harder to use the more you use it.
# Ship *configuration* (extensions/ship/config.json) is committed, by contrast,
# because settings are meant to travel with the repository (FR-041).
""".strip()


class Command(NamedTuple):
    """One entry from ``provides.commands``."""

    name: str  # e.g. "speckit.ship.status"
    file: str  # e.g. "commands/speckit.ship.status.md"
    description: str

    @property
    def skill_name(self) -> str:
        """``speckit.ship.status`` -> ``speckit-ship-status``.

        The separator comes from ``.specify/integration.json``'s
        ``invoke_separator`` for the claude integration, which is ``-``.
        """
        return self.name.replace(".", "-")


class InstallReport(NamedTuple):
    skills_written: List[str]
    skills_unchanged: List[str]
    # (skill_name, reason) for commands declared in the manifest that could not
    # be installed. Reported, never silently dropped.
    skills_skipped: List[tuple]
    gitignore_changed: bool

    @property
    def changed(self) -> bool:
        return bool(self.skills_written) or self.gitignore_changed


def extension_root() -> Path:
    """The ``.specify/extensions/ship`` directory this script lives in."""
    return Path(__file__).resolve().parent.parent


def repo_root(start: Path | None = None) -> Path:
    """Walk up to the directory holding ``.specify/``.

    Not ``git rev-parse``: install has to work before the repository question is
    settled, and the ``.specify`` marker is the one this extension actually
    depends on.
    """
    here = (start or extension_root()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".specify").is_dir():
            return candidate
    raise SystemExit(
        "[ship] Could not locate the repository root: no .specify/ directory "
        f"found at or above {here}"
    )


def read_commands(manifest: Path) -> List[Command]:
    """Read ``provides.commands`` from our own ``extension.yml``.

    A full YAML parser is not available (stdlib only) and not warranted — this
    reads one known list out of one file we author. It is strict on purpose:
    every entry must carry all three keys, and finding zero commands in a
    manifest that has a ``provides:`` block is an error, not an empty install.
    """
    if not manifest.is_file():
        raise SystemExit(f"[ship] Manifest not found: {manifest}")

    lines = manifest.read_text(encoding="utf-8").splitlines()

    in_commands = False
    commands: List[Command] = []
    current: dict = {}

    entry_re = re.compile(r"^\s{4}- (\w+):\s*(.*)$")
    field_re = re.compile(r"^\s{6}(\w+):\s*(.*)$")

    def flush() -> None:
        if not current:
            return
        missing = {"name", "file", "description"} - current.keys()
        if missing:
            raise SystemExit(
                f"[ship] Malformed command entry in {manifest}: "
                f"missing {', '.join(sorted(missing))} in {current!r}"
            )
        commands.append(
            Command(current["name"], current["file"], current["description"])
        )
        current.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue

        if re.match(r"^\s{2}commands:\s*$", line):
            in_commands = True
            continue

        if in_commands:
            # Any key at two-space indent that is not a list item ends the block.
            if re.match(r"^\s{0,2}\S", line):
                flush()
                in_commands = False
                continue

            entry = entry_re.match(line)
            if entry:
                flush()
                current[entry.group(1)] = _unquote(entry.group(2))
                continue

            field = field_re.match(line)
            if field:
                current[field.group(1)] = _unquote(field.group(2))
                continue

    flush()

    if not commands:
        raise SystemExit(
            f"[ship] No commands found in {manifest}. Refusing to report a "
            "successful install that registered nothing."
        )
    return commands


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def skill_body(command: Command, command_markdown: Path) -> str:
    """Compose the ``SKILL.md`` wrapper for one command.

    The wrapper carries the frontmatter the host reads and then defers to the
    command markdown, which is the single source of the instructions. It does
    not copy that markdown's content: two copies drift, and the ``source:`` line
    exists precisely so there is one.

    Callers must not reach here for a missing markdown file — see
    ``install()``, which skips those rather than generating a stub. A registered
    command whose instructions are absent is a control that looks operable and
    is not, which is the exact defect this tool exists to avoid.
    """
    body = command_markdown.read_text(encoding="utf-8")

    # Strip the command markdown's own frontmatter; the wrapper supplies its own.
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) == 3:
            body = parts[2].lstrip("\n")

    return (
        "---\n"
        f"name: {command.skill_name}\n"
        f"description: {command.description}\n"
        "compatibility: Requires spec-kit project structure with .specify/ directory\n"
        "metadata:\n"
        "  author: teamteddy\n"
        f"  source: ship:{command.file}\n"
        "---\n\n"
        f"{body}"
    )


def write_if_changed(path: Path, content: str) -> bool:
    """Write only when the content differs. Returns True when it wrote."""
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def ensure_gitignore(specify_dir: Path) -> bool:
    """Append the run-state ignore entries if absent. Returns True when changed.

    Idempotent per *entry*, not per block, so a partially-present block is
    completed rather than duplicated.
    """
    path = specify_dir / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    present = {line.strip() for line in existing.splitlines()}

    missing = [entry for entry in GITIGNORE_BLOCK if entry not in present]
    if not missing:
        return False

    addition = ""
    if existing and not existing.endswith("\n"):
        addition += "\n"
    # Only print the explanatory header when introducing the block fresh.
    if not any(entry in present for entry in GITIGNORE_BLOCK):
        addition += "\n" + GITIGNORE_HEADER + "\n"
    addition += "\n".join(missing) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(addition)
    return True


def install(root: Path, *, dry_run: bool = False) -> InstallReport:
    ext = extension_root()
    commands = read_commands(ext / "extension.yml")

    written: List[str] = []
    unchanged: List[str] = []
    skipped: List[tuple] = []

    for command in commands:
        markdown = ext / command.file
        if not markdown.is_file():
            # Register nothing rather than register a stub. A command that
            # appears in the skill list and has no instructions behind it is
            # worse than a command that is visibly absent.
            skipped.append((command.skill_name, f"{command.file} not present"))
            continue

        target = root / ".claude" / "skills" / command.skill_name / "SKILL.md"
        content = skill_body(command, markdown)
        if dry_run:
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            (written if current != content else unchanged).append(command.skill_name)
            continue
        (written if write_if_changed(target, content) else unchanged).append(
            command.skill_name
        )

    gitignore_changed = False if dry_run else ensure_gitignore(root / ".specify")

    return InstallReport(written, unchanged, skipped, gitignore_changed)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install the ship extension's commands into the host integration.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root to install into (default: the root containing this extension)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else repo_root()
    if not (root / ".specify").is_dir():
        print(
            f"[ship] {root} does not look like a spec-kit repository "
            "(no .specify/ directory).",
            file=sys.stderr,
        )
        return 1

    report = install(root, dry_run=args.dry_run)

    prefix = "[ship] Would install" if args.dry_run else "[ship] Installed"
    print(f"{prefix} into {root}")
    for name in report.skills_written:
        print(f"  + .claude/skills/{name}/SKILL.md")
    for name in report.skills_unchanged:
        print(f"  = .claude/skills/{name}/SKILL.md (unchanged)")
    for name, reason in report.skills_skipped:
        print(f"  - {name}: NOT installed — {reason}")
    if report.gitignore_changed:
        print("  + .specify/.gitignore run-state entries")
    if not report.changed and not report.skills_skipped:
        print("  nothing to do — already installed")
    if report.skills_skipped:
        print(
            f"\n[ship] {len(report.skills_skipped)} command(s) declared in "
            "extension.yml have no command markdown and were not registered."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
