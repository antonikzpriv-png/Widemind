"""Durable, bounded work. No model loop, financial ledger, or direct provider access."""
import json
import math
import re
import time
from dataclasses import asdict
from .contracts import Outcome, Plan
from .policy import Policy, integer
from .services import ResearchBrief, single_line, text, web_url
from .store import encode, identity


class Deferred(Exception):
    """Reconcile or await an external event/operator; do not hot-loop."""


class Business:
    def __init__(self, store, *, run_id, economy, tools, runtime, policy=None,
                 services=None, clock=time.time, mode="live"):
        if mode not in {"live", "demo", "test"}:
            raise ValueError("invalid mode")
        self.store, self.run_id = store, text(run_id, "run_id", 200)
        run_id = self.run_id
        self.economy, self.tools, self.runtime, self.clock = economy, tools, runtime, clock
        self.mode = mode
        if mode == "live" and any(getattr(client, "mode", None) in {"demo", "test"}
                                  for client in (economy, tools, runtime)):
            raise ValueError("fixture adapters cannot be used in live mode")
        store.bind_run_mode(run_id, mode)
        self.services = {s.name: s for s in (services if services is not None else [ResearchBrief()])}
        saved = store.one("SELECT document FROM settings WHERE run_id=?", (run_id,))
        self.policy = policy or (Policy(**json.loads(saved["document"])) if saved else Policy())
        if policy is not None:
            self.configure(policy)
        else:
            # Constructing a request-scoped facade must never overwrite a newer
            # operator policy saved after our initial read.
            store.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (run_id, encode(self.policy.document())))

    def close(self):
        """Close request/worker-owned connections once, including shared demo adapters."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        seen = set()
        first_error = None
        for resource in (self.store, self.tools, self.economy, self.runtime):
            if id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    first_error = first_error or exc
        if first_error:
            raise first_error

    def configure(self, policy):
        self.policy = policy
        self.store.db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET document=excluded.document",
                              (self.run_id, encode(policy.document())))

    def _active(self):
        # Fail closed on unknown/missing runtime state.
        return self.runtime.state(self.run_id) in {"active", "waiting"}

    def notify(self, *, source, external_id, kind, payload, due_at=None):
        if kind not in {"select", "offer", "advance", "payment.changed", "gmail.sync", "deadline", "reconcile"}:
            raise ValueError("unsupported event")
        due = self.clock() if due_at is None else due_at
        if type(due) not in {int, float} or not math.isfinite(due) or due < 0:
            raise ValueError("invalid scheduled wakeup time")
        if not isinstance(payload, dict) or len(encode(payload)) > 65536:
            raise ValueError("invalid event payload")
        return self.store.event(self.run_id, text(source, "source", 100),
                                text(external_id, "external_id", 300), kind, payload, due)

    def _enqueue(self, kind, entity, revision, delay=0):
        return self.notify(source="business", external_id=f"{kind}:{entity}:{revision}",
                           kind=kind, payload={"id": entity}, due_at=self.clock() + delay)

    def _customer(self, email):
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", email):
            raise ValueError("invalid customer email")
        email = single_line(email, "customer email", 254).lower()
        customer_id = identity(self.run_id, email)
        self.store.db.execute("INSERT OR IGNORE INTO customers VALUES (?,?,?,0,?)",
                              (customer_id, self.run_id, email, self.clock()))
        return customer_id

    def receive(self, *, source, external_id, service, scope, customer_email=None,
                origin="inbound", source_reference="", priority=1):
        if origin not in {"inbound", "discovered", "outbound"}:
            raise ValueError("invalid opportunity origin")
        integer(priority, "priority", 0, 100)
        text(service, "service", 100)
        text(source, "source", 100)
        text(external_id, "external_id", 300)
        if not isinstance(scope, dict) or len(encode(scope)) > 50000:
            raise ValueError("invalid scope")
        if origin in {"outbound", "discovered"}:
            web_url(source_reference)  # A targeted offer needs a concrete relevance reference.
        opportunity_id = identity(self.run_id, source, external_id)
        with self.store.transaction():
            customer_id = self._customer(customer_email) if customer_email else None
            previous = self.store.one("SELECT * FROM opportunities WHERE id=?", (opportunity_id,))
            expected = (customer_id, service, encode(scope), origin, source_reference, priority)
            if previous:
                actual = tuple(previous[k] for k in ("customer_id", "service", "scope", "origin", "source_reference", "priority"))
                if actual != expected:
                    raise ValueError("opportunity identity reused with different contents")
            else:
                self.store.db.execute("""INSERT INTO opportunities
                  (id,run_id,customer_id,origin,source_reference,service,scope,priority,status,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (opportunity_id, self.run_id, customer_id, origin, source_reference, service,
                   encode(scope), priority, "received", self.clock()))
                self.store.memory(self.run_id, opportunity_id, "request", {"source": source, "reference": source_reference},
                                  self.clock(), customer_id)
            self._enqueue("select", opportunity_id, "received")
        return opportunity_id

    def accept(self, quote_id, *, customer_email, evidence_reference):
        text(evidence_reference, "acceptance evidence", 1000)
        with self.store.transaction():
            quote = self.store.one("""SELECT q.*,o.customer_id,o.service,o.run_id,c.email
              FROM quotes q JOIN opportunities o ON o.id=q.opportunity_id
              JOIN customers c ON c.id=o.customer_id WHERE q.id=? AND o.run_id=?""", (quote_id, self.run_id))
            if not quote or quote["email"] != customer_email.lower():
                raise ValueError("quote/customer mismatch")
            order = self.store.one("SELECT * FROM orders WHERE quote_id=?", (quote_id,))
            if order:
                return order["id"]
            if not self._active() or quote["status"] != "offered" or quote["expires_at"] <= self.clock():
                raise ValueError("quote is not available for acceptance")
            if self._opted_out(quote["customer_id"]):
                raise ValueError("customer opted out")
            open_count = self.store.one("SELECT COUNT(*) AS n FROM orders WHERE run_id=? AND status NOT IN ('completed','cancelled','failed')", (self.run_id,))["n"]
            if open_count >= self.policy.max_open_orders:
                raise ValueError("commitment capacity reached")
            # Acceptance is conditional until settlement and spending authorization.
            order_id = identity(self.run_id, "order", quote_id)
            self.store.db.execute("""INSERT INTO orders
              (id,run_id,quote_id,customer_id,service,scope,status,price_micro_usd,cost_micro_usd,created_at,plan)
              VALUES (?,?,?,?,?,?,'accepted',?,?,?,?)""",
              (order_id, self.run_id, quote_id, quote["customer_id"], quote["service"], quote["scope"],
               quote["price_micro_usd"], quote["cost_micro_usd"], self.clock(), quote["plan"]))
            self.store.db.execute("UPDATE quotes SET status='accepted' WHERE id=?", (quote_id,))
            self.store.db.execute("UPDATE opportunities SET status='ordered' WHERE id=?", (quote["opportunity_id"],))
            self.store.memory(self.run_id, order_id, "acceptance", {"evidence": evidence_reference}, self.clock(), quote["customer_id"])
            self._enqueue("advance", order_id, "accepted")
        return order_id

    def reconsider(self, opportunity_id, *, evidence_reference, scope=None, customer_email=None):
        text(evidence_reference, "revision evidence", 1000)
        with self.store.transaction():
            opportunity = self.store.one("SELECT * FROM opportunities WHERE id=? AND run_id=?", (opportunity_id, self.run_id))
            if not opportunity or opportunity["status"] not in {"needs_review", "budget_wait", "received"}:
                raise ValueError("only unquoted opportunities may be revised")
            revised_scope = json.loads(opportunity["scope"]) if scope is None else scope
            if not isinstance(revised_scope, dict) or len(encode(revised_scope)) > 50000:
                raise ValueError("invalid scope")
            customer = self._customer(customer_email) if customer_email else opportunity["customer_id"]
            self.store.memory(self.run_id, opportunity_id, "revision:" + identity(evidence_reference),
                {"previous_scope": json.loads(opportunity["scope"]), "evidence": evidence_reference}, self.clock(), customer)
            self.store.db.execute("UPDATE opportunities SET scope=?,customer_id=?,status='received' WHERE id=?",
                (encode(revised_scope), customer, opportunity_id))
            self._enqueue("select", opportunity_id, identity(evidence_reference))

    def resolve_hold(self, order_id, *, decision, evidence_reference):
        """Explicit recovery, preserving original delivery deadlines and refund exposure."""
        text(evidence_reference, "resolution evidence", 1000)
        order = self.order(order_id)
        if not self._active() or order["status"] != "payment_hold":
            raise ValueError("order is not an active payment hold")
        snapshot = self._snapshot(order)
        if self.store.one("SELECT 1 FROM effects WHERE job_id=? AND state IN ('running','unknown','planned')", (order_id,)):
            raise ValueError("reconcile outstanding effects first")
        if decision == "resume":
            if not self._paid(order, snapshot) or order["funding_round"] >= 3:
                raise ValueError("cannot resume without verified payment, or retry bound exceeded")
            with self.store.transaction():
                updated = self.store.db.execute("""UPDATE orders SET funding_round=funding_round+1,status=?
                    WHERE id=? AND status='payment_hold' AND funding_round=?""",
                    ("ready" if order["artifact"] else "awaiting_payment", order_id, order["funding_round"]))
                if updated.rowcount != 1:
                    raise ValueError("order was already resolved")
                self._enqueue("advance", order_id, "resume:" + str(order["funding_round"] + 1))
        elif decision == "cancel":
            # Release was reconciled before entering payment_hold. Economy still
            # owns refund processing; cancelling never erases unrefunded receipts.
            self._status(order, "cancelled")
        else:
            raise ValueError("decision must be resume or cancel")
        self.store.memory(self.run_id, order_id, "resolution:" + identity(evidence_reference),
                          {"decision": decision, "evidence": evidence_reference}, self.clock(), order["customer_id"])

    def recall(self, customer_id=None):
        """Private bounded memory retrieval for host planners; never a public projection."""
        return self.store.all("""SELECT entity_id,kind,document,created_at FROM memory
          WHERE run_id=? AND (? IS NULL OR customer_id=?) ORDER BY created_at DESC,id DESC LIMIT 100""",
          (self.run_id, customer_id, customer_id))

    def opt_out(self, email, reference):
        with self.store.transaction():
            customer_id = self._customer(email)
            self.store.db.execute("UPDATE customers SET opted_out=1 WHERE id=?", (customer_id,))
            self.store.memory(self.run_id, customer_id, "opt_out", {"reference": text(reference, "opt-out reference")}, self.clock(), customer_id)

    def _opted_out(self, customer_id):
        return bool(self.store.one("SELECT opted_out FROM customers WHERE id=? AND run_id=?", (customer_id, self.run_id))["opted_out"])

    def control_runtime(self, *, state, evidence_reference, request_id):
        if state not in {"active", "paused", "stopped"}:
            raise ValueError("unsupported operator runtime state")
        evidence_reference = text(evidence_reference, "control evidence", 1000)
        request_id = text(request_id, "control request_id", 100)
        operation = getattr(self.runtime, "change_state", None)
        if not callable(operation):
            raise ValueError("runtime controls are not configured")
        return self._effect("runtime:" + request_id, "control", "runtime", "change_state",
            {"state": state, "evidence_reference": evidence_reference},
            lambda aid: operation(run_id=self.run_id, state=state, action_id=aid, evidence_reference=evidence_reference), control=True)

    def _effect_client(self, provider):
        if provider == "economy":
            return self.economy
        if provider == "tools":
            return self.tools
        if provider == "runtime":
            return self.runtime
        raise ValueError("unknown effect provider")

    def _effect(self, entity, step, provider, action, arguments, invoke, *, reconciliation=False, control=False):
        action_id = identity(self.run_id, entity, step)
        request = encode(arguments)
        logical_id = action_id
        existing = self.store.one("SELECT * FROM effects WHERE logical_id=? ORDER BY attempt DESC LIMIT 1", (logical_id,))
        if existing:
            action_id = existing["id"]
        client = self._effect_client(provider)
        if existing:
            if existing["request"] != request or existing["action"] != action:
                raise ValueError("effect identity reused with changed request")
            if existing["state"] == "planned":
                if reconciliation or (not control and not self._active()):
                    raise Deferred("runtime does not permit retry")
                claimed = self.store.db.execute("UPDATE effects SET state='running' WHERE id=? AND state='planned'", (action_id,))
                if claimed.rowcount != 1:
                    raise Deferred("effect claimed by another worker")
                try:
                    outcome = invoke(action_id)
                except Exception:
                    outcome = Outcome("unknown", reason="adapter exception; reconcile before retry")
            elif existing["state"] in {"succeeded", "failed"}:
                outcome = Outcome(existing["state"], json.loads(existing["result"] or "{}"), existing["reason"] or "")
            else:
                # A prior process may have died before, during, or after the call.
                outcome = client.reconcile(action_id=action_id)
        else:
            if reconciliation or (not control and not self._active()):
                raise Deferred("runtime does not permit new work")
            self.store.db.execute("""INSERT INTO effects
                (id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt)
                VALUES (?,?,?,?,?,?,'running',?,?,0)""",
                (action_id, self.run_id, entity, provider, action, request, self.clock(), logical_id))
            try:
                # The gateway MUST recheck runtime cancellation/capabilities as well.
                outcome = invoke(action_id)
            except Exception:
                outcome = Outcome("unknown", reason="adapter exception; reconcile before retry")
        outcome = self._validated_outcome(outcome, action)
        result = encode(outcome.data)
        self.store.db.execute("UPDATE effects SET state=?,result=?,reason=? WHERE id=?",
                              (outcome.state, result, outcome.reason[:1000], action_id))
        if outcome.state == "unknown":
            raise Deferred("external outcome uncertain; explicit reconciliation required")
        if outcome.state == "failed":
            raise Deferred(f"confirmed action failure: {outcome.reason[:200]}")
        return outcome.data

    @staticmethod
    def _validated_outcome(outcome, action):
        if not isinstance(outcome, Outcome) or not isinstance(outcome.state, str) or outcome.state not in {"succeeded", "failed", "unknown"}:
            return Outcome("unknown", reason="invalid adapter outcome")
        try:
            if not isinstance(outcome.data, dict) or not isinstance(outcome.reason, str):
                raise ValueError("invalid outcome shape")
            if len(encode(outcome.data)) > 2_000_000:
                raise ValueError("result exceeds limit")
            if action == "gmail.send" and outcome.state == "succeeded":
                if outcome.data.get("delivery_state") not in {"confirmed", "accepted", "unknown"}:
                    raise ValueError("missing transport state")
                single_line(outcome.data.get("message_id"), "message_id", 300)
                if "thread_id" in outcome.data:
                    single_line(outcome.data["thread_id"], "thread_id", 300)
        except (TypeError, ValueError, RecursionError):
            return Outcome("unknown", reason="invalid adapter result; reconciliation required")
        return outcome

    def _tool(self, entity, step, action, arguments, reservation=None, maximum=0):
        if not self._active():
            raise Deferred("runtime paused/stopped")
        if not self.tools.available(action):
            raise Deferred(f"tool unavailable: {action}")
        return self._effect(entity, step, "tools", action,
                            {"arguments": arguments, "reservation": reservation, "maximum": maximum},
                            lambda aid: self.tools.execute(run_id=self.run_id, job_id=entity, action_id=aid,
                                action=action, arguments=arguments, reservation_id=reservation,
                                maximum_micro_usd=maximum))

    def _email(self, entity, step, customer_id, subject, body, *, artifact=None, require_confirmation=False):
        if self._opted_out(customer_id):
            raise Deferred("customer opted out; operator must arrange delivery")
        email = self.store.one("SELECT email FROM customers WHERE id=?", (customer_id,))["email"]
        arguments = {"to": email, "subject": subject, "body": body}
        if artifact:
            arguments["attachment"] = artifact
        result = self._tool(entity, step, "gmail.send", arguments)
        # Transactional notices can progress after transport acceptance. Final
        # deliverables still require verified receipt; acceptance never earns revenue.
        permitted = {"confirmed"} if require_confirmation else {"accepted", "confirmed"}
        if result.get("delivery_state") not in permitted or not isinstance(result.get("message_id"), str) or not result["message_id"]:
            raise Deferred("mail accepted or uncertain; delivery not confirmed")
        self._record_mail(entity, step, customer_id, result)
        return result["message_id"]

    def _record_mail(self, entity, step, customer_id, result):
        self.store.db.execute("INSERT OR IGNORE INTO conversations VALUES (?,?,?,?,?,'outbound',?,?,?)",
          (identity(self.run_id, result["message_id"], "outbound"), self.run_id, customer_id,
           result.get("thread_id", result["message_id"]), result["message_id"], step, entity, self.clock()))

    def _assess(self, opportunity):
        service = self.services.get(opportunity["service"])
        if not service or service.capability not in self.policy.capabilities:
            return {"feasible": False, "reason": "unsupported or disabled capability"}
        if not opportunity["customer_id"]:
            return {"feasible": False, "reason": "needs an identified customer"}
        if opportunity["origin"] in {"outbound", "discovered"} and not self.policy.allow_outbound:
            return {"feasible": False, "reason": "outbound disabled"}
        if self._opted_out(opportunity["customer_id"]):
            return {"feasible": False, "reason": "customer opted out"}
        try:
            plan = service.plan(json.loads(opportunity["scope"]), self.policy.max_job_micro_usd)
            integer(plan.maximum_micro_usd, "service cost", 0, self.policy.max_job_micro_usd)
            price = self.policy.prices[service.name]
            if price <= plan.maximum_micro_usd:
                return {"feasible": False, "reason": "nonpositive expected contribution"}
            if not self.tools.available(plan.action) or not self.tools.available("gmail.send"):
                return {"feasible": False, "reason": "required tool unavailable"}
        except (ValueError, KeyError, TypeError):
            return {"feasible": False, "reason": "scope or configured price requires revision"}
        return {"feasible": True, "expected_cost_micro_usd": plan.maximum_micro_usd,
                "price_micro_usd": price, "expected_contribution_micro_usd": price - plan.maximum_micro_usd,
                "goals": list(self.policy.goals), "plan": asdict(plan)}

    def _select(self):
        with self.store.transaction():
            candidates = self.store.all("SELECT * FROM opportunities WHERE run_id=? AND status='received' ORDER BY created_at,id LIMIT 100", (self.run_id,))
            feasible = []
            for opportunity in candidates:
                assessment = self._assess(opportunity)
                self.store.db.execute("UPDATE opportunities SET assessment=?,status=? WHERE id=?",
                    (encode(assessment), "assessed" if assessment["feasible"] else "needs_review", opportunity["id"]))
                self.store.memory(self.run_id, opportunity["id"], "assessment:" + identity(assessment), assessment, self.clock(), opportunity["customer_id"])
                if assessment["feasible"]:
                    score = (self.policy.priority_weight * opportunity["priority"] * 1_000_000 +
                             self.policy.margin_weight * assessment["expected_contribution_micro_usd"] +
                             self.policy.goal_weights.get(opportunity["service"], 0) * 1_000_000)
                    feasible.append((score, opportunity["id"], opportunity, assessment))
            for _, _, opportunity, assessment in sorted(feasible, key=lambda row: (-row[0], row[1])):
                used = self.store.one("SELECT COALESCE(SUM(allowance_micro_usd),0) AS n FROM experiments WHERE run_id=?", (self.run_id,))["n"]
                cost = assessment["expected_cost_micro_usd"]
                if used + cost > self.policy.experiment_budget_micro_usd:
                    self.store.db.execute("UPDATE opportunities SET status='budget_wait' WHERE id=?", (opportunity["id"],))
                    continue
                self.store.db.execute("INSERT INTO experiments VALUES (?,?,?)", (self.run_id, opportunity["id"], cost))
                quote_id = identity(self.run_id, "quote", opportunity["id"])
                self.store.db.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,'pending',?)",
                    (quote_id, opportunity["id"], assessment["price_micro_usd"], cost,
                     self.policy.turnaround_seconds, self.clock() + self.policy.quote_valid_seconds, opportunity["scope"], encode(assessment["plan"])))
                self.store.db.execute("UPDATE opportunities SET status='offering' WHERE id=?", (opportunity["id"],))
                self._enqueue("offer", opportunity["id"], "pending")

    def _offer(self, opportunity_id):
        opportunity = self.store.one("SELECT * FROM opportunities WHERE id=? AND run_id=?", (opportunity_id, self.run_id))
        if not opportunity or opportunity["status"] != "offering":
            return
        quote = self.store.one("SELECT * FROM quotes WHERE opportunity_id=?", (opportunity_id,))
        if quote["expires_at"] <= self.clock():
            self.store.db.execute("UPDATE opportunities SET status='expired' WHERE id=?", (opportunity_id,))
            return
        if not self._assess(opportunity)["feasible"]:
            raise Deferred("policy or capability changed before offer")
        body = encode({"quote_id": quote["id"], "service": opportunity["service"],
            "scope": json.loads(quote["scope"]), "price_micro_usd": quote["price_micro_usd"], "currency": "USD",
            "maximum_delivery_seconds_after_funding": quote["turnaround_seconds"], "expires_at_utc_epoch": quote["expires_at"],
            "terms": "Full settlement and operating authorization required before commitment. No automatic charge. Reply with acceptance quoting quote_id."})
        self._email(opportunity_id, "offer", opportunity["customer_id"], "WideMind scoped offer", body)
        with self.store.transaction():
            self.store.db.execute("UPDATE quotes SET status='offered' WHERE id=?", (quote["id"],))
            self.store.db.execute("UPDATE opportunities SET status='offered' WHERE id=?", (opportunity_id,))

    def _snapshot(self, order):
        snapshot = self.economy.payment(run_id=self.run_id, job_id=order["id"])
        if not isinstance(snapshot, dict) or snapshot.get("status") not in {"pending", "settled", "refunded", "disputed", "partial"}:
            raise Deferred("invalid economy payment snapshot")
        if snapshot.get("run_id") != self.run_id or snapshot.get("job_id") != order["id"]:
            raise Deferred("economy snapshot identity mismatch")
        integer(snapshot.get("revision"), "payment revision")
        previous = json.loads(order["payment"])
        if previous and (snapshot["revision"] < previous["revision"] or
                         snapshot["revision"] == previous["revision"] and snapshot != previous):
            raise Deferred("stale or conflicting economy snapshot")
        if snapshot.get("currency") != "USD" or snapshot.get("mode") != self.mode:
            raise Deferred("payment currency or mode mismatch")
        for key in ("receipts_micro_usd", "refunds_micro_usd", "fees_micro_usd", "earned_micro_usd", "operating_cost_micro_usd"):
            integer(snapshot.get(key), key)
        if snapshot["earned_micro_usd"] > max(0, snapshot["receipts_micro_usd"] - snapshot["refunds_micro_usd"]):
            raise Deferred("earned revenue exceeds unrefunded receipts")
        if type(snapshot.get("costs_final")) is not bool:
            raise Deferred("economy must identify provisional costs")
        if snapshot["earned_micro_usd"] and order["status"] not in {"delivered", "completed"}:
            raise Deferred("economy reported earnings before delivery")
        with self.store.transaction():
            latest = json.loads(self.store.one("SELECT payment FROM orders WHERE id=?", (order["id"],))["payment"])
            if latest and (snapshot["revision"] < latest["revision"] or
                           snapshot["revision"] == latest["revision"] and snapshot != latest):
                raise Deferred("concurrent newer economy snapshot")
            self.store.db.execute("UPDATE orders SET payment=? WHERE id=?", (encode(snapshot), order["id"]))
        return snapshot

    def _paid(self, order, snapshot):
        return (snapshot["status"] == "settled" and
                snapshot["receipts_micro_usd"] - snapshot["refunds_micro_usd"] >= order["price_micro_usd"])

    def _status(self, order, status, **fields):
        allowed = {"artifact", "reservation_id", "delivery_due_at", "delivery_reference", "completed_at"}
        if set(fields) - allowed:
            raise ValueError("invalid status fields")
        with self.store.transaction():
            updates = {"status": status, **fields}
            updated = self.store.db.execute("UPDATE orders SET " + ",".join(k + "=?" for k in updates) +
                " WHERE id=? AND status=? AND funding_round=?",
                (*updates.values(), order["id"], order["status"], order["funding_round"]))
            if updated.rowcount != 1:
                raise Deferred("order changed concurrently; inspect current state before continuing")
            if "delivery_due_at" in fields:
                self.notify(source="business", external_id=f"deadline:{order['id']}", kind="deadline",
                            payload={"id": order["id"]}, due_at=fields["delivery_due_at"])
            if status not in {"completed", "cancelled", "failed", "awaiting_payment", "payment_hold"}:
                self._enqueue("advance", order["id"], f"{status}:{order['funding_round']}")

    def _advance(self, order_id):
        order = self.order(order_id)
        snapshot = self._snapshot(order)
        if order["status"] in {"failed", "cancelled"}:
            self.store.memory(self.run_id, order_id, "financial_update:" + identity(snapshot), snapshot, self.clock(), order["customer_id"])
            return
        if order["status"] == "completed":
            self._evaluate(order, snapshot)
            return
        if order["status"] == "delivered":
            reference = order["delivery_reference"]
            self._effect(order_id, "recognize", "economy", "record_fulfillment", {"reference": reference},
                lambda aid: self.economy.record_fulfillment(run_id=self.run_id, job_id=order_id,
                    action_id=aid, delivery_reference=reference))
            snapshot = self._snapshot(self.order(order_id))
            self._status(order, "completed", completed_at=self.clock())
            self._evaluate(order, snapshot)
            return
        if order["status"] == "funded":
            created = self.store.one("SELECT * FROM effects WHERE logical_id=? ORDER BY attempt DESC LIMIT 1",
                                     (identity(self.run_id, order_id, "create"),))
            if created and created["state"] == "succeeded":
                service = self.services.get(order["service"])
                if service is None:
                    raise Deferred("service validator unavailable for recovery")
                artifact = service.deliverable(json.loads(order["scope"]), json.loads(created["result"]))
                self._validate_artifact(artifact)
                self._status(order, "ready", artifact=encode(artifact))
                return
        # Payment changes never override uncertainty: reconcile in-flight external
        # effects first, even when delivery may already have happened.
        delivery = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='gmail.send' AND request LIKE ? ORDER BY attempt DESC LIMIT 1",
            (order_id, '%"attachment":%'))
        if order["status"] == "ready" and delivery:
            evidence = json.loads(delivery["result"] or "{}")
            checked = self._validated_outcome(Outcome(delivery["state"], evidence), "gmail.send")
            if checked.state == "succeeded" and evidence.get("delivery_state") == "confirmed":
                self._record_mail(order_id, "delivery", order["customer_id"], evidence)
                self._status(order, "delivered", delivery_reference=evidence["message_id"])
                return
            if delivery["state"] in {"running", "unknown"} or (delivery["state"] == "succeeded" and evidence.get("delivery_state") != "confirmed"):
                raise Deferred("delivery requires reconciliation before changing payment obligations")
        if not self._paid(order, snapshot) and order["status"] in {"funded", "ready", "payment_hold"}:
            pending = self.store.all("SELECT * FROM effects WHERE job_id=? AND state IN ('running','unknown')", (order_id,))
            if pending:
                raise Deferred("payment changed with unresolved effects; reconcile first")
            if order["reservation_id"]:
                self._effect(order_id, f"release:{order['funding_round']}", "economy", "release", {"reservation_id": order["reservation_id"]},
                    lambda aid: self.economy.release(run_id=self.run_id, job_id=order_id, action_id=aid,
                        reservation_id=order["reservation_id"]))
            self._status(order, "payment_hold")
            raise Deferred("payment changed; review commitment before resuming")
        if order["status"] == "accepted":
            link = self._effect(order_id, "payment_link", "economy", "payment_link",
                {"amount": order["price_micro_usd"], "currency": "USD"},
                lambda aid: self.economy.payment_link(run_id=self.run_id, job_id=order_id, action_id=aid,
                    amount_micro_usd=order["price_micro_usd"], currency="USD"))
            url = web_url(link.get("url"))
            self._email(order_id, "payment_request", order["customer_id"], "WideMind payment request",
                encode({"order_id": order_id, "payment_url": url, "terms": "Delivery timing starts after verified settlement and budget authorization."}))
            self._status(order, "awaiting_payment")
            self._enqueue("advance", order_id, "initial_payment_check")
        elif order["status"] == "awaiting_payment":
            if not self._paid(order, snapshot):
                return
            service = self.services.get(order["service"])
            if not service or service.capability not in self.policy.capabilities:
                raise Deferred("fulfillment capability disabled")
            plan = Plan(**json.loads(order["plan"]))
            if plan.maximum_micro_usd > self.policy.max_job_micro_usd:
                raise Deferred("configured per-job budget reduced")
            if not self.tools.available(plan.action):
                raise Deferred("fulfillment tool unavailable")
            reservation = self._effect(order_id, f"reserve:{order['funding_round']}", "economy", "reserve", {"maximum": order["cost_micro_usd"]},
                lambda aid: self.economy.reserve(run_id=self.run_id, job_id=order_id, action_id=aid,
                    maximum_micro_usd=order["cost_micro_usd"]))
            reservation_id = text(reservation.get("reservation_id"), "reservation_id", 300)
            quote = self.store.one("SELECT turnaround_seconds FROM quotes WHERE id=?", (order["quote_id"],))
            due = order["delivery_due_at"] or self.clock() + quote["turnaround_seconds"]
            self._status(order, "funded", reservation_id=reservation_id, delivery_due_at=due)
        elif order["status"] == "funded":
            service = self.services.get(order["service"])
            if not service or service.capability not in self.policy.capabilities:
                raise Deferred("fulfillment capability disabled")
            self._email(order_id, "commitment", order["customer_id"], "WideMind delivery commitment",
                encode({"order_id": order_id, "scope": json.loads(order["scope"]),
                        "delivery_due_at_utc_epoch": order["delivery_due_at"]}))
            scope = json.loads(order["scope"])
            plan = Plan(**json.loads(order["plan"]))
            if plan.maximum_micro_usd > self.policy.max_job_micro_usd:
                raise Deferred("configured per-job budget reduced")
            integer(plan.maximum_micro_usd, "cost maximum", 0, order["cost_micro_usd"])
            result = self._tool(order_id, "create", plan.action, plan.arguments, order["reservation_id"], plan.maximum_micro_usd)
            artifact = service.deliverable(scope, result)
            self._validate_artifact(artifact)
            self._status(order, "ready", artifact=encode(artifact))
        elif order["status"] == "ready":
            # Recheck persisted scope-check evidence and attachment shape before delivery.
            artifact = json.loads(order["artifact"])
            self._validate_artifact(artifact)
            if self.services.get(order["service"]) is None or self.services[order["service"]].capability not in self.policy.capabilities:
                raise Deferred("delivery capability disabled")
            reference = self._email(order_id, "delivery", order["customer_id"], "WideMind private delivery",
                encode({"order_id": order_id, "scope": json.loads(order["scope"]), "delivery_due_at_utc_epoch": order["delivery_due_at"]}), artifact=artifact, require_confirmation=True)
            self._status(order, "delivered", delivery_reference=reference)
        elif order["status"] == "payment_hold":
            raise Deferred("payment hold requires operator resolution; never automatically retry")

    @staticmethod
    def _validate_artifact(artifact):
        if not isinstance(artifact, dict):
            raise ValueError("missing deliverable")
        filename = single_line(artifact.get("filename"), "filename", 200)
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError("unsafe artifact filename")
        media_type = single_line(artifact.get("media_type"), "media_type", 100)
        if any(ch.isspace() for ch in media_type) or "/" not in media_type:
            raise ValueError("invalid media type")
        text(artifact.get("content"), "artifact content", 1_000_000)
        if not artifact.get("checks") or not all(v is True or type(v) is int and v > 0 for v in artifact["checks"].values()):
            raise ValueError("scope checks failed")

    def _evaluate(self, order, snapshot):
        self.store.db.execute("UPDATE opportunities SET status='completed' WHERE id=(SELECT opportunity_id FROM quotes WHERE id=?)", (order["quote_id"],))
        self.store.memory(self.run_id, order["id"], "completion", {
            "service": order["service"], "delivery_reference": order["delivery_reference"],
            "lesson": "Delivered scope verified; payment and measured operating costs remain economy-owned. No profitability inferred."},
            self.clock(), order["customer_id"])
        self.store.memory(self.run_id, order["id"], "evaluation:" + identity(snapshot), {
            "receipts_micro_usd": snapshot["receipts_micro_usd"], "earned_micro_usd": snapshot["earned_micro_usd"],
            "measured_contribution_micro_usd": (snapshot["receipts_micro_usd"] - snapshot["refunds_micro_usd"] -
                snapshot["fees_micro_usd"] - snapshot["operating_cost_micro_usd"]) if snapshot["costs_final"] else None,
            "costs_final": snapshot["costs_final"], "payment_status": snapshot["status"],
            "lesson": "Compare verified contribution and delivery quality before repeating this service; provisional costs cannot prove profit."},
            self.clock(), order["customer_id"])
        # Only enum-controlled, explicitly selected fields enter the public projection.
        self.store.db.execute("INSERT OR IGNORE INTO public_activity VALUES (?,?, 'job_completed',?,?)",
          (identity(self.run_id, order["id"], "public_complete"), self.run_id, order["service"], self.clock()))

    def _gmail(self, event):
        batch = self._tool("inbox", event["id"], "gmail.read", {"cursor": event["payload"].get("cursor")})
        messages = batch.get("messages")
        if not isinstance(messages, list) or len(messages) > 100:
            raise ValueError("invalid Gmail batch")
        updates = batch.get("delivery_updates", [])
        if not isinstance(updates, list) or len(updates) > 100:
            raise ValueError("invalid Gmail delivery update batch")
        for update in updates:
            if not isinstance(update, dict):
                continue
            action_id, entity = update.get("action_id"), update.get("job_id")
            effect = self.store.one("SELECT * FROM effects WHERE id=? AND run_id=? AND job_id=? AND action='gmail.send'",
                                    (action_id, self.run_id, entity))
            if not effect:
                continue
            self._reconcile(action_id)
            refreshed = self.store.one("SELECT * FROM effects WHERE id=?", (action_id,))
            outcome = self._validated_outcome(Outcome(refreshed["state"], json.loads(refreshed["result"] or "{}")), "gmail.send")
            if outcome.state == "succeeded" and outcome.data.get("delivery_state") == "confirmed":
                kind = "advance" if self.store.one("SELECT id FROM orders WHERE id=? AND run_id=?", (entity, self.run_id)) else "offer"
                self._enqueue(kind, entity, "receipt:" + action_id)
        for index, message in enumerate(messages):
            try:
                self._gmail_message(message)
            except (ValueError, KeyError, TypeError, AttributeError):
                # One malformed/ambiguous request cannot poison the whole inbox.
                # Retain only a reference for clarification, not raw untrusted text.
                reference = str(message.get("message_id", index))[:300] if isinstance(message, dict) else str(index)
                self.store.memory(self.run_id, event["id"] + ":" + reference, "needs_clarification",
                    {"message_reference": reference, "reason": "invalid request or unconfirmed commercial terms"}, self.clock())
        cursor = batch.get("next_cursor")
        if cursor and cursor != event["payload"].get("cursor"):
            self.notify(source="gmail-cursor", external_id=str(cursor), kind="gmail.sync", payload={"cursor": cursor})

    def _gmail_message(self, message):
        message_id = text(message.get("message_id"), "message_id", 300)
        sender = text(message.get("from"), "sender", 254)
        thread = text(message.get("thread_id"), "thread", 300)
        # Adapter must authenticate sender identity; body is untrusted task data.
        if message.get("sender_verified") is not True:
            return
        seen = self.store.one("SELECT 1 FROM conversations WHERE run_id=? AND message_id=? AND direction='inbound'", (self.run_id, message_id))
        if seen:
            return
        command = message.get("command", {})
        if not isinstance(command, dict):
            raise ValueError("invalid mail command")
        if command.get("type") == "request":
            reference = self.receive(source="gmail", external_id=message_id, service=command.get("service"),
                scope=command.get("scope"), customer_email=sender, source_reference=message_id)
        elif command.get("type") == "accept":
            reference = self.accept(command.get("quote_id"), customer_email=sender, evidence_reference=message_id)
        elif command.get("type") == "opt_out":
            self.opt_out(sender, message_id)
            reference = message_id
        else:
            # Preserve a reference for operator clarification, never pretend we
            # understood an arbitrary free-form message as accepted commercial terms.
            reference = message_id
            self.store.memory(self.run_id, message_id, "needs_clarification", {"message_id": message_id, "thread_id": thread}, self.clock())
        customer = self._customer(sender)
        self.store.db.execute("INSERT OR IGNORE INTO conversations VALUES (?,?,?,?,?,'inbound','customer_message',?,?)",
            (identity(self.run_id, message_id, "inbound"), self.run_id, customer, thread, message_id, reference, self.clock()))

    def tick(self):
        # Pausing never consumes an event. Reconciliation may run while stopped.
        now = self.clock()
        saved = self.store.one("SELECT document FROM settings WHERE run_id=?", (self.run_id,))
        self.policy = Policy(**json.loads(saved["document"]))
        active = self._active()
        with self.store.transaction():
            event = self.store.one("""SELECT * FROM events WHERE run_id=? AND
                ((state='queued' AND due_at<=?) OR (state='running' AND lease_until<=?))
                AND (? OR kind IN ('reconcile','payment.changed')) ORDER BY due_at,id LIMIT 1""", (self.run_id, now, now, active))
            if not event:
                return False
            if event["attempts"] >= 5:
                self.store.db.execute("UPDATE events SET state='failed',error='retry limit reached' WHERE id=?", (event["id"],))
                return True
            self.store.db.execute("UPDATE events SET state='running',lease_until=?,attempts=attempts+1 WHERE id=?", (now + 120, event["id"]))
        event["payload"] = json.loads(event["payload"])
        try:
            kind, entity = event["kind"], event["payload"].get("id")
            if kind == "select":
                self._select()
            elif kind == "offer":
                self._offer(entity)
            elif kind == "payment.changed" and not self._active():
                order = self.order(entity)
                snapshot = self._snapshot(order)
                self.store.memory(self.run_id, entity, "financial_update:" + identity(snapshot), snapshot, self.clock(), order["customer_id"])
                # Preserve a wakeup for resumed operation without doing any action
                # while paused/stopped. Duplicate provider hints remain idempotent.
                self._enqueue("advance", entity, "payment:" + str(snapshot["revision"]))
            elif kind in {"advance", "payment.changed"}:
                self._advance(entity)
            elif kind == "gmail.sync":
                self._gmail(event)
            elif kind == "deadline":
                self._deadline(entity, now)
            elif kind == "reconcile":
                self._reconcile(entity)
            self.store.db.execute("UPDATE events SET state='done',lease_until=NULL,error=NULL WHERE id=?", (event["id"],))
        except (Deferred, ValueError, KeyError, TypeError) as exc:
            # No paid retries or token spinning. New external signal/operator event
            # must explicitly wake blocked work after fixing the cause.
            self.store.db.execute("UPDATE events SET state='blocked',lease_until=NULL,error=? WHERE id=?", (str(exc)[:500], event["id"]))
            self.store.memory(self.run_id, event["id"], "attempt_result", {"outcome": "blocked", "reason": str(exc)[:500],
                "lesson": "Resolve the cause and reconcile external effects before scheduling another attempt."}, self.clock())
        except Exception:
            self.store.db.execute("UPDATE events SET state='blocked',lease_until=NULL,error='adapter unavailable; inspect private diagnostics' WHERE id=?", (event["id"],))
        return True

    def _deadline(self, order_id, now):
        order = self.order(order_id)
        if order["status"] in {"completed", "delivered", "cancelled", "failed"} or order["delivery_due_at"] is None or order["delivery_due_at"] > now:
            return
        delivery = self.store.one("SELECT * FROM effects WHERE job_id=? AND action='gmail.send' AND request LIKE ? ORDER BY attempt DESC LIMIT 1",
                                  (order_id, '%"attachment":%'))
        state = "delivery_overdue"
        if delivery:
            evidence = json.loads(delivery["result"] or "{}")
            checked = self._validated_outcome(Outcome(delivery["state"], evidence), "gmail.send")
            if checked.state == "succeeded" and evidence.get("delivery_state") == "confirmed":
                return
            state = "delivery_confirmation_overdue"
        self.store.memory(self.run_id, order_id, "deadline", {"state": state, "due_at": order["delivery_due_at"]}, now, order["customer_id"])

    def retry_effect(self, action_id, *, evidence_reference):
        """Operator/provider-triggered retry only after a confirmed terminal failure."""
        text(evidence_reference, "retry evidence", 1000)
        if not self._active():
            raise ValueError("runtime does not permit retry")
        with self.store.transaction():
            previous = self.store.one("SELECT * FROM effects WHERE id=? AND run_id=?", (action_id, self.run_id))
            if not previous or previous["state"] != "failed" or previous["attempt"] >= 2:
                raise ValueError("only confirmed failures can be retried, at most twice")
            request = json.loads(previous["request"])
            if previous["provider"] == "tools" and request.get("maximum", 0) > 0:
                raise ValueError("paid work requires a new approved plan and fresh reservation; never reuse a settled authorization")
            next_id = identity(action_id, "retry")
            self.store.db.execute("""INSERT OR IGNORE INTO effects
              (id,run_id,job_id,provider,action,request,state,created_at,logical_id,attempt)
              VALUES (?,?,?,?,?,?,'planned',?,?,?)""",
              (next_id, self.run_id, previous["job_id"], previous["provider"], previous["action"], previous["request"],
               self.clock(), previous["logical_id"], previous["attempt"] + 1))
            self.store.memory(self.run_id, next_id, "retry_authorization", {"evidence": evidence_reference}, self.clock())
            return next_id

    def _reconcile(self, action_id):
        effect = self.store.one("SELECT * FROM effects WHERE id=? AND run_id=?", (action_id, self.run_id))
        if not effect or effect["state"] not in {"running", "unknown", "succeeded"}:
            return
        if effect["state"] == "succeeded":
            previous = json.loads(effect["result"] or "{}")
            if effect["action"] != "gmail.send":
                return
            verified = self._validated_outcome(Outcome("succeeded", previous), "gmail.send")
            if verified.state == "succeeded" and previous.get("delivery_state") == "confirmed":
                return
        client = self._effect_client(effect["provider"])
        result = self._validated_outcome(client.reconcile(action_id=action_id), effect["action"])
        # A transport 'succeeded' response can initially only mean mail accepted.
        # Reconciliation can replace it with confirmed delivery/bounce evidence.
        self.store.db.execute("UPDATE effects SET state=?,result=?,reason=? WHERE id=?",
            (result.state, encode(result.data), result.reason[:1000], action_id))
        if result.state != "unknown":
            self.store.memory(self.run_id, action_id, "reconciliation", {"state": result.state}, self.clock())

    def order(self, order_id):
        order = self.store.one("SELECT * FROM orders WHERE id=? AND run_id=?", (order_id, self.run_id))
        if not order:
            raise ValueError("unknown order")
        payment = json.loads(order["payment"])
        order["unrefunded_receipts_micro_usd"] = max(0, payment.get("receipts_micro_usd", 0) - payment.get("refunds_micro_usd", 0))
        order["delivery_obligation_open"] = (order["status"] not in {"completed", "cancelled", "failed"} and
            (order["delivery_due_at"] is not None or order["unrefunded_receipts_micro_usd"] > 0))
        return order

    def public_activity(self):
        # Registry names cannot inject customer content into public service labels.
        return [{"kind": row["kind"], "service": row["service"] if row["service"] in self.services else "other",
                 "at_utc_epoch": row["created_at"]} for row in self.store.all(
            "SELECT kind,service,created_at FROM public_activity WHERE run_id=? ORDER BY created_at DESC LIMIT 100", (self.run_id,))]

    def diagnostics(self):
        return {"mode": self.mode, "run_id": self.run_id, "runtime_state": self.runtime.state(self.run_id),
                "events": self.store.all("SELECT id,kind,state,due_at,error FROM events WHERE run_id=? ORDER BY due_at", (self.run_id,)),
                "orders": [{key: order[key] for key in ("id", "status", "delivery_due_at", "payment", "delivery_obligation_open", "unrefunded_receipts_micro_usd")}
                    for order in (self.order(row["id"]) for row in self.store.all("SELECT id FROM orders WHERE run_id=?", (self.run_id,)))],
                "opportunities": self.store.all("SELECT id,service,status,assessment FROM opportunities WHERE run_id=?", (self.run_id,))}
