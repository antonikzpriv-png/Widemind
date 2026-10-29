"""Exact, durable accounting. Host-owned; never put this DB in an agent workspace.

The business process receives the protocol surface, never a writable wallet file.
All provider events are independently verified by StripeClient before these books
change. SQLite triggers prohibit rewriting financial entries.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time

from widemind.business.contracts import Outcome


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(*parts):
    return sha256(_json(parts).encode()).hexdigest()[:40]


def _text(value, name, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")
    return value


def _money(value, name="money"):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError(f"invalid integer {name}")
    return value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS economy_runs (
 run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, breached INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS economy_jobs (
 run_id TEXT NOT NULL REFERENCES economy_runs(run_id), job_id TEXT NOT NULL,
 price_micro_usd INTEGER NOT NULL DEFAULT 0, session_id TEXT UNIQUE,
 payment_intent_id TEXT, canonical TEXT NOT NULL DEFAULT '{}',
 fulfilled_reference TEXT, revision INTEGER NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL, PRIMARY KEY(run_id,job_id)
);
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
 run_id TEXT NOT NULL REFERENCES economy_runs(run_id), job_id TEXT,
 kind TEXT NOT NULL, delta_micro_usd INTEGER NOT NULL,
 currency TEXT NOT NULL CHECK(currency='USD'), source TEXT NOT NULL,
 evidence_reference TEXT NOT NULL, native TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger BEGIN SELECT RAISE(ABORT,'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger BEGIN SELECT RAISE(ABORT,'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS reservations (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, job_id TEXT NOT NULL,
 reserve_action_id TEXT NOT NULL UNIQUE, maximum_micro_usd INTEGER NOT NULL,
 state TEXT NOT NULL, execution_action_id TEXT UNIQUE, execution_maximum_micro_usd INTEGER,
 charged_micro_usd INTEGER NOT NULL DEFAULT 0,
 provisional INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
 FOREIGN KEY(run_id,job_id) REFERENCES economy_jobs(run_id,job_id)
);
CREATE TABLE IF NOT EXISTS economy_actions (
 action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, job_id TEXT NOT NULL,
 kind TEXT NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stripe_events (
 provider_scope TEXT NOT NULL, event_id TEXT NOT NULL, digest TEXT NOT NULL,
 state TEXT NOT NULL, jobs TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,
 PRIMARY KEY(provider_scope,event_id)
);
CREATE TABLE IF NOT EXISTS economy_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


class EconomyService:
    """One connection per host request/worker. No funding is implicit on construction."""
    def __init__(self, path, *, mode, stripe=None, clock=time.time):
        if mode not in {"live", "demo", "test"}:
            raise ValueError("explicit live/demo/test economy mode required")
        if stripe is not None and getattr(stripe, "mode", None) != mode:
            raise ValueError("payment provider and economy modes must match")
        self.path, self.mode, self.stripe, self.clock = Path(path), mode, stripe, clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
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
        try:
            self.db.executescript(_SCHEMA)
            with self.transaction():
                existing = self.one("SELECT value FROM economy_meta WHERE key='mode'")
                if existing and existing["value"] != mode:
                    raise ValueError("economy database mode cannot change")
                self.db.execute("INSERT OR IGNORE INTO economy_meta VALUES ('mode',?)", (mode,))
        except BaseException:
            self.db.close()
            raise

    @contextmanager
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
        return [dict(row) for row in self.db.execute(sql, args)]

    def close(self):
        self.db.close()

    def health(self):
        return {"name": "economy", "mode": self.mode, "configured": True,
                "stripe_configured": self.stripe is not None, "currency": "USD"}

    def _run(self, run_id):
        _text(run_id, "run_id")
        run = self.one("SELECT * FROM economy_runs WHERE run_id=?", (run_id,))
        if not run or run["mode"] != self.mode:
            raise ValueError("unknown or mismatched economy run; explicit initialization required")
        return run

    def _job(self, run_id, job_id, *, price=None):
        self._run(run_id)
        _text(job_id, "job_id")
        self.db.execute("""INSERT OR IGNORE INTO economy_jobs
            (run_id,job_id,price_micro_usd,updated_at) VALUES (?,?,?,?)""",
            (run_id, job_id, 0 if price is None else price, self.clock()))
        job = self.one("SELECT * FROM economy_jobs WHERE run_id=? AND job_id=?", (run_id, job_id))
        if price is not None:
            if job["price_micro_usd"] not in (0, price):
                raise ValueError("job price cannot change")
            if job["price_micro_usd"] == 0:
                self.db.execute("UPDATE economy_jobs SET price_micro_usd=? WHERE run_id=? AND job_id=?", (price,run_id,job_id))
                job["price_micro_usd"] = price
        return job

    def _entry(self, key, run_id, job_id, kind, delta, source, evidence, native=None):
        if type(delta) is not int:
            raise ValueError("financial entries require exact integer amounts")
        self.db.execute("""INSERT INTO ledger
            (event_key,run_id,job_id,kind,delta_micro_usd,currency,source,evidence_reference,native,created_at)
            VALUES (?,?,?,?,?,'USD',?,?,?,?)""",
            (key,run_id,job_id,kind,delta,source,_text(evidence,"evidence",1000),_json(native or {}),self.clock()))

    def create_run(self, run_id, *, opening_micro_usd=5_000_000, evidence_reference):
        """Operator-only initialization. Reopening does not create another grant."""
        _text(run_id,"run_id")
        _money(opening_micro_usd)
        _text(evidence_reference,"opening funding evidence",1000)
        with self.transaction():
            saved = self.one("SELECT * FROM economy_runs WHERE run_id=?", (run_id,))
            if saved:
                self._run(run_id)
                opening = self.one("SELECT delta_micro_usd FROM ledger WHERE event_key=?", (f"opening:{run_id}",))
                if opening["delta_micro_usd"] != opening_micro_usd:
                    raise ValueError("opening grant cannot change")
            else:
                self.db.execute("INSERT INTO economy_runs VALUES (?,?,0,?)", (run_id,self.mode,self.clock()))
                self._entry(f"opening:{run_id}",run_id,None,"opening",opening_micro_usd,
                            "owner_grant" if self.mode == "live" else f"{self.mode}_grant",evidence_reference)
        return self.balance(run_id=run_id)

    initialize_run = create_run

    def owner_funding(self, *, run_id, amount_micro_usd, external_event_id, evidence_reference, subsidy=False):
        """Operator/service-boundary only; never exposed as an agent or public route."""
        _money(amount_micro_usd)
        if type(subsidy) is not bool:
            raise ValueError("subsidy label must be explicit boolean")
        _text(external_event_id,"funding event")
        key = _id("owner-funding",run_id,external_event_id)
        kind = "subsidy" if subsidy else "owner_funding"
        with self.transaction():
            self._run(run_id)
            previous = self.one("SELECT * FROM ledger WHERE event_key=?", (key,))
            if previous:
                if previous["delta_micro_usd"] != amount_micro_usd or previous["kind"] != kind:
                    raise ValueError("funding identity conflict")
            else:
                self._entry(key,run_id,None,kind,amount_micro_usd,"owner",evidence_reference)
        return self.balance(run_id=run_id)

    def correct_operating_charge(self, *, run_id, entry_id, delta_micro_usd, external_event_id, evidence_reference):
        """Protected operator/provider correction: append, never alter the original charge."""
        if type(delta_micro_usd) is not int or abs(delta_micro_usd) > 10**15 or not delta_micro_usd:
            raise ValueError("nonzero integer correction required")
        _text(external_event_id,"correction event")
        key = _id("operating-correction",run_id,external_event_id)
        with self.transaction():
            self._run(run_id)
            original = self.one("SELECT * FROM ledger WHERE id=? AND run_id=? AND kind='operating'", (entry_id,run_id))
            if not original:
                raise ValueError("correction must reference an actual charge")
            previous = self.one("SELECT * FROM ledger WHERE event_key=?", (key,))
            if previous:
                if previous["delta_micro_usd"] != delta_micro_usd or previous["job_id"] != original["job_id"]:
                    raise ValueError("correction event identity conflict")
                return
            total = self.one("SELECT COALESCE(SUM(delta_micro_usd),0) AS n FROM ledger WHERE run_id=? AND job_id=? AND kind='operating'", (run_id,original["job_id"]))["n"]
            if total + delta_micro_usd > 0:
                raise ValueError("correction cannot invent negative operating cost")
            self._entry(key,run_id,original["job_id"],"operating",delta_micro_usd,"provider_usage_correction",evidence_reference,
                        {"original_entry_id":entry_id})
            self.db.execute("UPDATE economy_jobs SET revision=revision+1,updated_at=? WHERE run_id=? AND job_id=?", (self.clock(),run_id,original["job_id"]))

    def ledger_entries(self, *, run_id, limit=200):
        self._run(run_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid ledger page limit")
        return self.all("SELECT id,kind,delta_micro_usd,currency,source,evidence_reference,created_at FROM ledger WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id,limit))

    def _balance(self, run_id):
        run = self._run(run_id)
        totals = {row["kind"]: row["amount"] for row in self.all("SELECT kind,SUM(delta_micro_usd) AS amount FROM ledger WHERE run_id=? GROUP BY kind", (run_id,))}
        posted = sum(totals.values())
        reserved = self.one("""SELECT COALESCE(SUM(MAX(0,maximum_micro_usd-charged_micro_usd)),0) AS amount
            FROM reservations WHERE run_id=? AND state IN ('reserved','executing')""", (run_id,))["amount"]
        holds, earned = 0, 0
        for job in self.all("SELECT * FROM economy_jobs WHERE run_id=?", (run_id,)):
            canonical = json.loads(job["canonical"])
            unrefunded = max(0,canonical.get("receipts_micro_usd",0)-canonical.get("refunds_micro_usd",0))
            exposure = canonical.get("disputed_micro_usd",0) + canonical.get("pending_refunds_micro_usd",0)
            holds += max(unrefunded if not job["fulfilled_reference"] else 0, exposure)
            if job["fulfilled_reference"] and canonical.get("status") != "disputed":
                earned += min(job["price_micro_usd"],max(0,unrefunded-canonical.get("pending_refunds_micro_usd",0)))
        return {"run_id":run_id,"mode":self.mode,"currency":"USD","posted_micro_usd":posted,
            "spendable_micro_usd":posted-reserved-holds,"reserved_micro_usd":reserved,"holds_micro_usd":holds,
            "opening_micro_usd":totals.get("opening",0),"owner_funding_micro_usd":totals.get("owner_funding",0),
            "subsidies_micro_usd":totals.get("subsidy",0),"receipts_micro_usd":totals.get("receipt",0),
            "refunds_micro_usd":-totals.get("refund",0),"fees_micro_usd":-totals.get("fee",0),
            "operating_cost_micro_usd":-totals.get("operating",0),"earned_micro_usd":earned,
            "breached":bool(run["breached"]), "costs_final":not bool(self.one(
                "SELECT 1 FROM reservations WHERE run_id=? AND state IN ('reserved','executing')", (run_id,)))}

    def balance(self, *, run_id):
        with self.transaction():
            return self._balance(run_id)

    def _previous(self, action_id, kind, run_id, job_id, request):
        _text(action_id,"action_id")
        previous = self.one("SELECT * FROM economy_actions WHERE action_id=?", (action_id,))
        if previous and (previous["kind"],previous["run_id"],previous["job_id"],previous["request"]) != (kind,run_id,job_id,_json(request)):
            raise ValueError("action identity conflict")
        return previous

    def _action(self, action_id, kind, run_id, job_id, request, outcome):
        self.db.execute("""INSERT INTO economy_actions VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(action_id) DO UPDATE SET state=excluded.state,result=excluded.result,reason=excluded.reason""",
            (action_id,run_id,job_id,kind,_json(request),outcome.state,_json(outcome.data),outcome.reason[:1000],self.clock()))

    @staticmethod
    def _outcome(row):
        return Outcome(row["state"],json.loads(row["result"]),row["reason"])

    def reserve(self, *, run_id, job_id, action_id, maximum_micro_usd):
        _money(maximum_micro_usd)
        if maximum_micro_usd <= 0:
            raise ValueError("positive paid reservation required")
        request = {"maximum_micro_usd":maximum_micro_usd}
        with self.transaction():
            self._job(run_id,job_id)
            previous = self._previous(action_id,"reserve",run_id,job_id,request)
            if previous:
                return self._outcome(previous)
            balance = self._balance(run_id)
            if balance["breached"] or balance["spendable_micro_usd"] < maximum_micro_usd:
                outcome = Outcome("failed",reason="insufficient spendable credit or unresolved cost-limit breach")
            else:
                reservation_id = _id(run_id,job_id,"reservation",action_id)
                self.db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,'reserved',NULL,NULL,0,0,?)",
                    (reservation_id,run_id,job_id,action_id,maximum_micro_usd,self.clock()))
                self.db.execute("UPDATE economy_jobs SET revision=revision+1,updated_at=? WHERE run_id=? AND job_id=?", (self.clock(),run_id,job_id))
                outcome = Outcome("succeeded",{"reservation_id":reservation_id,"maximum_micro_usd":maximum_micro_usd})
            self._action(action_id,"reserve",run_id,job_id,request,outcome)
            return outcome

    def begin_execution(self, *, run_id, job_id, reservation_id, action_id, maximum_micro_usd):
        _money(maximum_micro_usd)
        _text(action_id,"execution action")
        with self.transaction():
            reservation = self.one("SELECT * FROM reservations WHERE id=?", (reservation_id,))
            if not reservation or reservation["run_id"] != run_id or reservation["job_id"] != job_id or reservation["state"] not in {"reserved","executing"}:
                return Outcome("failed",reason="reservation is absent, settled, or released")
            if maximum_micro_usd <= 0 or maximum_micro_usd > reservation["maximum_micro_usd"]:
                return Outcome("failed",reason="execution exceeds reservation ceiling")
            if reservation["execution_action_id"] not in (None,action_id):
                return Outcome("failed",reason="reservation belongs to another execution")
            balance = self._balance(run_id)
            if balance["breached"] or balance["spendable_micro_usd"] < 0:
                return Outcome("failed",reason="wallet coverage changed or run has unresolved cost-limit liability")
            if reservation["state"] == "executing" and reservation["execution_maximum_micro_usd"] != maximum_micro_usd:
                return Outcome("failed",reason="execution cost ceiling cannot change after dispatch authorization")
            if reservation["state"] == "reserved":
                self.db.execute("UPDATE reservations SET state='executing',execution_action_id=?,execution_maximum_micro_usd=? WHERE id=?", (action_id,maximum_micro_usd,reservation_id))
            return Outcome("succeeded",{"run_id":run_id,"job_id":job_id,"reservation_id":reservation_id,"action_id":action_id,
                "maximum_micro_usd":maximum_micro_usd,"reservation_state":"executing"})

    def settle_execution(self, *, reservation_id, action_id, actual_micro_usd, evidence_reference, provisional=False):
        _money(actual_micro_usd)
        _text(evidence_reference,"usage evidence",1000)
        if type(provisional) is not bool:
            raise ValueError("provisional must be boolean")
        with self.transaction():
            reservation = self.one("SELECT * FROM reservations WHERE id=?", (reservation_id,))
            if not reservation or reservation["execution_action_id"] != action_id or reservation["state"] not in {"executing","settled"}:
                return Outcome("failed",reason="reservation is not owned by this execution")
            if actual_micro_usd < reservation["charged_micro_usd"]:
                return Outcome("failed",reason="charges cannot be rewritten; use an evidenced compensating correction")
            if reservation["state"] == "settled":
                if actual_micro_usd != reservation["charged_micro_usd"] or provisional:
                    return Outcome("failed",reason="final settlement is immutable")
                return Outcome("succeeded",{"charged_micro_usd":actual_micro_usd,"provisional":False,"reservation_state":"settled"})
            delta = actual_micro_usd-reservation["charged_micro_usd"]
            if delta:
                self._entry(_id("usage",reservation_id,actual_micro_usd),reservation["run_id"],reservation["job_id"],
                    "operating",-delta,"provider_usage",evidence_reference)
            state = "executing" if provisional else "settled"
            self.db.execute("UPDATE reservations SET charged_micro_usd=?,provisional=?,state=? WHERE id=?",
                            (actual_micro_usd,int(provisional),state,reservation_id))
            if actual_micro_usd > reservation["execution_maximum_micro_usd"]:
                self.db.execute("UPDATE economy_runs SET breached=1 WHERE run_id=?", (reservation["run_id"],))
            self.db.execute("UPDATE economy_jobs SET revision=revision+1,updated_at=? WHERE run_id=? AND job_id=?",
                            (self.clock(),reservation["run_id"],reservation["job_id"]))
            return Outcome("succeeded",{"charged_micro_usd":actual_micro_usd,"provisional":provisional,"reservation_state":state})

    def release(self, *, run_id, job_id, action_id, reservation_id):
        request = {"reservation_id":reservation_id}
        with self.transaction():
            self._run(run_id)
            previous = self._previous(action_id,"release",run_id,job_id,request)
            if previous and previous["state"] != "unknown":
                return self._outcome(previous)
            reservation = self.one("SELECT * FROM reservations WHERE id=? AND run_id=? AND job_id=?", (reservation_id,run_id,job_id))
            if not reservation:
                outcome = Outcome("failed",reason="unknown reservation")
            elif reservation["state"] == "executing":
                outcome = Outcome("unknown",reason="execution liability unresolved; reconcile before release")
            else:
                if reservation["state"] == "reserved":
                    self.db.execute("UPDATE reservations SET state='released' WHERE id=?", (reservation_id,))
                    self.db.execute("UPDATE economy_jobs SET revision=revision+1,updated_at=? WHERE run_id=? AND job_id=?", (self.clock(),run_id,job_id))
                outcome = Outcome("succeeded",{"released":True,"reservation_id":reservation_id})
            self._action(action_id,"release",run_id,job_id,request,outcome)
            return outcome

    def payment_link(self, *, run_id, job_id, action_id, amount_micro_usd, currency):
        _money(amount_micro_usd)
        if amount_micro_usd <= 0 or currency != "USD" or amount_micro_usd % 10000:
            raise ValueError("Stripe USD quotes must be positive exact cents")
        request = {"amount_micro_usd":amount_micro_usd,"currency":currency}
        with self.transaction():
            self._job(run_id,job_id,price=amount_micro_usd)
            previous = self._previous(action_id,"payment_link",run_id,job_id,request)
            if previous and previous["state"] in {"succeeded","failed"}:
                return self._outcome(previous)
            if self.stripe is None:
                return Outcome("failed",reason="Stripe is not configured")
            if not previous:
                self._action(action_id,"payment_link",run_id,job_id,request,Outcome("unknown",reason="provider intent persisted before request"))
        try:
            if previous:
                outcome = self.stripe.reconcile_checkout(action_id=action_id)
            else:
                result = self.stripe.create_checkout(run_id=run_id,job_id=job_id,action_id=action_id,
                    amount_micro_usd=amount_micro_usd,currency=currency)
                outcome = Outcome("succeeded",result)
        except Exception:
            outcome = Outcome("unknown",reason="Stripe outcome uncertain; reconcile before retry")
        return self._save_link(action_id,outcome)

    def _save_link(self, action_id, outcome):
        if not isinstance(outcome,Outcome) or outcome.state not in {"succeeded","failed","unknown"} or not isinstance(outcome.data,dict):
            outcome = Outcome("unknown",reason="invalid Stripe reconciliation outcome")
        with self.transaction():
            intent = self.one("SELECT * FROM economy_actions WHERE action_id=? AND kind='payment_link'", (action_id,))
            if not intent:
                raise ValueError("unknown Stripe intent")
            if intent["state"] in {"succeeded","failed"}:
                return self._outcome(intent)
            if outcome.state == "succeeded":
                try:
                    session_id = _text(outcome.data.get("id"),"Stripe session")
                    url = _text(outcome.data.get("url"),"payment URL",2048)
                    if not url.startswith("https://"):
                        raise ValueError("payment URL must be HTTPS")
                    self.db.execute("UPDATE economy_jobs SET session_id=? WHERE run_id=? AND job_id=?", (session_id,intent["run_id"],intent["job_id"]))
                except (ValueError,sqlite3.IntegrityError):
                    outcome = Outcome("unknown",reason="invalid or conflicting Stripe session evidence")
            self.db.execute("UPDATE economy_actions SET state=?,result=?,reason=? WHERE action_id=?",
                            (outcome.state,_json(outcome.data),outcome.reason[:1000],action_id))
            return outcome

    def reconcile(self, *, action_id):
        intent = self.one("SELECT * FROM economy_actions WHERE action_id=?", (action_id,))
        if not intent:
            return Outcome("unknown",reason="no durable economy intent")
        if intent["state"] in {"succeeded","failed"}:
            return self._outcome(intent)
        if intent["kind"] == "payment_link" and self.stripe:
            try:
                outcome = self.stripe.reconcile_checkout(action_id=action_id)
            except Exception:
                return Outcome("unknown",reason="Stripe reconciliation unavailable")
            return self._save_link(action_id,outcome)
        if intent["kind"] == "release":
            return self.release(run_id=intent["run_id"],job_id=intent["job_id"],action_id=action_id,
                                reservation_id=json.loads(intent["request"])["reservation_id"])
        return self._outcome(intent)

    def _apply_payment(self, job, canonical):
        for key in ("receipts_micro_usd","refunds_micro_usd","fees_micro_usd","disputed_micro_usd","pending_refunds_micro_usd"):
            _money(canonical.get(key,0),key)
        if canonical.get("status") not in {"pending","partial","settled","refunded","disputed"}:
            raise ValueError("invalid verified Stripe state")
        if canonical.get("mode") != self.mode:
            raise ValueError("Stripe financial mode mismatch")
        if canonical.get("run_id") != job["run_id"] or canonical.get("job_id") != job["job_id"]:
            raise ValueError("Stripe job metadata mismatch")
        if len(_json(canonical.get("provider_native",{}))) > 16000:
            raise ValueError("provider evidence exceeds metadata limit")
        relevant = {key:canonical.get(key,0) for key in ("receipts_micro_usd","refunds_micro_usd","fees_micro_usd","disputed_micro_usd","pending_refunds_micro_usd")}
        relevant.update(status=canonical["status"],provider_reference=canonical.get("provider_reference",""))
        previous = json.loads(job["canonical"])
        if previous == relevant:
            return
        revision = job["revision"]+1
        for field,kind,sign in (("receipts_micro_usd","receipt",1),("refunds_micro_usd","refund",-1),("fees_micro_usd","fee",-1)):
            delta = relevant[field]-previous.get(field,0)
            if delta:
                self._entry(_id("stripe-state",job["run_id"],job["job_id"],revision,kind),job["run_id"],job["job_id"],
                    kind,sign*delta,"stripe_verified",str(canonical.get("provider_reference") or job["session_id"]),
                    canonical.get("provider_native",{}))
        self.db.execute("UPDATE economy_jobs SET canonical=?,revision=?,payment_intent_id=?,updated_at=? WHERE run_id=? AND job_id=?",
            (_json(relevant),revision,canonical.get("payment_intent_id"),self.clock(),job["run_id"],job["job_id"]))

    def refresh_payment(self, *, run_id, job_id):
        self._run(run_id)
        job = self.one("SELECT * FROM economy_jobs WHERE run_id=? AND job_id=?", (run_id,job_id))
        if not job or not job["session_id"] or self.stripe is None:
            return
        canonical = self.stripe.inspect_checkout(session_id=job["session_id"])
        with self.transaction():
            fresh = self.one("SELECT * FROM economy_jobs WHERE run_id=? AND job_id=?", (run_id,job_id))
            if fresh["canonical"] != job["canonical"]:
                raise ValueError("concurrent payment refresh superseded this provider read; requery")
            self._apply_payment(fresh,canonical)

    def payment(self, *, run_id, job_id):
        self.refresh_payment(run_id=run_id,job_id=job_id)
        with self.transaction():
            self._run(run_id)
            job = self.one("SELECT * FROM economy_jobs WHERE run_id=? AND job_id=?", (run_id,job_id))
            canonical = json.loads(job["canonical"]) if job else {}
            cost = -self.one("SELECT COALESCE(SUM(delta_micro_usd),0) AS n FROM ledger WHERE run_id=? AND job_id=? AND kind='operating'", (run_id,job_id))["n"]
            unresolved = self.one("SELECT 1 FROM reservations WHERE run_id=? AND job_id=? AND state IN ('reserved','executing')", (run_id,job_id))
            receipts,refunds = canonical.get("receipts_micro_usd",0),canonical.get("refunds_micro_usd",0)
            earned = min(job["price_micro_usd"],max(0,receipts-refunds-canonical.get("pending_refunds_micro_usd",0))) if job and job["fulfilled_reference"] and canonical.get("status") != "disputed" else 0
            return {"run_id":run_id,"job_id":job_id,"revision":job["revision"] if job else 0,"mode":self.mode,
                "status":canonical.get("status","pending"),"currency":"USD","receipts_micro_usd":receipts,
                "refunds_micro_usd":refunds,"fees_micro_usd":canonical.get("fees_micro_usd",0),"earned_micro_usd":earned,
                "operating_cost_micro_usd":cost,"costs_final":not bool(unresolved)}

    def record_fulfillment(self, *, run_id, job_id, action_id, delivery_reference):
        _text(delivery_reference,"confirmed delivery evidence",1000)
        request = {"delivery_reference":delivery_reference}
        with self.transaction():
            job = self._job(run_id,job_id)
            previous = self._previous(action_id,"fulfillment",run_id,job_id,request)
            if previous:
                return self._outcome(previous)
            if job["fulfilled_reference"] and job["fulfilled_reference"] != delivery_reference:
                raise ValueError("conflicting delivery evidence for fulfilled order")
            if not job["fulfilled_reference"]:
                self.db.execute("UPDATE economy_jobs SET fulfilled_reference=?,revision=revision+1,updated_at=? WHERE run_id=? AND job_id=?",
                    (delivery_reference,self.clock(),run_id,job_id))
            outcome = Outcome("succeeded",{"recorded":True,"delivery_reference":delivery_reference})
            self._action(action_id,"fulfillment",run_id,job_id,request,outcome)
            return outcome

    def handle_webhook(self, raw_body, signature):
        if self.stripe is None:
            raise ValueError("Stripe webhook not configured")
        event = self.stripe.verify_event(raw_body,signature)
        event_id = _text(event.get("id"),"Stripe event")
        scope = f"stripe:{getattr(self.stripe,'account_id',None) or 'platform'}:{self.mode}"
        digest = sha256(raw_body).hexdigest()
        with self.transaction():
            previous = self.one("SELECT * FROM stripe_events WHERE provider_scope=? AND event_id=?", (scope,event_id))
            if previous and previous["digest"] != digest:
                raise ValueError("Stripe event identity conflict")
            if previous and previous["state"] == "processed":
                return json.loads(previous["jobs"])
            self.db.execute("INSERT OR IGNORE INTO stripe_events VALUES (?,?,?,'running','[]',?)", (scope,event_id,digest,self.clock()))
        session_ids = self.stripe.session_ids_for_event(event)
        if event.get("type") == "balance.available":
            session_ids = [row["session_id"] for row in self.all("SELECT session_id FROM economy_jobs WHERE session_id IS NOT NULL LIMIT 1000")]
        if not isinstance(session_ids,list) or len(session_ids) > 1000:
            raise ValueError("invalid Stripe event routing")
        changed = []
        for session_id in set(session_ids):
            job = self.one("SELECT * FROM economy_jobs WHERE session_id=?", (session_id,))
            if job:
                self.refresh_payment(run_id=job["run_id"],job_id=job["job_id"])
                changed.append(job["job_id"])
        changed.sort()
        with self.transaction():
            self.db.execute("UPDATE stripe_events SET state='processed',jobs=? WHERE provider_scope=? AND event_id=?", (_json(changed),scope,event_id))
        return changed
