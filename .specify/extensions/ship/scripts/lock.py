"""The concurrency lock (FR-022).

One ship run at a time per repository. A second run is **refused, never queued**
— the spec is explicit about that, and it is the right call: a queued ship would
sit waiting to perform an outward-facing, hard-to-reverse action against a world
that has moved on since the developer asked for it.

Why a lock file rather than ``flock``: the refusal has to be *diagnostic*. FR-022
requires reporting what is holding the lock, and an advisory OS lock carries no
payload — it can say "busy" but not "your other terminal, pid 4821, on branch
feature/x, since 14:02". It also behaves poorly on network filesystems, which
developer checkouts sometimes are.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from scripts.state import now_iso


def lock_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".specify" / "extensions" / "ship" / "ship.lock"


def pid_alive(pid: int) -> bool:
    """Is this PID live on *this* host?

    ``signal 0`` performs the existence and permission check without delivering
    anything. ``EPERM`` means the process exists but belongs to another user —
    which is still alive, so the lock is still held.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class LockInfo:
    """The contents of a lock file, plus what we could work out about it."""

    def __init__(self, payload: Dict[str, Any], *, same_host: bool, alive: Optional[bool]):
        self.payload = payload
        self.same_host = same_host
        # None means "cannot be determined" — a lock from another host, whose
        # PID means nothing here. Never rendered as "dead".
        self.alive = alive

    @property
    def pid(self) -> int:
        return int(self.payload.get("pid", 0) or 0)

    @property
    def hostname(self) -> str:
        return str(self.payload.get("hostname", "unknown"))

    @property
    def branch(self) -> str:
        return str(self.payload.get("branch", "unknown"))

    @property
    def run_id(self) -> str:
        return str(self.payload.get("run_id", "unknown"))

    @property
    def started_at(self) -> str:
        return str(self.payload.get("started_at", "unknown"))

    @property
    def stale(self) -> bool:
        """Reclaimable: this host, and the process is gone.

        A lock from another host is never stale to us, however old it looks. We
        cannot see that host's process table, and reclaiming on a guess is how
        two runs end up merging the same PR.
        """
        return self.same_host and self.alive is False

    def describe(self) -> str:
        if self.same_host:
            liveness = "running" if self.alive else "not running"
        else:
            liveness = "liveness unknown from this host"
        return (
            f"pid {self.pid} on {self.hostname} ({liveness}), "
            f"branch {self.branch}, run {self.run_id}, since {self.started_at}"
        )


class AcquireResult:
    def __init__(
        self,
        *,
        acquired: bool,
        path: Path,
        held_by: Optional[LockInfo] = None,
        reclaimed: Optional[LockInfo] = None,
        message: str = "",
    ) -> None:
        self.acquired = acquired
        self.path = path
        self.held_by = held_by
        self.reclaimed = reclaimed
        self.message = message


def read(repo_root: Path) -> Optional[LockInfo]:
    """Read the current lock, or None when there isn't one.

    An unreadable lock file is treated as a held lock of unknown identity rather
    than as no lock. Deleting a lock we cannot parse would be the same mistake as
    reclaiming another host's.
    """
    path = lock_path(repo_root)
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("not an object")
    except (json.JSONDecodeError, ValueError, OSError):
        return LockInfo(
            {"pid": 0, "hostname": "unknown", "branch": "unknown", "run_id": "unknown",
             "started_at": "unknown"},
            same_host=False,
            alive=None,
        )

    same_host = payload.get("hostname") == socket.gethostname()
    alive: Optional[bool]
    if same_host:
        try:
            alive = pid_alive(int(payload.get("pid", 0) or 0))
        except (TypeError, ValueError):
            alive = None
    else:
        alive = None

    return LockInfo(payload, same_host=same_host, alive=alive)


def acquire(repo_root: Path, *, branch: str, run_id: str) -> AcquireResult:
    """Take the lock, or refuse with a description of who holds it.

    Written with ``O_CREAT | O_EXCL`` so that two runs racing for a free lock
    cannot both believe they won.
    """
    path = lock_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = read(repo_root)
    reclaimed: Optional[LockInfo] = None

    if existing is not None:
        if not existing.stale:
            return AcquireResult(
                acquired=False,
                path=path,
                held_by=existing,
                message=(
                    "Another ship run holds the lock for this repository: "
                    f"{existing.describe()}.\n"
                    "Concurrent runs are refused rather than queued. Wait for it "
                    "to finish, or remove "
                    f"{path} if you are certain it is not running."
                ),
            )
        reclaimed = existing
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    payload = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "branch": branch,
        "run_id": run_id,
        "started_at": now_iso(),
    }

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Lost a race between the staleness check and the create.
        current = read(repo_root)
        return AcquireResult(
            acquired=False,
            path=path,
            held_by=current,
            message=(
                "Another ship run took the lock while this one was starting"
                + (f": {current.describe()}." if current else ".")
            ),
        )

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")

    message = ""
    if reclaimed is not None:
        message = (
            "Reclaimed a stale lock left by "
            f"{reclaimed.describe()} — that process is no longer running on this "
            "host."
        )

    return AcquireResult(acquired=True, path=path, reclaimed=reclaimed, message=message)


def release(repo_root: Path, *, run_id: Optional[str] = None) -> bool:
    """Drop the lock. Returns True when a lock was removed.

    With ``run_id``, refuses to remove a lock belonging to a different run — a
    guard against a crashed run's cleanup handler deleting a successor's lock.
    """
    path = lock_path(repo_root)
    if not path.is_file():
        return False

    if run_id is not None:
        current = read(repo_root)
        if current is not None and current.run_id not in (run_id, "unknown"):
            return False

    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


class held:
    """Context manager wrapping acquire/release.

    Usage::

        with held(root, branch="x", run_id=rid) as result:
            if not result.acquired:
                ...refuse with exit code 40...
    """

    def __init__(self, repo_root: Path, *, branch: str, run_id: str) -> None:
        self.repo_root = repo_root
        self.branch = branch
        self.run_id = run_id
        self.result: Optional[AcquireResult] = None

    def __enter__(self) -> AcquireResult:
        self.result = acquire(self.repo_root, branch=self.branch, run_id=self.run_id)
        return self.result

    def __exit__(self, *exc) -> None:
        if self.result is not None and self.result.acquired:
            release(self.repo_root, run_id=self.run_id)
