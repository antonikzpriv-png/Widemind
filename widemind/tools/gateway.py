"""Persistent tool gateway. Credentials and billing stay outside the agent workspace."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from widemind.business.contracts import Outcome
from widemind.business.policy import integer
from widemind.business.services import single_line
from widemind.business.store import encode, identity
from .gmail import address, rfc_message_id
from .http import TransportError
from .research import ProviderResult


class LiveTools:
    def __init__(self, path, *, economy, runtime, gmail=None, research=None, mode="live",
                 enabled_actions=("gmail.read", "gmail.send", "research.collect"), allowed_recipients=None,
                 max_gmail_actions_per_day=500, max_research_actions_per_day=50, clock=time.time):
        if mode not in {"live", "test"}:
            raise ValueError("production gateway requires live or explicit test mode")
        if any(getattr(client, "mode", mode) != mode for client in (economy, runtime)):
            raise ValueError("gateway, economy and runtime modes must match")
        self.mode, self.economy, self.runtime = mode, economy, runtime
        self.gmail, self.research, self.clock = gmail, research, clock
        self.enabled_actions = frozenset(enabled_actions)
        self.max_gmail_actions_per_day = integer(max_gmail_actions_per_day, "daily Gmail limit", 1, 10000)
        self.max_research_actions_per_day = integer(max_research_actions_per_day, "daily research limit", 1, 1000)
        self.allowed_recipients = None if allowed_recipients is None else frozenset(address(item) for item in allowed_recipients)
        path = Path(path).resolve()
        self.lock_directory = path.parent / (path.name + ".actions")
        self.lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("CREATE TABLE IF NOT EXISTS gateway_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO gateway_settings VALUES ('mode',?)", (mode,))
        if self.db.execute("SELECT value FROM gateway_settings WHERE key='mode'").fetchone()[0] != mode:
            self.db.close()
            raise ValueError("tool journal mode cannot be changed")
        self.db.execute("""CREATE TABLE IF NOT EXISTS tool_actions(
          action_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,job_id TEXT NOT NULL,action TEXT NOT NULL,
          request TEXT NOT NULL,state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',reason TEXT NOT NULL DEFAULT '',
          context TEXT,provider_reference TEXT,actual_micro_usd INTEGER,settlement_pending INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL,rfc_message_id TEXT)
        """)
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS mail_identity ON tool_actions(rfc_message_id)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS delivery_evidence(
          action_id TEXT NOT NULL,evidence_reference TEXT NOT NULL,recipient TEXT NOT NULL,created_at REAL NOT NULL,
          PRIMARY KEY(action_id,evidence_reference))""")

    @contextmanager
    def _action_lock(self, action_id):
        # An OS lock spans intent, preparation, provider dispatch and settlement.
        # It is released by process death; reconciliation can never abort a still
        # active attempt or overwrite evidence from a concurrent completion.
        key = hashlib.sha256(action_id.encode()).hexdigest()
        fd = os.open(self.lock_directory / (key + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        stream = os.fdopen(fd, "a+b")
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(stream.fileno()).st_size == 0:
                        stream.write(b"\0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                pass
            yield acquired
        finally:
            stream.close()

    def available(self, action):
        if action not in self.enabled_actions:
            return False
        return self.gmail is not None if action in {"gmail.read", "gmail.send"} else self.research is not None if action == "research.collect" else False

    def validate_arguments(self, action, arguments):
        """Local pre-offer readiness only; no provider, model, or DNS calls."""
        try:
            if not self.available(action):
                raise ValueError("provider is not configured")
            if action == "research.collect":
                self.research.validate_scope(arguments)
            elif action == "gmail.send":
                recipient = address(arguments.get("to"))
                if self.allowed_recipients is not None and recipient not in self.allowed_recipients:
                    raise ValueError("recipient is outside controlled pilot allowlist")
            return {"ready": True, "reason": ""}
        except (ValueError, TypeError, KeyError, AttributeError):
            return {"ready": False, "reason": "scope needs configured provider/source/recipient permissions"}

    def integration_status(self):
        return [{"name": "Gmail", "status": "configured" if self.gmail else "missing", "detail": "Dedicated OAuth identity; delivery evidence required"},
                {"name": "OpenAI research", "status": "configured" if self.research else "missing", "detail": "Bounded inference on collected HTTPS evidence"}]

    def _row(self, action_id):
        result = self.db.execute("SELECT * FROM tool_actions WHERE action_id=?", (action_id,)).fetchone()
        return dict(result) if result else None

    def _generation(self, run_id):
        method = getattr(self.runtime, "cancellation_generation", None)
        return method(run_id) if callable(method) else None

    def _allow(self, run_id, action, generation=None):
        if not self.available(action) or self.runtime.state(run_id) not in {"active", "waiting"}:
            raise ValueError("tool unavailable, disabled, or runtime is not active")
        check = getattr(self.runtime, "capability_enabled", None)
        if callable(check) and not check(run_id, action):
            raise ValueError("control-plane capability disabled")
        cancelled = getattr(self.runtime, "cancelled", None)
        if callable(cancelled) and cancelled(run_id):
            raise ValueError("run stopped")
        if generation is not None and self._generation(run_id) != generation:
            raise ValueError("run was stopped during this attempt")

    def _outcome(self, row):
        state = row["state"] if row["state"] in {"succeeded", "failed"} and not row["settlement_pending"] else "unknown"
        return Outcome(state, json.loads(row["result"]), row["reason"])

    def _save(self, action_id, outcome, *, actual=None, reference=None, settlement_pending=0):
        self.db.execute("""UPDATE tool_actions SET state=?,result=?,reason=?,actual_micro_usd=?,provider_reference=?,settlement_pending=?
          WHERE action_id=?""", (outcome.state, encode(outcome.data), outcome.reason[:500], actual, reference, settlement_pending, action_id))
        return self._outcome(self._row(action_id))

    def execute(self, *, run_id, job_id, action_id, action, arguments, reservation_id, maximum_micro_usd):
        single_line(action_id, "action ID", 300)
        with self._action_lock(action_id) as acquired:
            if not acquired:
                return Outcome("unknown", reason="this action is currently executing or reconciling")
            return self._execute(run_id=run_id, job_id=job_id, action_id=action_id, action=action,
                                 arguments=arguments, reservation_id=reservation_id, maximum_micro_usd=maximum_micro_usd)

    def _execute(self, *, run_id, job_id, action_id, action, arguments, reservation_id, maximum_micro_usd):
        single_line(action_id, "action ID", 300)
        single_line(run_id, "run ID", 200)
        single_line(job_id, "job ID", 200)
        integer(maximum_micro_usd, "maximum cost")
        if not isinstance(arguments, dict) or len(encode(arguments)) > 1500000:
            raise ValueError("invalid tool arguments")
        request = encode({"arguments": arguments, "reservation_id": reservation_id, "maximum_micro_usd": maximum_micro_usd})
        previous = self._row(action_id)
        if previous:
            if previous["request"] != request or previous["run_id"] != run_id or previous["job_id"] != job_id or previous["action"] != action:
                raise ValueError("tool action identity reused with changed contents")
            # Do not automatically repeat a call, even if the previous process died
            # before dispatch. Explicit reconcile is the only uncertain path.
            return self._outcome(previous)
        generation = self._generation(run_id)
        try:
            self._allow(run_id, action, generation)
            if action in {"gmail.read", "gmail.send"} and maximum_micro_usd != 0:
                raise ValueError("Gmail incremental-cost ceiling must be zero")
            if action == "gmail.send" and self.allowed_recipients is not None and address(arguments.get("to")) not in self.allowed_recipients:
                raise ValueError("recipient not in controlled-recipient allowlist")
            if action == "research.collect" and (not reservation_id or maximum_micro_usd <= 0):
                raise ValueError("research requires bounded spending authorization")
        except ValueError:
            return Outcome("failed", reason="tool policy or capability check denied the action")
        # A unique insertion commits before any account/provider operation. Another
        # process with the same ID cannot race through the dispatch boundary.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            prefix = "gmail.%" if action.startswith("gmail.") else "research.%"
            ceiling = self.max_gmail_actions_per_day if action.startswith("gmail.") else self.max_research_actions_per_day
            day_start = (self.clock() // 86400) * 86400
            used = self.db.execute("SELECT COUNT(*) FROM tool_actions WHERE action LIKE ? AND created_at>=?", (prefix, day_start)).fetchone()[0]
            if used >= ceiling:
                self.db.execute("ROLLBACK")
                return Outcome("failed", reason="configured daily tool quota exhausted")
            self.db.execute("""INSERT INTO tool_actions(action_id,run_id,job_id,action,request,state,created_at,rfc_message_id)
              VALUES (?,?,?,?,?,'running',?,?)""", (action_id, run_id, job_id, action, request, self.clock(),
                rfc_message_id(action_id) if action == "gmail.send" else None))
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError:
            self.db.execute("ROLLBACK")
            return self._execute(run_id=run_id, job_id=job_id, action_id=action_id, action=action, arguments=arguments,
                                reservation_id=reservation_id, maximum_micro_usd=maximum_micro_usd)
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        before_dispatch = lambda: self._allow(run_id, action, generation)
        try:
            if action == "gmail.send":
                result = self.gmail.send(action_id, arguments, before_dispatch=before_dispatch)
                outcome = Outcome(result["state"], result.get("data", {}), result.get("reason", ""))
                return self._save(action_id, outcome)
            if action == "gmail.read":
                result = self.gmail.read(arguments)
                result["delivery_updates"] = self._acknowledge_messages(result.get("messages", []))
                return self._save(action_id, Outcome("succeeded", result))
            authorization = self.economy.begin_execution(run_id=run_id, job_id=job_id, reservation_id=reservation_id,
                                                        action_id=action_id, maximum_micro_usd=maximum_micro_usd)
            if not isinstance(authorization, Outcome) or authorization.state != "succeeded":
                return self._save(action_id, Outcome("failed" if isinstance(authorization, Outcome) and authorization.state == "failed" else "unknown",
                                                     reason="economy denied or could not establish execution authorization"))
            expected_authorization = {"run_id": run_id, "job_id": job_id, "reservation_id": reservation_id,
                                      "action_id": action_id, "maximum_micro_usd": maximum_micro_usd}
            if not isinstance(authorization.data, dict) or any(authorization.data.get(key) != value for key, value in expected_authorization.items()):
                return self._save(action_id, Outcome("failed", reason="economy authorization ownership mismatch"))
            try:
                prepared = self.research.prepare(action_id, arguments, maximum_micro_usd, before_dispatch=before_dispatch)
            except (ValueError, TypeError, KeyError, TransportError):
                return self._finish_research(action_id, ProviderResult("failed", actual_micro_usd=0,
                    reference="not-dispatched:" + action_id, reason="research preflight failed"))
            # Persist exact evidence before sending it to OpenAI so a restart never
            # refetches modified pages or pays to reconstruct a prior attempt.
            self.db.execute("UPDATE tool_actions SET context=? WHERE action_id=?", (encode(prepared), action_id))
            def paid_dispatch_guard():
                before_dispatch()
                # Source collection may take time. Revalidate current wallet
                # coverage and immutable ownership immediately before paid POST.
                current = self.economy.begin_execution(run_id=run_id, job_id=job_id, reservation_id=reservation_id,
                    action_id=action_id, maximum_micro_usd=maximum_micro_usd)
                if not isinstance(current, Outcome) or current.state != "succeeded" or not isinstance(current.data, dict) or any(current.data.get(key) != value for key, value in expected_authorization.items()):
                    raise ValueError("execution funding or ownership changed before dispatch")
            result = self.research.run(action_id, arguments, maximum_micro_usd, prepared=prepared, before_dispatch=paid_dispatch_guard)
            return self._finish_research(action_id, result)
        except Exception:
            # Never erase provider charge evidence if settlement itself failed.
            if self._row(action_id)["settlement_pending"]:
                return Outcome("unknown", reason="provider completed; financial settlement requires reconciliation")
            return self._save(action_id, Outcome("unknown", reason="provider outcome uncertain; reconcile without repeating"))

    def _finish_research(self, action_id, result):
        if not isinstance(result, ProviderResult):
            return self._save(action_id, Outcome("unknown", reason="invalid research result"))
        if result.state == "unknown" or result.actual_micro_usd is None:
            return self._save(action_id, Outcome("unknown", reason=result.reason))
        integer(result.actual_micro_usd, "actual provider cost")
        row = self._row(action_id)
        request = json.loads(row["request"])
        # Save provider evidence first; a crash in financial settlement is resumed
        # from this evidence, never by invoking inference again.
        outcome = Outcome(result.state, result.data, result.reason)
        self._save(action_id, outcome, actual=result.actual_micro_usd, reference=result.reference, settlement_pending=1)
        settled = self.economy.settle_execution(reservation_id=request["reservation_id"], action_id=action_id,
            actual_micro_usd=result.actual_micro_usd, evidence_reference=result.reference, provisional=False)
        if not isinstance(settled, Outcome) or settled.state != "succeeded":
            return Outcome("unknown", reason="provider completed; financial settlement requires reconciliation")
        self.db.execute("UPDATE tool_actions SET settlement_pending=0 WHERE action_id=?", (action_id,))
        return self._outcome(self._row(action_id))

    def reconcile(self, *, action_id):
        single_line(action_id, "action ID", 300)
        with self._action_lock(action_id) as acquired:
            if not acquired:
                return Outcome("unknown", reason="this action is currently executing or reconciling")
            return self._reconcile(action_id=action_id)

    def _reconcile(self, *, action_id):
        row = self._row(action_id)
        if not row:
            return Outcome("unknown", reason="gateway has no action evidence")
        if row["settlement_pending"]:
            return self._finish_research(action_id, ProviderResult(row["state"], json.loads(row["result"]),
                                        row["actual_micro_usd"], row["provider_reference"] or "", row["reason"]))
        if row["state"] in {"succeeded", "failed"}:
            # Accepted mail can evolve via explicit delivery evidence, never a
            # sent-folder guess. Terminal confirmed results remain immutable.
            return self._outcome(row)
        request = json.loads(row["request"])
        try:
            if row["action"] == "gmail.send":
                result = self.gmail.reconcile_send(action_id)
                return self._save(action_id, Outcome(result["state"], result.get("data", {}), result.get("reason", "")))
            if row["action"] == "gmail.read":
                result = self.gmail.read(request["arguments"])
                result["delivery_updates"] = self._acknowledge_messages(result.get("messages", []))
                return self._save(action_id, Outcome("succeeded", result))
            context = json.loads(row["context"] or "{}")
            if not context:
                # Dispatch is structurally after the committed context write. Its
                # absence proves this gateway did not send an inference request.
                return self._recover_before_dispatch(row, request)
            result = self.research.reconcile(action_id, {"_sources": context["sources"], "_scope": context["scope"]})
            return self._finish_research(action_id, result)
        except (TransportError, ValueError, KeyError, TypeError, AttributeError):
            return Outcome("unknown", reason="provider reconciliation unavailable")

    def _recover_before_dispatch(self, row, request):
        reference = "not-dispatched:" + row["action_id"]
        released = self.economy.release(run_id=row["run_id"], job_id=row["job_id"],
            action_id=identity(row["run_id"], row["action_id"], "not_dispatched_release"),
            reservation_id=request["reservation_id"])
        if not isinstance(released, Outcome):
            return Outcome("unknown", reason="pre-dispatch reservation cleanup uncertain")
        if released.state == "succeeded":
            return self._save(row["action_id"], Outcome("failed", reason="interrupted before provider dispatch"), actual=0, reference=reference)
        if released.state == "unknown":
            # An already-claimed reservation may only be finalized by its exact
            # action owner. No context proves POST was never reachable here.
            settled = self.economy.settle_execution(reservation_id=request["reservation_id"], action_id=row["action_id"],
                actual_micro_usd=0, evidence_reference=reference, provisional=False)
            if isinstance(settled, Outcome) and settled.state == "succeeded":
                return self._save(row["action_id"], Outcome("failed", reason="interrupted before provider dispatch"), actual=0, reference=reference)
        return Outcome("unknown", reason="pre-dispatch reservation ownership/cleanup requires reconciliation")

    def _acknowledge_messages(self, messages):
        updates = []
        for message in messages:
            if message.get("sender_verified") is not True or not isinstance(message.get("in_reply_to"), str):
                continue
            # A verified reply from the intended recipient is evidence the original
            # arrived, including an ordinary scoped acceptance reply. A model claim
            # or a bare From header never establishes receipt.
            row = self.db.execute("SELECT * FROM tool_actions WHERE rfc_message_id=?", (message["in_reply_to"],)).fetchone()
            row = dict(row) if row else None
            if not row or row["action"] != "gmail.send":
                continue
            action_id = row["action_id"]
            recipient = json.loads(row["request"])["arguments"]["to"]
            if address(message["from"]) != address(recipient):
                continue
            if row["state"] not in {"succeeded", "failed"}:
                self.reconcile(action_id=action_id)
                row = self._row(action_id)
            if row["state"] != "succeeded":
                continue
            confirmed = self.confirm_delivery(action_id=action_id, recipient_email=recipient,
                                  evidence_reference="authenticated-gmail-reply:" + message["message_id"])
            if confirmed.state != "succeeded":
                continue
            updates.append({"action_id": action_id, "job_id": row["job_id"], "delivery_state": "confirmed"})
        return updates

    def confirm_delivery(self, *, action_id, recipient_email, evidence_reference):
        single_line(action_id, "action ID", 300)
        with self._action_lock(action_id) as acquired:
            if not acquired:
                return Outcome("unknown", reason="mail action is still executing or reconciling")
            return self._confirm_delivery(action_id=action_id, recipient_email=recipient_email, evidence_reference=evidence_reference)

    def _confirm_delivery(self, *, action_id, recipient_email, evidence_reference):
        """Trusted control-plane attestation only; never expose as a public/model tool.

        The caller must authenticate the recipient acknowledgment or other actual
        delivery evidence. This records supplied evidence; it does not create it.
        """
        evidence_reference = single_line(evidence_reference, "delivery evidence", 1000)
        recipient_email = address(recipient_email)
        row = self._row(action_id)
        if not row or row["action"] != "gmail.send" or row["state"] != "succeeded":
            raise ValueError("mail acceptance must be reconciled before confirming delivery")
        arguments = json.loads(row["request"])["arguments"]
        if address(arguments["to"]) != recipient_email:
            raise ValueError("delivery recipient mismatch")
        data = json.loads(row["result"])
        single_line(data.get("message_id"), "provider message reference", 300)
        self.db.execute("INSERT OR IGNORE INTO delivery_evidence VALUES (?,?,?,?)", (action_id, evidence_reference, recipient_email, self.clock()))
        data.update({"delivery_state": "confirmed", "delivery_evidence_reference": evidence_reference})
        return self._save(action_id, Outcome("succeeded", data))

    def close(self):
        self.db.close()
