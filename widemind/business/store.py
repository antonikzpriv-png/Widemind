"""SQLite persistence. Connections are process/thread local; effects commit before I/O."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from importlib.resources import files


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(*parts):
    return hashlib.sha256(encode(parts).encode()).hexdigest()[:40]


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Private messages, artifacts, and operational memory must never be public files.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        # SQLite's journal-mode upgrade can return SQLITE_BUSY immediately even
        # with busy_timeout when two fresh processes initialize the same file.
        for attempt in range(6):
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or attempt == 5:
                    self.db.close()
                    raise
                time.sleep(0.01 * 2**attempt)
        self.migrate()

    def migrate(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS business_migrations(version TEXT PRIMARY KEY)")
        for entry in sorted(files("widemind.business").joinpath("migrations").iterdir(), key=lambda p: p.name):
            if not entry.name.endswith(".sql"):
                continue
            # One transaction also serializes concurrent startup migrations.
            with self.transaction():
                if not self.one("SELECT 1 FROM business_migrations WHERE version=?", (entry.name,)):
                    for statement in entry.read_text().split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    self.db.execute("INSERT INTO business_migrations VALUES (?)", (entry.name,))

    def bind_run_mode(self, run_id, mode):
        with self.transaction():
            saved = self.one("SELECT mode FROM business_runs WHERE run_id=?", (run_id,))
            if saved is None:
                self.db.execute("INSERT INTO business_runs VALUES (?,?,?)", (run_id, mode, "explicit new-run configuration"))
            elif saved["mode"] != mode:
                raise ValueError("run mode mismatch; legacy runs require explicit adopt-run review")

    def adopt_run_mode(self, run_id, mode, evidence_reference):
        """Operator migration only: never convert a classified run or known fixture payments."""
        if mode not in {"live", "demo", "test"} or not isinstance(evidence_reference, str) or not evidence_reference.strip():
            raise ValueError("mode and review evidence are required")
        with self.transaction():
            existing = self.one("SELECT mode FROM business_runs WHERE run_id=?", (run_id,))
            if not existing or existing["mode"] != "unclassified":
                raise ValueError("only an unclassified legacy run can be adopted")
            for row in self.all("SELECT payment FROM orders WHERE run_id=?", (run_id,)):
                observed = json.loads(row["payment"]).get("mode")
                if observed and observed != mode:
                    raise ValueError("legacy payment mode conflicts with proposed adoption")
            self.db.execute("UPDATE business_runs SET mode=?,evidence_reference=? WHERE run_id=?",
                            (mode, evidence_reference[:1000], run_id))
            self.memory(run_id, run_id, "mode_adoption", {"mode": mode, "evidence": evidence_reference[:1000]}, time.time())

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def event(self, run_id, source, external_id, kind, payload, due_at):
        event_id = identity(run_id, source, external_id)
        self.db.execute("""INSERT OR IGNORE INTO events
            (id,run_id,source,external_id,kind,payload,due_at) VALUES (?,?,?,?,?,?,?)""",
                        (event_id, run_id, source, external_id, kind, encode(payload), due_at))
        existing = self.one("SELECT kind,payload FROM events WHERE id=?", (event_id,))
        if existing != {"kind": kind, "payload": encode(payload)}:
            raise ValueError("event identity reused with different contents")
        return event_id

    def memory(self, run_id, entity, kind, document, now, customer=None):
        self.db.execute("INSERT OR IGNORE INTO memory VALUES (?,?,?,?,?,?,?)",
                        (identity(run_id, entity, kind), run_id, customer, entity, kind, encode(document), now))

    def close(self):
        self.db.close()
