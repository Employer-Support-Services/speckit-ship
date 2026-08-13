"""T030 — lock liveness, stale reclaim, and refusal (FR-022).

The distinction these tests protect: a lock held by a **live** process is a
refusal, a lock held by a **dead** process on **this host** is reclaimable, and
a lock from **another host** is neither — it is reported with its identity so a
human can judge. Collapsing the third case into the second is how two machines
end up merging the same pull request.
"""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path

from scripts import lock as lock_mod


class LockCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".specify" / "extensions" / "ship").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_lock(self, **overrides) -> Path:
        payload = {
            "pid": 999999,
            "hostname": socket.gethostname(),
            "branch": "feature/x",
            "run_id": "run-1",
            "started_at": "2026-08-12T10:00:00Z",
        }
        payload.update(overrides)
        path = lock_mod.lock_path(self.root)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


class TestPidLiveness(unittest.TestCase):
    def test_our_own_pid_is_alive(self) -> None:
        import os

        self.assertTrue(lock_mod.pid_alive(os.getpid()))

    def test_a_pid_that_cannot_exist_is_not_alive(self) -> None:
        self.assertFalse(lock_mod.pid_alive(0))
        self.assertFalse(lock_mod.pid_alive(-1))

    def test_an_almost_certainly_unused_pid_is_not_alive(self) -> None:
        # 2^22 is above the default pid_max on Linux.
        self.assertFalse(lock_mod.pid_alive(4194303))


class TestAcquire(LockCase):
    def test_acquires_when_no_lock_exists(self) -> None:
        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertTrue(result.acquired)
        self.assertTrue(result.path.is_file())

        payload = json.loads(result.path.read_text(encoding="utf-8"))
        self.assertEqual("feature/x", payload["branch"])
        self.assertEqual("run-1", payload["run_id"])
        self.assertEqual(socket.gethostname(), payload["hostname"])

    def test_refuses_when_a_live_process_holds_the_lock(self) -> None:
        import os

        self.write_lock(pid=os.getpid(), branch="feature/other", run_id="run-0")

        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertFalse(result.acquired)
        self.assertIsNotNone(result.held_by)
        self.assertEqual("feature/other", result.held_by.branch)

    def test_the_refusal_reports_who_holds_the_lock(self) -> None:
        """FR-022 wants a diagnosis, not a busy signal."""
        import os

        self.write_lock(pid=os.getpid(), branch="feature/other", run_id="run-0")

        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertIn(str(os.getpid()), result.message)
        self.assertIn("feature/other", result.message)
        self.assertIn("run-0", result.message)
        self.assertIn("refused rather than queued", result.message)

    def test_reclaims_a_stale_lock_from_this_host_and_reports_it(self) -> None:
        self.write_lock(pid=4194303, run_id="run-0")

        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertTrue(result.acquired)
        self.assertIsNotNone(result.reclaimed)
        self.assertIn("Reclaimed a stale lock", result.message)
        self.assertIn("run-0", result.message)

    def test_never_reclaims_a_lock_from_another_host(self) -> None:
        """We cannot see that host's process table. Reclaiming would be a guess."""
        self.write_lock(pid=1, hostname="some-other-machine", run_id="run-0")

        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertFalse(result.acquired)
        self.assertIn("some-other-machine", result.message)

    def test_another_hosts_lock_reports_liveness_as_unknown_not_dead(self) -> None:
        self.write_lock(pid=1, hostname="some-other-machine")

        info = lock_mod.read(self.root)

        self.assertFalse(info.same_host)
        self.assertIsNone(info.alive)
        self.assertFalse(info.stale)
        self.assertIn("liveness unknown", info.describe())

    def test_an_unreadable_lock_is_treated_as_held_not_absent(self) -> None:
        """Deleting a lock we cannot parse is the same mistake as reclaiming another host's."""
        lock_mod.lock_path(self.root).write_text("{ corrupt", encoding="utf-8")

        info = lock_mod.read(self.root)
        self.assertIsNotNone(info)
        self.assertFalse(info.stale)

        result = lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")
        self.assertFalse(result.acquired)


class TestRelease(LockCase):
    def test_release_removes_the_lock(self) -> None:
        lock_mod.acquire(self.root, branch="feature/x", run_id="run-1")

        self.assertTrue(lock_mod.release(self.root, run_id="run-1"))
        self.assertFalse(lock_mod.lock_path(self.root).exists())

    def test_release_of_a_missing_lock_reports_false_rather_than_raising(self) -> None:
        self.assertFalse(lock_mod.release(self.root, run_id="run-1"))

    def test_release_refuses_to_drop_another_runs_lock(self) -> None:
        """Guards against a crashed run's cleanup deleting its successor's lock."""
        lock_mod.acquire(self.root, branch="feature/x", run_id="run-2")

        self.assertFalse(lock_mod.release(self.root, run_id="run-1"))
        self.assertTrue(lock_mod.lock_path(self.root).exists())


class TestContextManager(LockCase):
    def test_the_lock_is_released_on_exit(self) -> None:
        with lock_mod.held(self.root, branch="feature/x", run_id="run-1") as result:
            self.assertTrue(result.acquired)
            self.assertTrue(lock_mod.lock_path(self.root).exists())

        self.assertFalse(lock_mod.lock_path(self.root).exists())

    def test_the_lock_is_released_even_when_the_body_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            with lock_mod.held(self.root, branch="feature/x", run_id="run-1"):
                raise RuntimeError("stage blew up")

        self.assertFalse(lock_mod.lock_path(self.root).exists())

    def test_a_refused_acquisition_does_not_delete_the_holders_lock_on_exit(self) -> None:
        import os

        self.write_lock(pid=os.getpid(), run_id="run-0")

        with lock_mod.held(self.root, branch="feature/x", run_id="run-1") as result:
            self.assertFalse(result.acquired)

        self.assertTrue(lock_mod.lock_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
