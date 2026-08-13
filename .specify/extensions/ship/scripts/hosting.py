"""The single seam between the engine and the hosting service.

Two implementations: ``GhClient`` here, and ``RecordedClient`` under ``tests/``
replaying captured payloads. Contract tests run both against the same
expectations, which is what keeps the recorded fixtures honest about the shapes
this machine's ``gh`` actually emits.

Four invariants from contracts/hosting-client.md, restated because each one has
a failure mode that is silent if you forget it:

1. **No method infers.** ``mergeable: "UNKNOWN"`` surfaces as undetermined,
   never as CONFLICTING. GitHub computes mergeability lazily, so the first query
   after a push very often returns UNKNOWN while a background job runs —
   treating that as a conflict fires the FR-018 repair against branches that
   merge cleanly.
2. **No method retries silently.** Retry policy belongs to the engine, so
   attempts are bounded and *recorded* rather than hidden in here.
3. **The client never writes state.** It returns; the engine records.
4. **The client never holds a credential.** ``gh`` owns the token. Nothing here
   reads, prints, or stores one.

The load-bearing version finding (research.md R3): on ``gh 2.4.0``,
``gh pr checks`` accepts only ``-w/--web`` — no ``--json``, no ``--watch``.
Checks are therefore read through ``pr_view``'s ``statusCheckRollup``. Do not
reintroduce ``gh pr checks`` for machine-readable output.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict, List, Optional, Sequence

GH_TIMEOUT = 120
GH_LONG_TIMEOUT = 600

# The one field set the checks, mergeability, and merge-SHA questions all read.
# One call answers all three, which keeps polling cheap (research.md R5).
PR_VIEW_FIELDS = "statusCheckRollup,mergeable,mergeStateStatus,state,mergeCommit,url,number,baseRefName,headRefName"

PR_FIND_FIELDS = "number,state,url,baseRefName,headRefName"

REPO_META_FIELDS = (
    "nameWithOwner,isPrivate,deleteBranchOnMerge,"
    "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed"
)

# Measured against gh 2.4.0, not assumed: this version rejects `workflowName`
# ("Unknown JSON field") and calls it `name`. It also has no `--branch` filter,
# which is why runs_for_sha filters client-side. Both are the same class of
# finding as the `gh pr checks` one in research.md R3 — a plausible field name
# from a later release fails at run time, mid-pipeline, after state has changed.
RUN_LIST_FIELDS = "headBranch,headSha,status,conclusion,databaseId,name,url,event"


class Result:
    """Every method returns one of these. A network hiccup is not a ``False``."""

    def __init__(
        self,
        *,
        ok: bool,
        value: Any = None,
        reason: str = "",
        raw: str = "",
        argv: Sequence[str] = (),
    ) -> None:
        self.ok = ok
        self.value = value
        # Machine token plus human sentence, matching Determined's reason shape.
        self.reason = reason
        self.raw = raw
        self.argv = list(argv)

    @property
    def undetermined(self) -> bool:
        return not self.ok

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<Result ok={self.ok} reason={self.reason!r}>"

    @classmethod
    def of(cls, value: Any, **kwargs) -> "Result":
        return cls(ok=True, value=value, **kwargs)

    @classmethod
    def unknown(cls, reason: str, **kwargs) -> "Result":
        return cls(ok=False, reason=reason, **kwargs)


class HostingClient:
    """The protocol the engine codes against. (T017)

    Structural, not enforced by ABC registration: ``RecordedClient`` lives in a
    different tree and implements the same twelve methods. Every one returns a
    ``Result`` that may be undetermined with a reason.
    """

    def probe(self) -> Result: raise NotImplementedError
    def default_branch(self) -> Result: raise NotImplementedError
    def repo_meta(self) -> Result: raise NotImplementedError
    def find_pr(self, branch: str) -> Result: raise NotImplementedError
    def create_pr(self, *, head: str, base: str, title: str, body: str, draft: bool = False) -> Result: raise NotImplementedError
    def pr_view(self, number: int) -> Result: raise NotImplementedError
    def failing_logs(self, run_id: str) -> Result: raise NotImplementedError
    def merge_pr(self, number: int, *, method: str, delete_branch: bool = False) -> Result: raise NotImplementedError
    def runs_for_sha(self, sha: str, branch: str) -> Result: raise NotImplementedError
    def watch_run(self, run_id: str, deadline: float) -> Result: raise NotImplementedError
    def dispatch_workflow(self, workflow: str, ref: str, inputs: Optional[Dict[str, str]] = None) -> Result: raise NotImplementedError
    def create_release(self, tag: str, notes: str = "", *, generate_notes: bool = True) -> Result: raise NotImplementedError


class GhClient(HostingClient):
    """Talks to GitHub through the ``gh`` CLI. (T018)"""

    def __init__(self, *, cwd: Optional[str] = None, timeout: int = GH_TIMEOUT) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self._probe_cache: Optional[Result] = None

    # -- invocation ------------------------------------------------------

    def _run(self, args: Sequence[str], *, timeout: Optional[int] = None) -> Result:
        argv = ["gh", *args]
        try:
            proc = subprocess.run(
                argv,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return Result.unknown(
                "gh-not-installed: the GitHub CLI is not installed or not on PATH",
                argv=argv,
            )
        except subprocess.TimeoutExpired:
            return Result.unknown(
                f"gh-timeout: `{' '.join(argv[:3])}` did not return within "
                f"{timeout or self.timeout}s",
                argv=argv,
            )

        if proc.returncode != 0:
            return Result.unknown(
                f"gh-failed: {_first_line(proc.stderr) or f'gh exited {proc.returncode}'}",
                raw=proc.stderr,
                argv=argv,
            )
        return Result.of(proc.stdout, raw=proc.stdout, argv=argv)

    def _run_json(self, args: Sequence[str], *, timeout: Optional[int] = None) -> Result:
        result = self._run(args, timeout=timeout)
        if not result.ok:
            return result
        try:
            return Result.of(json.loads(result.value), raw=result.raw, argv=result.argv)
        except json.JSONDecodeError as exc:
            return Result.unknown(
                f"gh-unparseable: `{' '.join(result.argv[:3])}` returned output that "
                f"is not JSON ({exc})",
                raw=result.raw,
                argv=result.argv,
            )

    # -- probe (T019) ----------------------------------------------------

    def probe(self, *, refresh: bool = False) -> Result:
        """Reachability, authentication, and the capability map. (FR-004)

        Keyed by *intended operation*, not by version number. A version string is
        a proxy for a surface; the measured surface is the record. An operation
        we cannot establish support for is reported ``None`` — undetermined —
        and never assumed present, because a client that claims a capability it
        lacks sends the engine down a path that fails mid-run, after state has
        already changed.
        """
        if self._probe_cache is not None and not refresh:
            return self._probe_cache

        version_result = self._run(["--version"])
        if not version_result.ok:
            self._probe_cache = Result.unknown(version_result.reason)
            return self._probe_cache

        gh_version = _parse_gh_version(version_result.value)

        auth = self._run(["auth", "status"])
        # `gh auth status` writes its report to stderr and exits non-zero when
        # unauthenticated, so both streams matter here.
        auth_text = (auth.value or "") + (auth.raw or "")
        authenticated = auth.ok
        host = _parse_gh_host(auth_text)

        capabilities = self._capabilities(gh_version)

        payload = {
            "reachable": bool(auth.ok or "Logged in" in auth_text),
            "authenticated": authenticated,
            "host": host,
            "gh_version": gh_version,
            "capabilities": capabilities,
        }

        if not authenticated:
            self._probe_cache = Result(
                ok=True,
                value=payload,
                reason=(
                    "gh-unauthenticated: `gh auth status` reports no usable "
                    f"credential{f' for {host}' if host else ''}. Run `gh auth login`."
                ),
            )
            return self._probe_cache

        self._probe_cache = Result.of(payload)
        return self._probe_cache

    def _capabilities(self, gh_version: Optional[str]) -> Dict[str, Optional[bool]]:
        """Which operations this ``gh`` supports, measured from its own help.

        ``pr_checks_json`` is measured rather than assumed because it is the one
        that changed under us: on 2.4.0 ``gh pr checks`` takes only ``-w/--web``.
        The engine never uses it either way — this entry exists so the profile
        *reports* the constraint rather than the code silently working around an
        unstated one.
        """
        capabilities: Dict[str, Optional[bool]] = {}

        checks_help = self._run(["pr", "checks", "--help"])
        if checks_help.ok:
            capabilities["pr_checks_json"] = "--json" in checks_help.value
        else:
            capabilities["pr_checks_json"] = None

        pr_view_help = self._run(["pr", "view", "--help"])
        if pr_view_help.ok:
            capabilities["pr_view_json"] = "--json" in pr_view_help.value
        else:
            capabilities["pr_view_json"] = None

        run_help = self._run(["run", "list", "--help"])
        if run_help.ok:
            capabilities["run_list_json"] = "--json" in run_help.value
            # 2.4.0 has no --branch filter, so runs_for_sha filters client-side.
            capabilities["run_list_branch_filter"] = "--branch" in run_help.value
        else:
            capabilities["run_list_json"] = None
            capabilities["run_list_branch_filter"] = None

        workflow_help = self._run(["workflow", "run", "--help"])
        capabilities["workflow_run"] = workflow_help.ok if workflow_help.ok else None

        release_help = self._run(["release", "create", "--help"])
        capabilities["release_create"] = release_help.ok if release_help.ok else None

        capabilities["gh_version"] = None if gh_version is None else True
        return capabilities

    # -- repository ------------------------------------------------------

    def default_branch(self) -> Result:
        result = self._run_json(["repo", "view", "--json", "defaultBranchRef"])
        if not result.ok:
            return result
        name = (result.value or {}).get("defaultBranchRef", {}).get("name")
        if not name:
            return Result.unknown(
                "gh-no-default-branch: `gh repo view` returned no defaultBranchRef",
                raw=result.raw,
            )
        return Result.of(name, raw=result.raw)

    def repo_meta(self) -> Result:
        result = self._run_json(["repo", "view", "--json", REPO_META_FIELDS])
        if not result.ok:
            return result
        data = result.value or {}
        permitted = [
            name
            for name, key in (
                ("squash", "squashMergeAllowed"),
                ("merge", "mergeCommitAllowed"),
                ("rebase", "rebaseMergeAllowed"),
            )
            if data.get(key) is True
        ]
        return Result.of(
            {
                "name_with_owner": data.get("nameWithOwner"),
                "private": data.get("isPrivate"),
                "delete_branch_on_merge": data.get("deleteBranchOnMerge"),
                "permitted_merge_methods": permitted,
            },
            raw=result.raw,
        )

    # -- pull requests ---------------------------------------------------

    def find_pr(self, branch: str) -> Result:
        """The existing open PR for ``branch``, or ``None``. Powers FR-009 adoption.

        ``gh pr view`` exits non-zero when there is no PR, which is an *answer*,
        not a failure — distinguishing the two is what stops a transient error
        from producing a duplicate PR.
        """
        result = self._run(["pr", "view", branch, "--json", PR_FIND_FIELDS])
        if not result.ok:
            if _looks_like_no_pr(result.reason, result.raw):
                return Result.of(None, reason="no-pr-for-branch")
            return result

        try:
            data = json.loads(result.value)
        except json.JSONDecodeError as exc:
            return Result.unknown(f"gh-unparseable: `gh pr view {branch}` ({exc})")

        return Result.of(
            {
                "number": data.get("number"),
                "url": data.get("url"),
                "state": data.get("state"),
                "base": data.get("baseRefName"),
                "head": data.get("headRefName"),
            },
            raw=result.raw,
        )

    def create_pr(
        self, *, head: str, base: str, title: str, body: str, draft: bool = False
    ) -> Result:
        import tempfile
        import os as _os

        fd, body_path = tempfile.mkstemp(prefix="ship-pr-body-", suffix=".md")
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)

            args = ["pr", "create", "-B", base, "-H", head, "-t", title, "-F", body_path]
            if draft:
                args.append("-d")
            result = self._run(args, timeout=GH_LONG_TIMEOUT)
        finally:
            try:
                _os.unlink(body_path)
            except OSError:
                pass

        if not result.ok:
            return result

        url = _first_url(result.value)
        number = _pr_number_from_url(url) if url else None
        return Result.of({"url": url, "number": number, "state": "OPEN"}, raw=result.raw)

    def pr_view(self, number: int) -> Result:
        """One call serving checks, mergeability, and the merge SHA.

        ``mergeable`` is normalized to ``None`` on ``UNKNOWN`` — invariant 1.
        The caller re-polls; it never reads UNKNOWN as a conflict.
        """
        result = self._run_json(["pr", "view", str(number), "--json", PR_VIEW_FIELDS])
        if not result.ok:
            return result

        data = result.value or {}
        raw_mergeable = data.get("mergeable")

        merge_commit = data.get("mergeCommit") or {}
        return Result.of(
            {
                "number": data.get("number"),
                "url": data.get("url"),
                "state": data.get("state"),
                "base": data.get("baseRefName"),
                "head": data.get("headRefName"),
                # None means "GitHub has not computed it yet", NOT "conflicting".
                "mergeable": None if raw_mergeable in (None, "UNKNOWN") else raw_mergeable,
                "mergeable_raw": raw_mergeable,
                "merge_state_status": data.get("mergeStateStatus"),
                "merge_commit_sha": merge_commit.get("oid") if merge_commit else None,
                "rollup": data.get("statusCheckRollup") or [],
                # Three states, not two, and conflating any pair of them is a
                # defect:
                #   a list        -> checks reported; reduce it
                #   []            -> the repository genuinely has no checks
                #   null/absent   -> GitHub did not compute a rollup
                #
                # Measured on real payloads: a CONFLICTING pull request returns
                # the key `statusCheckRollup` present with value **null**. A
                # membership test (`"statusCheckRollup" in data`) therefore
                # reads that as "present and empty" and reduces it to
                # no-checks-configured — turning "we could not tell" into a
                # statement about the repository. Test the value, not the key.
                "rollup_present": data.get("statusCheckRollup") is not None,
            },
            raw=result.raw,
        )

    def merge_pr(self, number: int, *, method: str, delete_branch: bool = False) -> Result:
        flag = {"squash": "--squash", "merge": "--merge", "rebase": "--rebase"}.get(method)
        if flag is None:
            return Result.unknown(f"unknown-merge-method: {method!r}")

        args = ["pr", "merge", str(number), flag]
        if delete_branch:
            args.append("-d")

        result = self._run(args, timeout=GH_LONG_TIMEOUT)
        if not result.ok:
            return result

        # gh does not return the merge commit; re-read it from the PR, which is
        # the system of record for it.
        view = self.pr_view(number)
        merge_sha = view.value.get("merge_commit_sha") if view.ok else None
        state = view.value.get("state") if view.ok else None

        return Result.of(
            {
                "merged": state == "MERGED" if state else None,
                "merge_commit_sha": merge_sha,
                "state": state,
                "output": result.value.strip(),
            },
            raw=result.raw,
        )

    # -- workflow runs ---------------------------------------------------

    def runs_for_sha(self, sha: str, branch: str) -> Result:
        """Candidate release runs for observed mode.

        Filtered client-side: ``gh run list`` on 2.4.0 has no ``--branch`` flag,
        so pushing the predicate to the server is not an option here.
        """
        result = self._run_json(["run", "list", "--limit", "100", "--json", RUN_LIST_FIELDS])
        if not result.ok:
            return result

        runs = result.value or []
        matches = [
            run
            for run in runs
            if run.get("headSha") == sha and run.get("headBranch") == branch
        ]
        return Result.of(matches, raw=result.raw)

    def run_status(self, run_id: str) -> Result:
        result = self._run_json(
            ["run", "view", str(run_id), "--json", "status,conclusion,url,databaseId"]
        )
        return result

    def watch_run(self, run_id: str, deadline: float) -> Result:
        """Poll one run to a terminal outcome or the deadline.

        Polls rather than shelling to ``gh run watch``: watch blocks with no
        deadline of its own, and FR-044 requires the wait to be bounded and to
        report progress while it runs.
        """
        import time

        interval = 10
        while True:
            status = self.run_status(run_id)
            if not status.ok:
                return status

            data = status.value or {}
            if data.get("status") == "completed":
                return Result.of(
                    {
                        "status": "completed",
                        "conclusion": data.get("conclusion"),
                        "url": data.get("url"),
                        "run_id": run_id,
                    }
                )

            if time.time() >= deadline:
                return Result.unknown(
                    "release-not-confirmed: the release run had not reached a "
                    f"terminal outcome within the configured wait "
                    f"(last status: {data.get('status')})",
                )

            time.sleep(min(interval, max(1, deadline - time.time())))
            interval = min(30, interval + 10)

    def failing_logs(self, run_id: str, *, limit: int = 8000) -> Result:
        """The failing job's own output (FR-017), truncated with a marker."""
        result = self._run(["run", "view", str(run_id), "--log-failed"], timeout=GH_LONG_TIMEOUT)
        if not result.ok:
            return result
        text = result.value
        if len(text) > limit:
            text = text[-limit:]
            text = f"[... truncated to the last {limit} characters ...]\n{text}"
        return Result.of(text)

    # -- release actions -------------------------------------------------

    def dispatch_workflow(
        self, workflow: str, ref: str, inputs: Optional[Dict[str, str]] = None
    ) -> Result:
        args = ["workflow", "run", workflow, "--ref", ref]
        for key, value in (inputs or {}).items():
            args.extend(["-f", f"{key}={value}"])
        result = self._run(args, timeout=GH_LONG_TIMEOUT)
        if not result.ok:
            return result
        return Result.of({"workflow": workflow, "ref": ref, "output": result.value.strip()})

    def create_release(self, tag: str, notes: str = "", *, generate_notes: bool = True) -> Result:
        args = ["release", "create", tag]
        if generate_notes:
            args.append("--generate-notes")
        if notes:
            args.extend(["--notes", notes])
        result = self._run(args, timeout=GH_LONG_TIMEOUT)
        if not result.ok:
            return result
        return Result.of({"tag": tag, "url": _first_url(result.value), "output": result.value.strip()})


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _first_url(text: str) -> Optional[str]:
    match = re.search(r"https?://\S+", text or "")
    return match.group(0) if match else None


def _pr_number_from_url(url: str) -> Optional[int]:
    match = re.search(r"/pull/(\d+)", url or "")
    return int(match.group(1)) if match else None


def _parse_gh_version(text: str) -> Optional[str]:
    match = re.search(r"gh version (\S+)", text or "")
    return match.group(1) if match else None


def _parse_gh_host(text: str) -> Optional[str]:
    match = re.search(r"(?:Logged in to|✓\s+Logged in to)\s+(\S+)", text or "")
    if match:
        return match.group(1)
    match = re.search(r"^(\S+)$", (text or "").strip().splitlines()[0] if text else "")
    return None


def _looks_like_no_pr(reason: str, raw: str) -> bool:
    """Distinguish "there is no PR" from "the query failed".

    Collapsing these would be an FR-009 defect: a transient failure read as
    "no PR exists" produces a duplicate pull request on the next stage.
    """
    haystack = f"{reason} {raw}".lower()
    return any(
        needle in haystack
        for needle in (
            "no pull requests found",
            "no open pull requests",
            "could not resolve to a pullrequest",
            "no pull request found",
        )
    )
