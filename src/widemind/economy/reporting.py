"""Deliberate financial publication and read-only local consistency checks.

Neither function authenticates a caller, talks to a provider, or repairs records.
The application must authorize run selection before invoking either function.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .service import EconomyError, EconomyService, OPENING_CREDIT, fingerprint, ident


CASH_CATEGORIES = (
    "opening_grant", "owner_funding", "test_funding", "customer_receipt",
    "processor_fee", "operating_charge", "refund", "chargeback",
    "chargeback_reversal", "payment_adjustment", "correction",
)
METRICS = ("earned_revenue_micro_usd", "infrastructure_subsidy_micro_usd")
_PAYMENT_KINDS = {
    "receipt": "customer_receipt", "refund": "refund", "chargeback": "chargeback",
    "chargeback_reversal": "chargeback_reversal", "adjustment": "payment_adjustment",
}


def public_summary(service: EconomyService, run_id: str) -> dict:
    """Publish only fixed names and integer totals from one atomic balance read.

    Unknown future cash categories enter a numeric aggregate, never output keys.
    Live mode describes the run configuration, not independent payment validation.
    """
    balance = service.balance(run_id)
    amounts = {key: balance[key] for key in (
        "posted_micro_usd", "reserved_micro_usd", "held_micro_usd", "available_micro_usd"
    )}
    totals, metrics = balance["totals"], balance["metrics"]
    values = [*amounts.values(), *totals.values(), *(metrics.get(key, 0) for key in METRICS)]
    if (
        balance["mode"] not in ("test", "live")
        or any(type(value) is not int for value in values)
        or amounts["posted_micro_usd"] != sum(totals.values())
        or amounts["available_micro_usd"] != amounts["posted_micro_usd"] - amounts["reserved_micro_usd"] - amounts["held_micro_usd"]
    ):
        raise EconomyError("invalid_projection", "Accounting projection is inconsistent", 503)
    categories = {key: totals.get(key, 0) for key in CASH_CATEGORIES}
    return {
        "version": 1,
        "currency": "USD",
        "unit": "micro-USD",
        "mode": balance["mode"],
        "verification_scope": "local_accounting_only",
        **amounts,
        "exhausted": amounts["available_micro_usd"] <= 0,
        "cash_categories": categories,
        "unclassified_cash_micro_usd": sum(value for key, value in totals.items() if key not in CASH_CATEGORIES),
        "noncash_metrics": {key: metrics.get(key, 0) for key in METRICS},
    }


def audit_integrity(service: EconomyService, run_id: str, *, max_records: int = 100_000) -> dict:
    """Check stored accounting relationships in one bounded, read-only snapshot.

    Check results contain fixed codes and counts, never private record contents.
    A clean result is local consistency evidence, not a provider/bank audit or a
    proof that privileged administrators have never rewritten the database.
    """
    ident(run_id, "run_id")
    if type(max_records) is not int or not 1 <= max_records <= 1_000_000:
        raise EconomyError("invalid_input", "Audit limit must be an integer from 1 to 1000000", 422)
    connection = None
    try:
        path = Path(service.path).absolute()
        connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3, 4, 5):
            return _report(False, [{"code": "supported_schema", "ok": False, "violations": 1}], {}, version)
        run = connection.execute("SELECT mode FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise EconomyError("not_found", "Run not found", 404)
        if run["mode"] not in ("test", "live"):
            return _report(False, [{"code": "run_mode", "ok": False, "violations": 1}], {}, version)
        return _audit_snapshot(connection, run_id, run["mode"], max_records, version)
    except sqlite3.DatabaseError:
        # SQLite error text can contain private file paths or schema/record data.
        raise EconomyError("audit_storage_error", "Accounting audit could not read a consistent database", 503) from None
    finally:
        if connection is not None:
            connection.close()


def _report(complete, checks, inspected, version, mode=None):
    result = {
        "version": 1, "verification_scope": "local_accounting_consistency",
        "complete": complete, "ok": complete and all(item["ok"] for item in checks),
        "database_user_version": version, "checks": checks, "inspected": inspected,
    }
    if mode is not None:
        result["mode"] = mode
    return result


def _audit_snapshot(db, run_id, mode, max_records, version):
    queries = {
        "ledger_entries": "SELECT * FROM ledger WHERE run_id=?",
        "reservations": "SELECT * FROM reservations WHERE run_id=?",
        "holds": "SELECT * FROM holds WHERE run_id=?",
        "charges": "SELECT c.* FROM charges c JOIN reservations r USING(reservation_id) WHERE r.run_id=?",
        "provisional_charges": "SELECT c.* FROM provisional_charges c JOIN reservations r USING(reservation_id) WHERE r.run_id=?",
        "payment_transactions": "SELECT * FROM payment_transactions WHERE run_id=?",
        "payment_components": "SELECT c.* FROM payment_components c LEFT JOIN payment_transactions t USING(provider,account,mode,transaction_id) LEFT JOIN ledger l ON l.entry_id=c.entry_id WHERE t.run_id=? OR l.run_id=?",
        "payments": "SELECT * FROM payments WHERE run_id=?",
        "events": "SELECT * FROM events WHERE run_id=?",
        "reconciliations": "SELECT * FROM reconciliations WHERE run_id=?",
    }
    rows = {}
    count = 0
    for name, query in queries.items():
        arguments = (*([run_id] * query.count("?")), max_records - count + 1)
        rows[name] = [dict(row) for row in db.execute(query + " LIMIT ?", arguments)]
        count += len(rows[name])
        if count > max_records:
            return _report(False, [{"code": "record_limit", "ok": False, "violations": 1}], {}, version, mode)
    inspected = {key: len(value) for key, value in rows.items()}
    failures = {}

    def check(code, valid):
        failures[code] = failures.get(code, 0) + int(not valid)

    def payload(value):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, RecursionError):
            check("structured_records", False)
            return {}
        if not isinstance(parsed, dict):
            check("structured_records", False)
            return {}
        check("structured_records", True)
        return parsed

    def array(value):
        valid = isinstance(value, list)
        check("structured_records", valid)
        return value if valid else []

    numeric_fields = {
        "ledger_entries": ("amount_micro_usd",),
        "reservations": ("amount_micro_usd", "provisional_micro_usd"),
        "holds": ("amount_micro_usd",),
        "charges": ("amount_micro_usd",), "provisional_charges": ("amount_micro_usd",),
        "payment_transactions": ("amount_micro_usd", "fee_micro_usd", "net_micro_usd"),
        "payment_components": ("amount_micro_usd",),
    }
    if any(type(row[field]) is not int for name, fields in numeric_fields.items() for row in rows[name] for field in fields):
        return _report(False, [{"code": "integer_monetary_storage", "ok": False, "violations": 1}], inspected, version, mode)
    if any(type(row[field]) is not int or row[field] < 0 for row in rows["payments"] for field in ("released_micro_usd", "pending_liability_micro_usd", "revision")):
        return _report(False, [{"code": "payment_projection_fields", "ok": False, "violations": 1}], inspected, version, mode)

    ledger = {row["entry_id"]: row for row in rows["ledger_entries"]}
    metadata = {key: payload(row["metadata"]) for key, row in ledger.items()}
    events = [(row, payload(row["data"])) for row in rows["events"]]
    hold_events = {}
    reservation_events = {}
    authorizations = {}
    provisional_events = {}
    states = {"economy.reserved": "reserved", "economy.dispatched": "running", "economy.runtime_not_performed": "reserved", "economy.cost_unknown": "unknown", "economy.charge_settled": "settled", "economy.reservation_cancelled": "cancelled"}
    for row, data in sorted(events, key=lambda item: item[0]["sequence"]):
        if row["type"] == "economy.hold_changed" and isinstance(data.get("hold_id"), str):
            hold_events[data["hold_id"]] = data
        if row["type"] in states and isinstance(data.get("reservation_id"), str):
            reservation_events[data["reservation_id"]] = states[row["type"]]
        if row["type"] == "economy.reserved" and isinstance(data.get("reservation_id"), str):
            authorizations.setdefault(data["reservation_id"], []).append(data)
        if row["type"] == "economy.cost_unknown" and isinstance(data.get("reservation_id"), str) and type(data.get("reported_subtotal_micro_usd")) is int:
            provisional_events[data["reservation_id"]] = data
    holds = {row["hold_id"]: row for row in rows["holds"]}
    for hold_id in holds.keys() | hold_events.keys():
        actual, recorded = holds.get(hold_id), hold_events.get(hold_id)
        check("hold_event_projection", actual is not None and recorded is not None and actual["amount_micro_usd"] == recorded.get("amount_micro_usd") and actual["kind"] == recorded.get("kind"))
    opening = [row for row in ledger.values() if row["kind"] == "opening_grant"]
    check("opening_credit", len(opening) == 1 and opening[0]["amount_micro_usd"] == OPENING_CREDIT and opening[0]["category"] == "opening_grant")
    check("opening_event", sum(row["type"] == "economy.run_opened" and data.get("opening_micro_usd") == OPENING_CREDIT for row, data in events) == 1)

    transactions = {(row["provider"], row["account"], row["mode"], row["transaction_id"]): row for row in rows["payment_transactions"]}
    payments = {(row["provider"], row["account"], row["mode"], row["payment_id"]): row for row in rows["payments"]}
    components = {}
    component_entries = set()
    verified_positive = set()
    covered_transactions = set()
    event_reconciliations = {data["reconciliation_id"]: row["sequence"] for row, data in events if row["type"] == "economy.payment_reconciled" and isinstance(data.get("reconciliation_id"), str)}
    payment_reconciliations = {}
    for row in rows["reconciliations"]:
        data = payload(row["summary"])
        check("reconciliation_events", row["reconciliation_id"] in event_reconciliations)
        payment_key = (row["provider"], row["account"], mode, row["payment_id"])
        check("reconciliation_payment", payment_key in payments)
        payment_reconciliations.setdefault(payment_key, []).append((event_reconciliations.get(row["reconciliation_id"], -1), row, data))
        for transaction_id in array(data.get("posted_transaction_ids", [])):
            if isinstance(transaction_id, str):
                covered_transactions.add((row["provider"], row["account"], mode, transaction_id))
        for observation in array(data.get("observations", [])):
            if isinstance(observation, dict) and observation.get("settled") is True and isinstance(observation.get("transaction_id"), str):
                verified_positive.add((row["provider"], row["account"], mode, observation["transaction_id"]))
    for component in rows["payment_components"]:
        key = tuple(component[name] for name in ("provider", "account", "mode", "transaction_id"))
        transaction = transactions.get(key)
        check("payment_component_transaction", transaction is not None)
        if transaction is None:
            continue
        entry = ledger.get(component["entry_id"])
        amount = transaction["amount_micro_usd"] if component["component"] == "gross" else -transaction["fee_micro_usd"]
        kind = _PAYMENT_KINDS.get(transaction["kind"]) if component["component"] == "gross" else "processor_fee"
        check("payment_component_journal", entry is not None and entry["amount_micro_usd"] == amount == component["amount_micro_usd"] and entry["kind"] == kind and entry["reference"] == transaction["transaction_id"])
        check("payment_component_attribution", entry is not None and all(metadata[entry["entry_id"]].get(name) == transaction[name] for name in ("provider", "account", "mode", "payment_id", "transaction_id")))
        check("positive_settlement_evidence", amount <= 0 or key in verified_positive)
        check("payment_component_events", key in covered_transactions)
        check("payment_component_identity", component["entry_id"] not in component_entries)
        component_entries.add(component["entry_id"])
        components[(key, component["component"])] = component
    release_events = {}
    for row, data in sorted(events, key=lambda item: item[0]["sequence"]):
        if row["type"] != "economy.customer_funds_released":
            continue
        payment_id = data.get("payment_id")
        scoped = [name in data for name in ("provider", "account", "mode")]
        if all(scoped) and all(isinstance(data[name], str) for name in ("provider", "account", "mode")) and isinstance(payment_id, str):
            payment_key = (data["provider"], data["account"], data["mode"], payment_id)
        elif not any(scoped) and isinstance(payment_id, str):
            candidates = [key for key in payments if key[3] == payment_id]
            check("legacy_release_scope", len(candidates) == 1)
            if len(candidates) != 1:
                continue
            payment_key = candidates[0]
        else:
            check("release_event_scope", False)
            continue
        check("release_event_scope", payment_key in payments)
        if payment_key in payments:
            release_events.setdefault(payment_key, []).append((row["sequence"], data))
    payment_net = {}
    for component in rows["payment_components"]:
        transaction_key = tuple(component[name] for name in ("provider", "account", "mode", "transaction_id"))
        transaction = transactions.get(transaction_key)
        if transaction is not None:
            payment_key = (*transaction_key[:3], transaction["payment_id"])
            payment_net[payment_key] = payment_net.get(payment_key, 0) + component["amount_micro_usd"]
    for key, payment in payments.items():
        check("payment_mode", payment["mode"] == mode)
        releases = release_events.get(key, [])
        recorded_release = 0
        for _, data in releases:
            amount = data.get("released_micro_usd")
            valid = type(amount) is int and amount >= recorded_release
            check("release_event_amount", valid)
            if valid:
                recorded_release = amount
        check("payment_release_authorization", payment["released_micro_usd"] == recorded_release)
        reconciliations = sorted(payment_reconciliations.get(key, []), key=lambda item: item[0])
        pending = reconciliations[-1][2].get("pending_liability_micro_usd", 0) if reconciliations else 0
        check("payment_pending_projection", type(pending) is int and pending >= 0 and payment["pending_liability_micro_usd"] == pending)
        check("payment_revision_projection", payment["revision"] == len(reconciliations) + len(releases))
        mutations = [(sequence, record["revision"], True) for sequence, record, _ in reconciliations] + [(sequence, None, False) for sequence, _ in releases]
        for revision, (_, recorded_revision, is_reconciliation) in enumerate(sorted(mutations, key=lambda item: item[0]), 1):
            if is_reconciliation:
                check("reconciliation_revision", type(recorded_revision) is int and recorded_revision == revision)
        hold_id = "payment:" + fingerprint(list(key))
        actual_hold = holds.get(hold_id)
        expected = max(0, payment_net.get(key, 0) - payment["released_micro_usd"], payment["pending_liability_micro_usd"])
        check("payment_hold_projection", (actual_hold is not None and actual_hold["amount_micro_usd"] == expected) or (actual_hold is None and expected == 0 and payment["revision"] == 0))
    for key, transaction in transactions.items():
        check("payment_transaction_binding", (*key[:3], transaction["payment_id"]) in payments and transaction["mode"] == mode)
        check("payment_amount_identity", transaction["amount_micro_usd"] - transaction["fee_micro_usd"] == transaction["net_micro_usd"])
        for name, amount in (("gross", transaction["amount_micro_usd"]), ("fee", -transaction["fee_micro_usd"])):
            check("known_liability_posted", amount >= 0 or (key, name) in components)
            check("settled_component_posted", amount <= 0 or key not in verified_positive or (key, name) in components)
    for entry in ledger.values():
        if entry["kind"] in {*_PAYMENT_KINDS.values(), "processor_fee"}:
            check("payment_journal_coverage", entry["entry_id"] in component_entries)

    costs = {}
    for key, entry in ledger.items():
        if entry["kind"] == "operating_charge":
            reservation_id = metadata[key].get("reservation_id")
            check("operating_charge_identity", isinstance(reservation_id, str))
            if isinstance(reservation_id, str):
                costs[reservation_id] = costs.get(reservation_id, 0) + entry["amount_micro_usd"]
    charges = {row["reservation_id"]: row for row in rows["charges"]}
    provisional = {row["reservation_id"]: row for row in rows["provisional_charges"]}
    reservations = {row["reservation_id"]: row for row in rows["reservations"]}
    for key, reservation in reservations.items():
        check("reservation_event_projection", reservation_events.get(key) == reservation["state"])
        recorded = authorizations.get(key, [])
        limits = payload(reservation["limits_json"])
        check("reservation_authorization", len(recorded) == 1 and all(reservation[name] == recorded[0].get(name) for name in ("amount_micro_usd", "provider", "account", "action_id")) and limits == recorded[0].get("limits"))
        recorded_subtotal = provisional_events.get(key, {}).get("reported_subtotal_micro_usd", 0)
        check("provisional_event_projection", reservation["provisional_micro_usd"] == recorded_subtotal and (reservation["provisional_micro_usd"] == 0 or reservation["state"] in ("unknown", "settled")))
        check("reservation_amount_state", reservation["amount_micro_usd"] >= 0 and reservation["provisional_micro_usd"] >= 0 and reservation["state"] in ("reserved", "running", "unknown", "settled", "cancelled") and ((reservation["state"] == "settled" and type(reservation["actual_micro_usd"]) is int and reservation["actual_micro_usd"] >= 0) or (reservation["state"] != "settled" and reservation["actual_micro_usd"] is None)))
        final = charges.get(key)
        partial = provisional.get(key)
        actual = reservation["actual_micro_usd"] if reservation["state"] == "settled" else reservation["provisional_micro_usd"]
        check("operating_charge_journal", type(actual) is int and costs.get(key, 0) == -actual)
        check("reservation_charge_state", (reservation["state"] == "settled") == (final is not None))
        if final:
            check("final_charge_identity", final["amount_micro_usd"] == actual and (final["provider"], final["account"], final["mode"]) == (reservation["provider"], reservation["account"], mode))
        if partial:
            check("partial_charge_identity", partial["amount_micro_usd"] == reservation["provisional_micro_usd"])
    check("orphan_operating_charge", not (set(costs) - set(reservations)))
    direct_event_entries = {data.get("entry_id") for _, data in events if isinstance(data.get("entry_id"), str)}
    cost_event_reservations = {data["reservation_id"] for row, data in events if row["type"] in ("economy.cost_unknown", "economy.charge_settled") and isinstance(data.get("reservation_id"), str)}
    for key, entry in ledger.items():
        if entry["kind"] == "opening_grant" or key in component_entries:
            continue
        if entry["kind"] == "operating_charge":
            reservation_id = metadata[key].get("reservation_id")
            check("financial_event_coverage", isinstance(reservation_id, str) and reservation_id in cost_event_reservations)
        else:
            check("financial_event_coverage", key in direct_event_entries)
        if entry["kind"] in ("earned_revenue", "infrastructure_subsidy"):
            check("noncash_metric_journal", entry["amount_micro_usd"] == 0 and type(metadata[key].get("reported_micro_usd")) is int)
        if entry["kind"] == "correction":
            root = entry
            visited = set()
            while root is not None and root["kind"] == "correction" and root["entry_id"] not in visited:
                visited.add(root["entry_id"])
                root = ledger.get(root["reference"])
            check("correction_chain", root is not None and root["kind"] != "correction")
            if root is not None and root["kind"] == "operating_charge":
                reservation_id = metadata[root["entry_id"]].get("reservation_id")
                reservation = reservations.get(reservation_id) if isinstance(reservation_id, str) else None
                check("active_provisional_corrections", reservation is not None and reservation["state"] == "settled")
    # Foreign-key errors anywhere in this accounting database are material to its health.
    check("database_foreign_keys", db.execute("PRAGMA foreign_key_check").fetchone() is None)
    checks = [{"code": code, "ok": failures[code] == 0, "violations": failures[code]} for code in sorted(failures)]
    return _report(True, checks, inspected, version, mode)
