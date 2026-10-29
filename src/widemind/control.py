"""Trusted SQLite journal: atomic reservations, append-only charges and receipts.

An unresolved paid action retains its unused reservation. Reconciliation reports
cumulative measured cost; only the positive delta is appended to the ledger.
This module is not exposed to generated code or model-supplied tool arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from .config import private_directory
from .tools.contracts import Quote, ToolError, ToolResult, Usage


def json_text(value, maximum=8_000_000):
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_input", "Expected finite JSON data.") from exc
    if len(data.encode()) > maximum:
        raise ToolError("output_limit", "JSON data exceeds its configured size limit.")
    return data


def money(value, *, positive=False):
    if type(value) is not int or value < int(positive) or value > 9_000_000_000_000_000:
        raise ToolError("accounting", "Money must be bounded integer micro-USD.")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ToolError("invalid_input", "IDs must contain 1–128 letters, digits, dots, dashes, colons or underscores.")
    return value


class ControlStore:
    def __init__(self, directory: str | Path):
        self.root = private_directory(Path(directory))
        self.path = self.root / "control.sqlite3"
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        info = self.path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ToolError("configuration", "Control database must be private and owner-owned.")
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger (
                  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                  event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, amount INTEGER NOT NULL,
                  detail TEXT NOT NULL, created REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
                  BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
                CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
                  BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
                CREATE TABLE IF NOT EXISTS actions (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), job_id TEXT,
                  tool TEXT NOT NULL, version INTEGER NOT NULL, arguments TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, config TEXT NOT NULL, policy TEXT NOT NULL,
                  quote TEXT NOT NULL, reservation_id TEXT NOT NULL UNIQUE,
                  maximum INTEGER NOT NULL, remaining INTEGER NOT NULL, paid INTEGER NOT NULL,
                  charged INTEGER NOT NULL DEFAULT 0, reservation_status TEXT NOT NULL,
                  status TEXT NOT NULL, state TEXT NOT NULL DEFAULT '{}',
                  dispatched INTEGER NOT NULL DEFAULT 0, intents TEXT NOT NULL DEFAULT '[]',
                  result TEXT, error TEXT, claim TEXT, lease REAL,
                  cancelled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS actions_run ON actions(run_id);
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def create_run(self, run_id, opening_microusd=5_000_000):
        identifier(run_id)
        money(opening_microusd)
        with self.connection(True) as db:
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
                return self._balance(db, run_id)
            db.execute("INSERT INTO runs(id,created) VALUES (?,?)", (run_id, time.time()))
            db.execute("INSERT INTO ledger(run_id,event_id,kind,amount,detail,created) VALUES (?,?,?,?,?,?)",
                       (run_id, "funding:" + run_id, "owner_funding", opening_microusd,
                        '{"currency":"USD","source":"operator_opening_grant"}', time.time()))
            return self._balance(db, run_id)

    @staticmethod
    def _balance(db, run_id):
        run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ToolError("run_not_found", "Initialize the owner-funded run before executing tools.")
        ledger = db.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE run_id=?", (run_id,)).fetchone()[0]
        held = db.execute("SELECT COALESCE(SUM(remaining),0) FROM actions WHERE run_id=? AND reservation_status='active'", (run_id,)).fetchone()[0]
        return {"run_id": run_id, "currency": "USD", "ledger_microusd": ledger,
                "reserved_microusd": held, "spendable_microusd": ledger-held, "paused": bool(run["paused"])}

    def balance(self, run_id):
        with self.connection() as db:
            return self._balance(db, run_id)

    def pause(self, run_id, paused=True):
        with self.connection(True) as db:
            if db.execute("UPDATE runs SET paused=? WHERE id=?", (int(paused), run_id)).rowcount != 1:
                raise ToolError("run_not_found", "Run does not exist.")

    def cancel(self, action_id):
        with self.connection(True) as db:
            if db.execute("UPDATE actions SET cancelled=1 WHERE id=?", (action_id,)).rowcount != 1:
                raise ToolError("not_found", "Action does not exist.")

    def get(self, action_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ToolError("not_found", "Action does not exist.")
            return dict(row)

    def prepare(self, *, action_id, run_id, job_id, tool, version, arguments, config, policy, quote: Quote):
        for value in (action_id, run_id):
            identifier(value)
        if job_id is not None:
            identifier(job_id)
        money(quote.maximum_microusd, positive=quote.paid)
        if quote.currency != "USD" or type(quote.paid) is not bool:
            raise ToolError("accounting", "Only explicit USD quotes are supported.")
        if not quote.paid and quote.maximum_microusd:
            raise ToolError("accounting", "A positive quote must declare a paid provider.")
        args = json_text(arguments)
        identity = json_text([run_id, job_id, tool, version, arguments])
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        with self.connection(True) as db:
            existing = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ToolError("action_conflict", "Action ID already belongs to different inputs.")
                return dict(existing), False
            balance = self._balance(db, run_id)
            if balance["paused"]:
                raise ToolError("paused", "Run is paused by its operator.")
            if quote.paid:
                if db.execute("SELECT 1 FROM actions WHERE run_id=? AND paid=1 AND status='uncertain' AND reservation_status='active'", (run_id,)).fetchone():
                    raise ToolError("unresolved_liability", "Reconcile the run's uncertain paid action before more paid execution.")
                if balance["spendable_microusd"] < quote.maximum_microusd:
                    raise ToolError("budget_exhausted", "Insufficient spendable credit for this reservation.")
            now = time.time()
            db.execute('''INSERT INTO actions(id,run_id,job_id,tool,version,arguments,fingerprint,config,policy,
                       quote,reservation_id,maximum,remaining,paid,reservation_status,status,created,updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active','planned',?,?)''',
                       (action_id,run_id,job_id,tool,version,args,fingerprint,json_text(config),json_text(policy),
                        json_text(asdict(quote)),str(uuid.uuid4()),quote.maximum_microusd,
                        quote.maximum_microusd,int(quote.paid),now,now))
            return dict(db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()), True

    def claim(self, action_id, seconds, *, reconciliation=False):
        token = str(uuid.uuid4())
        with self.connection(True) as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ToolError("not_found", "Action does not exist.")
            now = time.time()
            if row["claim"] and row["lease"] > now:
                raise ToolError("busy", "Another worker holds this action's execution lease.")
            if row["status"] in {"succeeded", "failed", "unavailable", "cancelled"}:
                return None
            if row["status"] in {"running", "reconciling"}:
                db.execute("UPDATE actions SET status='uncertain' WHERE id=?", (action_id,))
                if not reconciliation:
                    return None
            if not reconciliation and row["status"] == "uncertain":
                return None
            db.execute("UPDATE actions SET claim=?,lease=?,status=?,updated=? WHERE id=?",
                       (token,now+seconds+10,"reconciling" if reconciliation else "running",now,action_id))
            return token

    def authorize(self, action_id, claim, *, reconciliation=False):
        with self.connection() as db:
            row = db.execute("SELECT a.*,r.paused FROM actions a JOIN runs r ON r.id=a.run_id WHERE a.id=?", (action_id,)).fetchone()
            if not row or row["claim"] != claim or row["lease"] < time.time() or row["reservation_status"] != "active":
                raise ToolError("authorization", "Action permit is missing or expired.")
            if not reconciliation and (row["paused"] or row["cancelled"]):
                raise ToolError("cancelled", "Action or run was stopped by its operator.")

    def finish(self, action_id, claim, status, *, result: ToolResult | None = None, usage: Usage | None = None, error=None):
        if result is not None:
            usage = result.usage
        if usage is not None:
            if usage.cost_microusd is not None:
                money(usage.cost_microusd)
            if any(type(v) is not int or v < 0 for v in usage.units.values()):
                raise ToolError("accounting", "Usage units must be nonnegative integers.")
        if result is None and usage is not None:
            result = ToolResult({}, usage)
        encoded_result = json_text(asdict(result), 4_000_000) if result else None
        with self.connection(True) as db:
            row = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row["claim"] != claim:
                raise ToolError("authorization", "Execution lease no longer belongs to this worker.")
            charged = row["charged"]
            if usage and usage.cost_microusd is not None:
                if usage.cost_microusd < charged:
                    raise ToolError("accounting", "Cumulative provider usage cannot silently reverse an earlier charge.")
                delta = usage.cost_microusd - charged
                if delta:
                    db.execute("INSERT INTO ledger(run_id,event_id,kind,amount,detail,created) VALUES (?,?,?,?,?,?)",
                               (row["run_id"],f"charge:{action_id}:{usage.cost_microusd}","provider_charge",-delta,
                                json_text(asdict(usage)),time.time()))
                    charged += delta
            # Missing usage on a dispatched paid request cannot resolve its liability.
            if row["paid"] and row["dispatched"] and (usage is None or usage.cost_microusd is None):
                status = "uncertain"
            remaining = max(0, row["maximum"] - charged) if status == "uncertain" else 0
            reservation_status = "active" if status == "uncertain" else "settled"
            db.execute('''UPDATE actions SET status=?,result=COALESCE(?,result),error=?,charged=?,remaining=?,
                          reservation_status=?,claim=NULL,lease=NULL,updated=? WHERE id=?''',
                       (status,encoded_result,json_text(error) if error else None,charged,remaining,
                        reservation_status,time.time(),action_id))
        return self.receipt(action_id)

    def receipt(self, action_id):
        row = self.get(action_id)
        return {"action_id": row["id"], "run_id": row["run_id"], "job_id": row["job_id"],
                "tool": row["tool"], "tool_version": row["version"], "status": row["status"],
                "reservation_id": row["reservation_id"], "currency": "USD",
                "reserved_microusd": row["remaining"], "charged_microusd": row["charged"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": json.loads(row["error"]) if row["error"] else None,
                "trust": "untrusted", "visibility": "private", "created": row["created"], "updated": row["updated"]}


class JournalState:
    def __init__(self, store: ControlStore, claim: str):
        self.store, self.claim = store, claim

    def load(self, action_id):
        return json.loads(self.store.get(action_id)["state"])

    def update(self, action_id, patch: Mapping):
        if not isinstance(patch, Mapping):
            raise ToolError("invalid_input", "Action state patch must be an object.")
        with self.store.connection(True) as db:
            row = db.execute("SELECT state,claim FROM actions WHERE id=?", (action_id,)).fetchone()
            if not row or row["claim"] != self.claim:
                raise ToolError("authorization", "Action state write requires its current lease.")
            state = json.loads(row["state"])
            state.update(patch)
            db.execute("UPDATE actions SET state=?,updated=? WHERE id=?", (json_text(state,32_000_000),time.time(),action_id))

    def dispatched(self, action_id, intent: Mapping):
        with self.store.connection(True) as db:
            row = db.execute("SELECT intents,claim FROM actions WHERE id=?", (action_id,)).fetchone()
            if not row or row["claim"] != self.claim:
                raise ToolError("authorization", "Dispatch requires its current lease.")
            intents = json.loads(row["intents"])
            if len(intents) >= 32:
                raise ToolError("output_limit", "Too many provider dispatches for one action.")
            intents.append(dict(intent))
            db.execute("UPDATE actions SET dispatched=1,intents=?,updated=? WHERE id=?", (json_text(intents),time.time(),action_id))
