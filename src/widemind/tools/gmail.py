"""Dedicated-account Gmail adapter. All outputs and receipts are private task data.

Message-ID permits reconciliation; Gmail offers no send idempotency key. An
ambiguous mutation is never repeated automatically, including after restart.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses
from typing import Any, Mapping
from urllib.parse import quote, urlencode, urlsplit

from . import gmail_oauth
from .contracts import (Quote, Reconciliation, ToolContext, ToolError, ToolResult,
                        ToolSpec, Unavailable, Uncertain, Usage)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
OPERATIONS = frozenset({"profile", "list", "read", "thread", "attachment", "draft", "send"})
WRITES = frozenset({"draft", "send"})
_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_MSGID = re.compile(r"^<[^<>\s@]+@[^<>\s@]+>$")


def _invalid(message: str) -> ToolError:
    return ToolError("invalid_arguments", message)


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise _invalid(f"{name} must be an integer from {low} to {high}.")
    return value


def _text(value: Any, name: str, *, maximum: int = 2048, headers: bool = False) -> str:
    try:
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        valid = False
    if not valid:
        raise _invalid(f"{name} must be a bounded string.")
    if "\x00" in value or (headers and any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise _invalid(f"{name} contains invalid control characters.")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise _invalid(f"{name} must be a Gmail identifier.")
    return value


def _limit(config: Mapping[str, Any], name: str, default: int, maximum: int) -> int:
    v = config.get(name, default)
    if type(v) is not int or not 1 <= v <= maximum:
        raise Unavailable(f"Owner Gmail configuration {name} must be from 1 to {maximum}.")
    return v


def _addresses(value: Any, name: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 100 or (required and not value):
        raise _invalid(f"{name} must be a list of at most 100 mailbox addresses.")
    result = []
    for address in value:
        address = _text(address, name, maximum=320, headers=True)
        # Accept bare mailbox addresses only: no groups, display names or comments.
        parsed = getaddresses([address])
        if (len(parsed) != 1 or parsed[0][0] or parsed[0][1] != address
                or address.count("@") != 1 or any(c.isspace() for c in address)
                or any(c in address for c in '<>,;():\\"')):
            raise _invalid(f"{name} must contain bare mailbox addresses.")
        local, domain = address.rsplit("@", 1)
        if not local or not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise _invalid(f"{name} contains an invalid mailbox address.")
        result.append(address)
    return result


def _validate(args: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    if not isinstance(args, Mapping) or not isinstance(args.get("operation"), str) or args["operation"] not in OPERATIONS:
        raise _invalid("operation must be profile, list, read, thread, attachment, draft, or send.")
    op = args["operation"]
    allowed = {"operation"}
    if op == "list":
        allowed |= {"query", "max_results", "max_pages", "page_token", "include_spam_trash", "label_ids"}
        _text(args.get("query", ""), "query")
        _integer(args.get("max_results", 100), "max_results", 1, 500)
        _integer(args.get("max_pages", 1), "max_pages", 1, _limit(config, "max_pages", 5, 50))
        if "page_token" in args:
            _text(args["page_token"], "page_token", maximum=4096)
        if type(args.get("include_spam_trash", False)) is not bool:
            raise _invalid("include_spam_trash must be boolean.")
        if "label_ids" in args:
            if not isinstance(args["label_ids"], list) or len(args["label_ids"]) > 20:
                raise _invalid("label_ids must be a bounded list.")
            for label in args["label_ids"]:
                _identifier(label, "label_ids")
    elif op in {"read", "thread", "attachment"}:
        name = "thread_id" if op == "thread" else "message_id"
        allowed.add(name)
        _identifier(args.get(name), name)
        if op == "attachment":
            allowed |= {"attachment_id", "output_path"}
            _text(args.get("attachment_id"), "attachment_id", maximum=2048)
            if not args.get("attachment_id"):
                raise _invalid("attachment_id is required.")
            _text(args.get("output_path"), "output_path", maximum=1024)
            if not args.get("output_path"):
                raise _invalid("output_path is required inside the agent workspace.")
    elif op in WRITES:
        allowed |= {"to", "cc", "bcc", "subject", "text", "html", "attachments",
                    "thread_id", "in_reply_to", "references"}
        recipients = sum((_addresses(args.get(k, []), k, required=k == "to") for k in ("to", "cc", "bcc")), [])
        if len(recipients) > _limit(config, "max_recipients", 20, 100):
            raise _invalid("The message exceeds the configured recipient limit.")
        _text(args.get("subject"), "subject", maximum=998, headers=True)
        max_body = _limit(config, "max_message_bytes", 8_000_000, 25_000_000)
        _text(args.get("text", ""), "text", maximum=max_body)
        if "html" in args:
            _text(args["html"], "html", maximum=max_body)
        if not args.get("text") and not args.get("html"):
            raise _invalid("text or html must contain message content.")
        attachments = args.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > 20:
            raise _invalid("attachments must be a list of at most 20 workspace files.")
        for a in attachments:
            if not isinstance(a, Mapping) or set(a) - {"path", "filename", "content_type"}:
                raise _invalid("Each attachment requires a workspace path and optional filename/content_type.")
            _text(a.get("path"), "attachment path", maximum=1024)
            if not a.get("path"):
                raise _invalid("An attachment requires a workspace path.")
            if "filename" in a:
                filename = _text(a["filename"], "filename", maximum=255, headers=True)
                if not filename or any(c in filename for c in "/\\") or filename in {".", ".."}:
                    raise _invalid("Attachment filename must be a single safe filename.")
            if "content_type" in a and (not isinstance(a["content_type"], str) or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", a["content_type"])):
                raise _invalid("Attachment content_type must be a MIME type.")
        if "thread_id" in args:
            _identifier(args["thread_id"], "thread_id")
            if "in_reply_to" not in args or not args.get("references"):
                raise _invalid("A thread reply requires in_reply_to, references, and its matching Subject.")
        if "in_reply_to" in args:
            if not _MSGID.fullmatch(_text(args["in_reply_to"], "in_reply_to", maximum=998, headers=True)):
                raise _invalid("in_reply_to must be an RFC Message-ID enclosed in angle brackets.")
        if "references" in args:
            refs = args["references"]
            if not isinstance(refs, list) or len(refs) > 50:
                raise _invalid("references must contain at most 50 RFC Message-IDs.")
            for ref in refs:
                if not _MSGID.fullmatch(_text(ref, "references", maximum=998, headers=True)):
                    raise _invalid("references must contain RFC Message-IDs enclosed in angle brackets.")
    if set(args) - allowed:
        raise _invalid("Arguments contain fields unsupported by this Gmail operation.")
    return op


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decode(data: Any, maximum: int) -> bytes:
    if not isinstance(data, str) or len(data) > (maximum + 2) // 3 * 4 + 8:
        raise ToolError("gmail_invalid_response", "Gmail returned content beyond the configured size bound.")
    try:
        decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise ToolError("gmail_invalid_response", "Gmail returned invalid base64url content.") from None
    if len(decoded) > maximum:
        raise ToolError("gmail_invalid_response", "Gmail returned content beyond the configured size bound.")
    return decoded


class _Counter:
    def __init__(self, ctx: ToolContext):
        self.transport, self.ctx = ctx.transport, ctx
        self.units = {"http_attempts": 0, "gmail_attempts": 0, "oauth_attempts": 0,
                      "send_attempts": 0, "draft_attempts": 0, "response_bytes": 0}
        stored = ctx.load_state().get("gmail_usage", {})
        for key in self.units:
            if type(stored.get(key)) is int and stored[key] >= 0:
                self.units[key] = stored[key]

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.units["http_attempts"] += 1
        host = urlsplit(url).hostname
        self.units["gmail_attempts" if host == "gmail.googleapis.com" else "oauth_attempts"] += 1
        if method == "POST" and host == "gmail.googleapis.com":
            self.units["send_attempts" if url.endswith("/send") else "draft_attempts"] += 1
        # Attempts remain measurable even when the provider drops the response
        # or the worker dies before it can produce a ToolResult.
        self.ctx.save_state({"gmail_usage": dict(self.units)})
        response = self.transport.request(method, url, **kwargs)
        self.units["response_bytes"] += len(response.body)
        self.ctx.save_state({"gmail_usage": dict(self.units)})
        return response

    def usage(self) -> Usage:
        return Usage(cost_microusd=0, units=dict(self.units), details={"provider": "gmail", "basis": "cumulative_action_attempts_not_quota_units", "shared_hosting_costs": "accounted_separately"})


class _Rejected(ToolError):
    pass


def _api(ctx: ToolContext, token: str, method: str, path: str, *, query: Any = None,
         body: Mapping[str, Any] | None = None, maximum: int = 2_000_000) -> dict:
    ctx.check()
    url = API + path + (("?" + urlencode(query, doseq=True)) if query else "")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = _canonical(body)
        headers["Content-Type"] = "application/json"
    try:
        response = ctx.transport.request(method, url, headers=headers, body=data,
                                         timeout=ctx.remaining(), max_bytes=maximum, cancel=ctx.check)
    except Exception:
        raise ToolError("gmail_transport", "Gmail request ended without a usable response.") from None
    if response.url != url or len(response.body) > maximum:
        raise ToolError("gmail_invalid_response", "Gmail returned an unexpected location or oversized response.")
    try:
        payload = json.loads(response.body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ToolError("gmail_invalid_response", "Gmail returned an unreadable response.") from None
    if not isinstance(payload, dict):
        raise ToolError("gmail_invalid_response", "Gmail returned an invalid response object.")
    if not 200 <= response.status < 300:
        error = payload.get("error")
        if (response.status in {400, 401, 403, 404, 405, 413, 415, 422, 429}
                and isinstance(error, dict) and error.get("code") == response.status):
            raise _Rejected("gmail_rejected", f"Gmail rejected the request (HTTP {response.status}); inspect private integration settings.")
        raise ToolError("gmail_provider_error", "Gmail did not confirm request completion.")
    return payload


def _enforce_recipients(args: Mapping[str, Any], ctx: ToolContext) -> None:
    policy_ = ctx.policy
    profile = policy_.get("profile", "development")
    allowed = policy_.get("allowed_email_recipients", [])
    if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed):
        raise Unavailable("Owner email recipient policy is invalid.")
    any_allowed = profile == "production" and policy_.get("allow_any_email_recipient") is True
    if profile not in {"development", "sandbox", "production"}:
        raise Unavailable("Owner runtime email profile is invalid.")
    recipients = [r.casefold() for k in ("to", "cc", "bcc") for r in args.get(k, [])]
    if not any_allowed and not set(recipients) <= {v.casefold() for v in allowed}:
        raise ToolError("recipient_not_allowed", "An email recipient is outside the owner-controlled recipient policy.")


def _mime(args: Mapping[str, Any], ctx: ToolContext, message_id: str) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = ctx.config["expected_account"]
    for k in ("to", "cc", "bcc"):
        if args.get(k):
            message[k.title()] = ", ".join(args[k])
    message["Subject"] = args["subject"]
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = message_id
    if args.get("in_reply_to"):
        message["In-Reply-To"] = args["in_reply_to"]
    if args.get("references"):
        message["References"] = " ".join(args["references"])
    message.set_content(args.get("text", ""))
    if "html" in args:
        message.add_alternative(args["html"], subtype="html")
    maximum = _limit(ctx.config, "max_attachment_bytes", 8_000_000, 20_000_000)
    total = 0
    for a in args.get("attachments", []):
        ctx.check()
        content = ctx.workspace.read_bytes(a["path"], max_bytes=maximum)
        total += len(content)
        if total > maximum:
            raise _invalid("Combined attachments exceed the configured byte limit.")
        filename = a.get("filename", a["path"].replace("\\", "/").rsplit("/", 1)[-1])
        if not filename or filename in {".", ".."} or any(c in filename for c in "\r\n\x00/\\"):
            raise _invalid("Attachment filename must be a single safe filename.")
        maintype, subtype = a.get("content_type", "application/octet-stream").split("/", 1)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    # Boundaries and Date are frozen with the private intended MIME receipt.
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            part.set_boundary("widemind-" + _hash((message_id + str(index)).encode())[:40])
    raw = message.as_bytes()
    if len(raw) > _limit(ctx.config, "max_message_bytes", 8_000_000, 25_000_000):
        raise _invalid("Encoded MIME message exceeds the configured byte limit.")
    return raw


def _fingerprint(raw: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    headers = {k: " ".join(str(message.get(k, "")).split()) for k in
               ("From", "To", "Cc", "Bcc", "Subject", "Message-ID", "Date", "In-Reply-To", "References")}
    parts = []
    for p in message.walk():
        if p.is_multipart():
            continue
        content = p.get_payload(decode=True) or b""
        if p.get_content_maintype() == "text":
            content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        parts.append([p.get_content_type(), p.get_content_disposition(), p.get_filename(),
                      p.get("Content-ID"), _hash(content)])
    return _hash(_canonical({"headers": headers, "parts": parts}))


def _message(data: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
    if not isinstance(data, Mapping):
        raise ToolError("gmail_invalid_response", "Gmail returned an invalid message object.")
    result = {"id": data.get("id"), "thread_id": data.get("threadId"), "label_ids": data.get("labelIds", []),
              "internal_date": data.get("internalDate"), "headers": [], "body_parts": [], "attachments": []}
    maximum = _limit(config, "max_read_bytes", 2_000_000, 20_000_000)
    budget, count = maximum, 0

    def walk(part: Any, depth: int) -> None:
        nonlocal budget, count
        if not isinstance(part, dict) or depth > 20 or count >= 500:
            raise ToolError("gmail_invalid_response", "Gmail MIME structure exceeds safe parsing limits.")
        count += 1
        body, mime = part.get("body", {}), part.get("mimeType", "application/octet-stream")
        if not isinstance(body, dict) or not isinstance(mime, str):
            raise ToolError("gmail_invalid_response", "Gmail returned an invalid MIME part.")
        if depth == 0:
            result["headers"] = part.get("headers", [])
        headers = part.get("headers", [])
        if (not isinstance(headers, list) or len(headers) > 1000
                or any(not isinstance(h, dict) or not isinstance(h.get("name"), str)
                       or not isinstance(h.get("value"), str) for h in headers)):
            raise ToolError("gmail_invalid_response", "Gmail returned invalid MIME headers.")
        if body.get("attachmentId") or part.get("filename"):
            result["attachments"].append({"attachment_id": body.get("attachmentId"), "filename": part.get("filename", ""),
                                           "mime_type": mime, "size": body.get("size"), "part_id": part.get("partId")})
        if mime.startswith("text/") and not part.get("filename") and body.get("data"):
            decoded = _decode(body["data"], budget)
            budget -= len(decoded)
            header = Message()
            for h in headers:
                if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type":
                    header["Content-Type"] = str(h.get("value", ""))
            try:
                text = decoded.decode(header.get_content_charset() or "utf-8", errors="replace")
            except LookupError:
                text = decoded.decode("utf-8", errors="replace")
            result["body_parts"].append({"mime_type": mime, "text": text})
        children = part.get("parts", [])
        if not isinstance(children, list):
            raise ToolError("gmail_invalid_response", "Gmail returned invalid child MIME parts.")
        for child in children:
            walk(child, depth + 1)

    if data.get("payload") is not None:
        walk(data["payload"], 0)
    return result


class GmailAdapter:
    spec = ToolSpec("gmail", "Read dedicated Gmail, retrieve attachments, create drafts and send policy-authorized email.", effect="external")

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        _validate(arguments, config)
        gmail_oauth.validate_config(config)
        return Quote(provider="gmail", maximum_microusd=0, paid=False,
                     limits={"max_pages": _limit(config, "max_pages", 5, 50),
                             "max_message_bytes": _limit(config, "max_message_bytes", 8_000_000, 25_000_000)})

    def availability(self, config: Mapping[str, Any], secrets: Any) -> dict:
        return gmail_oauth.availability(config, secrets)

    def _verified(self, ctx: ToolContext) -> tuple[str, dict]:
        token = gmail_oauth.access_token(ctx)
        verified = gmail_oauth.verify(ctx, token)
        return token, verified

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        counter = _Counter(ctx)
        ctx = replace(ctx, transport=counter)
        try:
            ctx.check()
            return self._execute(arguments, ctx, counter)
        except ToolError as error:
            error.usage = counter.usage()
            raise
        except Exception:
            error = ToolError("gmail_operation", "Gmail operation could not finish; inspect its private receipt before retrying.")
            error.usage = counter.usage()
            raise error from None
        except BaseException as error:
            error.usage = counter.usage()
            raise

    def _execute(self, arguments: Mapping[str, Any], ctx: ToolContext, counter: _Counter) -> ToolResult:
        op = _validate(arguments, ctx.config)
        if op in WRITES:
            return self._write(arguments, ctx, counter)
        if op == "attachment":
            stored = ctx.load_state()
            if stored.get("gmail_attachment") or stored.get("local_intent"):
                recovered = self._reconcile_attachment(arguments, ctx, counter)
                if recovered.outcome == "completed":
                    return recovered.result
                raise Uncertain("The attachment write remains unresolved; no file was downloaded or written again.", usage=counter.usage())
        token, verified = self._verified(ctx)
        maximum = _limit(ctx.config, "max_read_bytes", 2_000_000, 20_000_000)
        if op == "profile":
            return ToolResult(verified, counter.usage())
        if op == "list":
            query = {"q": arguments.get("query", ""), "maxResults": arguments.get("max_results", 100),
                     "includeSpamTrash": str(arguments.get("include_spam_trash", False)).lower()}
            if arguments.get("label_ids"):
                query["labelIds"] = arguments["label_ids"]
            token_ = arguments.get("page_token")
            messages, pages = [], 0
            for _ in range(arguments.get("max_pages", 1)):
                if token_:
                    query["pageToken"] = token_
                page = _api(ctx, token, "GET", "/messages", query=query, maximum=maximum)
                rows = page.get("messages", [])
                if not isinstance(rows, list):
                    raise ToolError("gmail_invalid_response", "Gmail returned an invalid message list.")
                messages.extend({"id": m.get("id"), "thread_id": m.get("threadId")} for m in rows if isinstance(m, dict))
                pages += 1
                token_ = page.get("nextPageToken")
                if not token_:
                    break
            return ToolResult({"messages": messages, "pages": pages, "next_page_token": token_, "truncated": bool(token_)}, counter.usage())
        if op == "read":
            message = _api(ctx, token, "GET", "/messages/" + quote(arguments["message_id"], safe=""), query={"format": "full"}, maximum=maximum)
            return ToolResult({"message": _message(message, ctx.config)}, counter.usage())
        if op == "thread":
            thread = _api(ctx, token, "GET", "/threads/" + quote(arguments["thread_id"], safe=""), query={"format": "full"}, maximum=maximum)
            messages = thread.get("messages", [])
            if not isinstance(messages, list) or len(messages) > 500:
                raise ToolError("gmail_invalid_response", "Gmail returned an invalid or oversized thread.")
            return ToolResult({"thread_id": thread.get("id"), "messages": [_message(m, ctx.config) for m in messages]}, counter.usage())
        # Attachment bytes are written only by the isolated Workspace API.
        maximum = _limit(ctx.config, "max_attachment_bytes", 8_000_000, 20_000_000)
        attachment = _api(ctx, token, "GET", "/messages/" + quote(arguments["message_id"], safe="") + "/attachments/" + quote(arguments["attachment_id"], safe=""), maximum=(maximum + 2) // 3 * 4 + 1024)
        content = _decode(attachment.get("data"), maximum)
        intent = {"provider": "gmail_attachment", "arguments_sha256": _hash(_canonical(dict(arguments))),
                  "account": str(ctx.config.get("expected_account", "")).casefold(), "job_id": ctx.job_id,
                  "output_path": arguments["output_path"], "sha256": _hash(content), "bytes": len(content)}
        ctx.save_state({"local_intent": intent, "gmail_attachment": {"intent": intent}})
        ctx.check()
        artifact = ctx.workspace.write_bytes(arguments["output_path"], content, overwrite=False)
        artifact = {**artifact, "path": arguments["output_path"], "sha256": intent["sha256"], "size_bytes": len(content),
                    "job_id": ctx.job_id, "visibility": "private", "trust": "untrusted"}
        data = {"bytes": len(content), "sha256": intent["sha256"], "output_path": arguments["output_path"],
                "job_id": ctx.job_id, "visibility": "private"}
        ctx.save_state({"gmail_attachment": {"intent": intent, "completed": True, "data": data, "artifacts": [artifact]}})
        ctx.check()
        return ToolResult(data, counter.usage(), artifacts=[artifact])

    @staticmethod
    def _outcome(outcome: str, counter: _Counter, detail: str, result: ToolResult | None = None) -> Reconciliation:
        return Reconciliation(outcome, result or ToolResult({}, counter.usage()), detail)

    def _reconcile_attachment(self, args: Mapping[str, Any], ctx: ToolContext, counter: _Counter) -> Reconciliation:
        state = ctx.load_state()
        receipt = state.get("gmail_attachment", {})
        intent = receipt.get("intent", {}) if isinstance(receipt, dict) else {}
        expected = {"provider": "gmail_attachment", "arguments_sha256": _hash(_canonical(dict(args))),
                    "account": str(ctx.config.get("expected_account", "")).casefold(), "job_id": ctx.job_id,
                    "output_path": args["output_path"]}
        if (not isinstance(intent, dict) or any(intent.get(k) != v for k, v in expected.items())
                or state.get("local_intent") != intent or receipt.get("completed") is not True):
            return self._outcome("uncertain", counter, "No matching completed private attachment receipt proves the local write; file existence is not proof.")
        digest, size = intent.get("sha256"), intent.get("bytes")
        data, artifacts = receipt.get("data"), receipt.get("artifacts")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or type(size) is not int or size < 0
                or not isinstance(data, dict) or data.get("sha256") != digest or data.get("bytes") != size
                or data.get("output_path") != args["output_path"] or data.get("job_id") != ctx.job_id
                or not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict)
                or artifacts[0].get("path") != args["output_path"] or artifacts[0].get("sha256") != digest
                or artifacts[0].get("size_bytes") != size or artifacts[0].get("job_id") != ctx.job_id):
            return self._outcome("uncertain", counter, "The completed attachment receipt does not match its private intent and artifact identity.")
        return self._outcome("completed", counter, "Recovered the durable completed private attachment receipt.",
                             ToolResult(data, counter.usage(), artifacts=artifacts))

    def _write(self, args: Mapping[str, Any], ctx: ToolContext, counter: _Counter) -> ToolResult:
        _enforce_recipients(args, ctx)
        old = ctx.load_state().get("gmail", {})
        digest = _hash(_canonical(dict(args)))
        expected = str(ctx.config.get("expected_account", "")).casefold()
        if old and (old.get("arguments_sha256") != digest or old.get("account") != expected):
            raise ToolError("action_conflict", "The Gmail action identity is already bound to a different request or account.")
        if old.get("completed"):
            return ToolResult(old["result"], counter.usage())
        if old.get("rejected"):
            raise ToolError("gmail_rejected", "Gmail explicitly rejected this recorded request; it was not repeated.")
        if old.get("attempted"):
            reconciliation = self._reconcile(args, ctx, counter)
            if reconciliation.outcome == "completed":
                return reconciliation.result
            raise Uncertain("The previous Gmail attempt remains unresolved; no message was repeated.", usage=counter.usage())
        token, _ = self._verified(ctx)
        if not old:
            message_id = "<widemind-" + _hash((expected + "\0" + ctx.action_id).encode()) + "@widemind.invalid>"
            raw = _mime(args, ctx, message_id)
            old = {"version": 1, "operation": args["operation"], "account": expected,
                   "arguments_sha256": digest, "message_id": message_id,
                   "raw": base64.urlsafe_b64encode(raw).decode(), "mime_sha256": _hash(raw),
                   "content_fingerprint": _fingerprint(raw), "thread_id": args.get("thread_id")}
            ctx.save_state({"gmail": old})
        # Save before mark_dispatched as well: interruption between these durable
        # writes causes conservative reconciliation, never a second POST.
        old["attempted"] = True
        ctx.save_state({"gmail": old})
        ctx.mark_dispatched({"provider": "gmail", "operation": args["operation"], "account": expected,
                             "message_id": old["message_id"], "mime_sha256": old["mime_sha256"]})
        message = {"raw": old["raw"]}
        if old.get("thread_id"):
            message["threadId"] = old["thread_id"]
        try:
            response = _api(ctx, token, "POST", "/drafts" if args["operation"] == "draft" else "/messages/send",
                            body={"message": message} if args["operation"] == "draft" else message)
            if args["operation"] == "draft":
                provider_id = _identifier(response.get("id"), "returned draft ID")
                msg = response.get("message", {})
                if not isinstance(msg, dict):
                    raise ToolError("gmail_invalid_response", "Gmail returned an invalid draft message.")
                result = {"operation": "draft", "draft_id": provider_id, "message_id": msg.get("id"), "thread_id": msg.get("threadId"), "rfc_message_id": old["message_id"], "provider_accepted": True}
            else:
                provider_id = _identifier(response.get("id"), "returned message ID")
                result = {"operation": "send", "message_id": provider_id, "thread_id": response.get("threadId"), "rfc_message_id": old["message_id"], "provider_accepted": True, "delivery_confirmed": False}
        except _Rejected:
            old["rejected"] = True
            ctx.save_state({"gmail": old})
            raise
        except Exception:
            raise Uncertain("Gmail may have accepted the request; reconcile this action before further delivery.", usage=counter.usage()) from None
        old.update({"completed": True, "result": result})
        ctx.save_state({"gmail": old})
        return ToolResult(result, counter.usage())

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        counter = _Counter(ctx)
        ctx = replace(ctx, transport=counter)
        try:
            ctx.check()
            op = _validate(arguments, ctx.config)
            if op == "attachment":
                return self._reconcile_attachment(arguments, ctx, counter)
            if op not in WRITES:
                return self._outcome("not_executed", counter, "Read operations have no external mutation to repeat; measured attempts are retained.")
            return self._reconcile(arguments, ctx, counter)
        except ToolError as error:
            error.usage = counter.usage()
            raise
        except Exception:
            return self._outcome("uncertain", counter, "Gmail reconciliation could not establish the outcome; measured attempts are retained.")
        except BaseException as error:
            error.usage = counter.usage()
            raise

    def _reconcile(self, args: Mapping[str, Any], ctx: ToolContext, counter: _Counter) -> Reconciliation:
        state = ctx.load_state().get("gmail", {})
        if (not state or state.get("arguments_sha256") != _hash(_canonical(dict(args)))
                or state.get("account") != str(ctx.config.get("expected_account", "")).casefold()):
            return self._outcome("uncertain", counter, "No matching private Gmail intent is available.")
        if state.get("completed"):
            return Reconciliation("completed", ToolResult(state["result"], counter.usage()), "Recovered the durable provider acceptance receipt.")
        if state.get("rejected"):
            return self._outcome("not_executed", counter, "The provider explicitly rejected the recorded request.")
        try:
            token, _ = self._verified(ctx)
            is_draft = state["operation"] == "draft"
            path = "/drafts" if is_draft else "/messages"
            query = {"q": "rfc822msgid:" + state["message_id"], "includeSpamTrash": "true", "maxResults": 100}
            candidates = []
            for _ in range(_limit(ctx.config, "max_pages", 5, 50)):
                response = _api(ctx, token, "GET", path, query=query)
                rows = response.get("drafts" if is_draft else "messages", [])
                if not isinstance(rows, list):
                    return self._outcome("uncertain", counter, "Gmail returned invalid reconciliation candidates.")
                candidates.extend(rows)
                page = response.get("nextPageToken")
                if not page:
                    break
                query["pageToken"] = page
            else:
                return self._outcome("uncertain", counter, "The bounded reconciliation search has more pages.")
            # Multiple IDs are ambiguous even if one has the expected body.
            ids = {r.get("id") for r in candidates if isinstance(r, dict) and isinstance(r.get("id"), str)}
            if len(ids) != 1:
                return self._outcome("uncertain", counter, "Gmail search did not identify exactly one candidate; absence never proves failure.")
            provider_id = _identifier(next(iter(ids)), "candidate ID")
            candidate = _api(ctx, token, "GET", path + "/" + quote(provider_id, safe=""), query={"format": "raw"},
                             maximum=_limit(ctx.config, "max_message_bytes", 8_000_000, 25_000_000) * 2)
            message = candidate.get("message", {}) if is_draft else candidate
            labels = message.get("labelIds", []) if isinstance(message, dict) else []
            if not isinstance(message, dict) or (not is_draft and (not isinstance(labels, list) or "SENT" not in labels)):
                return self._outcome("uncertain", counter, "The candidate does not prove the requested provider operation.")
            raw = _decode(message.get("raw"), _limit(ctx.config, "max_message_bytes", 8_000_000, 25_000_000))
            if _fingerprint(raw) != state.get("content_fingerprint"):
                return self._outcome("uncertain", counter, "The candidate does not match the recorded message contents and recipients.")
            result = {"operation": state["operation"], "message_id": message.get("id"), "thread_id": message.get("threadId"),
                      "rfc_message_id": state["message_id"], "provider_accepted": True, "reconciled": True}
            if is_draft:
                result["draft_id"] = provider_id
            else:
                result["delivery_confirmed"] = False
            state.update({"completed": True, "result": result})
            ctx.save_state({"gmail": state})
            return Reconciliation("completed", ToolResult(result, counter.usage()), "Found one provider record matching the private intended message.")
        except Exception:
            return self._outcome("uncertain", counter, "Gmail reconciliation could not establish the outcome; no mutation was retried.")
