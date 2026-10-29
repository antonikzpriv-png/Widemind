"""Persistent operator control plane, separate from the agent's editable workspace.

This module controls dispatch and exposes cooperative cancellation. It cannot
cancel an already accepted remote request or establish operating-system isolation.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .business.contracts import Outcome
from .business.store import encode, identity

STATES = frozenset({"active", "waiting", "paused", "stopped", "exhausted", "failed"})
DEFAULT_CAPABILITIES = ("gmail.read", "gmail.send", "research.collect")


def _reference(value, name, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"invalid {name}")
    return value.strip()


def _capabilities(values):
    if not isinstance(values, (tuple, list)) or len(values) > 100:
        raise ValueError("capabilities must be a bounded list")
    if any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", v) for v in values):
        raise ValueError("invalid capability name")
    return sorted(set(values))


class RuntimeControl:
    """A run is explicitly initialized paused; ordinary request construction funds nothing."""
    def __init__(self, path, *, mode="live", clock=time.time):
        if mode not in {"live", "test", "demo"}:
            raise ValueError("invalid runtime mode")
        self.mode, self.clock = mode, clock
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        for attempt in range(6):
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == 5:
                    self.db.close()
                    raise
                time.sleep(0.01 * 2**attempt)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runtime_runs (
              run_id TEXT PRIMARY KEY, mode TEXT NOT NULL,
              state TEXT NOT NULL, capabilities TEXT NOT NULL,
              revision INTEGER NOT NULL, stop_generation INTEGER NOT NULL,
              created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_actions (
              action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
              request TEXT NOT NULL, outcome TEXT NOT NULL,
              previous_state TEXT, resulting_state TEXT,
              revision INTEGER, created_at REAL NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS runtime_actions_no_update
              BEFORE UPDATE ON runtime_actions BEGIN SELECT RAISE(ABORT,'runtime audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS runtime_actions_no_delete
              BEFORE DELETE ON runtime_actions BEGIN SELECT RAISE(ABORT,'runtime audit is append-only'); END;
        """)

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _run(self, run_id):
        row = self.db.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        if row and row["mode"] != self.mode:
            raise ValueError("runtime run mode mismatch")
        return row

    def initialize(self, run_id, *, initial_state="paused", capabilities=DEFAULT_CAPABILITIES,
                   evidence_reference="explicit operator initialization"):
        run_id = _reference(run_id, "run_id", 200)
        evidence_reference = _reference(evidence_reference, "initialization evidence")
        if initial_state not in STATES:
            raise ValueError("invalid initial state")
        capabilities = _capabilities(capabilities)
        with self._transaction():
            row = self._run(run_id)
            if row is None:
                self.db.execute("INSERT INTO runtime_runs VALUES (?,?,?,?,0,0,?)",
                                (run_id, self.mode, initial_state, encode(capabilities), self.clock()))
                result = Outcome("succeeded", {"run_id": run_id, "state": initial_state, "revision": 0})
                request = {"operation": "initialize", "mode": self.mode, "state": initial_state,
                           "capabilities": capabilities, "evidence_reference": evidence_reference}
                self._record(identity("runtime-initialize", run_id, self.mode), run_id, request, result, None, initial_state, 0)
            else:
                # Repeating setup never silently resumes a stopped run or re-enables
                # capabilities; use an explicit audited change instead.
                result = Outcome("succeeded", {"run_id": run_id, "state": row["state"], "revision": row["revision"]})
        return result

    def state(self, run_id):
        row = self._run(run_id)
        return row["state"] if row else "failed"

    def capability_enabled(self, run_id, action):
        row = self._run(run_id)
        return bool(row and action in json.loads(row["capabilities"]))

    def cancelled(self, run_id):
        """Call before dispatch and between cooperative work steps, including after resume."""
        return self.state(run_id) not in {"active", "waiting"}

    def cancellation_generation(self, run_id):
        """An active tool can retain this value and detect stop→resume during its call."""
        row = self._run(run_id)
        return row["stop_generation"] if row else -1

    def health(self, run_id):
        row = self._run(run_id)
        return {"name": "runtime", "status": "ready" if row else "not_initialized",
                "mode": self.mode, "state": row["state"] if row else "failed",
                "controls_available": bool(row),
                "detail": "Persistent dispatch controls; remote cancellation is cooperative."}

    def _record(self, action_id, run_id, request, outcome, previous, resulting, revision):
        self.db.execute("INSERT INTO runtime_actions VALUES (?,?,?,?,?,?,?,?)",
                        (action_id, run_id, encode(request), encode({"state": outcome.state, "data": outcome.data,
                         "reason": outcome.reason}), previous, resulting, revision, self.clock()))

    def _existing(self, action_id, run_id, request):
        row = self.db.execute("SELECT * FROM runtime_actions WHERE action_id=?", (action_id,)).fetchone()
        if row:
            if row["run_id"] != run_id or row["request"] != encode(request):
                raise ValueError("runtime action identity reused with changed contents")
            return Outcome(**json.loads(row["outcome"]))
        return None

    def change_state(self, *, run_id, state, action_id, evidence_reference):
        run_id = _reference(run_id, "run_id", 200)
        action_id = _reference(action_id, "action_id", 300)
        evidence_reference = _reference(evidence_reference, "control evidence")
        if state not in STATES:
            raise ValueError("invalid runtime state")
        request = {"operation": "change_state", "state": state, "evidence_reference": evidence_reference}
        with self._transaction():
            row = self._run(run_id)
            previous = self._existing(action_id, run_id, request)
            if previous:
                return previous
            if row is None:
                result = Outcome("failed", reason="runtime run not initialized")
                self._record(action_id, run_id, request, result, None, None, None)
                return result
            revision = row["revision"] + 1
            generation = row["stop_generation"] + (state not in {"active", "waiting"})
            self.db.execute("UPDATE runtime_runs SET state=?,revision=?,stop_generation=? WHERE run_id=?",
                            (state, revision, generation, run_id))
            result = Outcome("succeeded", {"run_id": run_id, "state": state, "revision": revision,
                                          "cancellation_generation": generation})
            self._record(action_id, run_id, request, result, row["state"], state, revision)
            return result

    def configure_capabilities(self, *, run_id, capabilities, action_id, evidence_reference):
        run_id = _reference(run_id, "run_id", 200)
        action_id = _reference(action_id, "action_id", 300)
        capabilities = _capabilities(capabilities)
        evidence_reference = _reference(evidence_reference, "capability evidence")
        request = {"operation": "configure_capabilities", "capabilities": capabilities,
                   "evidence_reference": evidence_reference}
        with self._transaction():
            row = self._run(run_id)
            previous = self._existing(action_id, run_id, request)
            if previous:
                return previous
            if row is None:
                result = Outcome("failed", reason="runtime run not initialized")
                self._record(action_id, run_id, request, result, None, None, None)
                return result
            revision = row["revision"] + 1
            # Capability revocation also invalidates a previously captured token.
            generation = row["stop_generation"] + 1
            self.db.execute("UPDATE runtime_runs SET capabilities=?,revision=?,stop_generation=? WHERE run_id=?",
                            (encode(capabilities), revision, generation, run_id))
            result = Outcome("succeeded", {"run_id": run_id, "capabilities": capabilities, "revision": revision,
                                          "cancellation_generation": generation})
            self._record(action_id, run_id, request, result, row["state"], row["state"], revision)
            return result

    def reconcile(self, *, action_id):
        row = self.db.execute("SELECT run_id,outcome FROM runtime_actions WHERE action_id=?", (action_id,)).fetchone()
        if row:
            self._run(row["run_id"])  # Mode pinning applies to replay as well as new changes.
        return Outcome(**json.loads(row["outcome"])) if row else Outcome("unknown", reason="runtime action not recorded")

    def close(self):
        self.db.close()
