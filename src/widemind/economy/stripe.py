"""Stripe Checkout and settlement adapter.

All accounting inputs below originate in authenticated Stripe reads. Signed
webhooks are wake-ups, not receipts. Fixtures use HTTPX transports; production
requests always use Stripe's TLS endpoint and an explicit account/mode.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .service import EconomyError

API_VERSION = "2024-06-20"
MAX_WEBHOOK_BYTES = 1_048_576
MAX_PAGES = 100
MICRO_PER_CENT = 10_000


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EconomyError("invalid_provider_record", f"Stripe {name} is not an integer", 502)
    return value


def _identifier(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 255:
        raise EconomyError("invalid_provider_record", f"Invalid Stripe {prefix} identifier", 502)
    if not value.isascii() or any(not (c.isalnum() or c == "_") for c in value):
        raise EconomyError("invalid_provider_record", "Malformed Stripe identifier", 502)
    return value


def _id(value: Any) -> str | None:
    return value.get("id") if isinstance(value, dict) else value


class StripePayments:
    """A configured Stripe business account. Keep this instance outside agent access."""

    def __init__(self, service, *, api_key: str, account: str, mode: str,
                 webhook_secret: str, success_url: str, cancel_url: str,
                 client: httpx.Client | None = None, clock=time.time):
        if mode not in {"test", "live"}:
            raise EconomyError("invalid_config", "Stripe mode must be test or live", 422)
        if not api_key.startswith((f"sk_{mode}_", f"rk_{mode}_")):
            raise EconomyError("invalid_config", "Stripe secret key does not match the configured mode", 422)
        _identifier(account, "acct_")
        if not webhook_secret.startswith("whsec_") or len(webhook_secret) < 12:
            raise EconomyError("invalid_config", "A Stripe webhook signing secret is required", 422)
        for url in (success_url, cancel_url):
            parsed = urlsplit(url)
            sandbox_loopback = mode == "test" and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if not parsed.hostname or parsed.username or parsed.password or not (parsed.scheme == "https" or sandbox_loopback):
                raise EconomyError("invalid_config", "Checkout return URLs require HTTPS (sandbox loopback permits HTTP)", 422)
        self.service = service
        self.account = account
        self.mode = mode
        self.webhook_secret = webhook_secret
        self.success_url = success_url
        self.cancel_url = cancel_url
        self.clock = clock
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=False)
        self._headers = {"Authorization": f"Bearer {api_key}", "Stripe-Account": account, "Stripe-Version": API_VERSION}
        self._verified = False

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _request(self, method: str, path: str, *, params=None, data=None, idempotency_key=None) -> dict:
        headers = dict(self._headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self.client.request(method, f"https://api.stripe.com/v1{path}", headers=headers, params=params, data=data)
        except httpx.HTTPError:
            # Do not include request/response bodies, keys, customer records or
            # URLs in public errors. The durable checkout claim survives this.
            raise EconomyError("provider_unavailable", "Stripe outcome is unknown; reconcile before retrying", 503) from None
        if response.status_code == 429 or response.status_code >= 500:
            raise EconomyError("provider_unavailable", "Stripe is temporarily unavailable; reconciliation remains pending", 503)
        if response.status_code >= 400:
            raise EconomyError("provider_rejected", f"Stripe rejected the request (HTTP {response.status_code})", 502)
        try:
            result = response.json()
        except ValueError:
            raise EconomyError("invalid_provider_record", "Stripe returned invalid JSON", 502) from None
        if not isinstance(result, dict):
            raise EconomyError("invalid_provider_record", "Stripe returned an unexpected response", 502)
        requested_id = path.rsplit("/", 1)[-1]
        if method == "GET" and requested_id.startswith(("cs_", "pi_", "ch_", "re_", "dp_", "txn_")) and result.get("id") != requested_id:
            raise EconomyError("invalid_provider_record", "Stripe response identity differs from the requested object", 502)
        return result

    def verify_setup(self) -> dict:
        account = self._request("GET", "/account")
        if account.get("id") != self.account:
            raise EconomyError("provider_account_mismatch", "Stripe credentials do not identify the configured account", 502)
        self._verified = True
        return {"provider": "stripe", "account": self.account, "mode": self.mode,
                "charges_enabled": account.get("charges_enabled") is True,
                "details_submitted": account.get("details_submitted") is True}

    def _ensure_setup(self) -> None:
        if not self._verified:
            self.verify_setup()

    def _assert_mode(self, record: dict, *, required=True) -> None:
        if not required and "livemode" not in record:
            return
        if record.get("livemode") is not (self.mode == "live"):
            raise EconomyError("provider_mode_mismatch", "Stripe object is from a different mode", 502)

    def create_checkout(self, run_id: str, request_id: str, job_id: str | None,
                        amount_micro_usd: int, description: str = "WideMind order") -> dict:
        if isinstance(amount_micro_usd, bool) or not isinstance(amount_micro_usd, int) or amount_micro_usd <= 0 or amount_micro_usd % MICRO_PER_CENT:
            raise EconomyError("invalid_amount", "Stripe USD checkout amounts must be positive whole cents (10,000 micro-USD)", 422)
        if amount_micro_usd < 500_000 or amount_micro_usd > 99_999_999 * MICRO_PER_CENT:
            raise EconomyError("invalid_amount", "Stripe checkout requires USD 0.50 to USD 999,999.99", 422)
        if not isinstance(description, str) or not description.strip() or len(description) > 200:
            raise EconomyError("invalid_description", "Checkout description must contain 1 to 200 characters", 422)
        details = {"description": description, "success_url": self.success_url, "cancel_url": self.cancel_url}
        claim = self.service.record_checkout(run_id, "stripe", self.account, self.mode,
                                             request_id, job_id, amount_micro_usd, details)
        if claim.get("payment_id") and claim.get("url"):
            return claim
        created_at = claim.get("created_at")
        if not isinstance(created_at, str):
            raise EconomyError("checkout_reconciliation_required", "Checkout creation time is unavailable; manual reconciliation is required", 409)
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise EconomyError("checkout_reconciliation_required", "Checkout creation time is invalid", 409) from None
        if self.clock() - created > 23 * 3600:
            raise EconomyError("checkout_reconciliation_required", "Checkout request exceeded the safe Stripe retry window; reconcile it manually", 409)
        self._ensure_setup()
        request_key = hashlib.sha256(json.dumps(["stripe", self.account, self.mode, run_id, request_id], separators=(",", ":")).encode()).hexdigest()
        record = self._request("POST", "/checkout/sessions", idempotency_key=f"widemind-{request_key}", data={
            "mode": "payment", "success_url": self.success_url, "cancel_url": self.cancel_url,
            "client_reference_id": request_key, "metadata[widemind_request]": request_key,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(amount_micro_usd // MICRO_PER_CENT),
            "line_items[0][price_data][product_data][name]": description,
            "line_items[0][quantity]": "1",
        })
        self._assert_mode(record)
        payment_id = _identifier(record.get("id"), "cs_")
        url = record.get("url")
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or urlsplit(url).hostname != "checkout.stripe.com":
            raise EconomyError("invalid_provider_record", "Stripe did not return a hosted checkout URL", 502)
        return self.service.finish_checkout("stripe", self.account, self.mode, request_id, payment_id, url)

    def verify_webhook(self, raw_body: bytes, signature: str) -> tuple[dict, str]:
        if not isinstance(raw_body, bytes) or len(raw_body) > MAX_WEBHOOK_BYTES or not isinstance(signature, str) or len(signature) > 8192:
            raise EconomyError("invalid_signature", "Invalid Stripe webhook body or signature", 400)
        timestamps, signatures = [], []
        for part in signature.split(","):
            key, separator, value = part.strip().partition("=")
            if separator and key == "t":
                timestamps.append(value)
            elif separator and key == "v1":
                signatures.append(value)
        if len(timestamps) != 1 or not timestamps[0].isascii() or not timestamps[0].isdigit() or len(timestamps[0]) > 12:
            raise EconomyError("invalid_signature", "Invalid Stripe webhook timestamp", 400)
        timestamp = int(timestamps[0])
        if abs(self.clock() - timestamp) > 300:
            raise EconomyError("invalid_signature", "Stripe webhook timestamp is outside the allowed window", 400)
        expected = hmac.new(self.webhook_secret.encode(), timestamps[0].encode() + b"." + raw_body, hashlib.sha256).hexdigest()
        if not any(len(value) == 64 and value.isascii() and all(c in "0123456789abcdef" for c in value) and hmac.compare_digest(value, expected) for value in signatures):
            raise EconomyError("invalid_signature", "Stripe webhook signature verification failed", 400)
        try:
            event = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            raise EconomyError("invalid_webhook", "Stripe webhook body is not valid JSON", 400) from None
        if not isinstance(event, dict):
            raise EconomyError("invalid_webhook", "Stripe webhook is not an event object", 400)
        _identifier(event.get("id"), "evt_")
        self._assert_mode(event)
        if event.get("account", self.account) != self.account:
            raise EconomyError("provider_account_mismatch", "Stripe event is for a different account", 400)
        return event, hashlib.sha256(raw_body).hexdigest()

    def handle_webhook(self, raw_body: bytes, stripe_signature: str) -> dict:
        event, digest = self.verify_webhook(raw_body, stripe_signature)
        event_type = event.get("type", "")
        if not isinstance(event_type, str) or len(event_type) > 200:
            raise EconomyError("invalid_webhook", "Stripe event type is invalid", 400)
        obj = event.get("data", {}).get("object") if isinstance(event.get("data"), dict) else None
        if not isinstance(obj, dict):
            raise EconomyError("invalid_webhook", "Stripe event object is missing", 400)
        if event_type.startswith("checkout.session."):
            payment_id = _identifier(obj.get("id"), "cs_")
        elif event_type.startswith("payment_intent."):
            payment_id = self._session_for_intent(_identifier(obj.get("id"), "pi_"))
        elif event_type.startswith("charge.dispute."):
            dispute = self._request("GET", f"/disputes/{_identifier(obj.get('id'), 'dp_')}")
            payment_id = self._session_for_charge(_identifier(_id(dispute.get("charge")), "ch_"))
        elif event_type.startswith("refund.") or event_type == "charge.refund.updated":
            refund = self._request("GET", f"/refunds/{_identifier(obj.get('id'), 're_')}")
            payment_id = self._session_for_charge(_identifier(_id(refund.get("charge")), "ch_"))
        elif event_type.startswith("charge."):
            payment_id = self._session_for_charge(_identifier(obj.get("id"), "ch_"))
        else:
            return {"accepted": True, "handled": False, "reason": "unsupported_event", "event_id": event["id"]}
        self._ensure_setup()
        result = self.reconcile(payment_id, event_id=event["id"], event_digest=digest)
        return {"accepted": True, "handled": True, "event_id": event["id"], "payment": result}

    def _list(self, path: str, params: dict) -> list[dict]:
        records, seen = [], set()
        cursor = None
        for _ in range(MAX_PAGES):
            query = {**params, "limit": 100}
            if cursor:
                query["starting_after"] = cursor
            page = self._request("GET", path, params=query)
            data = page.get("data")
            if not isinstance(data, list) or not isinstance(page.get("has_more"), bool) or any(not isinstance(item, dict) for item in data):
                raise EconomyError("invalid_provider_record", "Stripe returned an invalid paginated response", 502)
            for item in data:
                if not isinstance(item.get("id"), str) or item["id"] in seen:
                    raise EconomyError("invalid_provider_record", "Stripe pagination repeated a record", 502)
                seen.add(item["id"])
                records.append(item)
            if not page["has_more"]:
                return records
            if not data:
                raise EconomyError("incomplete_provider_snapshot", "Stripe pagination ended before all records were returned", 502)
            cursor = data[-1]["id"]
        raise EconomyError("incomplete_provider_snapshot", "Stripe pagination limit reached; no partial snapshot was posted", 502)

    def _session_for_charge(self, charge_id: str) -> str:
        self._ensure_setup()
        charge = self._request("GET", f"/charges/{charge_id}")
        self._assert_mode(charge)
        return self._session_for_intent(_identifier(_id(charge.get("payment_intent")), "pi_"))

    def _session_for_intent(self, intent_id: str) -> str:
        self._ensure_setup()
        matches = []
        for session in self._list("/checkout/sessions", {"payment_intent": intent_id}):
            self._assert_mode(session)
            session_id = _identifier(session.get("id"), "cs_")
            try:
                self.service.payment("stripe", self.account, self.mode, session_id)
            except EconomyError as error:
                if error.status == 404:
                    continue
                raise
            matches.append(session_id)
        if len(matches) != 1:
            raise EconomyError("unmapped_payment", "Stripe payment does not have exactly one registered WideMind checkout", 404 if not matches else 409)
        return matches[0]

    def _transaction(self, value, kind: str, *, sources: set[str] | None = None) -> dict | None:
        if not value:
            return None
        transaction = value if isinstance(value, dict) else self._request("GET", f"/balance_transactions/{_identifier(value, 'txn_')}")
        _identifier(transaction.get("id"), "txn_")
        source_id = _id(transaction.get("source"))
        if source_id is not None and (not isinstance(source_id, str) or (sources is not None and source_id not in sources)):
            raise EconomyError("invalid_provider_record", "Stripe balance transaction belongs to a different source", 502)
        if transaction.get("currency") != "usd":
            raise EconomyError("unsupported_currency", "Only verified USD Stripe settlement is supported; no implicit FX credit was posted", 409)
        amount = _integer(transaction.get("amount"), "balance amount")
        fee = _integer(transaction.get("fee"), "balance fee")
        net = _integer(transaction.get("net"), "balance net")
        if amount - fee != net or transaction.get("status") not in {"pending", "available"}:
            raise EconomyError("invalid_provider_record", "Stripe gross, fee, net or settlement status is inconsistent", 502)
        result = {"transaction_id": transaction["id"], "kind": kind,
                "amount_micro_usd": amount * MICRO_PER_CENT, "fee_micro_usd": fee * MICRO_PER_CENT,
                "net_micro_usd": net * MICRO_PER_CENT, "currency": "USD",
                "settled": transaction["status"] == "available",
                "native": {key: transaction.get(key) for key in ("id", "type", "source", "currency", "amount", "fee", "net", "status", "available_on", "created", "exchange_rate", "fee_details")}}
        # A source may be expanded to a full Charge with private billing data.
        # Retain only its stable identifier in accounting supporting records.
        result["native"]["source"] = source_id
        return result

    def reconcile(self, payment_id: str, *, event_id=None, event_digest=None) -> dict:
        payment_id = _identifier(payment_id, "cs_")
        self._ensure_setup()
        for attempt in range(3):
            binding = self.service.payment("stripe", self.account, self.mode, payment_id)
            session = self._request("GET", f"/checkout/sessions/{payment_id}")
            self._assert_mode(session)
            if session.get("mode") != "payment":
                raise EconomyError("invalid_provider_record", "Stripe checkout mode mismatch", 502)
            transactions, pending_liability = [], 0
            status = "failed" if session.get("status") == "expired" else "pending"
            intent_id = _id(session.get("payment_intent"))
            if intent_id:
                intent_id = _identifier(intent_id, "pi_")
                intent = self._request("GET", f"/payment_intents/{intent_id}")
                self._assert_mode(intent)
                if intent.get("status") in {"canceled", "requires_payment_method"}:
                    status = "failed"
                latest = _id(intent.get("latest_charge"))
                charges = self._list("/charges", {"payment_intent": intent_id})
                charge_ids = [_identifier(charge.get("id"), "ch_") for charge in charges]
                if latest and latest not in charge_ids:
                    raise EconomyError("incomplete_provider_snapshot", "Stripe latest charge is absent from the complete charge history", 502)
                captured_total, refunded_total, disputed = 0, 0, False
                latest_failed = False
                for charge_id in charge_ids:
                    charge = self._request("GET", f"/charges/{charge_id}")
                    self._assert_mode(charge)
                    if _id(charge.get("payment_intent")) != intent_id:
                        raise EconomyError("invalid_provider_record", "Stripe charge is not owned by the checkout's payment intent", 502)
                    amount = _integer(charge.get("amount"), "charge amount")
                    captured = _integer(charge.get("amount_captured"), "captured amount")
                    if amount <= 0 or captured < 0 or captured > amount or type(charge.get("paid")) is not bool or type(charge.get("captured")) is not bool:
                        raise EconomyError("invalid_provider_record", "Stripe charge amount or capture state is invalid", 502)
                    if charge.get("currency") != "usd" or session.get("currency") != "usd":
                        raise EconomyError("unsupported_currency", "Only USD checkout and charge records are supported", 409)
                    if _integer(session.get("amount_total"), "checkout total") != amount:
                        raise EconomyError("invalid_provider_record", "Stripe checkout total differs from its charge", 502)
                    receipt = self._transaction(charge.get("balance_transaction"), "receipt", sources={charge_id})
                    failure = self._transaction(charge.get("failure_balance_transaction"), "adjustment", sources={charge_id})
                    if receipt:
                        gross = receipt["amount_micro_usd"]
                        if gross <= 0 or (charge["captured"] and gross != captured * MICRO_PER_CENT):
                            raise EconomyError("invalid_provider_record", "Stripe receipt differs from the captured charge", 502)
                        # Authorization alone is not receipt. A failure reversal
                        # means both historical balance movements must be kept,
                        # including a receipt that settled before later failure.
                        if not failure and not (charge["paid"] and charge["captured"]):
                            receipt["settled"] = False
                        receipt["native"].update(charge_id=charge_id, payment_intent=intent_id,
                            charge_currency=charge["currency"], charge_amount=amount, amount_captured=captured,
                            paid=charge["paid"], captured=charge["captured"])
                        transactions.append(receipt)
                        if receipt["settled"]:
                            captured_total += gross
                            status = "settled"
                    if failure:
                        transactions.append(failure)
                        captured_total += failure["amount_micro_usd"] if failure["settled"] or failure["amount_micro_usd"] < 0 else 0
                        latest_failed |= charge_id == latest
                    refunds = self._list("/refunds", {"charge": charge_id})
                    for refund in refunds:
                        self._assert_mode(refund, required=False)
                        refund_id = _identifier(refund.get("id"), "re_")
                        if _id(refund.get("charge")) != charge_id:
                            raise EconomyError("invalid_provider_record", "Stripe refund belongs to another charge", 502)
                        refund_amount = _integer(refund.get("amount"), "refund amount") * MICRO_PER_CENT
                        refund_status = refund.get("status")
                        if refund_amount < 0 or refund_status not in {"pending", "requires_action", "succeeded", "failed", "canceled"}:
                            raise EconomyError("invalid_provider_record", "Stripe refund amount or status is invalid", 502)
                        debit = self._transaction(refund.get("balance_transaction"), "refund", sources={refund_id})
                        reversal = self._transaction(refund.get("failure_balance_transaction"), "adjustment", sources={refund_id})
                        principal_posted = 0
                        if debit:
                            if debit["amount_micro_usd"] > 0:
                                raise EconomyError("invalid_provider_record", "Stripe refund debit has a positive amount", 502)
                            principal_posted = -debit["amount_micro_usd"]
                            transactions.append(debit)
                        if reversal:
                            transactions.append(reversal)
                        # A succeeded refund is a known obligation even if its
                        # balance transaction has not appeared yet. Fee-only or
                        # partial debits cannot release the principal exposure.
                        if refund_status in {"pending", "requires_action", "succeeded"}:
                            pending_liability += max(0, refund_amount-principal_posted)
                        if refund_status == "succeeded":
                            refunded_total += refund_amount
                    for dispute in self._list("/disputes", {"charge": charge_id}):
                        self._assert_mode(dispute, required=False)
                        dispute_id = _identifier(dispute.get("id"), "dp_")
                        if _id(dispute.get("charge")) != charge_id:
                            raise EconomyError("invalid_provider_record", "Stripe dispute belongs to another charge", 502)
                        dispute_transactions = dispute.get("balance_transactions")
                        if not isinstance(dispute_transactions, list):
                            raise EconomyError("invalid_provider_record", "Stripe dispute balance transactions are unavailable", 502)
                        principal_posted = 0
                        for value in dispute_transactions:
                            # Stripe versions/payment rails may identify the
                            # dispute or its original charge as the source.
                            tx = self._transaction(value, "chargeback", sources={dispute_id, charge_id})
                            if tx is not None:
                                gross = tx["amount_micro_usd"]
                                if gross > 0:
                                    tx["kind"] = "chargeback_reversal"
                                if gross < 0 or tx["settled"]:
                                    principal_posted -= gross
                                transactions.append(tx)
                        dispute_status = dispute.get("status")
                        if dispute_status not in {"won", "warning_closed", "lost", "needs_response", "under_review", "warning_needs_response", "warning_under_review"}:
                            raise EconomyError("invalid_provider_record", "Stripe dispute status is unrecognized", 502)
                        if dispute_status not in {"won", "warning_closed"}:
                            disputed = True
                            exposure = _integer(dispute.get("amount"), "dispute amount") * MICRO_PER_CENT
                            if exposure < 0:
                                raise EconomyError("invalid_provider_record", "Stripe dispute amount is negative", 502)
                            pending_liability += max(0, exposure-max(0, principal_posted))
                if captured_total > 0:
                    status = "settled"
                elif latest_failed or intent.get("status") in {"canceled", "requires_payment_method"}:
                    status = "failed"
                if refunded_total:
                    status = "refunded" if refunded_total >= captured_total else "partially_refunded"
                if disputed:
                    status = "disputed"
            snapshot = {"provider": "stripe", "account": self.account, "mode": self.mode,
                        "payment_id": payment_id, "run_id": binding["run_id"], "job_id": binding.get("job_id"),
                        "expected_revision": binding["revision"], "status": status,
                        "transactions": transactions, "pending_liability_micro_usd": pending_liability}
            try:
                return self.service.apply_payment_snapshot(snapshot, event_id=event_id, event_digest=event_digest)
            except EconomyError as error:
                if error.code != "stale_snapshot" or attempt == 2:
                    raise
        raise AssertionError("unreachable")
