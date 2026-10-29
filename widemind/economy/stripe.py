"""Stripe Checkout and settlement reads, with no authority to alter the local ledger.

All money is provider-native USD cents converted exactly to integer micro-USD.
Webhook contents are authenticated hints: inspect_checkout queries current Stripe
objects rather than crediting event payload amounts. No live calls run in tests.
"""
from __future__ import annotations

import hashlib
import contextlib
import contextvars
import functools
import hmac
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from widemind.business.contracts import Outcome

MAX_BODY = 2_000_000
MAX_PAGES = 10
API_ROOT = "https://api.stripe.com"
API_VERSION = "2024-06-20"


class StripeError(RuntimeError):
    """Sanitized provider failure. Uncertain writes require reconciliation."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status
        self.uncertain = status is None or status in {409, 429} or status >= 500


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _integer(value, name, *, signed=False):
    if type(value) is not int or (not signed and value < 0) or abs(value) > 10**15:
        raise StripeError(f"invalid Stripe {name}")
    return value


def _identifier(value, name, prefix=None):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or value.strip() != value:
        raise ValueError(f"invalid {name}")
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError(f"invalid {name}")
    if prefix and not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_]+", value):
        raise ValueError(f"invalid {name}")
    return value


def _https_url(value, name):
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"invalid {name}")
    parsed = urlsplit(value)
    _ = parsed.port
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"invalid {name}")
    return value


def _decode(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON field")
            out[key] = value
        return out
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise StripeError("invalid Stripe JSON response") from exc
    if not isinstance(value, dict):
        raise StripeError("invalid Stripe object")
    return value


def _bounded(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._operation():
            return method(self, *args, **kwargs)
    return call


class StripeClient:
    """Dedicated-account Stripe client; credentials never enter returned records.

    Optional transport(method,url,headers,body,timeout) returns
    (HTTP status, response headers, bytes). It is for controlled transport injection,
    not an alternate permission/billing route. Default urllib preserves proxy/CA.
    """

    def __init__(self, *, api_key, webhook_secret, mode="live", success_url,
                 cancel_url, account_id=None, transport=None, clock=time.time):
        if mode not in {"live", "test"}:
            raise ValueError("Stripe mode must be live or test")
        if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
            raise ValueError("Stripe API credential is required")
        # Stripe mode is chosen by the credential, not a request flag. Reject a
        # contradictory key before any POST can create a real Checkout session.
        key_mode = next((candidate for candidate in ("live", "test")
                         if api_key.startswith((f"sk_{candidate}_", f"rk_{candidate}_"))), None)
        if key_mode is not None:
            if key_mode != mode:
                raise ValueError("Stripe credential prefix contradicts configured mode")
            if api_key in {f"sk_{key_mode}_", f"rk_{key_mode}_"}:
                raise ValueError("Stripe API credential is incomplete")
        elif transport is None:
            # Fictional opaque keys are usable only with an explicitly injected
            # offline transport; the real network path requires a mode-pinned key.
            raise ValueError("Stripe network credentials require an explicit sk_/rk_ live/test prefix")
        if not isinstance(webhook_secret, str) or not webhook_secret or any(c.isspace() for c in webhook_secret):
            raise ValueError("Stripe webhook secret is required")
        self.mode, self.clock = mode, clock
        self._api_key, self._webhook_secret = api_key, webhook_secret
        self.success_url = _https_url(success_url, "success URL")
        self.cancel_url = _https_url(cancel_url, "cancel URL")
        self.account_id = _identifier(account_id, "connected account", "acct_") if account_id else None
        self.transport = transport or self._urllib_transport
        self._budget = contextvars.ContextVar("widemind_stripe_request_budget", default=None)

    @contextlib.contextmanager
    def _operation(self):
        if self._budget.get() is not None:
            yield
            return
        token = self._budget.set([50, time.monotonic() + 45])
        try:
            yield
        finally:
            self._budget.reset(token)

    @contextlib.contextmanager
    def operation_budget(self, *, seconds=10, requests=20):
        """Share a finite budget across a host-selected batch of provider reads."""
        if type(seconds) not in {int,float} or not 0 < seconds <= 45 or type(requests) is not int or not 1 <= requests <= 50:
            raise ValueError("invalid Stripe operation budget")
        if self._budget.get() is not None:
            yield
            return
        token = self._budget.set([requests,time.monotonic()+seconds])
        try:
            yield
        finally:
            self._budget.reset(token)

    @staticmethod
    def _urllib_transport(method, url, headers, body, timeout):
        # No redirect may forward Stripe authorization to another origin. Standard
        # urllib handlers preserve inherited proxy configuration and default CA trust.
        opener = build_opener(_NoRedirect())
        request = Request(url, data=body, headers=headers, method=method)
        deadline = time.monotonic()+timeout
        try:
            with opener.open(request, timeout=timeout) as response:
                chunks, size = [], 0
                reader = getattr(response, "read1", response.read)
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise StripeError("Stripe response deadline exceeded")
                    raw = getattr(getattr(response,"fp",None),"raw",None)
                    sock = getattr(raw,"_sock",None)
                    if sock is not None:
                        sock.settimeout(remaining)
                    chunk = reader(min(65536, MAX_BODY + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise StripeError("Stripe response exceeds body limit")
                return response.status, dict(response.headers), b"".join(chunks)
        except HTTPError as exc:
            try:
                return exc.code, dict(exc.headers), b""
            finally:
                exc.close()
        except (URLError, OSError, TimeoutError) as exc:
            raise StripeError("Stripe transport unavailable; reconcile writes before retry") from exc

    def _request(self, method, path, params=None, *, action_id=None):
        if not path.startswith("/v1/") or ".." in path or "#" in path:
            raise ValueError("invalid Stripe resource")
        headers = {"Authorization": "Bearer " + self._api_key,
                   "Stripe-Version": API_VERSION, "Accept": "application/json",
                   "User-Agent": "WideMind/0.1 Stripe adapter"}
        if self.account_id:
            headers["Stripe-Account"] = self.account_id
        body = None
        url = API_ROOT + path
        if params:
            encoded = urlencode(params, doseq=True)
            if method == "GET":
                url += ("&" if "?" in url else "?") + encoded
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                body = encoded.encode()
        if action_id:
            headers["Idempotency-Key"] = _identifier(action_id, "action_id")
        budget = self._budget.get()
        remaining = 15
        if budget is not None:
            remaining = min(15, budget[1] - time.monotonic())
            if budget[0] <= 0 or remaining <= 0:
                raise StripeError("Stripe operation request/deadline limit reached")
            budget[0] -= 1
        try:
            status, _, raw = self.transport(method, url, headers, body, remaining)
        except StripeError:
            raise
        except Exception as exc:
            # Do not include transport exceptions that can contain request headers.
            raise StripeError("Stripe transport outcome unknown") from exc
        if budget is not None and time.monotonic() > budget[1]:
            raise StripeError("Stripe operation deadline exceeded")
        if type(status) is not int or not isinstance(raw, bytes) or len(raw) > MAX_BODY:
            raise StripeError("invalid or oversized Stripe response")
        if not 200 <= status < 300:
            raise StripeError(f"Stripe request rejected (HTTP {status})", status=status)
        return _decode(raw)

    def _mode(self, obj, *, required=False):
        if not isinstance(obj, dict):
            raise StripeError("invalid Stripe object shape")
        actual = obj.get("livemode")
        if required or actual is not None:
            if type(actual) is not bool or actual != (self.mode == "live"):
                raise StripeError("Stripe account mode mismatch")

    @staticmethod
    def _metadata(session):
        metadata = session.get("metadata")
        if not isinstance(metadata, dict):
            raise StripeError("Stripe session metadata missing")
        try:
            return {key: _identifier(metadata.get(key), f"session {key}")
                    for key in ("run_id", "job_id", "action_id")}
        except ValueError as exc:
            raise StripeError("invalid Stripe session identity metadata") from exc

    def _session(self, session_id):
        session_id = _identifier(session_id, "session ID", "cs_")
        session = self._request("GET", "/v1/checkout/sessions/" + session_id,
                                {"expand[]": "payment_intent.latest_charge.balance_transaction"})
        self._mode(session, required=True)
        if session.get("id") != session_id or session.get("object") != "checkout.session":
            raise StripeError("Stripe session identity mismatch")
        return session

    @_bounded
    def create_checkout(self, *, run_id, job_id, action_id, amount_micro_usd, currency="USD"):
        if currency != "USD":
            raise ValueError("Stripe adapter currently supports native USD only")
        amount = _integer(amount_micro_usd, "checkout amount")
        if amount <= 0 or amount % 10_000:
            raise ValueError("Stripe USD Checkout amount must be exact whole cents")
        metadata = {key: _identifier(value, key) for key, value in
                    (("run_id", run_id), ("job_id", job_id), ("action_id", action_id))}
        params = {"mode": "payment", "success_url": self.success_url,
                  "cancel_url": self.cancel_url, "client_reference_id": metadata["job_id"],
                  "line_items[0][price_data][currency]": "usd",
                  "line_items[0][price_data][product_data][name]": "WideMind agreed service",
                  "line_items[0][price_data][unit_amount]": amount // 10_000,
                  "line_items[0][quantity]": 1}
        for key, value in metadata.items():
            params[f"metadata[{key}]"] = value
            params[f"payment_intent_data[metadata][{key}]"] = value
        session = self._request("POST", "/v1/checkout/sessions", params, action_id=action_id)
        self._mode(session, required=True)
        if self._metadata(session) != metadata:
            raise StripeError("Stripe checkout metadata mismatch")
        session_id = _identifier(session.get("id"), "session ID", "cs_")
        if session.get("object") != "checkout.session":
            raise StripeError("invalid Stripe checkout result")
        return {"id": session_id, "url": _https_url(session.get("url"), "checkout URL")}

    def _pages(self, path, params):
        params = dict(params, limit=100)
        for _ in range(MAX_PAGES):
            page = self._request("GET", path, params)
            data = page.get("data")
            if page.get("object") != "list" or not isinstance(data, list) or len(data) > 100:
                raise StripeError("invalid Stripe list response")
            for obj in data:
                if not isinstance(obj, dict):
                    raise StripeError("invalid Stripe list object")
                yield obj
            if page.get("has_more") is False:
                return
            if page.get("has_more") is not True or not data:
                raise StripeError("invalid Stripe pagination")
            params["starting_after"] = _identifier(data[-1].get("id"), "pagination ID")
        raise StripeError("Stripe reconciliation page limit reached")

    @_bounded
    def reconcile_checkout(self, *, action_id, session_id=None):
        _identifier(action_id, "action_id")
        try:
            sessions = [self._session(session_id)] if session_id else self._pages("/v1/checkout/sessions", {})
            matches = []
            for session in sessions:
                self._mode(session, required=True)
                metadata = session.get("metadata", {})
                if not isinstance(metadata, dict) or metadata.get("action_id") != action_id:
                    continue
                self._metadata(session)
                matches.append(session)
            if len(matches) > 1:
                return Outcome("unknown", reason="multiple Checkout sessions claim this action; operator reconciliation required")
            if matches:
                session = matches[0]
                session_id = _identifier(session.get("id"), "session ID", "cs_")
                if session.get("status") == "expired":
                    return Outcome("failed", {"id": session_id}, "Stripe Checkout session expired")
                if session.get("url"):
                    return Outcome("succeeded", {"id": session_id, "url": _https_url(session["url"], "checkout URL")})
                return Outcome("unknown", {"id": session_id}, "Checkout exists without a reusable URL; inspect settlement")
        except (StripeError, ValueError):
            return Outcome("unknown", reason="Stripe Checkout reconciliation unavailable")
        return Outcome("unknown", reason="Stripe has no bounded reconciliation evidence; do not reissue blindly")

    def verify_event(self, raw_body, signature):
        if not isinstance(raw_body, bytes) or len(raw_body) > MAX_BODY or not isinstance(signature, str) or len(signature) > 8192:
            raise ValueError("invalid Stripe webhook envelope")
        timestamps, signatures = [], []
        for item in signature.split(","):
            key, sep, value = item.strip().partition("=")
            if sep and key == "t":
                if not value.isascii() or not value.isdigit():
                    raise ValueError("invalid Stripe signature timestamp")
                timestamps.append(int(value))
            elif sep and key == "v1" and re.fullmatch(r"[a-fA-F0-9]{64}", value):
                signatures.append(value.lower())
        if len(timestamps) != 1 or not signatures or abs(self.clock() - timestamps[0]) > 300:
            raise ValueError("expired or invalid Stripe signature")
        signed = str(timestamps[0]).encode() + b"." + raw_body
        expected = hmac.new(self._webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, value) for value in signatures):
            raise ValueError("Stripe webhook signature mismatch")
        try:
            event = _decode(raw_body)
            self._mode(event, required=True)
            _identifier(event.get("id"), "event ID", "evt_")
            _identifier(event.get("type"), "event type")
            if not isinstance(event.get("data"), dict) or not isinstance(event["data"].get("object"), dict):
                raise ValueError("invalid Stripe event data")
            if event.get("account") != self.account_id:
                raise ValueError("Stripe webhook account mismatch")
        except StripeError as exc:
            raise ValueError("invalid Stripe webhook object or mode") from exc
        return event

    @_bounded
    def session_ids_for_event(self, event):
        """Map a verified event to known sessions using read-only provider queries.

        balance.available has no job identity and returns []; the economy owner
        must reconcile its known pending jobs on that event or scheduled wakeup.
        """
        self._mode(event, required=True)
        if event.get("account") != self.account_id:
            raise ValueError("Stripe webhook account mismatch")
        obj = event.get("data", {}).get("object", {})
        kind = obj.get("object")
        if kind == "checkout.session":
            return [_identifier(obj.get("id"), "session ID", "cs_")]
        payment_intent = obj.get("payment_intent")
        if kind == "payment_intent":
            payment_intent = obj.get("id")
        elif kind in {"refund", "dispute"} and obj.get("charge"):
            charge_id = obj["charge"].get("id") if isinstance(obj["charge"], dict) else obj["charge"]
            charge = self._request("GET", "/v1/charges/" + _identifier(charge_id, "charge ID", "ch_"))
            self._mode(charge)
            payment_intent = charge.get("payment_intent")
        if isinstance(payment_intent, dict):
            payment_intent = payment_intent.get("id")
        if not payment_intent:
            return []
        payment_intent = _identifier(payment_intent, "payment intent", "pi_")
        result = []
        for session in self._pages("/v1/checkout/sessions", {"payment_intent": payment_intent}):
            self._mode(session, required=True)
            self._metadata(session)
            if session.get("payment_intent") != payment_intent and not (
                isinstance(session.get("payment_intent"), dict) and session["payment_intent"].get("id") == payment_intent
            ):
                raise StripeError("Stripe session payment identity mismatch")
            result.append(_identifier(session.get("id"), "session ID", "cs_"))
        return result

    def _expanded(self, value, resource, prefix, *, expand=None):
        if isinstance(value, dict):
            obj = value
        else:
            obj = self._request("GET", "/v1/" + resource + "/" + _identifier(value, resource + " ID", prefix),
                                {"expand[]": expand} if expand else None)
            if obj.get("id") != value:
                raise StripeError("Stripe expanded resource identity mismatch")
        self._mode(obj)
        _identifier(obj.get("id"), resource + " ID", prefix)
        return obj

    @_bounded
    def inspect_checkout(self, *, session_id):
        session = self._session(session_id)
        metadata = self._metadata(session)
        if session.get("currency") != "usd":
            raise StripeError("Stripe Checkout is not native USD")
        native = {"currency": "usd", "session_id": session["id"], "session_status": session.get("status"),
                  "settlement_basis": "Stripe available balance; bank payout not implied"}
        result = {**metadata, "mode": self.mode, "status": "pending", "receipts_micro_usd": 0,
                  "refunds_micro_usd": 0, "fees_micro_usd": 0, "disputed_micro_usd": 0,
                  "pending_refunds_micro_usd": 0, "provider_reference": session["id"],
                  "payment_intent_id": None, "provider_native": native}
        if not session.get("payment_intent"):
            return result
        intent = self._expanded(session["payment_intent"], "payment_intents", "pi_", expand="latest_charge.balance_transaction")
        result["payment_intent_id"] = intent["id"]
        native["payment_intent_id"] = intent["id"]
        if intent.get("currency") != "usd":
            raise StripeError("Stripe payment intent is not native USD")
        if not intent.get("latest_charge"):
            return result
        charge = self._expanded(intent["latest_charge"], "charges", "ch_", expand="balance_transaction")
        if charge.get("currency") != "usd":
            raise StripeError("Stripe charge is not native USD")
        if charge.get("payment_intent") not in {None, intent["id"]}:
            raise StripeError("Stripe charge/payment identity mismatch")
        if charge.get("paid") is not True or charge.get("captured") is not True or intent.get("status") != "succeeded":
            return result
        captured = _integer(charge.get("amount_captured", charge.get("amount")), "captured amount")
        if not charge.get("balance_transaction"):
            return result
        transaction = self._expanded(charge["balance_transaction"], "balance_transactions", "txn_")
        if transaction.get("currency") != "usd":
            raise StripeError("Stripe balance transaction is not native USD")
        if _integer(transaction.get("amount"), "balance amount", signed=True) != captured:
            raise StripeError("Stripe captured/balance amount mismatch")
        native.update({"charge_id": charge["id"], "balance_transaction_id": transaction["id"],
                       "balance_status": transaction.get("status"), "gross_cents": captured})
        if transaction.get("status") != "available":
            return result
        fees = _integer(transaction.get("fee"), "balance fee", signed=True)
        refund_data = charge.get("refunds")
        if isinstance(refund_data, dict) and refund_data.get("has_more") is False and isinstance(refund_data.get("data"), list):
            refunds = refund_data["data"]
        else:
            refunds = list(self._pages("/v1/refunds", {"charge": charge["id"]}))
        refunded, pending_refunds = 0, 0
        seen_refunds = set()
        for refund in refunds:
            self._mode(refund)
            refund_id = _identifier(refund.get("id"), "refund ID", "re_")
            if refund_id in seen_refunds:
                raise StripeError("duplicate Stripe refund object")
            seen_refunds.add(refund_id)
            if refund.get("currency") != "usd" or refund.get("charge") not in {None, charge["id"]}:
                raise StripeError("Stripe refund identity/currency mismatch")
            amount = _integer(refund.get("amount"), "refund amount")
            if refund.get("status") == "succeeded":
                refunded += amount
                if refund.get("balance_transaction"):
                    balance = self._expanded(refund["balance_transaction"], "balance_transactions", "txn_")
                    if balance.get("currency") != "usd":
                        raise StripeError("Stripe refund balance currency mismatch")
                    fees += _integer(balance.get("fee"), "refund fee", signed=True)
            elif refund.get("status") in {"pending", "requires_action"}:
                pending_refunds += amount
            elif refund.get("status") not in {"failed", "canceled"}:
                raise StripeError("unknown Stripe refund status")
        aggregate_refund = _integer(charge.get("amount_refunded", refunded), "charge refund total")
        # Depending on payment method, Stripe may include pending refunds in its
        # aggregate before they become successful. Keep those amounts on hold;
        # never invent a completed refund from the aggregate alone.
        if not refunded <= aggregate_refund <= refunded + pending_refunds:
            raise StripeError("Stripe refund aggregate is inconsistent; reconcile later")
        lost, disputed = 0, 0
        seen_disputes = set()
        for dispute in self._pages("/v1/disputes", {"charge": charge["id"]}):
            self._mode(dispute)
            dispute_id = _identifier(dispute.get("id"), "dispute ID", "dp_")
            if dispute_id in seen_disputes:
                raise StripeError("duplicate Stripe dispute object")
            seen_disputes.add(dispute_id)
            if dispute.get("currency") != "usd" or dispute.get("charge") not in {None, charge["id"]}:
                raise StripeError("Stripe dispute identity/currency mismatch")
            amount = _integer(dispute.get("amount"), "dispute amount")
            balances = dispute.get("balance_transactions", [])
            if not isinstance(balances, list) or len(balances) > 30:
                raise StripeError("invalid Stripe dispute balance records")
            seen_balances = set()
            for value in balances:
                balance = self._expanded(value, "balance_transactions", "txn_")
                if balance["id"] in seen_balances or balance.get("currency") != "usd":
                    raise StripeError("invalid duplicate/currency dispute balance")
                seen_balances.add(balance["id"])
                fees += _integer(balance.get("fee"), "dispute fee", signed=True)
            status = dispute.get("status")
            if status == "lost":
                lost += amount
            elif status in {"needs_response", "under_review", "warning_needs_response", "warning_under_review"}:
                disputed += amount
            elif status not in {"won", "warning_closed"}:
                raise StripeError("unknown Stripe dispute status")
        if fees < 0:
            raise StripeError("Stripe net fee credit requires explicit reconciliation")
        reversals = refunded + lost  # Disjoint provider liabilities; never cap/hide double debits.
        native.update({"refund_cents": refunded, "lost_dispute_cents": lost,
                       "active_dispute_cents": disputed, "pending_refund_cents": pending_refunds,
                       "fee_cents": fees})
        if disputed:
            status = "disputed"
        elif reversals >= captured:
            status = "refunded"
        elif reversals or pending_refunds:
            status = "partial"
        else:
            status = "settled"
        result.update(status=status, receipts_micro_usd=captured * 10_000,
                      refunds_micro_usd=reversals * 10_000, fees_micro_usd=fees * 10_000,
                      disputed_micro_usd=disputed * 10_000,
                      pending_refunds_micro_usd=pending_refunds * 10_000)
        return result
