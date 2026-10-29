"""EXPLICIT DEMO/TEST ONLY. Scripted fixtures, not live providers or a real ledger.

No network calls, actual mail, customer, funds, research collection, or revenue.
The fixture's canned payment and usage records are deliberately labelled demo.
"""
import hashlib
import json
import sqlite3
from pathlib import Path
from .business.contracts import Outcome
from .business.engine import Business
from .business.store import Store, encode


def synthetic_research(arguments):
    """Version-two protocol fixture. No page was fetched or model consulted."""
    sources = [
        {"title": "DEMO fixture: SQLite transactions", "url": "https://www.sqlite.org/lang_transaction.html",
         "excerpt": "SQLite supports transactions. Fixture text; not fetched in this demo."},
        {"title": "DEMO fixture: Python sqlite3", "url": "https://docs.python.org/3/library/sqlite3.html",
         "excerpt": "Python provides a sqlite3 interface. Fixture text; not fetched in this demo."},
    ]
    examples = [
        "SQLite keeps application data in a local database file and supports explicit transaction boundaries. A small team can simplify deployment while defining ownership, backup responsibilities and a reversible migration approach.",
        "Write contention and recovery procedures require project-specific testing before adoption. Measure concurrent update workloads, document restore steps and inspect the hosting filesystem instead of assuming suitability from an isolated example.",
        "A deployment budget should distinguish engineering time, storage allocation and support responsibilities. Request measured usage and document the assumptions behind every estimate before comparing alternative implementation choices.",
        "Access controls need an explicit customer-data inventory and a documented retention schedule. Review who can read exported records and use a separately protected backup location for sensitive material.",
        "Operational monitoring should record failed writes, queue latency and restore outcomes. Agree concrete alert ownership and test the response procedure with a controlled exercise before relying on it.",
        "Implementation can start with a small reversible pilot and representative input data. Assign acceptance criteria, review results with the customer and preserve an exit path if the workload exceeds expectations.",
        "Alternative products should be compared against the same requested workload and service obligations. Verify vendor statements independently and identify where evaluation conditions differ from the customer's planned deployment.",
        "A final decision should identify the remaining unknowns and the next evidence to collect. Record responsibility for each follow-up and keep observed results separate from provisional recommendations.",
    ]
    sections = [{"heading": heading,
                 "text": f"DEMO synthetic {heading} finding for {arguments['topic']}: {examples[index % len(examples)]}",
                 "source_urls": [source["url"] for source in sources]}
                for index, heading in enumerate(arguments["sections"])]
    lines = ["# " + arguments["topic"], "", "DEMO synthetic reviewed-research protocol fixture; no live research performed.", ""]
    for section in sections:
        lines.extend(["## " + section["heading"], "", section["text"], ""])
    lines.extend(["## Sources", "", *[source["title"] + ": " + source["url"] for source in sources], ""])
    content = "\n".join(lines)
    artifact = {"path": "demo/research-brief.md", "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "size_bytes": len(content.encode()), "media_type": "text/markdown"}
    return {"synthesis_version": 2, "quality": {"passed": True, "synthetic_fixture": True},
            "checks": {"scope_sections": True, "citations": True, "source_count": len(sources)},
            "sources": sources, "sections": sections,
            "document": {"filename": "research-brief.md", "media_type": "text/markdown", "content": content, "artifact": artifact},
            "plan": dict(arguments), "private_evidence_manifest": {"fixture": "private synthetic evidence; never customer output"}}


class DemoAdapters:
    def __init__(self, path, *, mode="demo"):
        if mode not in {"demo", "test"}:
            raise ValueError("fixtures require explicit demo/test mode")
        self.mode = mode
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("CREATE TABLE IF NOT EXISTS fixture_values (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS fixture_outcomes (id TEXT PRIMARY KEY,document TEXT NOT NULL)")
        self.disabled = set()
        self.unknown_actions = set()
        self.delivery_state = "confirmed"
        self.messages = []
        self.calls = []
        if self.get("state") is None:
            self.set("state", "active")
            self.set("funds", 5_000_000)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM fixture_values WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT INTO fixture_values VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    def state(self, run_id):
        return self.get("state")

    def available(self, action):
        return action in {"gmail.read", "gmail.send", "research.collect", "writing.create"} and action not in self.disabled

    def save(self, action_id, outcome):
        self.db.execute("INSERT OR REPLACE INTO fixture_outcomes VALUES (?,?)", (action_id, encode({"state": outcome.state, "data": outcome.data, "reason": outcome.reason})))
        return outcome

    def reconcile(self, *, action_id):
        row = self.db.execute("SELECT document FROM fixture_outcomes WHERE id=?", (action_id,)).fetchone()
        return Outcome(**json.loads(row[0])) if row else Outcome("unknown", reason="fixture has no evidence")

    def _once(self, action_id, action, build):
        previous = self.reconcile(action_id=action_id)
        if previous.state != "unknown":
            return previous
        self.calls.append((action_id, action))
        result = build()
        self.save(action_id, result)
        if action in self.unknown_actions:
            # Side effect happened, but response was lost. Reconcile recovers it.
            return Outcome("unknown", reason="demo response lost")
        return result

    def reserve(self, *, run_id, job_id, action_id, maximum_micro_usd):
        def result():
            if self.get("funds") < maximum_micro_usd:
                return Outcome("failed", reason="insufficient funds (fixture)")
            self.set("funds", self.get("funds") - maximum_micro_usd)
            return Outcome("succeeded", {"reservation_id": f"demo-reservation-{action_id}"})
        return self._once(action_id, "reserve", result)

    def payment_link(self, *, run_id, job_id, action_id, amount_micro_usd, currency):
        return self._once(action_id, "payment_link", lambda: Outcome("succeeded", {"url": f"https://payments.example.invalid/demo/{job_id}"}))

    def payment(self, *, run_id, job_id):
        return self.get(f"payment:{job_id}", {"status": "pending", "currency": "USD", "mode": self.mode,
            "run_id": run_id, "job_id": job_id, "revision": 0,
            "receipts_micro_usd": 0, "refunds_micro_usd": 0, "fees_micro_usd": 0, "earned_micro_usd": 0, "operating_cost_micro_usd": 0, "costs_final": False})

    def set_payment(self, job_id, *, receipts=2_000_000, refunds=0, status="settled", run_id="test-run"):
        self.set(f"payment:{job_id}", {"status": status, "currency": "USD", "mode": self.mode,
            "run_id": run_id, "job_id": job_id, "revision": self.get(f"payment:{job_id}", {}).get("revision", 0) + 1,
            "receipts_micro_usd": receipts, "refunds_micro_usd": refunds,
            "fees_micro_usd": 0, "earned_micro_usd": max(0, receipts - refunds) if self.get(f"fulfilled:{job_id}") else 0,
            "operating_cost_micro_usd": self.get(f"cost:{job_id}", 0), "costs_final": bool(self.get(f"fulfilled:{job_id}"))})

    def record_fulfillment(self, *, run_id, job_id, action_id, delivery_reference):
        def result():
            self.set(f"fulfilled:{job_id}", delivery_reference)
            payment = self.payment(run_id=run_id, job_id=job_id)
            payment["operating_cost_micro_usd"] = self.get(f"cost:{job_id}", 0)
            payment["costs_final"] = True
            payment["revision"] += 1
            payment["earned_micro_usd"] = max(0, payment["receipts_micro_usd"] - payment["refunds_micro_usd"]) if payment["status"] == "settled" else 0
            self.set(f"payment:{job_id}", payment)
            return Outcome("succeeded", {"recorded": True, "mode": self.mode})
        return self._once(action_id, "record_fulfillment", result)

    def release(self, *, run_id, job_id, action_id, reservation_id):
        return self._once(action_id, "release", lambda: Outcome("succeeded", {"released": True}))

    def execute(self, *, run_id, job_id, action_id, action, arguments, reservation_id, maximum_micro_usd):
        def result():
            if not self.available(action) or self.state(run_id) not in {"active", "waiting"}:
                return Outcome("failed", reason="demo tool disabled or run paused")
            if action == "gmail.read":
                return Outcome("succeeded", {"messages": self.messages, "next_cursor": None})
            if action == "gmail.send":
                return Outcome("succeeded", {"message_id": f"demo-mail-{action_id}", "thread_id": f"demo-thread-{job_id}", "delivery_state": self.delivery_state})
            if not reservation_id or maximum_micro_usd <= 0:
                return Outcome("failed", reason="no bounded reservation")
            self.set(f"cost:{job_id}", maximum_micro_usd // 2)
            if action == "writing.create":
                return Outcome("succeeded", {"text": "DEMO writing fixture"})
            return Outcome("succeeded", synthetic_research(arguments))
        return self._once(action_id, action, result)

    def close(self):
        self.db.close()


def make_demo(directory, *, mode="demo"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(directory / "business.sqlite3")
    adapters = DemoAdapters(directory / "demo-fixtures.sqlite3", mode=mode)
    business = Business(store, run_id="demo-run", economy=adapters, tools=adapters, runtime=adapters, mode=mode)
    return business, adapters


def drain(business, limit=100):
    for _ in range(limit):
        if not business.tick():
            return
    raise RuntimeError("unexpected busy workflow")


def demonstrate(directory):
    business, adapters = make_demo(directory)
    try:
        # Idempotent script can be rerun against the same private directory.
        request = {"message_id": "demo-request-1", "thread_id": "demo-thread-1", "from": "customer@example.invalid", "sender_verified": True,
                   "command": {"type": "request", "service": "research-brief", "scope":
                               {"topic": "DEMO: SQLite for a small application", "sections": ["Overview", "Limitations"], "min_sources": 2}}}
        adapters.messages = [request]
        business.notify(source="demo", external_id="inbox-1", kind="gmail.sync", payload={})
        drain(business)
        quote = business.store.one("SELECT id FROM quotes")
        if not quote:
            raise RuntimeError("demo did not produce a quote")
        adapters.messages = [{"message_id": "demo-accept-1", "thread_id": "demo-thread-1", "from": "customer@example.invalid", "sender_verified": True,
                              "command": {"type": "accept", "quote_id": quote["id"]}}]
        business.notify(source="demo", external_id="inbox-2", kind="gmail.sync", payload={})
        drain(business)
        order = business.store.one("SELECT id FROM orders")
        if not order:
            raise RuntimeError("demo did not produce an order")
        adapters.set_payment(order["id"], run_id="demo-run")
        business.notify(source="demo-economy", external_id="settlement-1", kind="payment.changed", payload={"id": order["id"]})
        drain(business)
        result = business.order(order["id"])
        if result["status"] != "completed":
            raise RuntimeError(f"demo blocked: {encode(business.diagnostics())}")
        artifact = json.loads(result["artifact"])
        target = Path(directory) / "DEMO-research-brief.md"
        target.write_text(artifact["content"])
        return {"mode": "demo", "live_effects": False, "order_id": result["id"], "status": result["status"],
                "artifact": str(target), "payment_fixture": json.loads(result["payment"]),
                "warning": "Scripted fixtures only. No real customer, email, payment, revenue, or live research."}
    finally:
        business.store.close()
        adapters.close()
