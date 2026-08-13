# Contract: Hosting Client

One seam between the engine and the hosting service. Two implementations: `GhClient` (shells
out to `gh`) and `RecordedClient` (replays captured `gh --json` payloads, used only in tests).
Isolating the seam is also what keeps a second hosting service addable later without touching
the state machine — the spec puts other services out of scope but asks that nothing preclude
them.

Every method returns a **result object**, never a raw exception, and every result can be
`undetermined` with a reason. That is the shape FR-028 needs: a network hiccup is not a
`false`.

## Capability probe

```
probe() -> {reachable, authenticated, host, gh_version, capabilities: {…}}
```

Run at preflight (FR-004). The capabilities map is keyed by the operation the run intends,
not by a version number — a version string is a proxy, and the measured surface is the record.

The probe on this machine's `gh 2.4.0` returns `pr_checks_json: false`, which is why the
engine reads checks through `pr_view(...)` instead (research.md R3). A client that reports a
capability it does not have would send the engine down a path that fails mid-run, after state
has changed — so an unprobeable capability is reported `undetermined`, never assumed present.

## Methods

| Method | Backed by | Returns |
|---|---|---|
| `default_branch()` | `gh repo view --json defaultBranchRef` | `Determined<string>` |
| `repo_meta()` | `gh repo view --json nameWithOwner,isPrivate,deleteBranchOnMerge,mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed` | Permitted merge methods, for the FR-036 disabled-control reason |
| `find_pr(branch)` | `gh pr view <branch> --json number,state,url,baseRefName,headRefName` | PR or `None`. Powers FR-009 adoption |
| `create_pr(head, base, title, body_file, draft)` | `gh pr create -H -B -t -F [-d]` | PR |
| `pr_view(number)` | `gh pr view <n> --json statusCheckRollup,mergeable,mergeStateStatus,state,mergeCommit,url` | One call serving checks, mergeability, and merge SHA |
| `failing_logs(run_id)` | `gh run view <id> --log-failed` | Log excerpt for FR-017 |
| `merge_pr(number, method, delete_branch)` | `gh pr merge --squash\|--merge\|--rebase [-d]` | Merge result incl. `merge_commit_sha` |
| `runs_for_sha(sha, branch)` | `gh run list --json headBranch,headSha,status,conclusion,databaseId,workflowName` filtered client-side | Candidate release runs for observed mode |
| `watch_run(run_id, deadline)` | `gh run watch` / polled `gh run view --json status,conclusion` | Terminal outcome or `undetermined` |
| `dispatch_workflow(file, ref, inputs)` | `gh workflow run` | Dispatch result for executed mode |
| `create_release(tag, notes)` | `gh release create` | Release result for executed mode |

`gh run list` in `gh 2.4.0` has no `--branch` filter, so `runs_for_sha` filters the JSON
client-side rather than pushing the predicate to the server.

## Invariants

1. **No method infers.** `pr_view` returning `mergeable: "UNKNOWN"` surfaces as
   `undetermined`, never as `CONFLICTING` — otherwise the FR-018 conflict repair fires
   against branches that merge cleanly (research.md R5).
2. **No method retries silently.** Retry policy is the engine's, so attempts are recorded and
   bounded rather than hidden in the client.
3. **The client never writes state.** It returns; the engine records.
4. **The client never holds a credential.** `gh` owns the token; nothing here reads, prints,
   or stores one, and no token value passes through the engine.
5. **`RecordedClient` exists only under `tests/`.** No production path may construct it. That
   boundary is enforced by an import check in CI, not by convention — the whole feature is a
   promise that reported state is real, and a fake reachable from production would break it
   silently.
