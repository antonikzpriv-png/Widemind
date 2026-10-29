"""Durable creative stages. Paid inference is always an ordinary reserved tool action."""
import hashlib
import json
from .engine import Deferred
from .contracts import Plan
from .services import text
from .store import encode, identity


class CreativeFulfillment:
    def _creative_service(self, order):
        service = self.services.get(order["service"])
        return service if getattr(service, "enhanced", False) else None

    def _assess(self, opportunity):
        result = super()._assess(opportunity)
        service = self._creative_service(opportunity)
        if result.get("feasible") and service and not all(self.tools.available(tool) for tool in service.required_tools):
            return {"feasible": False, "reason": "enhanced generation, rendering or isolated verification is unavailable"}
        return result

    def _creative_job(self, order, service):
        scope = json.loads(order["scope"])
        service.validate_scope(scope)
        plan = Plan(**json.loads(order["plan"]))
        limits = plan.arguments
        if (limits.get("fulfillment_version") != 1 or limits.get("renderer") != service.renderer or
                limits.get("max_revisions") != scope["max_revisions"] or limits.get("max_generations_per_round") != 2 or
                limits.get("per_generation_micro_usd", 0) <= 0 or limits.get("max_output_tokens") != 2048 or
                limits.get("revision_window_seconds") != 172800 or
                plan.maximum_micro_usd != order["cost_micro_usd"] or
                limits["per_generation_micro_usd"] * 2 * (scope["max_revisions"] + 1) != order["cost_micro_usd"]):
            raise Deferred("frozen creative budget or plan is invalid")
        digest = hashlib.sha256(order["scope"].encode()).hexdigest()
        with self.store.transaction():
            job = self.store.one("SELECT * FROM creative_jobs WHERE order_id=?", (order["id"],))
            if job is None:
                self.store.db.execute("INSERT INTO creative_jobs(order_id,run_id,scope_hash,limits_json,updated_at) VALUES (?,?,?,?,?)",
                    (order["id"], self.run_id, digest, encode(limits), self.clock()))
                for revision in range(scope["max_revisions"] + 1):
                    for attempt in range(2):
                        for kind, tool, maximum in (("generate", "ai.generate", limits["per_generation_micro_usd"]), ("verify", service.renderer, 0)):
                            action_id = identity(self.run_id, order["id"], f"creative:{revision}:{attempt}:{kind}")
                            self.store.db.execute("""INSERT INTO creative_stages(id,run_id,order_id,revision,attempt,kind,action_id,tool,maximum_micro_usd,created_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?)""", (action_id, self.run_id, order["id"], revision, attempt, kind, action_id, tool, maximum, self.clock()))
                job = self.store.one("SELECT * FROM creative_jobs WHERE order_id=?", (order["id"],))
            if job["scope_hash"] != digest or job["limits_json"] != encode(limits):
                raise Deferred("accepted scope and budget are immutable; request a new scope change")
        return job, scope, limits

    def _creative_update(self, job, state, **values):
        if set(values) - {"attempt", "errors", "revision_deadline"}:
            raise ValueError("invalid creative state field")
        fields = {"state": state, "updated_at": self.clock(), **values}
        with self.store.transaction():
            updated = self.store.db.execute("UPDATE creative_jobs SET " + ",".join(f"{key}=?" for key in fields) +
                " WHERE order_id=? AND revision=? AND attempt=? AND state=?",
                (*fields.values(), job["order_id"], job["revision"], job["attempt"], job["state"]))
            if updated.rowcount != 1:
                raise Deferred("creative stage changed concurrently")
            self._enqueue("advance", job["order_id"], f"creative:{job['revision']}:{values.get('attempt', job['attempt'])}:{state}")
            if state == "complete" and values.get("revision_deadline"):
                self.notify(source="creative-expiry", external_id=f"{job['order_id']}:{job['revision']}",
                    kind="deadline", payload={"id": job["order_id"]}, due_at=values["revision_deadline"])

    def _stage(self, job, kind):
        stage = self.store.one("SELECT * FROM creative_stages WHERE order_id=? AND revision=? AND attempt=? AND kind=?",
            (job["order_id"], job["revision"], job["attempt"], kind))
        if not stage or not stage["reservation_id"] or stage["state"] == "cancelled":
            raise Deferred("creative stage has no live budget authorization")
        return stage

    def _allocate_creative(self, order):
        # All paid generation/repair/revision slots fit the accepted total. Reserve
        # them before promising work; never pay twice for an interrupted stage.
        for stage in self.store.all("SELECT * FROM creative_stages WHERE order_id=? ORDER BY revision,attempt,kind", (order["id"],)):
            if stage["reservation_id"]:
                continue
            request = {"tool_action_id": stage["action_id"], "tool": stage["tool"], "maximum_micro_usd": stage["maximum_micro_usd"],
                "max_tokens": 2048 if stage["kind"] == "generate" else None}
            reservation = self._effect(order["id"], "creative-reserve:" + stage["id"], "economy", "reserve_action", request,
                lambda aid, request=request: self.economy.reserve_action(run_id=self.run_id, job_id=order["id"], action_id=aid, **request))
            reservation_id = text(reservation.get("reservation_id"), "reservation ID", 300)
            self.store.db.execute("UPDATE creative_stages SET reservation_id=?,state='reserved' WHERE id=? AND reservation_id IS NULL",
                (reservation_id, stage["id"]))

    def _execute_creative(self, order, stage, arguments):
        request = encode(arguments)
        if stage["request_json"] is not None and stage["request_json"] != request:
            raise Deferred("stage inputs changed; reconcile the immutable original action")
        self.store.db.execute("UPDATE creative_stages SET request_json=? WHERE id=? AND request_json IS NULL", (request, stage["id"]))
        if stage["state"] == "complete":
            return json.loads(stage["result_json"])
        step = f"creative:{stage['revision']}:{stage['attempt']}:{stage['kind']}"
        try:
            result = self._tool(order["id"], step, stage["tool"], arguments, stage["reservation_id"], stage["maximum_micro_usd"])
        except Deferred:
            effect = self.store.one("SELECT * FROM effects WHERE id=?", (stage["action_id"],))
            if not effect or effect["state"] != "failed":raise
            # A confirmed terminal validation/tool failure can consume this slot.
            # Unknown effects never enter repair; the next slot has fresh funding.
            result = {**json.loads(effect["result"] or "{}"), "verified": False,
                      "errors": ["A confirmed tool/validation failure prevented this generation or check."]}
        self.store.db.execute("UPDATE creative_stages SET state='complete',result_json=? WHERE id=?", (encode(result), stage["id"]))
        return result

    @staticmethod
    def _design(result):
        value = result.get("json")
        if value is None:
            raw = result.get("text")
            if not isinstance(raw, str) or len(raw.encode()) > 32000:
                raise ValueError("model omitted a bounded design")
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite design")))
        if not isinstance(value, dict) or len(encode(value).encode()) > 32000:
            raise ValueError("model did not supply a bounded design object")
        return value

    def _creative_repair(self, job, errors):
        errors = [str(error)[:500] for error in errors[:20]]
        if job["attempt"] >= 1:
            self._cancel_slots(self.order(job["order_id"]), all_unused=True)
            self._creative_update(job, "needs_review", errors=encode(errors))
            raise Deferred("generation and repair limit reached; existing artifacts and costs preserved")
        self._creative_update(job, "generate", attempt=1, errors=encode(errors))

    def _cancel_slots(self, order, *, revision=None, all_unused=False):
        for stage in self.store.all("SELECT * FROM creative_stages WHERE order_id=? AND state IN ('pending','reserved')", (order["id"],)):
            if not all_unused and (stage["revision"] != revision):
                continue
            effect = self.store.one("SELECT * FROM effects WHERE id=?", (stage["action_id"],))
            if effect and effect["state"] in {"running", "unknown"}:
                raise Deferred("uncertain creative execution must be reconciled before releasing its cap")
            if effect and effect["state"] in {"succeeded", "failed"}:
                request = json.loads(effect["request"])
                if (effect["run_id"] != self.run_id or effect["job_id"] != order["id"] or
                        effect["provider"] != "tools" or effect["action"] != stage["tool"] or
                        request.get("reservation") != stage["reservation_id"] or
                        request.get("maximum") != stage["maximum_micro_usd"] or
                        encode(request.get("arguments")) != stage["request_json"]):
                    raise Deferred("completed creative effect binding requires review")
                # A crash can persist the authoritative tool outcome before the
                # stage update. Preserve its consumed charge instead of trying
                # to cancel a started/settled reservation as though it were free.
                result = json.loads(effect["result"] or "{}")
                if effect["state"] == "failed":
                    result = {**result, "verified": False,
                              "errors": ["A confirmed tool/validation failure prevented this generation or check."]}
                self.store.db.execute("UPDATE creative_stages SET state='complete',result_json=? WHERE id=? AND state IN ('pending','reserved')",
                    (encode(result), stage["id"]))
                continue
            if stage["reservation_id"]:
                self._effect(order["id"], "creative-release:" + stage["id"], "economy", "release",
                    {"reservation_id": stage["reservation_id"]}, lambda aid, stage=stage: self.economy.release(
                        run_id=self.run_id, job_id=order["id"], action_id=aid, reservation_id=stage["reservation_id"]))
            self.store.db.execute("UPDATE creative_stages SET state='cancelled' WHERE id=? AND state IN ('pending','reserved')", (stage["id"],))

    def _manifest(self, order, job, scope, artifact):
        history = self.store.all("SELECT version,manifest FROM artifact_versions WHERE order_id=? ORDER BY version", (order["id"],))
        costs = []
        for stage in self.store.all("SELECT * FROM creative_stages WHERE order_id=? AND state='complete' ORDER BY revision,attempt,kind", (order["id"],)):
            result = json.loads(stage["result_json"])
            accounting = result.get("_accounting", {})
            costs.append({"action_id": stage["action_id"], "reservation_id": stage["reservation_id"], "stage": stage["kind"],
                "revision": stage["revision"], "attempt": stage["attempt"], "maximum_micro_usd": stage["maximum_micro_usd"],
                "charged_micro_usd": accounting.get("charged_micro_usd"), "provisional": accounting.get("provisional"),
                "cost_status": "recorded" if accounting.get("charged_micro_usd") is not None else "unavailable"})
        return {"manifest_version": 1, "artifact_version": job["revision"] + 1, "run_id": self.run_id,
            "order_id": order["id"], "service": order["service"], "scope": scope, "scope_sha256": job["scope_hash"],
            "accepted_budget_micro_usd": order["cost_micro_usd"], "creation_costs": costs,
            "cost_boundary": "Recorded direct tool/model charges; local compute/storage remain disclosed owner infrastructure. Provisional usage is not a final invoice.",
            "files": [{k: f[k] for k in ("filename", "media_type", "encoding", "size", "sha256", "role")} for f in artifact["files"]],
            "checks": artifact["checks"], "created_at": self.clock(), "revision_feedback": job["feedback"],
            "customer_review_notes": artifact.get("review_notes", []),
            "revision_history": [{"version": h["version"], "manifest_sha256": hashlib.sha256(h["manifest"].encode()).hexdigest()} for h in history],
            "quality_boundary": "Automated checks verify observable geometry/rendering or calculator behavior. Arbitrary visual prose and subjective quality remain customer review."}

    def _creative_step(self, order, service, job, scope, limits):
        if not self._active():raise Deferred("runtime paused/stopped")
        if service.capability not in self.policy.capabilities:
            raise Deferred("fulfillment capability disabled")
        if order["cost_micro_usd"] > self.policy.max_job_micro_usd:
            raise Deferred("operator reduced the per-job spending ceiling")
        if job["state"] == "allocating":
            self._allocate_creative(order)
            self._creative_update(job, "generate")
            return
        if job["state"] == "generate":
            stage = self._stage(job, "generate")
            previous = self.store.one("SELECT result_json FROM creative_stages WHERE order_id=? AND kind='generate' AND state='complete' AND (revision<? OR (revision=? AND attempt<?)) ORDER BY revision DESC,attempt DESC LIMIT 1",
                (order["id"], job["revision"], job["revision"], job["attempt"]))
            previous_design = None
            if previous:
                try:previous_design = self._design(json.loads(previous["result_json"]))
                except (ValueError, TypeError, RecursionError):pass
            arguments = service.generation(scope, limits, feedback=job["feedback"], errors=json.loads(job["errors"]), previous=previous_design)
            result = self._execute_creative(order, stage, arguments)
            try:self._design(result)
            except (ValueError, TypeError, RecursionError):
                self._creative_repair(job, ["Model output was not a valid bounded JSON design."]);return
            self._creative_update(job, "verify")
            return
        if job["state"] == "verify":
            generation = self._stage(job, "generate")
            design = self._design(json.loads(generation["result_json"]))
            stage = self._stage(job, "verify")
            result = self._execute_creative(order, stage, {"scope": scope, "design": design})
            if result.get("verified") is not True:
                errors = result.get("errors")
                self._creative_repair(job, errors if isinstance(errors, list) and errors else ["Independent artifact checks failed."]);return
            try:artifact = service.deliverable(scope, result)
            except (ValueError, TypeError, KeyError):
                self._creative_repair(job, ["Artifact contents, required files or digest checks failed."]);return
            manifest = self._manifest(order, job, scope, artifact)
            artifact["manifest"] = manifest
            with self.store.transaction():
                self.store.db.execute("INSERT OR IGNORE INTO artifact_versions VALUES (?,?,?,?,?,?,NULL)",
                    (order["id"], job["revision"] + 1, self.run_id, encode(manifest), encode(artifact), self.clock()))
                # A crash after the insert must recover the exact original
                # version and timestamp, not silently create another manifest.
                persisted = self.store.one("SELECT artifact FROM artifact_versions WHERE order_id=? AND version=?", (order["id"], job["revision"] + 1))
                saved_artifact = json.loads(persisted["artifact"])
                if saved_artifact["files"] != artifact["files"] or saved_artifact["checks"] != artifact["checks"]:
                    raise Deferred("existing immutable artifact version conflicts with recovered output")
                artifact = saved_artifact
            self._cancel_slots(order, revision=job["revision"])
            self._creative_update(job, "ready")
            if job["revision"] == 0 and order["status"] == "funded":
                self._status(order, "ready", artifact=encode(artifact))
            return
        if job["state"] == "ready":
            record = self.store.one("SELECT * FROM artifact_versions WHERE order_id=? AND version=?", (order["id"], job["revision"] + 1))
            artifact = json.loads(record["artifact"])
            self._validate_artifact(artifact)
            step = "delivery" if job["revision"] == 0 else f"delivery:r{job['revision']}"
            reference = self._email(order["id"], step, order["customer_id"], "WideMind verified private delivery",
                encode({"order_id": order["id"], "artifact_version": job["revision"] + 1,
                        "scope_checks": artifact["checks"], "revision_budget_frozen": True}), artifact=artifact)
            with self.store.transaction():
                self.store.db.execute("UPDATE artifact_versions SET published_at=COALESCE(published_at,?) WHERE order_id=? AND version=?", (self.clock(), order["id"], job["revision"] + 1))
                self.store.db.execute("UPDATE orders SET artifact=? WHERE id=?", (encode(artifact), order["id"]))
            deadline = job["revision_deadline"] or self.clock() + limits["revision_window_seconds"]
            self._creative_update(job, "complete", revision_deadline=deadline)
            if self.clock() >= deadline:self._cancel_slots(order, all_unused=True)
            if job["revision"] == 0 and order["status"] == "ready":
                self._status(order, "delivered", delivery_reference=reference)
            return
        if job["state"] == "needs_review":
            raise Deferred("bounded repair attempts exhausted; a new paid scope change or operator review is required")

    def _advance(self, order_id):
        order = self.order(order_id)
        service = self._creative_service(order)
        if not service or order["status"] in {"accepted", "cancelled", "failed", "payment_hold"}:
            return super()._advance(order_id)
        job, scope, limits = self._creative_job(order, service)
        if job["state"] == "expiring":
            # Operator reconciliation wakes the order through advance. Resume
            # the already claimed expiry even if its original event is blocked.
            return self._deadline(order_id, self.clock())
        if job["state"] == "complete" and job["revision_deadline"]:
            # Idempotently repair a historical completed record whose expiry
            # schedule was interrupted before atomic scheduling was introduced.
            self.notify(source="creative-expiry", external_id=f"{order_id}:{job['revision']}",
                kind="deadline", payload={"id": order_id}, due_at=job["revision_deadline"])
        snapshot = self._snapshot(order)
        if not self._paid(order, snapshot):
            if order["status"] == "awaiting_payment":return
            if self.store.one("SELECT 1 FROM effects WHERE job_id=? AND state IN ('running','unknown')", (order_id,)):
                raise Deferred("payment changed; reconcile uncertain effects before releasing unused creative budget")
            self._cancel_slots(order, all_unused=True)
            self.store.db.execute("UPDATE creative_jobs SET state='payment_hold' WHERE order_id=?", (order_id,))
            raise Deferred("verified payment changed; further creative work stopped")
        if order["status"] == "awaiting_payment":
            if not self._active():raise Deferred("runtime paused/stopped")
            self._allocate_creative(order)
            quote = self.store.one("SELECT turnaround_seconds FROM quotes WHERE id=?", (order["quote_id"],))
            self._status(order, "funded", delivery_due_at=order["delivery_due_at"] or self.clock() + quote["turnaround_seconds"])
            return
        # Recover the two cross-record publication crash windows before ever
        # entering the legacy single-action fulfillment path. Only persisted
        # verified artifacts and committed portal publication can advance state.
        if job["revision"] == 0 and order["status"] == "funded" and job["state"] in {"ready", "complete"}:
            saved = self.store.one("SELECT artifact FROM artifact_versions WHERE order_id=? AND version=1", (order_id,))
            if not saved:raise Deferred("verified initial artifact is unavailable for recovery")
            artifact = json.loads(saved["artifact"])
            self._validate_artifact(artifact)
            self._status(order, "ready", artifact=saved["artifact"])
            return
        if job["revision"] == 0 and job["state"] == "complete" and order["status"] == "ready":
            published = self.store.one("SELECT artifact FROM artifact_versions WHERE order_id=? AND version=1 AND published_at IS NOT NULL", (order_id,))
            notification = self.store.one("SELECT id,artifact FROM host_notifications WHERE run_id=? AND entity_id=? AND purpose='delivery'", (self.run_id, order_id))
            if not published or not notification or notification["artifact"] != published["artifact"]:
                raise Deferred("initial private publication requires reconciliation")
            self._status(order, "delivered", delivery_reference=notification["id"])
            return
        if job["state"] == "payment_hold":
            # No cancelled reservation may be resurrected. A newly funded scope
            # change is required after a verified payment loss.
            raise Deferred("creative work remains on payment hold; arrange a separately quoted scope change")
        if job["state"] not in {"complete", "expired"}:
            if order["status"] == "funded":
                self._email(order_id, "commitment", order["customer_id"], "WideMind delivery commitment",
                    encode({"order_id": order_id, "scope": scope, "total_budget_micro_usd": order["cost_micro_usd"],
                            "generation_attempts_per_round": 2, "included_revisions": scope["max_revisions"], "revision_window_seconds": limits["revision_window_seconds"]}))
            return self._creative_step(order, service, job, scope, limits)
        return super()._advance(order_id)

    def request_revision(self, order_id, *, request_id, feedback):
        request_id = text(request_id, "change request ID", 200)
        feedback = text(feedback, "revision feedback", 2000)
        order = self.order(order_id)
        service = self._creative_service(order)
        if not service:raise ValueError("template offerings do not include enhanced revisions")
        job, scope, _ = self._creative_job(order, service)
        request = encode({"kind": "revision", "feedback": feedback})
        key = identity(self.run_id, order_id, request_id)
        with self.store.transaction():
            old = self.store.one("SELECT * FROM customer_change_requests WHERE id=?", (key,))
            if old:
                if old["request_json"] != request:raise ValueError("change request ID reused with different feedback")
                return json.loads(old["result_json"])
            if (job["state"] != "complete" or not job["revision_deadline"] or self.clock() >= job["revision_deadline"] or
                    job["revision"] >= scope["max_revisions"] or order["status"] not in {"delivered", "completed"}):
                raise ValueError("no included revision remains; request a separately quoted scope change")
            if not self._active():raise ValueError("runtime must be resumed before requesting a revision")
            next_revision = job["revision"] + 1
            changed = self.store.db.execute("UPDATE creative_jobs SET revision=?,attempt=0,state='generate',feedback=?,errors='[]',updated_at=? WHERE order_id=? AND state='complete' AND revision=?",
                (next_revision, feedback, self.clock(), order_id, job["revision"]))
            if changed.rowcount != 1:raise ValueError("another revision was already scheduled")
            result = {"scheduled": True, "revision": next_revision, "version": next_revision + 1, "scopeFrozen": True, "budgetMicros": str(order["cost_micro_usd"])}
            self.store.db.execute("INSERT INTO customer_change_requests VALUES (?,?,?,?,?,?)", (key, self.run_id, order_id, request, encode(result), self.clock()))
            self._enqueue("advance", order_id, f"creative:{next_revision}:0:generate")
            return result

    def _deadline(self, order_id, now):
        job = self.store.one("SELECT * FROM creative_jobs WHERE order_id=? AND run_id=?", (order_id, self.run_id))
        if job and job["revision_deadline"] and now >= job["revision_deadline"]:
            with self.store.transaction():
                # Compete with customer revision acceptance under the same write
                # lock before touching any cap. Re-read after waiting for it.
                current = self.store.one("SELECT * FROM creative_jobs WHERE order_id=? AND run_id=?", (order_id, self.run_id))
                if current["state"] not in {"complete", "expired", "expiring", "needs_review", "payment_hold"}:
                    return
                claimed = self.store.db.execute("UPDATE creative_jobs SET state='expiring' WHERE order_id=? AND state=? AND revision=? AND revision_deadline<=?",
                    (order_id, current["state"], current["revision"], now))
                if claimed.rowcount != 1:return
            self._cancel_slots(self.order(order_id), all_unused=True)
            self.store.db.execute("UPDATE creative_jobs SET state='expired' WHERE order_id=? AND state='expiring'", (order_id,))
            return
        return super()._deadline(order_id, now)
