"""Private stdin bridge for the foundation; central economy remains authoritative.

No SDK development wallet, financial database, or model-provided credentials are
accepted. Provider output is persisted before the Node runtime posts its receipt.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from widemind.http import HTTPTransport
from .gmail import GmailClient, GmailConfig, address
from .http import HTTPS, TransportError
from .research import OpenAIResearch, ResearchConfig, ProviderResult
from .validation import ResearchBrief, integer, single_line, validate_artifact


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def identifier(value):
    return single_line(value, "identifier", 200)


class Settings:
    def __init__(self, env):
        self.env = env
        self.mode = env.get("WIDEMIND_BUSINESS_TOOLS_PROFILE")
        if self.mode not in {"provider-test", "live"}:
            raise ValueError("explicit business tools profile required")
        self.journal = Path(env["WIDEMIND_BUSINESS_TOOLS_JOURNAL"])
        if not self.journal.is_absolute():
            raise ValueError("absolute protected journal required")
        self.journal = self.journal.resolve()
        for key in ("WIDEMIND_WORKSPACE", "WIDEMIND_BUSINESS_WORKSPACE"):
            if env.get(key) and self.journal.is_relative_to(Path(env[key]).resolve()):
                raise ValueError("journal must be outside agent workspace")
        for key in ("WIDEMIND_ECONOMY_DB", "WIDEMIND_DATABASE", "WIDEMIND_BUSINESS_STATE_DB"):
            if env.get(key) and self.journal == Path(env[key]).resolve():
                raise ValueError("tool journal must not be another database")
        parsed = urlsplit(env["WIDEMIND_ECONOMY_URL"])
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("private economy endpoint required")
        self.economy_url = env["WIDEMIND_ECONOMY_URL"].rstrip("/")
        self.token = env["WIDEMIND_GATEWAY_TOKEN"]
        if len(self.token) < 32 or len(self.token) > 512 or any(ord(c) < 33 for c in self.token):
            raise ValueError("gateway token required")
        recipients = json.loads(env.get("WIDEMIND_BUSINESS_ALLOWED_RECIPIENTS", "[]"))
        if not isinstance(recipients, list) or len(recipients) > 100:
            raise ValueError("invalid controlled recipients")
        self.recipients = {address(item) for item in recipients}
        self.gmail_daily_limit = integer(int(env.get("WIDEMIND_BUSINESS_GMAIL_DAILY_LIMIT", "500")), "Gmail daily limit", 1, 10000)
        self.research_daily_limit = integer(int(env.get("WIDEMIND_BUSINESS_RESEARCH_DAILY_LIMIT", "50")), "research daily limit", 1, 1000)

    def profile(self, tool):
        gmail = tool in {"gmail.read", "gmail.send"}
        return {"provider": "gmail" if gmail else "openai",
                "account": identifier(self.env["WIDEMIND_BUSINESS_GMAIL_ACCOUNT" if gmail else "WIDEMIND_BUSINESS_OPENAI_ACCOUNT"]),
                "mode": "sandbox" if self.mode == "provider-test" else "live"}

    def clients(self, transport, tool, prepared=None):
        env = self.env
        gmail = research = None
        if tool.startswith("gmail.") and all(env.get(key) for key in ("WIDEMIND_GMAIL_ADDRESS", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")):
            gmail = GmailClient(GmailConfig(env["WIDEMIND_GMAIL_ADDRESS"], env["GMAIL_CLIENT_ID"],
                                env["GMAIL_CLIENT_SECRET"], env["GMAIL_REFRESH_TOKEN"]), transport=transport)
        if tool == "research.collect" and env.get("OPENAI_API_KEY"):
            prices = prepared.get("pricing") if prepared else None
            payload = prepared.get("payload", {}) if prepared else {}
            research = OpenAIResearch(ResearchConfig(env["OPENAI_API_KEY"],
                input_micro_usd_per_million_tokens=prices["input"] if prices else int(env["OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS"]),
                output_micro_usd_per_million_tokens=prices["output"] if prices else int(env["OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS"]),
                cached_input_micro_usd_per_million_tokens=prices["cached_input"] if prices else int(env["OPENAI_CACHED_INPUT_MICRO_USD_PER_MILLION_TOKENS"]),
                model=payload.get("model") or env["OPENAI_MODEL"], max_output_tokens=payload.get("max_completion_tokens") or int(env.get("OPENAI_MAX_OUTPUT_TOKENS", "1800")),
                max_input_bytes=int(env.get("OPENAI_MAX_INPUT_BYTES", "24000")),
                source_urls=tuple(json.loads(env.get("WIDEMIND_RESEARCH_SOURCE_URLS", "[]"))),
                allowed_source_hosts=tuple(json.loads(env.get("WIDEMIND_RESEARCH_ALLOWED_HOSTS", "[]")))), transport=transport)
        return gmail, research


class VerificationDenied(ValueError):
    pass


class DispatchUncertain(Exception):
    pass


class Gateway:
    def __init__(self, settings, *, verify=None, transport=None, cancelled=None, payment=None, authorization=None):
        self.settings = settings
        self.verify_override, self.cancelled = verify, cancelled or (lambda: False)
        self.payment_override = payment
        self.authorization_override = authorization
        self.provider_transport = transport or HTTPS(public_proxy_guarded=settings.env.get("WIDEMIND_BUSINESS_PUBLIC_PROXY_GUARDED") == "true")
        self.db = None
        self.action = self.reservation = self.context = self.profile = None
        self.deadline = 0
        self.control_marker = None
        self.operation = None

    def _open(self):
        path = self.settings.journal
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO settings VALUES ('mode',?)", (self.settings.mode,))
        if self.db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] != self.settings.mode:
            raise ValueError("journal profile cannot change")
        self.db.execute("""CREATE TABLE IF NOT EXISTS actions(
          id TEXT PRIMARY KEY,binding TEXT NOT NULL,action TEXT NOT NULL,profile TEXT NOT NULL,
          state TEXT NOT NULL,context TEXT NOT NULL,receipt TEXT,created REAL NOT NULL)""")

    @contextmanager
    def _lock(self, action_id):
        directory = self.settings.journal.parent / (self.settings.journal.name + ".actions")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(directory / (hashlib.sha256(action_id.encode()).hexdigest() + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        stream = os.fdopen(fd, "a+b")
        acquired = False
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.write(b"\0"); stream.flush(); stream.seek(0)
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

    def _check(self):
        if self.cancelled() or time.monotonic() >= self.deadline:
            raise ValueError("operation cancelled or deadline exceeded")
        state_path = self.settings.env.get("WIDEMIND_BUSINESS_STATE_DB")
        if state_path and self.operation == "execute":
            with sqlite3.connect(Path(state_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
                row = db.execute("SELECT desired,state FROM runtime_runs WHERE id=?", (self.action["runId"],)).fetchone()
                if not row or row[0] != "run" or row[1] not in {"active", "waiting"}:
                    raise ValueError("runtime no longer permits dispatch")
                planned = db.execute("SELECT action_json FROM runtime_actions WHERE id=? AND run_id=?", (self.action["actionId"], self.action["runId"])).fetchone()
                if not planned or encoded(json.loads(planned[0])) != encoded(self.action):
                    raise ValueError("action differs from immutable runtime intent")
                # Runtime revisions also track inbox delivery and ordinary wakes.
                # Only durable pause/stop/resume events invalidate an attempt.
                marker = db.execute("""SELECT COALESCE(MAX(id),0) FROM runtime_events
                  WHERE run_id=? AND kind IN ('operator.pause','operator.stop','operator.resume')""", (self.action["runId"],)).fetchone()[0]
                if self.control_marker is None:
                    self.control_marker = marker
                elif self.control_marker != marker:
                    raise ValueError("runtime controls changed during attempt")

    def _rpc(self, path, request):
        body = encoded(request).encode()
        if len(body) > 1_000_000:
            raise ValueError("control input too large")
        response = HTTPTransport().request("POST", self.settings.economy_url + path,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.settings.token},
            body=body, timeout=min(4, max(.001, self.deadline-time.monotonic())), max_bytes=65536,
            cancel=self._check)
        if response.status != 200:
            raise ValueError("control operation denied")
        return json.loads(response.body)

    def _verify(self, dispatch=False):
        self._check()
        request = {"action": self.action, "reservation": self.reservation, "dispatch": dispatch}
        if self.verify_override:
            result = self.verify_override(request)
        else:
            result = self._rpc("/v1/runtime/verify", request)
        if result.get("kind") != "verified" or result.get("profile") != self.profile:
            if dispatch:
                # A closed/duplicate gate may represent an earlier provider bill
                # whose journal was lost. It is never proof of zero liability.
                raise DispatchUncertain("central dispatch cannot establish a new attempt")
            raise VerificationDenied("central authorization/profile denied")
        return result

    def _still_reserved(self):
        request = {key: self.action[key] for key in ("runId", "actionId", "tool", "limit")}
        response = self.authorization_override(request) if self.authorization_override else self._rpc("/v1/runtime/authorize", request)
        return response.get("kind") == "authorized" and response.get("reservation") == self.reservation

    def _context(self):
        self.db.execute("UPDATE actions SET context=? WHERE id=?", (encoded(self.context), self.action["actionId"]))

    def _admit(self):
        self._check()
        if self.context.get("stage") == "dispatched":
            return
        if self.action["tool"] == "research.collect":
            self._paid_order()
        # Commit uncertainty before the non-idempotent central dispatch gate.
        self.context["stage"] = "dispatching"
        self._context()
        self._verify(True)
        self.context["stage"] = "dispatched"
        self.context.setdefault("executionStartedAt", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self._context()

    def _paid_order(self):
        """Late order funding/scope/policy check; wallet cash alone is insufficient."""
        job = identifier(self.action.get("jobId"))
        state_path = self.settings.env.get("WIDEMIND_BUSINESS_STATE_DB")
        if not state_path:
            raise ValueError("protected business state required for research")
        with sqlite3.connect(Path(state_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=.5) as db:
            row = db.execute("""SELECT o.status,o.price_micros,o.cost_micros,o.scope,q.scope,q.cost_micros,q.price_micros,s.document
              FROM business_orders o JOIN business_quotes q ON q.id=o.quote_id
              JOIN business_settings s ON s.run_id=o.run_id WHERE o.id=? AND o.run_id=?""", (job, self.action["runId"])).fetchone()
        if not row or row[0] != "creating" or row[1] != row[6] or row[2] != row[5] or row[3] != row[4]:
            raise ValueError("order is not an approved unfinished commitment")
        policy = json.loads(row[7])
        maximum = int(self.action["limit"]["maximum"]["micros"])
        if ("research" not in policy["capabilities"] or maximum > int(row[2]) or maximum > int(policy["maxJobMicros"])
                or encoded(json.loads(row[3])) != encoded(self.action["input"])):
            raise ValueError("frozen scope or current execution policy changed")
        request = {"runId": self.action["runId"], "jobId": job, "mode": self.profile["mode"]}
        payment = self.payment_override(request) if self.payment_override else self._rpc("/v1/runtime/commerce/order", request)
        if (not isinstance(payment, dict) or any(payment.get(key) != value for key, value in request.items())
                or payment.get("currency") != "USD" or payment.get("priceMicros") != row[1]
                or payment.get("paymentStatus") != "settled"):
            raise ValueError("job settlement is no longer valid")
        def amount(key):
            value = payment.get(key)
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 30:
                raise ValueError("invalid payment amount")
            return int(value)
        if (amount("pendingLiabilityMicros") != 0 or amount("receiptsMicros") - amount("refundsMicros")
                - amount("chargebacksMicros") + amount("chargebackReversalsMicros") < int(row[1])):
            raise ValueError("job payment changed before inference")
    def request(self, method, url, **kwargs):
        self._check()
        tool = self.action["tool"]
        external = (tool == "research.collect" and method == "POST" and url == OpenAIResearch.API
                    or tool == "gmail.send" and method == "POST" and url == GmailClient.API + "/messages/send"
                    or tool == "gmail.read" and url.startswith(GmailClient.API + "/messages"))
        kwargs["before_dispatch"] = self._admit if external else self._check
        kwargs["checkpoint"] = self._check
        kwargs["timeout"] = min(kwargs.get("timeout", 20), max(.001, self.deadline-time.monotonic()))
        return self.provider_transport.request(method, url, **kwargs)

    def _finish(self, status, result, actual, reference):
        try:
            reference = identifier(reference)
        except ValueError:
            reference = "private-usage-observation:" + self.action["actionId"]
        integer(actual, "actual charge", 0, 9223372036854775807)
        receipt = {"version": 1, "actionId": self.action["actionId"], "status": status, "result": result,
            "usage": {"actualCharge": {"currency": "USD", "micros": str(actual)}, "provider": self.profile["provider"],
                "accountRef": self.profile["account"], "externalEventId": reference,
                "billableFailure": status == "failed" and actual > 0, "provisional": False, "evidenceRef": reference},
            "externalRef": reference}
        # Enforce the result bound while retaining any observed billable charge.
        if len(encoded(receipt).encode()) > self.action["limit"]["maxOutputBytes"]:
            receipt["status"] = "failed"
            receipt["result"] = {"error": "provider output exceeded authorized result bound"}
            receipt["usage"]["billableFailure"] = actual > 0
        self.db.execute("UPDATE actions SET state='completed',receipt=? WHERE id=?", (encoded(receipt), self.action["actionId"]))
        return {"kind": "receipt", "receipt": receipt}

    def _research_result(self, result):
        if result.state == "unknown" or result.actual_micro_usd is None:
            return {"kind": "unknown", "reason": "Provider usage is uncertain; retain authorization"}
        data = result.data
        if result.state == "succeeded":
            try:
                artifact = ResearchBrief().deliverable(self.context["prepared"]["scope"], data)
                data = {"sources": data["sources"], "sections": [{"heading": row["heading"], "text": row["text"],
                    "sourceUrls": row["source_urls"]} for row in data["sections"]], "artifact": {
                        "filename": artifact["filename"], "mediaType": artifact["media_type"], "content": artifact["content"], "checks": artifact["checks"]}}
                started = self.context.get("executionStartedAt")
                if not isinstance(started, str) or not started.endswith("Z"):
                    raise ValueError("original provider dispatch time unavailable")
                datetime.fromisoformat(started.replace("Z", "+00:00"))
                data["executionStartedAt"] = started
            except Exception:
                return self._finish("failed", {"error": "Billed provider output failed final artifact validation"}, result.actual_micro_usd, result.reference)
        return self._finish(result.state, data or {"error": result.reason}, result.actual_micro_usd, result.reference)

    def _receipts(self, messages):
        updates = []
        for message in messages:
            if not message.get("sender_verified") or not message.get("in_reply_to"):
                continue
            row = self.db.execute("SELECT * FROM actions WHERE state='completed' AND receipt IS NOT NULL AND json_extract(action,'$.tool')='gmail.send' AND json_extract(receipt,'$.result.rfcMessageId')=?", (message["in_reply_to"],)).fetchone()
            if not row:
                continue
            action, receipt = json.loads(row["action"]), json.loads(row["receipt"])
            command = message.get("command", {})
            if (not isinstance(command, dict) or command.get("type") != "acknowledge"
                    or command.get("action_id", command.get("actionId")) != row["id"]
                    or "action_id" in command and "actionId" in command and command["action_id"] != command["actionId"]
                    or "attachment" not in action["input"]):
                continue
            if action["runId"] != self.action["runId"] or receipt["status"] != "succeeded" or address(action["input"]["to"]) != message["from"]:
                continue
            evidence = "authenticated-gmail-reply:" + message["message_id"]
            receipt["result"].update(deliveryState="confirmed", evidenceRef=evidence)
            self.db.execute("UPDATE actions SET receipt=? WHERE id=?", (encoded(receipt), row["id"]))
            updates.append({"actionId": row["id"], "jobId": action.get("jobId"), "deliveryState": "confirmed", "evidenceRef": evidence})
        return updates

    def call(self, operation, action, reservation):
        self.action, self.reservation = action, reservation
        self.operation = operation
        action_id, tool = identifier(action["actionId"]), action["tool"]
        if tool not in {"gmail.read", "gmail.send", "research.collect"} or action.get("version") != 1:
            raise ValueError("unsupported action")
        if any(reservation.get(key) != action.get(key) for key in ("actionId", "runId", "limit")):
            raise ValueError("reservation differs from action")
        limit = action["limit"]
        duration = integer(limit["maxDurationMs"], "duration", 1, 300000)
        integer(limit["maxOutputBytes"], "output limit", 512, 1048576)
        maximum = limit["maximum"]
        if maximum.get("currency") != "USD" or not isinstance(maximum.get("micros"), str) or not maximum["micros"].isdigit():
            raise ValueError("exact USD maximum required")
        maximum = int(maximum["micros"])
        self.deadline = time.monotonic() + duration / 1000
        binding = hashlib.sha256(encoded({"action": action, "reservation": reservation}).encode()).hexdigest()
        self._open()
        with self._lock(action_id) as acquired:
            if not acquired:
                return {"kind": "unknown", "reason": "Action is currently executing or reconciling"}
            row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if row and row["binding"] != binding:
                raise ValueError("immutable action identity changed")
            if row and row["receipt"]:
                return {"kind": "receipt", "receipt": json.loads(row["receipt"])}
            self.profile = json.loads(row["profile"]) if row else self.settings.profile(tool)
            self.context = json.loads(row["context"]) if row else {"stage": "preparing"}
            self._verify(False)
            if (not row and operation == "execute" or row and self.context["stage"] == "preparing") and not self._still_reserved():
                return {"kind": "unknown", "reason": "Existing central dispatch/liability requires reviewed journal recovery"}
            gmail, research = self.settings.clients(self, tool, self.context.get("prepared") if row else None)
            if row:
                if operation != "reconcile":
                    return {"kind": "unknown", "reason": "Existing attempt requires observational reconciliation"}
                if self.context["stage"] == "preparing":
                    return self._finish("failed", {"error": "Interrupted before provider dispatch"}, 0, "not-dispatched:" + action_id)
                if tool == "research.collect" and research and self.context.get("prepared", {}).get("pricing"):
                    prepared = self.context["prepared"]
                    return self._research_result(research.reconcile(action_id, {"_sources": prepared["sources"], "_scope": prepared["scope"], "_pricing": prepared["pricing"]}))
                if tool == "gmail.send" and gmail:
                    result = gmail.reconcile_send(action_id)
                    if result["state"] == "succeeded":
                        data = result["data"]
                        return self._finish("succeeded", {"messageId": data["message_id"], "threadId": data["thread_id"], "deliveryState": "accepted", "rfcMessageId": data["rfc_message_id"]}, 0, data["message_id"])
                if tool == "gmail.read":
                    return self._finish("failed", {"error": "Interrupted read; schedule a fresh inbox batch"}, 0, "interrupted-read:" + action_id)
                return {"kind": "unknown", "reason": "No definitive provider outcome; authorization retained"}
            if operation != "execute":
                return {"kind": "unknown", "reason": "No private action evidence"}
            arguments = action["input"]
            if not isinstance(arguments, dict):
                raise ValueError("structured tool input required")
            self.db.execute("BEGIN IMMEDIATE")
            try:
                prefix = "gmail.%" if tool.startswith("gmail.") else "research.%"
                ceiling = self.settings.gmail_daily_limit if tool.startswith("gmail.") else self.settings.research_daily_limit
                start = (time.time() // 86400) * 86400
                used = self.db.execute("SELECT COUNT(*) FROM actions WHERE json_extract(action,'$.tool') LIKE ? AND created>=? AND COALESCE(json_extract(context,'$.quotaDenied'),0)=0", (prefix, start)).fetchone()[0]
                if used >= ceiling:
                    self.context["quotaDenied"] = True
                self.db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,NULL,?)", (action_id, binding, encoded(action), encoded(self.profile), "running", encoded(self.context), time.time()))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            if self.context.get("quotaDenied"):
                return self._finish("failed", {"error": "Configured daily provider attempt quota exhausted"}, 0, "quota-denied:" + action_id)
            try:
                if tool == "research.collect":
                    if not research or maximum < 1:
                        raise ValueError("research unavailable or not funded")
                    token_cap = integer(limit.get("maxTokens"), "total token cap", 100, 200000)
                    arguments = {"topic": arguments.get("topic"), "sections": arguments.get("sections"), "min_sources": arguments.get("minSources", 2), **({"source_urls": arguments["sourceUrls"]} if "sourceUrls" in arguments else {})}
                    prepared = research.prepare(action_id, arguments, maximum, before_dispatch=self._check)
                    if len(encoded(prepared["payload"]).encode()) + 2048 + research.config.max_output_tokens > token_cap:
                        raise ValueError("provider total token bound exceeds reservation")
                    self.context["prepared"] = prepared
                    self._context()
                    return self._research_result(research.run(action_id, arguments, maximum, prepared=prepared, before_dispatch=self._check))
                if maximum != 0 or not gmail:
                    raise ValueError("Gmail requires configured identity and zero incremental cost")
                if tool == "gmail.send":
                    if self.settings.mode == "provider-test" and not self.settings.recipients:
                        raise ValueError("provider-test requires controlled recipients")
                    if self.settings.recipients and address(arguments.get("to")) not in self.settings.recipients:
                        raise ValueError("recipient outside controlled allowlist")
                    if "attachment" in arguments:
                        artifact = arguments["attachment"]
                        arguments = {**arguments, "attachment": {**artifact, "media_type": artifact["mediaType"]}}
                        validate_artifact(arguments["attachment"])
                    result = gmail.send(action_id, arguments, before_dispatch=self._check, before_network=self._check)
                    if result["state"] == "unknown":
                        return {"kind": "unknown", "reason": "Gmail send requires observational reconciliation"}
                    data = result.get("data", {})
                    return self._finish(result["state"], {"messageId": data.get("message_id"), "threadId": data.get("thread_id"), "deliveryState": data.get("delivery_state"), "rfcMessageId": data.get("rfc_message_id")}, 0, data.get("message_id") or "rejected:" + action_id)
                result = gmail.read(arguments, before_dispatch=self._check)
                updates = self._receipts(result["messages"])
                messages = [{"messageId": item["message_id"], "threadId": item["thread_id"], "from": item["from"], "senderVerified": item["sender_verified"], "command": item["command"], "inReplyTo": item["in_reply_to"]} for item in result["messages"]]
                return self._finish("succeeded", {"messages": messages, "nextCursor": result.get("next_cursor"), "deliveryUpdates": updates}, 0, "inbox:" + action_id)
            except VerificationDenied:
                return self._finish("failed", {"error": "Central dispatch denied"}, 0, "not-dispatched:" + action_id)
            except Exception:
                if self.context["stage"] == "preparing":
                    return self._finish("failed", {"error": "Provider preflight failed before dispatch"}, 0, "not-dispatched:" + action_id)
                return {"kind": "unknown", "reason": "Provider outcome uncertain; reconcile without repeating"}

    def close(self):
        if self.db:
            self.db.close()


def main():
    gateway = None
    try:
        # No shell arguments, paths, credentials, or provider exceptions are echoed.
        raw = sys.stdin.buffer.read(1_500_001)
        if len(raw) > 1_500_000:
            raise ValueError("input too large")
        request = json.loads(raw)
        gateway = Gateway(Settings(os.environ))
        result = gateway.call(request["operation"], request["action"], request["reservation"])
    except Exception:
        result = {"kind": "unknown", "reason": "Private tool boundary unavailable; retain authorization"}
    finally:
        if gateway:
            gateway.close()
    print(encoded(result))


if __name__ == "__main__":
    main()
