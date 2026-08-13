#!/usr/bin/env python3
"""Audit recorded ship state for the one claim that must never be untrue.

SC-012: no release is reported as complete without a confirmation from the
release path itself. Concretely, a release record with `outcome: "released"`
and empty `evidence` is a direct violation — the tool would be asserting that
something reached production with nothing behind the claim.

**This audit is a backstop, not the enforcement.** The enforcement is at the
write: `make_release_record` and `validate_release_record` refuse an empty
evidence string for every outcome, so the violating record cannot be
constructed. An audit that finds nothing therefore proves less than it appears
to — it confirms the writer held, on the runs that happened. It would not catch
a record written by some future path that bypassed the writer, which is exactly
why the check is worth being able to run.

Exit codes: 0 clean, 1 violations found, 2 nothing to audit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def audit(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return every release record that claims a release it cannot support."""
    violations: List[Dict[str, Any]] = []

    for run in document.get("runs", []):
        for stage in run.get("stages", []):
            if stage.get("stage") != "release":
                continue
            record = stage.get("release") or {}
            if not record:
                continue

            evidence = record.get("evidence")
            outcome = record.get("outcome")

            if outcome == "released" and not (isinstance(evidence, str) and evidence.strip()):
                violations.append(
                    {
                        "run_id": run.get("run_id"),
                        "problem": "released with empty evidence",
                        "record": record,
                    }
                )

            # A release must be attributable to a merge. Without the commit it
            # correlates to, "released" describes nothing in particular.
            if outcome == "released" and not record.get("from_merge_sha"):
                violations.append(
                    {
                        "run_id": run.get("run_id"),
                        "problem": "released with no merge commit to attribute it to",
                        "record": record,
                    }
                )

            # A stage that reached a terminal release outcome must carry the
            # confirmation that authorized it (FR-013).
            if outcome in ("released", "failed") and not stage.get("confirmation"):
                violations.append(
                    {
                        "run_id": run.get("run_id"),
                        "problem": f"release {outcome} with no confirmation for its run",
                        "record": record,
                    }
                )

    return violations


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit_releases.py",
        description="Audit recorded ship state for release claims without evidence (SC-012).",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = args.root / ".specify" / "extensions" / "ship" / "state.json"

    if not path.is_file():
        message = (
            f"No recorded ship state at {path}. Nothing to audit — this is not a "
            "pass, it is an empty set."
        )
        print(json.dumps({"audited": 0, "violations": [], "note": message}) if args.json else message)
        return 2

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 2

    runs = document.get("runs", [])
    release_records = [
        stage
        for run in runs
        for stage in run.get("stages", [])
        if stage.get("stage") == "release" and stage.get("release")
    ]
    violations = audit(document)

    if args.json:
        print(json.dumps({"audited": len(release_records), "violations": violations}, indent=2))
    else:
        print(f"Audited {len(runs)} run(s), {len(release_records)} release record(s).")
        if violations:
            print("\nVIOLATIONS:")
            for v in violations:
                print(f"  {v['run_id']}: {v['problem']}")
        else:
            print("VIOLATIONS: none")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
