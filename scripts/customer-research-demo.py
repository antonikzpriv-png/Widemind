#!/usr/bin/env python3
"""Offline customer research qualification; all providers and finances are fixtures.

Uses the actual integrated gateway, HTTP adapters, journal and confined files.
The reservation-family fixture models budget splits, not a production ledger.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from widemind.integrated_service import IntegratedTools
from widemind.tools.ai import AIAdapter
from widemind.tools.contracts import ToolError
from widemind.tools.files import FilesAdapter
from widemind.tools.research import ResearchAdapter
from widemind.tools.research_workflow import ResearchWorkflow
from widemind.tools.search import SearchAdapter


SCOPES = {
    "dental": {"topic": "Dental appointment scheduling", "context": "A neighborhood dental clinic needs a clear subscription and calendar rollout decision.",
               "sections": ["Monthly subscription price", "Calendar setup and staff training"],
               "requirements": ["Explain the subscription price.", "Explain calendar setup and staff training."]},
    "bicycle": {"topic": "Bicycle repair booking", "context": "A mobile bicycle repair shop needs transparent workshop fees and export portability.",
                "sections": ["Workshop booking fees", "Repair booking export"],
                "requirements": ["Explain workshop booking fees.", "Explain repair booking export portability."]},
}

PAGES = {
    "dental-a": "Dental appointment scheduling uses a monthly subscription priced at $40. Dental scheduling setup connects the appointment calendar through a local integration.",
    "dental-b": "Dental appointment scheduling has a $40 subscription price per month. Dental appointment setup requires calendar configuration and staff training before deployment.",
    "bicycle-a": "Bicycle repair booking charges a workshop fee of $12. Bicycle repair booking export supports CSV portability for customer repair records.",
    "bicycle-b": "Bicycle repair booking requires a $12 workshop booking payment. Bicycle repair booking export provides CSV files for moving workshop customer records.",
}


class ReservationFamilyFixture:
    """Explicitly synthetic authority fixture; no wallet, revenue or grants."""
    financial_verification = "UNVERIFIED: controlled reservation-family fixture, not production economy settlement"

    def __init__(self):
        self.rows, self.starts, self.settlements, self.events = {}, [], [], {}

    def admit(self, action):
        identity = "parent-" + action["actionId"]
        maximum = int(action["limit"]["maximum"]["micros"])
        self.rows[identity] = {"reservation_id": identity, "run_id": action["runId"], "action_id": action["actionId"],
                               "provider": action["tool"], "account": "dedicated-tools", "amount_micro_usd": maximum,
                               "remaining_micro_usd": maximum, "charged_micro_usd": 0, "state": "reserved",
                               "limits": {"max_duration_ms": action["limit"]["maxDurationMs"],
                                          "max_storage_bytes": action["limit"]["maxOutputBytes"]}}
        return {"reservationId": identity, "runId": action["runId"], "actionId": action["actionId"],
                "limit": action["limit"], "authorization": "explicit-synthetic-financial-fixture"}

    def reservation(self, identity):
        if identity not in self.rows:
            raise ToolError("not_found", "Fixture reservation does not exist")
        return copy.deepcopy(self.rows[identity])

    def reserve_child(self, parent_id, *, action_id, amount_micro_usd, idempotency_key, provider, account, limits):
        identity = "child-" + action_id
        parent = self.rows[parent_id]
        existing = self.rows.get(identity)
        if existing:
            if (existing["parent_reservation_id"], existing["provider"], existing["amount_micro_usd"], existing["limits"]) != (parent_id, provider, amount_micro_usd, limits):
                raise ToolError("conflict", "Immutable fixture child reservation changed")
            return copy.deepcopy(existing)
        if type(amount_micro_usd) is not int or amount_micro_usd < 0 or amount_micro_usd > parent["remaining_micro_usd"]:
            raise ToolError("budget_exhausted", "The held parent job cap cannot fund another child quote")
        parent["remaining_micro_usd"] -= amount_micro_usd
        self.rows[identity] = {"reservation_id": identity, "parent_reservation_id": parent_id,
                               "run_id": parent["run_id"], "action_id": action_id, "provider": provider, "account": account,
                               "amount_micro_usd": amount_micro_usd, "remaining_micro_usd": amount_micro_usd,
                               "charged_micro_usd": 0, "limits": copy.deepcopy(limits), "state": "reserved"}
        return copy.deepcopy(self.rows[identity])

    def reservation_children(self, parent_id):
        return [copy.deepcopy(row) for row in self.rows.values() if row.get("parent_reservation_id") == parent_id]

    def start(self, identity):
        row = self.rows[identity]
        if row["state"] == "reserved":
            row["state"] = "running"
            self.starts.append(identity)
        elif row["state"] not in {"running", "unknown"}:
            raise ToolError("authorization", "Settled fixture reservation cannot start again")
        return copy.deepcopy(row)

    def settle(self, identity, receipt):
        row = self.rows[identity]
        usage = receipt["usage"]
        amount = int(usage["actualCharge"]["micros"])
        event = (identity, usage["externalEventId"], amount)
        if event in self.events:
            return copy.deepcopy(row)
        if amount > row["amount_micro_usd"] or amount < row["charged_micro_usd"]:
            raise ToolError("budget_exhausted", "Measured fixture liability violates the admitted child cap")
        self.events[event] = copy.deepcopy(receipt)
        row["charged_micro_usd"] = amount
        row["state"] = "unknown" if usage["provisional"] else "settled"
        row["remaining_micro_usd"] = row["amount_micro_usd"] - amount if usage["provisional"] else 0
        self.settlements.append((identity, copy.deepcopy(receipt)))
        return copy.deepcopy(row)

    def mark_unknown(self, identity, action_id):
        assert self.rows[identity]["action_id"] == action_id
        self.rows[identity]["state"] = "unknown"


def _prompt_data(body):
    prompt = body["input"]
    separator = prompt.find("\n\n{")
    if separator < 0:
        raise ValueError("Expected the actual synthesis helper's instructions plus JSON evidence")
    return json.loads(prompt[separator + 2:])


def fixture_document(data, *, fabricate=False, omit_conflicts=False):
    """Human-written fixture answers; IDs come from actually retrieved spans."""
    plan = data["frozen_customer_plan"]
    spans = data["offset_verified_evidence"]
    dental = "dental" in plan["topic"].lower()
    required = "Dental" if dental else "Bicycle repair"
    if not spans or not all(required.lower() in span["text"].lower() for span in spans):
        raise ValueError("Fixture synthesis requires actual retrieved customer-specific evidence")
    answers = [
        "Dental appointment scheduling has a monthly subscription price of $40; this is the recurring scheduling cost.",
        "Dental appointment rollout needs calendar integration and staff training, making setup a workflow change rather than only a scheduling subscription purchase.",
    ] if dental else [
        "The bicycle repair workshop booking fee is $12, so the shop can make that fee visible when explaining its repair booking process.",
        "CSV export provides bicycle repair booking portability; moving workshop customer records remains a practical migration task for the shop.",
    ]
    document = {"sections": [], "requirements": [], "conflicts": []}
    for index, section in enumerate(plan["sections"]):
        evidence_ids = data["coverage"][section["id"]]
        document["sections"].append({"id": section["id"], "heading": section["heading"],
                                     "facts": [{"text": answers[index], "evidence_ids": ["E999"] if fabricate else evidence_ids}],
                                     "inferences": [], "unresolved_questions": [],
                                     "requirement_ids": [item["id"] for item in plan["requirements"] if section["id"] in item["section_ids"]]})
    for requirement in plan["requirements"]:
        document["requirements"].append({"id": requirement["id"], "status": "answered", "section_ids": requirement["section_ids"]})
    if not omit_conflicts:
        for conflict in data["recorded_conflicts"]:
            document["conflicts"].append({"id": conflict["id"], "evidence_ids": conflict["evidence_ids"],
                                          "discussion": "Dental appointment scheduling subscription pricing is uncertain: the monthly price differs at $40 versus $60. The subscription price remains unresolved."})
    return document


@contextmanager
def provider_fixtures(*, scenario="valid"):
    """Actual loopback HTTP. No live provider traffic or model invocation."""
    state = {"requests": [], "ai_posts": [], "scenario": scenario, "after_ai_response": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value, content_type="application/json"):
            raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Request-ID", "synthetic-response-" + str(len(state["ai_posts"])))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            request = urlsplit(self.path)
            state["requests"].append(("GET", self.path))
            if request.path == "/search":
                query = parse_qs(request.query)["q"][0]
                customer = "dental" if "dental" in query.lower() else "bicycle"
                count = 1 if scenario == "insufficient" else 2
                results = [{"url": origin + "/source/" + customer + "-" + suffix, "title": customer + " source " + suffix}
                           for suffix in ["a", "b"][:count]]
                self.respond(200, {"results": results})
                return
            key = request.path.removeprefix("/source/")
            text = PAGES.get(key)
            if text is None:
                self.respond(404, {"error": "unknown controlled source"})
                return
            if scenario == "unrelated":
                text = "An astronomy observatory measures distant stars and records telescope images for academic research."
            elif scenario in {"conflict", "conflict_omitted"} and key == "dental-b":
                text = "Dental appointment scheduling uses a monthly subscription priced at $60. Dental appointment setup requires calendar configuration and staff training before deployment."
            self.respond(200, '<html><head><title>' + key + '</title><meta property="article:published_time" content="2026-10-20"></head><body><p>' + text + '</p></body></html>', "text/html")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append(("POST", self.path))
            if self.path != "/v1/responses":
                self.respond(404, {"error": "unknown controlled provider operation"})
                return
            data = _prompt_data(body)
            state["ai_posts"].append({"body": body, "data": data})
            count = len(state["ai_posts"])
            fabricate = scenario == "fabricated" or scenario in {"correction", "budget_correction"} and count == 1
            draft = fixture_document(data, fabricate=fabricate, omit_conflicts=scenario == "conflict_omitted")
            self.respond(200, {"id": "resp_fixture_" + str(count), "status": "completed",
                               "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(draft)}]}],
                               "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300}})
            if state["after_ai_response"]:
                state["after_ai_response"](count)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
    os.environ.update(NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    try:
        yield origin, state
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def config(root, origin, *, corrections=1):
    return {"control_dir": str(root / "control"), "workspace_dir": str(root / "workspace"),
            "policy": {"profile": "sandbox", "allow_external_effects": True, "network": {"trusted_fixture_origins": [origin]}},
            "tools": {
                "research.collect": {"enabled": True, "_dependencies_ready": True, "source_mode": "search", "allow_local_http": True,
                                     "offline_fixture_label": "Synthetic sources and human-written model responses; no live customer, search, inference or payment is verified.",
                                     "max_sources": 2, "max_search_queries": 2, "max_source_chars": 12000,
                                     "max_correction_passes": corrections, "max_output_tokens": 1000, "max_prompt_bytes": 50000},
                "research.search": {"enabled": True, "provider": "searxng", "endpoint": origin + "/search", "allow_local_http": True,
                                    "trusted_fixture_origins": [origin], "request_microusd": 0, "allocation": "Explicit synthetic owner-subsidized search fixture"},
                "research.fetch": {"enabled": True, "trusted_fixture_origins": [origin]},
                "ai.generate": {"enabled": True, "endpoint": origin + "/v1", "allow_local_http": True,
                                "model": "synthetic-human-written-fixture", "secret_ref": "fixture-model-key",
                                "model_context_tokens": 20000, "max_output_tokens": 1000, "max_input_bytes": 100000,
                                "max_output_bytes": 50000, "maximum_microusd": 100,
                                "tariff": {"input_microusd_per_million_tokens": 1000, "output_microusd_per_million_tokens": 2000}},
                "files": {"enabled": True}}}


def application(owner_config, economy):
    app = IntegratedTools(owner_config, economy, adapters=[ResearchWorkflow(), SearchAdapter(), ResearchAdapter(), AIAdapter(), FilesAdapter()])
    app.secrets.set("fixture-model-key", "synthetic-fixture-only-provider-key")
    return app


def request(customer, *, maximum=100, action_id=None):
    args = {**copy.deepcopy(SCOPES[customer]), "min_sources": 2, "as_of": "2026-10-29", "max_age_days": 30}
    identity = action_id or "offline-" + customer
    return {"version": 1, "actionId": identity, "runId": "offline-research-run", "jobId": "customer-" + customer,
            "workKey": identity, "tool": "research.collect", "input": args,
            "limit": {"maximum": {"currency": "USD", "micros": str(maximum)}, "maxDurationMs": 120000, "maxOutputBytes": 1000000}}


def run_demo(output):
    output = Path(output).absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.stat().st_mode & 0o077:
        raise ValueError("The offline output directory must be private (mode 0700)")
    if any(output.iterdir()):
        raise ValueError("Use a fresh empty output directory for this isolated offline demonstration")
    results = []
    with provider_fixtures() as (origin, providers):
        for customer in SCOPES:
            root = output / customer
            economy = ReservationFamilyFixture()
            app = application(config(root, origin), economy)
            action = request(customer)
            reservation = economy.admit(action)
            outcome = app.execute({"action": action, "reservation": reservation})
            if outcome.get("kind") != "receipt" or outcome["receipt"]["status"] != "succeeded":
                raise RuntimeError("Offline research did not complete: " + json.dumps(outcome))
            receipt = outcome["receipt"]
            economy.settle(reservation["reservationId"], receipt)
            data = receipt["result"]["data"]
            results.append({"customer": customer, "topic": data["plan"]["topic"],
                            "document_path": str(app.workspace.root / data["document"]["artifact"]["path"]),
                            "manifest_path": str(app.workspace.root / data["evidence_manifest"]["artifact"]["path"]),
                            "questions": [section["question"] for section in data["plan"]["sections"]],
                            "paid_child_receipts": [r for r in data["child_receipts"] if r["tool"] == "ai.generate"],
                            "checks": data["checks"], "receipt": receipt})
        summary = {"mode": "OFFLINE CONTROLLED FIXTURES", "financial_verification": ReservationFamilyFixture.financial_verification,
                   "provider_verification": "Synthetic local HTTP search, pages and human-written Responses fixtures; no live inference or independent revenue",
                   "customers": results, "ai_http_posts": len(providers["ai_posts"])}
        path = output / "offline-summary.json"
        path.write_text(json.dumps(summary, indent=2))
        return path, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Private local output directory for this synthetic demonstration")
    args = parser.parse_args()
    path, summary = run_demo(args.output)
    print(json.dumps({"mode": summary["mode"], "financial_verification": summary["financial_verification"],
                      "summary": str(path), "customers": [item["document_path"] for item in summary["customers"]]}, indent=2))
