"""Replays captured ``gh --json`` payloads through the HostingClient protocol.

**This module lives under ``tests/`` and must never be reachable from a
production path.** ``tests/contract/test_no_fake_in_production.py`` enforces that
with an import walk rather than trusting convention — see invariant 5 of
contracts/hosting-client.md. A fake wired into production would not fail loudly;
it would report a plausible green and be believed.

It is a test double in test code, which the "no mocks in production" rule
explicitly permits. What makes it useful rather than merely convenient is that
it replays *recorded* payloads: the field names, the null-vs-absent distinctions,
and the enum spellings are GitHub's, not ours. A hand-written fake would agree
with whatever the implementation happened to assume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.hosting import HostingClient, Result

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gh"


def load_fixture(name: str) -> Dict[str, Any]:
    """Read one fixture by filename (with or without the .json suffix)."""
    if not name.endswith(".json"):
        name = f"{name}.json"
    path = FIXTURES / name
    if not path.is_file():
        available = sorted(p.name for p in FIXTURES.glob("*.json"))
        raise FileNotFoundError(
            f"No such gh fixture: {name}. Available: {', '.join(available)}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_pr_view(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the same normalization ``GhClient.pr_view`` applies.

    Kept in one place so the two implementations cannot drift on the part that
    matters most — in particular that ``UNKNOWN`` becomes ``None`` rather than
    being passed through as a mergeability verdict.
    """
    raw_mergeable = payload.get("mergeable")
    merge_commit = payload.get("mergeCommit") or {}
    rollup = payload.get("statusCheckRollup")

    return {
        "number": payload.get("number"),
        "url": payload.get("url"),
        "state": payload.get("state"),
        "base": payload.get("baseRefName"),
        "head": payload.get("headRefName"),
        "mergeable": None if raw_mergeable in (None, "UNKNOWN") else raw_mergeable,
        "mergeable_raw": raw_mergeable,
        "merge_state_status": payload.get("mergeStateStatus"),
        "merge_commit_sha": merge_commit.get("oid") if merge_commit else None,
        "rollup": rollup or [],
        "rollup_present": rollup is not None,
    }


class RecordedClient(HostingClient):
    """A HostingClient backed by recorded payloads.

    Every method records its calls so tests can assert on *how* the engine used
    the client — that it re-polled an UNKNOWN mergeable rather than concluding,
    for instance, which is behavior a return value alone cannot show.
    """

    def __init__(
        self,
        *,
        pr_views: Optional[List[Dict[str, Any]]] = None,
        prs: Optional[Dict[str, Any]] = None,
        default_branch: str = "trunk",
        repo_meta: Optional[Dict[str, Any]] = None,
        run_list: Optional[List[Dict[str, Any]]] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        authenticated: bool = True,
        logs: str = "",
        # None models the common case: branch protection needs admin scope, so
        # requiredness usually cannot be read at all. A list models a repository
        # where it can.
        required_check_names: Optional[List[str]] = None,
    ) -> None:
        self._required_check_names = required_check_names
        # A list, consumed in order, so a test can model "UNKNOWN, then
        # MERGEABLE" — the lazy-computation sequence. The last entry repeats
        # once exhausted.
        self._pr_views = list(pr_views or [])
        self._prs = prs or {}
        self._default_branch = default_branch
        self._repo_meta = repo_meta or {
            "name_with_owner": "acme/thing",
            "private": False,
            "delete_branch_on_merge": False,
            "permitted_merge_methods": ["squash", "merge", "rebase"],
        }
        self._run_list = list(run_list or [])
        self._capabilities = capabilities or {
            "pr_view_json": True,
            "pr_checks_json": False,  # gh 2.4.0 — the floor version
            "run_list_json": True,
            "run_list_branch_filter": False,
            "workflow_run": True,
            "release_create": True,
        }
        self._authenticated = authenticated
        self._logs = logs

        self.calls: List[tuple] = []
        self.merged: List[Dict[str, Any]] = []
        self.created_prs: List[Dict[str, Any]] = []
        self.dispatched: List[Dict[str, Any]] = []
        self.released: List[Dict[str, Any]] = []

    # -- helpers ---------------------------------------------------------

    @classmethod
    def from_fixture(cls, name: str, **kwargs) -> "RecordedClient":
        return cls(pr_views=[load_fixture(name)], **kwargs)

    @classmethod
    def from_fixtures(cls, *names: str, **kwargs) -> "RecordedClient":
        return cls(pr_views=[load_fixture(n) for n in names], **kwargs)

    def _next_pr_view(self) -> Dict[str, Any]:
        if not self._pr_views:
            raise AssertionError("RecordedClient has no pr_view payloads configured")
        if len(self._pr_views) == 1:
            return self._pr_views[0]
        return self._pr_views.pop(0)

    def call_count(self, method: str) -> int:
        return sum(1 for name, *_ in self.calls if name == method)

    # -- protocol --------------------------------------------------------

    def probe(self) -> Result:
        self.calls.append(("probe",))
        payload = {
            "reachable": True,
            "authenticated": self._authenticated,
            "host": "github.com",
            "gh_version": "2.4.0",
            "capabilities": self._capabilities,
        }
        if not self._authenticated:
            return Result(
                ok=True,
                value=payload,
                reason="gh-unauthenticated: no usable credential for github.com",
            )
        return Result.of(payload)

    def default_branch(self) -> Result:
        self.calls.append(("default_branch",))
        if self._default_branch is None:
            return Result.unknown("gh-no-default-branch: no defaultBranchRef reported")
        return Result.of(self._default_branch)

    def repo_meta(self) -> Result:
        self.calls.append(("repo_meta",))
        return Result.of(self._repo_meta)

    def required_checks(self, branch: str) -> Result:
        """Which checks branch protection requires on ``branch``.

        An undetermined result is the *common* case, not an error: reading
        protection usually needs admin scope. Returning an empty list instead
        would silently mean "nothing is required", which turns a permission gap
        into a claim about the repository.
        """
        self.calls.append(("required_checks", branch))
        if self._required_check_names is None:
            return Result.unknown(
                "gh-failed: branch protection for this branch could not be read"
            )
        return Result.of(list(self._required_check_names))

    def find_pr(self, branch: str) -> Result:
        self.calls.append(("find_pr", branch))
        if branch not in self._prs:
            return Result.of(None, reason="no-pr-for-branch")
        return Result.of(self._prs[branch])

    def create_pr(
        self, *, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> Result:
        self.calls.append(("create_pr", head, base))
        number = 9000 + len(self.created_prs)
        record = {
            "url": f"https://github.com/acme/thing/pull/{number}",
            "number": number,
            "state": "OPEN",
            "title": title,
            "body": body,
            "draft": draft,
            "base": base,
            "head": head,
        }
        self.created_prs.append(record)
        # Adopted by a subsequent find_pr, so a resumed run sees it.
        self._prs[head] = {
            "number": number,
            "url": record["url"],
            "state": "OPEN",
            "base": base,
            "head": head,
        }
        return Result.of(record)

    def pr_view(self, number: int) -> Result:
        self.calls.append(("pr_view", number))
        return Result.of(normalize_pr_view(self._next_pr_view()))

    def failing_logs(self, run_id: str, *, limit: int = 8000) -> Result:
        self.calls.append(("failing_logs", run_id))
        if not self._logs:
            return Result.unknown(
                "gh-failed: no log was recorded for this run in the fixture set"
            )
        return Result.of(self._logs)

    def merge_pr(self, number: int, *, method: str, delete_branch: bool = False) -> Result:
        self.calls.append(("merge_pr", number, method))
        record = {"number": number, "method": method, "delete_branch": delete_branch}
        self.merged.append(record)
        view = normalize_pr_view(self._next_pr_view())
        return Result.of(
            {
                "merged": True,
                "merge_commit_sha": view.get("merge_commit_sha"),
                "state": "MERGED",
                "output": "merged (recorded)",
            }
        )

    def runs_for_sha(self, sha: str, branch: str) -> Result:
        self.calls.append(("runs_for_sha", sha, branch))
        matches = [
            run
            for run in self._run_list
            if run.get("headSha") == sha and run.get("headBranch") == branch
        ]
        return Result.of(matches)

    def run_status(self, run_id: str) -> Result:
        self.calls.append(("run_status", run_id))
        for run in self._run_list:
            if str(run.get("databaseId")) == str(run_id):
                return Result.of(
                    {
                        "status": run.get("status"),
                        "conclusion": run.get("conclusion"),
                        "url": run.get("url"),
                        "databaseId": run.get("databaseId"),
                    }
                )
        return Result.unknown(f"gh-failed: no run {run_id} in the fixture set")

    def watch_run(self, run_id: str, deadline: float) -> Result:
        self.calls.append(("watch_run", run_id))
        status = self.run_status(run_id)
        if not status.ok:
            return status
        data = status.value
        if data.get("status") != "completed":
            return Result.unknown(
                "release-not-confirmed: the release run had not reached a terminal "
                f"outcome within the configured wait (last status: {data.get('status')})"
            )
        return Result.of(
            {
                "status": "completed",
                "conclusion": data.get("conclusion"),
                "url": data.get("url"),
                "run_id": run_id,
            }
        )

    def dispatch_workflow(
        self, workflow: str, ref: str, inputs: Optional[Dict[str, str]] = None
    ) -> Result:
        self.calls.append(("dispatch_workflow", workflow, ref))
        record = {"workflow": workflow, "ref": ref, "inputs": inputs or {}}
        self.dispatched.append(record)
        return Result.of({**record, "output": "dispatched (recorded)"})

    def create_release(self, tag: str, notes: str = "", *, generate_notes: bool = True) -> Result:
        self.calls.append(("create_release", tag))
        record = {"tag": tag, "url": f"https://github.com/acme/thing/releases/tag/{tag}"}
        self.released.append(record)
        return Result.of({**record, "output": "released (recorded)"})
