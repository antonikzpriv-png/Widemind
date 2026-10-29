"""A cheap scheduler, independent of the website. No inference while idle."""
import os
import time
from contextlib import contextmanager


@contextmanager
def worker_lock(path):
    """One worker per database, including lease recovery after process death."""
    fd = os.open(str(path) + ".worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
    stream = os.fdopen(fd, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


def serve(business, *, database_path, once=False):
    with worker_lock(database_path):
        while True:
            worked = business.tick()
            if once:
                return
            if not worked:
                due = business.store.one("SELECT MIN(due_at) AS at FROM events WHERE run_id=? AND state='queued'", (business.run_id,))["at"]
                # At most a 30s notification latency; this is SQLite polling, no model
                # or provider call. External schedulers may invoke --once instead.
                delay = 30 if due is None else max(1, min(30, due - time.time()))
                time.sleep(delay)
