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
    balance = getattr(business.economy, "balance", None)
    rows = [] if callable(balance) or business.mode == "live" or getattr(business, "uses_fixtures", True) is False else business.store.all(
        "SELECT payment FROM orders WHERE run_id=?", (business.run_id,))
    snapshots = [json.loads(row["payment"]) for row in rows if row["payment"] != "{}"]
    for snapshot in snapshots:
        for key in keys:
            value = snapshot.get(key)
            if type(value) is int and value >= 0:
                result[key] += value
    result["costs_final"] = bool(snapshots) and all(s.get("costs_final") is True for s in snapshots)
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


def console_view(business, *, collection=None, offset=0):
    # One WAL read snapshot keeps record pages and run-wide counts consistent
    # while the independent worker commits new states. It does not reserve spend
    # or take a business write lock while reading separate control-plane services.
    owns_snapshot = not business.store.db.in_transaction
    if owns_snapshot:
        business.store.db.execute("BEGIN")
    try:
        result = _console_view(business, collection=collection, offset=offset)
        if owns_snapshot:
            business.store.db.execute("COMMIT")
        return result
    except BaseException:
        if owns_snapshot:
            business.store.db.execute("ROLLBACK")
        raise


def _console_view(business, *, collection=None, offset=0):
    collections = {"opportunities", "orders", "effects", "events"}
    if collection is not None and collection not in collections:
        raise ValueError("unsupported console collection")
    if type(offset) is not int or not 0 <= offset <= 10**9 or collection is None and offset:
        raise ValueError("invalid console page")
    limit = 500
    page_offset = lambda key: offset if key == collection else 0
    run = business.run_id
    totals = {key: business.store.one("SELECT COUNT(*) AS n FROM " + key + " WHERE run_id=?", (run,))["n"] for key in collections}
    opportunities = [_decode(row, ("scope", "assessment")) for row in business.store.all(
        """SELECT * FROM opportunities WHERE run_id=? ORDER BY
        CASE WHEN status IN ('completed','expired','rejected') THEN 1 ELSE 0 END,created_at DESC,id DESC LIMIT ? OFFSET ?""",
        (run, limit, page_offset("opportunities")))]
    quotes = []
    if opportunities:
        ids = [row["id"] for row in opportunities]
        quotes = [_decode(row, ("scope", "plan")) for row in business.store.all(
            "SELECT * FROM quotes WHERE opportunity_id IN (" + ",".join("?" for _ in ids) + ")", ids)]
    orders = []
    for row in business.store.all("""SELECT id FROM orders WHERE run_id=? ORDER BY
        CASE WHEN status IN ('completed','cancelled','failed') THEN 1 ELSE 0 END,created_at DESC,id DESC LIMIT ? OFFSET ?""",
        (run, limit, page_offset("orders"))):
        order = business.order(row["id"])
        order.pop("artifact", None)
        orders.append(_decode(order, ("scope", "plan", "payment")))
    customer_ids = sorted({row["customer_id"] for row in (*opportunities, *orders) if row.get("customer_id")})
    customers = []
    for start in range(0, len(customer_ids), 400):
        batch = customer_ids[start:start+400]
        customers.extend(business.store.all("SELECT id,email,opted_out,created_at FROM customers WHERE run_id=? AND id IN (" +
                                            ",".join("?" for _ in batch) + ")", (run, *batch)))
    effects = business.store.all("""SELECT e.id,e.job_id,e.provider,e.action,e.state,e.attempt,e.reason,
        CASE WHEN e.state IN ('unknown','running','planned','failed') AND NOT EXISTS
        (SELECT 1 FROM effects newer WHERE newer.logical_id=e.logical_id AND newer.attempt>e.attempt)
        THEN 1 ELSE 0 END AS active_issue
        FROM effects e WHERE run_id=? ORDER BY active_issue DESC,created_at DESC,id DESC LIMIT ? OFFSET ?""",
        (run, limit, page_offset("effects")))
    for effect in effects:
        effect["active_issue"] = bool(effect["active_issue"])
    events = business.store.all("""SELECT id,kind,state,due_at,error FROM events WHERE run_id=? ORDER BY
        CASE WHEN state IN ('blocked','running','queued','failed') THEN 0 ELSE 1 END,due_at DESC,id DESC LIMIT ? OFFSET ?""",
        (run, limit, page_offset("events")))
    open_expression = """status NOT IN ('completed','delivered','cancelled','failed') AND
        (delivery_due_at IS NOT NULL OR COALESCE(json_extract(payment,'$.receipts_micro_usd'),0) > COALESCE(json_extract(payment,'$.refunds_micro_usd'),0))"""
    counts = {"opportunities": totals["opportunities"], "orders": totals["orders"],
        "open_commitments": business.store.one("SELECT COUNT(*) AS n FROM orders WHERE run_id=? AND " + open_expression, (run,))["n"],
        "unresolved_effects": business.store.one("""SELECT COUNT(*) AS n FROM effects e WHERE run_id=?
            AND state IN ('unknown','running','planned','failed') AND NOT EXISTS
            (SELECT 1 FROM effects newer WHERE newer.logical_id=e.logical_id AND newer.attempt>e.attempt)""", (run,))["n"],
        "blocked_events": business.store.one("SELECT COUNT(*) AS n FROM events WHERE run_id=? AND state IN ('blocked','failed')", (run,))["n"]}
    for key, states in (("completed_orders", ("completed",)), ("awaiting_payment", ("accepted", "awaiting_payment")),
                        ("in_fulfillment", ("funded", "ready", "delivered", "payment_hold"))):
        counts[key] = business.store.one("SELECT COUNT(*) AS n FROM orders WHERE run_id=? AND status IN (" +
                                        ",".join("?" for _ in states) + ")", (run, *states))["n"]
    pages = {"opportunities": opportunities, "orders": orders, "effects": effects, "events": events}
    pagination = {key: {"offset": page_offset(key), "limit": limit, "total": totals[key],
                        "has_more": page_offset(key) + len(rows) < totals[key], "has_previous": page_offset(key) > 0}
                  for key, rows in pages.items()}
    return {"schema_version": 1, "mode": business.mode, "run_id": run,
            "execution_profile": getattr(business, "execution_profile", business.mode),
            "uses_fixtures": getattr(business, "uses_fixtures", business.mode != "live"),
            "runtime_state": business.runtime.state(run),
            "controls_available": callable(getattr(business.runtime, "change_state", None)),
            "policy": business.policy.document(), "summary": financial_summary(business), "counts": counts, "pagination": pagination,
            "opportunities": opportunities, "quotes": quotes, "orders": orders, "customers": customers,
            "effects": effects, "events": events, "integrations": integration_status(business), "memories": business.recall()}


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
