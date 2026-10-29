"""Selected public projection and authenticated console read model.

Read requests never perform provider execution or create demo transactions.
Financial totals prefer the authoritative economy; cached per-order values are
explicitly marked and never represented as the run's spendable wallet.
"""
import json


def _decode(row, fields):
    result = dict(row)
    for field in fields:
        if field in result and result[field] is not None:
            result[field] = json.loads(result[field])
    return result


def financial_summary(business):
    keys = ("receipts_micro_usd", "earned_micro_usd", "operating_cost_micro_usd", "fees_micro_usd", "refunds_micro_usd")
    result = {key: 0 for key in keys}
    result.update({"spendable_micro_usd": None, "posted_micro_usd": None, "reserved_micro_usd": None,
                   "holds_micro_usd": None, "currency": "USD", "mode": business.mode,
                   "source": "cached_order_snapshots", "costs_final": False})
    rows = business.store.all("SELECT payment FROM orders WHERE run_id=?", (business.run_id,))
    snapshots = [json.loads(row["payment"]) for row in rows if row["payment"] != "{}"]
    for snapshot in snapshots:
        for key in keys:
            value = snapshot.get(key)
            if type(value) is int and value >= 0:
                result[key] += value
    result["costs_final"] = bool(snapshots) and all(s.get("costs_final") is True for s in snapshots)
    balance = getattr(business.economy, "balance", None)
    if callable(balance):
        try:
            current = balance(run_id=business.run_id)
            mandatory = (*keys, "spendable_micro_usd", "posted_micro_usd", "reserved_micro_usd", "holds_micro_usd")
            valid = (isinstance(current, dict) and current.get("mode") == business.mode and
                     current.get("currency") == "USD" and current.get("run_id", business.run_id) == business.run_id and
                     all(type(current.get(key)) is int for key in mandatory))
            if not valid:
                raise ValueError("incomplete economy balance")
            for key in (*mandatory, "opening_micro_usd", "owner_funding_micro_usd", "subsidies_micro_usd"):
                if type(current.get(key)) is int:
                    result[key] = current[key]
            result["source"] = "economy"
            result["costs_final"] = current.get("costs_final", False) is True
        except Exception:
            for key in list(result):
                if key.endswith("_micro_usd"):
                    result[key] = None
            result.update({"economy_unavailable": True, "source": "unavailable", "costs_final": False})
    elif business.mode == "live" or getattr(business, "uses_fixtures", True) is False:
        for key in list(result):
            if key.endswith("_micro_usd"):
                result[key] = None
        result.update({"economy_unavailable": True, "source": "unavailable", "costs_final": False})

    return result


def integration_status(business):
    configured = getattr(business, "integration_status", None)
    if callable(configured):
        configured = configured()
    if isinstance(configured, list):
        # Host supplies deliberate non-secret readiness fields only.
        return [{"name": str(row.get("name", "integration")), "status": str(row.get("status", "unavailable")),
                 "detail": str(row.get("detail", ""))} for row in configured]
    return [{"name": "Gmail", "status": "demo" if business.mode != "live" else "configured" if business.tools.available("gmail.send") else "unavailable",
             "detail": "Offline fixtures; no real email" if business.mode != "live" else "Dedicated mailbox adapter"},
            {"name": "Research", "status": "demo" if business.mode != "live" else "configured" if business.tools.available("research.collect") else "unavailable",
             "detail": "Offline fixtures; no live research" if business.mode != "live" else "Bounded research gateway"},
            {"name": "Economy", "status": "demo" if business.mode != "live" else "configured",
             "detail": "Scripted payment fixtures" if business.mode != "live" else "Injected accounting service; inspect qualification separately"}]


def console_view(business):
    opportunities = [_decode(row, ("scope", "assessment")) for row in business.store.all(
        "SELECT * FROM opportunities WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 500", (business.run_id,))]
    quotes = [_decode(row, ("scope", "plan")) for row in business.store.all(
        "SELECT q.* FROM quotes q JOIN opportunities o ON o.id=q.opportunity_id WHERE o.run_id=? ORDER BY q.expires_at DESC LIMIT 500", (business.run_id,))]
    orders = []
    for row in business.store.all("SELECT id FROM orders WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 500", (business.run_id,)):
        order = business.order(row["id"])
        order.pop("artifact", None)
        orders.append(_decode(order, ("scope", "plan", "payment")))
    return {"schema_version": 1, "mode": business.mode, "run_id": business.run_id,
            "execution_profile": getattr(business, "execution_profile", business.mode),
            "uses_fixtures": getattr(business, "uses_fixtures", business.mode != "live"),
            "runtime_state": business.runtime.state(business.run_id),
            "controls_available": callable(getattr(business.runtime, "change_state", None)),
            "policy": business.policy.document(), "summary": financial_summary(business),
            "opportunities": opportunities, "quotes": quotes, "orders": orders,
            "customers": business.store.all("SELECT id,email,opted_out,created_at FROM customers WHERE run_id=? ORDER BY created_at DESC LIMIT 500", (business.run_id,)),
            "effects": business.store.all("SELECT id,job_id,provider,action,state,attempt,reason FROM effects WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 500", (business.run_id,)),
            "events": business.store.all("SELECT id,kind,state,due_at,error FROM events WHERE run_id=? ORDER BY due_at DESC LIMIT 500", (business.run_id,)),
            "integrations": integration_status(business), "memories": business.recall()}


def public_view(business, contact_email=None):
    descriptions = {"research-brief": "A private research brief with agreed sections and cited sources."}
    offerings = [{"name": service.name, "description": descriptions.get(service.name, "Scoped work through a configured service."),
                  "price_micro_usd": business.policy.prices[service.name]}
                 for service in business.services.values() if service.capability in business.policy.capabilities and service.name in business.policy.prices]
    # Never expose raw ledger rows, job IDs, customers, conversations, or artifacts.
    return {"mode": business.mode,
            "execution_profile": getattr(business, "execution_profile", business.mode),
            "uses_fixtures": getattr(business, "uses_fixtures", business.mode != "live"), "run_state": business.runtime.state(business.run_id),
            "offerings": offerings, "activity": business.public_activity(),
            "finances": financial_summary(business), "contact_email": contact_email or getattr(business, "contact_email", None)}
