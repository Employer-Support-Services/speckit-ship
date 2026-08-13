"""Ship configuration: load with defaults, validate on save.

``config.json`` is *committed* — settings travel with the repository (FR-041) —
which is the opposite posture from ``state.json``. See research.md R7.

Configuration values are **not** wrapped in ``Determined``. A config value is a
human's stated intent, not an observation: it is present or absent, and absence
means "use the documented default", not "we could not tell".

Validation (FR-040) rejects the whole document rather than repairing it, names
the specific problem, and leaves the previous file byte-identical. A partially
applied configuration is worse than a rejected one, because the developer would
have no way to know which half took effect.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA_VERSION = 1

COMPOSITION_MODES = ("manual", "commits", "drafted")
MERGE_METHODS = ("squash", "merge", "rebase")
RELEASE_MODES = ("observed", "executed", "none")

# (minimum, maximum) for every limits.* key, from contracts/ship-config.schema.json.
LIMIT_RANGES = {
    "checks_wait_seconds": (60, 86400),
    "release_wait_seconds": (60, 86400),
    "repair_budget": (0, 5),
    "freshness_seconds": (60, 86400),
}

DEFAULTS: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    # null means "the branch currently checked out" (FR-037).
    "source_branch": None,
    # null means "detect per the research.md R4 precedence". Never 'main' (FR-002).
    "target_branch": None,
    "remote": "origin",
    "pr": {
        "composition": "commits",
        "merge_method": "squash",
        "title_template": None,
        "draft": False,
    },
    "release": {
        # null means "detect at preflight per research.md R6" (FR-039, FR-043).
        "mode": None,
        "action": None,
        "observed_workflow": None,
    },
    "limits": {
        "checks_wait_seconds": 1800,
        "release_wait_seconds": 1800,
        "repair_budget": 2,
        "freshness_seconds": 900,
    },
    "cleanup": {
        "delete_branch": True,
        "return_to_integration": True,
    },
}


class ConfigError(Exception):
    """A configuration was rejected. The message names the specific problem."""


def config_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".specify" / "extensions" / "ship" / "config.json"


def defaults() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """One level of nesting is all the schema has, so this is deliberately shallow."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            merged = dict(result[key])
            merged.update(value)
            result[key] = merged
        else:
            result[key] = value
    return result


class LoadResult:
    def __init__(
        self,
        config: Dict[str, Any],
        *,
        condition: str = "ok",
        message: str = "",
        path: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.condition = condition  # ok | missing | unparseable
        self.message = message
        self.path = path

    @property
    def degraded(self) -> bool:
        return self.condition != "ok"


def load(repo_root: Path) -> LoadResult:
    """Load configuration, filling every absent key with its documented default.

    An unreadable config degrades to defaults with the condition reported, for
    the same reason ``state.load`` degrades: refusing to run because a settings
    file is malformed helps nobody, and the defaults are all documented.
    """
    path = config_path(repo_root)

    if not path.is_file():
        return LoadResult(
            defaults(),
            condition="missing",
            message=f"No ship configuration at {path}; using documented defaults.",
            path=path,
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level is not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        return LoadResult(
            defaults(),
            condition="unparseable",
            message=(
                f"Ship configuration at {path} could not be parsed ({exc}); "
                "using documented defaults. The file was left untouched — fix or "
                "delete it."
            ),
            path=path,
        )

    return LoadResult(_merge(DEFAULTS, raw), path=path)


def _fail(problems: List[str], message: str) -> None:
    problems.append(message)


def validate_config(
    config: Dict[str, Any],
    *,
    remote_branch_exists: Optional[Callable[[str, str], Optional[bool]]] = None,
    permitted_merge_methods: Optional[List[str]] = None,
    known_remotes: Optional[List[str]] = None,
) -> List[str]:
    """Validate a configuration document. Returns the list of problems found.

    An empty list means valid. Callers reject the whole document on a non-empty
    list — see ``save``.

    The three optional callbacks are the checks that need the world:

    ``remote_branch_exists(remote, branch)``
        Returns True, False, or **None for "could not check"**. None is not a
        failure: an offline developer must still be able to save a config. It is
        a check that did not run, and it is reported as such rather than
        resolved either way.

    ``permitted_merge_methods``
        When None, the merge-method check is skipped and the control is expected
        to render disabled with the reason (FR-036) rather than be saved
        optimistically.

    ``known_remotes``
        When None, the remote name is not checked against the repository.
    """
    problems: List[str] = []

    version = config.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int):
        _fail(problems, f"schema_version must be an integer, got {version!r}")

    remote = config.get("remote", "origin")
    if not isinstance(remote, str) or not remote:
        _fail(problems, f"remote must be a non-empty string, got {remote!r}")
    elif known_remotes is not None and remote not in known_remotes:
        _fail(
            problems,
            f"remote {remote!r} is not configured in this repository "
            f"(found: {', '.join(known_remotes) or 'none'})",
        )

    source = config.get("source_branch")
    target = config.get("target_branch")

    for label, branch in (("source_branch", source), ("target_branch", target)):
        if branch is not None and (not isinstance(branch, str) or not branch):
            _fail(problems, f"{label} must be a non-empty string or null, got {branch!r}")

    # FR-005's self-PR refusal, enforced at configuration time as well as at run
    # time. Catching it here means the developer learns about it when they make
    # the mistake, not eight stages into a run.
    if source is not None and target is not None and source == target:
        _fail(
            problems,
            f"source_branch and target_branch are both {source!r}: a branch "
            "cannot be shipped into itself",
        )

    if target is not None and isinstance(target, str) and remote_branch_exists is not None:
        exists = remote_branch_exists(remote if isinstance(remote, str) else "origin", target)
        if exists is False:
            _fail(
                problems,
                f"target_branch {target!r} does not resolve on remote {remote!r}",
            )
        # exists is None -> could not check; not a failure, and not silently a pass.

    pr = config.get("pr") or {}
    if not isinstance(pr, dict):
        _fail(problems, f"pr must be an object, got {type(pr).__name__}")
        pr = {}

    composition = pr.get("composition", "commits")
    if composition not in COMPOSITION_MODES:
        _fail(
            problems,
            f"pr.composition must be one of {', '.join(COMPOSITION_MODES)}, "
            f"got {composition!r}",
        )

    merge_method = pr.get("merge_method", "squash")
    if merge_method not in MERGE_METHODS:
        _fail(
            problems,
            f"pr.merge_method must be one of {', '.join(MERGE_METHODS)}, "
            f"got {merge_method!r}",
        )
    elif permitted_merge_methods is not None and merge_method not in permitted_merge_methods:
        _fail(
            problems,
            f"pr.merge_method {merge_method!r} is not enabled on this repository "
            f"(permitted: {', '.join(permitted_merge_methods) or 'none'})",
        )

    if not isinstance(pr.get("draft", False), bool):
        _fail(problems, f"pr.draft must be a boolean, got {pr.get('draft')!r}")

    release = config.get("release") or {}
    if not isinstance(release, dict):
        _fail(problems, f"release must be an object, got {type(release).__name__}")
        release = {}

    mode = release.get("mode")
    if mode is not None and mode not in RELEASE_MODES:
        _fail(
            problems,
            f"release.mode must be one of {', '.join(RELEASE_MODES)} or null, "
            f"got {mode!r}",
        )

    action = release.get("action")
    if mode == "executed":
        # The tool never composes a release action (spec Assumptions). Executed
        # mode without a declared action is not a mode it can fall back from.
        if not isinstance(action, dict) or not action:
            _fail(
                problems,
                "release.mode is 'executed' but release.action is not set. The "
                "repository must declare its own release action — this tool runs "
                "what the repository declares and never composes one.",
            )
        else:
            problems.extend(_validate_release_action(action))
    elif action is not None:
        if not isinstance(action, dict):
            _fail(problems, f"release.action must be an object or null, got {action!r}")
        else:
            problems.extend(_validate_release_action(action))

    limits = config.get("limits") or {}
    if not isinstance(limits, dict):
        _fail(problems, f"limits must be an object, got {type(limits).__name__}")
        limits = {}

    for key, (low, high) in LIMIT_RANGES.items():
        if key not in limits:
            continue
        value = limits[key]
        if not isinstance(value, int) or isinstance(value, bool):
            _fail(problems, f"limits.{key} must be an integer, got {value!r}")
        elif not (low <= value <= high):
            _fail(problems, f"limits.{key} must be between {low} and {high}, got {value}")

    unknown_limits = set(limits) - set(LIMIT_RANGES)
    if unknown_limits:
        _fail(problems, f"unknown limits key(s): {', '.join(sorted(unknown_limits))}")

    cleanup = config.get("cleanup") or {}
    if not isinstance(cleanup, dict):
        _fail(problems, f"cleanup must be an object, got {type(cleanup).__name__}")
        cleanup = {}
    for key in ("delete_branch", "return_to_integration"):
        if key in cleanup and not isinstance(cleanup[key], bool):
            _fail(problems, f"cleanup.{key} must be a boolean, got {cleanup[key]!r}")

    known_top = set(DEFAULTS)
    unknown_top = set(config) - known_top
    if unknown_top:
        _fail(problems, f"unknown configuration key(s): {', '.join(sorted(unknown_top))}")

    return problems


def _validate_release_action(action: Dict[str, Any]) -> List[str]:
    """One of three declared shapes: workflow, release, or script."""
    problems: List[str] = []
    shapes = [key for key in ("workflow", "release", "script") if key in action]

    if len(shapes) != 1:
        problems.append(
            "release.action must declare exactly one of 'workflow', 'release', "
            f"or 'script'; found {shapes or 'none'}"
        )
        return problems

    shape = shapes[0]
    allowed = {
        "workflow": {"workflow", "ref", "inputs"},
        "release": {"release"},
        "script": {"script"},
    }[shape]

    unexpected = set(action) - allowed
    if unexpected:
        problems.append(
            f"release.action ({shape} form) has unexpected key(s): "
            f"{', '.join(sorted(unexpected))}"
        )

    if shape == "workflow" and not isinstance(action.get("workflow"), str):
        problems.append("release.action.workflow must be a workflow filename")
    if shape == "script" and not isinstance(action.get("script"), str):
        problems.append("release.action.script must be a repository-relative path")
    if shape == "release" and not isinstance(action.get("release"), dict):
        problems.append("release.action.release must be an object")

    return problems


def save(repo_root: Path, config: Dict[str, Any], **validation_kwargs) -> Path:
    """Validate then write atomically. Raises ``ConfigError`` on any problem.

    On rejection nothing is written — the previous configuration is retained
    byte-identical (FR-040). The temp-file-plus-replace is what guarantees the
    "no half-written file" half of that requirement even if the process dies
    mid-write.
    """
    problems = validate_config(config, **validation_kwargs)
    if problems:
        raise ConfigError(
            "Configuration rejected; the previous configuration was retained.\n  - "
            + "\n  - ".join(problems)
        )

    document = dict(config)
    document["schema_version"] = SCHEMA_VERSION

    path = config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path
