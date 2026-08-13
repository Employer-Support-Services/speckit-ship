"""Recorded ship state: ``Determined[T]``, atomic read-modify-write, degradation.

This module is the truthfulness substrate. Its whole job is to make two spec
rules structural rather than a matter of per-field discipline:

  FR-027 — every recorded value carries the time it was captured.
  FR-028 — a value the tool could not establish is recorded as *undetermined
           with a reason*, never as a default or an inferred stand-in.

Both hold because every observed value is wrapped (``Determined``), and because
``validate_determined`` rejects the one pairing that would let a guess pass as
an observation: ``determined: false`` with a non-null value. That single rule is
what makes SC-007 checkable by a linter instead of by reading the UI.

Configuration is deliberately *not* wrapped — see ``config.py``. A config value
is a human's stated intent, not an observation.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

STAGES = (
    "preflight",
    "commit",
    "publish",
    "pull_request",
    "checks",
    "merge",
    "release",
    "cleanup",
)

STAGE_OUTCOMES = ("succeeded", "failed", "skipped", "undetermined", "in_progress")

FAILURE_CLASSIFICATIONS = (
    "merge_conflict",
    "check_failure",
    "precondition",
    "permission",
)

RUN_STATUSES = ("in_progress", "halted", "complete")

CHECK_OUTCOMES = (
    "success",
    "failure",
    "pending",
    "skipped",
    "neutral",
    "cancelled",
    "timed_out",
)

RELEASE_MODES = ("observed", "executed")
RELEASE_OUTCOMES = ("released", "failed", "undetermined")

REPAIR_TARGETS = ("merge_conflict", "check_failure")
REPAIR_AUTHORITIES = ("mechanical", "proposed")
REPAIR_SUBSEQUENT = ("cleared", "still_failing", "undetermined", "not_reached")


class StateError(Exception):
    """A write was refused because it would have recorded something untrue."""


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def now_iso() -> str:
    """ISO-8601, UTC, second precision — the stamp FR-027 requires."""
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_iso(value: str) -> Optional[_dt.datetime]:
    """Parse a stamp this module wrote. Returns None rather than raising."""
    if not isinstance(value, str):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Determined[T]  (T008)
# --------------------------------------------------------------------------


def determined(value: Any, source: str) -> Dict[str, Any]:
    """A value the tool actually observed.

    ``source`` names *how* it was obtained — ``git-symbolic-ref``,
    ``gh-repo-view``, ``user-answer``, ``config``. This is what lets FR-004 and
    SC-002 be audited after the fact rather than argued about: the record says
    which system answered, so a wrong answer is traceable to a wrong source.
    """
    if not source:
        raise StateError(
            "determined() requires a non-empty source: a value with no stated "
            "provenance is indistinguishable from a guess"
        )
    return {
        "determined": True,
        "value": value,
        "captured_at": now_iso(),
        "source": source,
    }


def undetermined(reason: str) -> Dict[str, Any]:
    """An explicit statement that the tool could not establish the value.

    ``reason`` is a stable machine token plus a human sentence, e.g.
    ``"no-checks-configured: the repository reports no checks against this
    pull request"``. The token is what code branches on; the sentence is what
    the developer reads.
    """
    if not reason:
        raise StateError(
            "undetermined() requires a reason: an unexplained blank is the "
            "placeholder FR-028 forbids, wearing a different hat"
        )
    return {
        "determined": False,
        "value": None,
        "captured_at": now_iso(),
        "reason": reason,
    }


def is_determined(wrapped: Any) -> bool:
    return isinstance(wrapped, dict) and wrapped.get("determined") is True


def value_of(wrapped: Any, default: Any = None) -> Any:
    """Read a wrapped value, or ``default`` when it was not determined.

    Callers that render must not use this — an undetermined value renders as
    undetermined (FR-032), not as a fallback. It exists for control flow, where
    "we don't know, so take the cautious branch" is the correct reading.
    """
    return wrapped["value"] if is_determined(wrapped) else default


def reason_of(wrapped: Any) -> Optional[str]:
    if isinstance(wrapped, dict) and wrapped.get("determined") is False:
        return wrapped.get("reason")
    return None


def validate_determined(wrapped: Any, *, path: str = "value") -> Dict[str, Any]:
    """Reject any wrapper that could let an unobserved value read as fact. (T009)

    Called on every write path. The load-bearing clause is the third one:
    ``determined: false`` paired with a non-null value is exactly the shape
    FR-028 forbids, and refusing it on write is what makes SC-007 mechanical.
    """
    if not isinstance(wrapped, dict):
        raise StateError(f"{path}: expected a Determined object, got {type(wrapped).__name__}")

    if "determined" not in wrapped:
        raise StateError(f"{path}: missing 'determined'")
    if "captured_at" not in wrapped:
        raise StateError(f"{path}: missing 'captured_at' — FR-027 requires a capture time")
    if parse_iso(wrapped["captured_at"]) is None:
        raise StateError(f"{path}: 'captured_at' is not an ISO-8601 timestamp")

    flag = wrapped["determined"]
    if flag is True:
        if "value" not in wrapped:
            raise StateError(f"{path}: determined:true requires a 'value'")
        if not wrapped.get("source"):
            raise StateError(
                f"{path}: determined:true requires a non-empty 'source' naming how "
                "the value was obtained"
            )
        unexpected = set(wrapped) - {"determined", "value", "captured_at", "source"}
        if unexpected:
            raise StateError(f"{path}: unexpected keys for a determined value: {sorted(unexpected)}")

    elif flag is False:
        if wrapped.get("value") is not None:
            raise StateError(
                f"{path}: determined:false paired with a non-null value "
                f"({wrapped['value']!r}). This is the pairing FR-028 forbids — an "
                "undetermined value carrying a stand-in reads to every consumer as "
                "an observation."
            )
        if not wrapped.get("reason"):
            raise StateError(
                f"{path}: determined:false requires a non-empty 'reason'"
            )
        unexpected = set(wrapped) - {"determined", "value", "captured_at", "reason"}
        if unexpected:
            raise StateError(f"{path}: unexpected keys for an undetermined value: {sorted(unexpected)}")

    else:
        raise StateError(f"{path}: 'determined' must be a boolean, got {flag!r}")

    return wrapped


def validate_tree(node: Any, *, path: str = "$") -> None:
    """Walk a document and validate every Determined wrapper found in it.

    Recognizes a wrapper by the presence of both ``determined`` and
    ``captured_at``, which is the shape no other object in the schema has.
    """
    if isinstance(node, dict):
        if "determined" in node and "captured_at" in node:
            validate_determined(node, path=path)
            return
        for key, child in node.items():
            validate_tree(child, path=f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            validate_tree(child, path=f"{path}[{index}]")


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def state_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".specify" / "extensions" / "ship" / "state.json"


def empty_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"extension": "ship", "version": "0.1.0"},
        "profile": {
            "is_repository": undetermined(
                "not-yet-observed: preflight has not run in this repository"
            ),
            "verified_at": now_iso(),
        },
        "runs": [],
    }


# --------------------------------------------------------------------------
# Degradation  (T011)
# --------------------------------------------------------------------------


class LoadResult:
    """The outcome of reading ``state.json``.

    ``read_only`` is the one field callers must respect: a state file written by
    a newer schema is readable but must not be rewritten, because writing it
    back through an older writer would silently drop fields the newer version
    depends on.

    No load condition aborts a ship run (FR-029). That is why this is a result
    object and not an exception.
    """

    def __init__(
        self,
        document: Dict[str, Any],
        *,
        condition: str = "ok",
        message: str = "",
        read_only: bool = False,
        moved_aside: Optional[Path] = None,
    ) -> None:
        self.document = document
        self.condition = condition  # ok | missing | unparseable | newer | older
        self.message = message
        self.read_only = read_only
        self.moved_aside = moved_aside

    @property
    def degraded(self) -> bool:
        return self.condition != "ok"

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<LoadResult {self.condition} read_only={self.read_only}>"


def load(repo_root: Path) -> LoadResult:
    """Read ``state.json``, degrading rather than failing. (T011)

    Four conditions, none of which may stop a ship run:

    ==============  ====================================================
    missing         treat as empty state
    unparseable     move aside as ``state.json.corrupt-<timestamp>``,
                    start fresh
    newer version   report, read-only, do not write
    older version   migrate forward on write, keep unknown keys
    ==============  ====================================================
    """
    path = state_path(repo_root)

    if not path.is_file():
        return LoadResult(
            empty_state(),
            condition="missing",
            message=f"No recorded ship state at {path}; starting fresh.",
        )

    raw = path.read_text(encoding="utf-8")

    try:
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("top level is not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        stamp = now_iso().replace(":", "").replace("-", "")
        aside = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, aside)
        except OSError:  # pragma: no cover - filesystem refusal
            aside = None
        return LoadResult(
            empty_state(),
            condition="unparseable",
            message=(
                f"Recorded ship state at {path} could not be parsed ({exc}). "
                + (f"Moved aside as {aside.name}; " if aside else "")
                + "starting fresh. No run was aborted for this."
            ),
            moved_aside=aside,
        )

    version = document.get("schema_version")

    if not isinstance(version, int):
        document["schema_version"] = SCHEMA_VERSION
        return LoadResult(
            document,
            condition="older",
            message=(
                f"Recorded ship state at {path} carries no usable schema_version "
                f"({version!r}); treating it as version {SCHEMA_VERSION} and "
                "migrating forward on the next write."
            ),
        )

    if version > SCHEMA_VERSION:
        return LoadResult(
            document,
            condition="newer",
            message=(
                f"Recorded ship state at {path} was written by schema_version "
                f"{version}, newer than this extension understands "
                f"({SCHEMA_VERSION}). Reading it read-only — this run will not "
                "write state, so nothing that version records is lost."
            ),
            read_only=True,
        )

    if version < SCHEMA_VERSION:
        return LoadResult(
            document,
            condition="older",
            message=(
                f"Recorded ship state at {path} is schema_version {version}; "
                f"migrating forward to {SCHEMA_VERSION} on the next write. "
                "Unknown keys are preserved."
            ),
        )

    return LoadResult(document)


# --------------------------------------------------------------------------
# Atomic read-modify-write  (T010)
# --------------------------------------------------------------------------


def save(repo_root: Path, document: Dict[str, Any]) -> Path:
    """Write ``state.json`` atomically, validating every wrapper first.

    Temp file plus ``os.replace``, matching the Companion's ``.spec-context.json``
    handling. The atomicity is not incidental: the Ship view watches this file
    (FR-035), and a watcher must never observe a half-written document.
    """
    validate_tree(document)

    document = dict(document)
    document["schema_version"] = SCHEMA_VERSION

    path = state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def update(repo_root: Path, mutate) -> LoadResult:
    """Read, apply ``mutate`` to a deep copy, validate, write. (T010)

    Unknown top-level keys survive because ``mutate`` receives the whole loaded
    document — a newer writer's fields are carried through untouched rather than
    rebuilt from a known-keys template.

    A ``read_only`` load is honored: the mutation is skipped and the caller is
    told, rather than the run being aborted (FR-029).
    """
    result = load(repo_root)
    if result.read_only:
        return result

    working = copy.deepcopy(result.document)
    mutate(working)
    save(repo_root, working)
    result.document = working
    return result


# --------------------------------------------------------------------------
# Record writers  (T012)
# --------------------------------------------------------------------------


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StateError(message)


def make_run_id(branch: str, when: Optional[str] = None) -> str:
    """``<ISO-8601 compact>-<branch-slug>``, stable across resumptions."""
    stamp = (when or now_iso()).replace("-", "").replace(":", "").replace("Z", "")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", branch).strip("-").lower() or "branch"
    return f"{stamp}-{slug}"


def new_run(
    *,
    branch: str,
    target_branch: str,
    run_id: Optional[str] = None,
    head_sha: Optional[str] = None,
) -> Dict[str, Any]:
    _require(bool(branch), "new_run: branch is required")
    _require(bool(target_branch), "new_run: target_branch is required")
    _require(
        branch != target_branch,
        f"new_run: refusing a run from {branch!r} into itself — this is the "
        "FR-005 self-PR refusal at the record layer",
    )
    return {
        "run_id": run_id or make_run_id(branch),
        "branch": branch,
        "target_branch": target_branch,
        "head_sha": head_sha,
        "pr": None,
        "merge_commit_sha": None,
        "stages": [],
        "repairs": [],
        "status": "in_progress",
        "halt_reason": None,
        "started_at": now_iso(),
        "ended_at": None,
    }


def make_confirmation(
    *, granted_by: str, prompt: str = "", granted_at: Optional[str] = None
) -> Dict[str, Any]:
    """A per-run authorization.

    ``scope`` is the constant ``"run"`` by design. The spec excludes a persistent
    always-yes setting, so the record gives it nowhere to live — a confirmation
    cannot be written that outlives its run.
    """
    _require(bool(granted_by), "make_confirmation: granted_by is required")
    record = {
        "granted_by": granted_by,
        "granted_at": granted_at or now_iso(),
        "scope": "run",
    }
    if prompt:
        record["prompt"] = prompt
    return record


def make_stage(
    *,
    stage: str,
    outcome: str,
    reason: Optional[str] = None,
    classification: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    checks: Optional[List[Dict[str, Any]]] = None,
    release: Optional[Dict[str, Any]] = None,
    confirmation: Optional[Dict[str, Any]] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Stage Outcome, enforcing the schema's conditional requirements.

    These are the conditions that turn advisory rules into refusals:

    * ``failed`` requires a ``classification`` (FR-016) — "it broke" is not a
      report.
    * ``undetermined`` and ``skipped`` require a ``reason`` (FR-028).
    * ``merge`` and ``release`` reaching a terminal outcome require a
      ``confirmation`` scoped to this run (FR-013, SC-006).
    """
    _require(stage in STAGES, f"make_stage: unknown stage {stage!r}")
    _require(outcome in STAGE_OUTCOMES, f"make_stage: unknown outcome {outcome!r}")

    if outcome == "failed":
        _require(
            classification in FAILURE_CLASSIFICATIONS,
            f"make_stage({stage}): a failed stage requires one of "
            f"{FAILURE_CLASSIFICATIONS}, got {classification!r} (FR-016)",
        )
    else:
        _require(
            classification is None,
            f"make_stage({stage}): classification is only meaningful on a failed "
            f"stage; outcome is {outcome!r}. An undetermined outcome is not a "
            "classification — it is a separate answer.",
        )

    if outcome in ("undetermined", "skipped"):
        _require(
            bool(reason),
            f"make_stage({stage}): outcome {outcome!r} requires a reason (FR-028)",
        )

    if stage in ("merge", "release") and outcome in ("succeeded", "failed"):
        _require(
            confirmation is not None,
            f"make_stage({stage}): a {outcome} {stage} requires a confirmation "
            "for this run (FR-013, SC-006). There is no unattended path to this "
            "record.",
        )
        _require(
            confirmation.get("scope") == "run",
            f"make_stage({stage}): confirmation.scope must be 'run', got "
            f"{confirmation.get('scope')!r}",
        )

    if release is not None:
        validate_release_record(release)

    record: Dict[str, Any] = {
        "stage": stage,
        "outcome": outcome,
        "started_at": started_at or now_iso(),
        "ended_at": ended_at if ended_at is not None else (
            None if outcome == "in_progress" else now_iso()
        ),
    }
    if classification is not None:
        record["classification"] = classification
    if reason is not None:
        record["reason"] = reason
    if detail is not None:
        record["detail"] = detail
    if checks is not None:
        record["checks"] = [validate_check_result(c) for c in checks]
    if release is not None:
        record["release"] = release
    if confirmation is not None:
        record["confirmation"] = confirmation
    return record


def validate_check_result(check: Dict[str, Any]) -> Dict[str, Any]:
    _require(bool(check.get("name")), "check result: name is required")
    _require(
        check.get("outcome") in CHECK_OUTCOMES,
        f"check result {check.get('name')!r}: unknown outcome {check.get('outcome')!r}",
    )
    _require(bool(check.get("captured_at")), "check result: captured_at is required (FR-027)")
    if "required" in check:
        validate_determined(check["required"], path=f"check[{check['name']}].required")
    return check


def make_check_result(
    *,
    name: str,
    outcome: str,
    required: Dict[str, Any],
    url: Optional[str] = None,
    log_excerpt: Optional[str] = None,
) -> Dict[str, Any]:
    """One check reported against the PR.

    ``required`` is wrapped because it genuinely can be unknown: when branch
    protection cannot be read, an optional-looking failure may in fact be
    required, and recording ``false`` there would gate a merge on a guess.
    """
    return validate_check_result(
        {
            "name": name,
            "required": required,
            "outcome": outcome,
            "url": url,
            "log_excerpt": log_excerpt,
            "captured_at": now_iso(),
        }
    )


def validate_release_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """The one record the tool must never be able to write untruthfully.

    ``evidence`` is required and non-empty for *every* outcome, including
    ``released``. This is the schema-level expression of FR-015 and SC-012: a
    release claimed with nothing behind it is refused at the write, not caught
    in review.
    """
    _require(
        record.get("mode") in RELEASE_MODES,
        f"release record: mode must be one of {RELEASE_MODES}, got {record.get('mode')!r}",
    )
    _require(
        isinstance(record.get("from_merge_sha"), str) and len(record["from_merge_sha"]) >= 7,
        "release record: from_merge_sha is required and must be a real SHA — it is "
        "the correlation key between a merge and a release (research R6)",
    )
    _require(
        record.get("outcome") in RELEASE_OUTCOMES,
        f"release record: outcome must be one of {RELEASE_OUTCOMES}, got "
        f"{record.get('outcome')!r}",
    )
    evidence = record.get("evidence")
    _require(
        isinstance(evidence, str) and evidence.strip() != "",
        f"release record: outcome {record.get('outcome')!r} written with empty "
        "evidence. FR-015 and SC-012 forbid reporting a release that nothing "
        "confirmed — a release is complete only when the release path itself "
        "said so.",
    )
    _require(bool(record.get("confirmed_at")), "release record: confirmed_at is required")
    return record


def make_release_record(
    *,
    mode: str,
    from_merge_sha: str,
    outcome: str,
    evidence: str,
    identifier: Optional[str] = None,
) -> Dict[str, Any]:
    return validate_release_record(
        {
            "mode": mode,
            "from_merge_sha": from_merge_sha,
            "identifier": identifier,
            "outcome": outcome,
            "evidence": evidence,
            "confirmed_at": now_iso(),
        }
    )


def make_repair_attempt(
    *,
    attempt: int,
    targets: str,
    authority: str,
    description: str,
    pushed_sha: Optional[str] = None,
    subsequent_checks: str = "not_reached",
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
) -> Dict[str, Any]:
    _require(attempt >= 1, f"repair attempt: attempt must be 1-based, got {attempt}")
    _require(targets in REPAIR_TARGETS, f"repair attempt: unknown target {targets!r}")
    _require(
        authority in REPAIR_AUTHORITIES,
        f"repair attempt: unknown authority {authority!r}",
    )
    _require(bool(description), "repair attempt: description is required")
    _require(
        subsequent_checks in REPAIR_SUBSEQUENT,
        f"repair attempt: unknown subsequent_checks {subsequent_checks!r}",
    )
    if authority == "proposed":
        _require(
            pushed_sha is None,
            "repair attempt: a 'proposed' repair was described and awaited, not "
            "applied (Acceptance 2.4) — it cannot carry a pushed_sha",
        )
    return {
        "attempt": attempt,
        "targets": targets,
        "authority": authority,
        "description": description,
        "pushed_sha": pushed_sha,
        "subsequent_checks": subsequent_checks,
        "started_at": started_at or now_iso(),
        "ended_at": ended_at,
    }


# --------------------------------------------------------------------------
# Run helpers
# --------------------------------------------------------------------------


def find_run(document: Dict[str, Any], run_id: str) -> Optional[Dict[str, Any]]:
    for run in document.get("runs", []):
        if run.get("run_id") == run_id:
            return run
    return None


def latest_run_for_branch(
    document: Dict[str, Any], branch: str
) -> Optional[Dict[str, Any]]:
    """Newest-last ordering, so the last match is the current one."""
    for run in reversed(document.get("runs", [])):
        if run.get("branch") == branch:
            return run
    return None


def stage_record(run: Dict[str, Any], stage: str) -> Optional[Dict[str, Any]]:
    """The most recent record for ``stage`` in ``run``, or None."""
    for entry in reversed(run.get("stages", [])):
        if entry.get("stage") == stage:
            return entry
    return None


def stage_outcome(run: Dict[str, Any], stage: str) -> Optional[str]:
    record = stage_record(run, stage)
    return record.get("outcome") if record else None


def append_stage(run: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Append a stage record, replacing a trailing ``in_progress`` for that stage.

    The replacement is what makes the write-ahead journal (research R8) work: a
    stage is recorded ``in_progress`` before its side effect and its outcome
    after, and the outcome supersedes the marker rather than accumulating beside
    it.
    """
    stages = run.setdefault("stages", [])
    for index in range(len(stages) - 1, -1, -1):
        existing = stages[index]
        if existing.get("stage") == record["stage"]:
            if existing.get("outcome") == "in_progress":
                record.setdefault("started_at", existing.get("started_at"))
                stages[index] = record
                return record
            break
    stages.append(record)
    return record


def set_halted(
    run: Dict[str, Any], *, classification: str, message: str, stage: str
) -> Dict[str, Any]:
    _require(
        classification in FAILURE_CLASSIFICATIONS,
        f"set_halted: unknown classification {classification!r}",
    )
    _require(stage in STAGES, f"set_halted: unknown stage {stage!r}")
    run["status"] = "halted"
    run["halt_reason"] = {
        "classification": classification,
        "message": message,
        "stage": stage,
    }
    run["ended_at"] = now_iso()
    return run


def set_complete(run: Dict[str, Any]) -> Dict[str, Any]:
    run["status"] = "complete"
    run["ended_at"] = now_iso()
    return run
