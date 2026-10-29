"""Dedicated Gmail OAuth adapter. Sending acceptance never proves recipient delivery."""
import base64
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import getaddresses
import hashlib
import json
import re
import time
import urllib.parse

from .validation import single_line, text
from .http import HTTPS, TransportError


@dataclass(frozen=True)
class GmailConfig:
    email_address: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    timeout_seconds: int = 20

    def __post_init__(self):
        address(self.email_address)
        for key in ("client_id", "client_secret", "refresh_token"):
            text(getattr(self, key), "Gmail OAuth credential")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60:
            raise ValueError("invalid Gmail timeout")


def address(value):
    value = single_line(value, "email address", 254)
    parsed = getaddresses([value])
    if len(parsed) != 1 or parsed[0][0] or parsed[0][1] != value or not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
        raise ValueError("plain email address required")
    return value.lower()


def rfc_message_id(action_id):
    return "<" + hashlib.sha256(action_id.encode()).hexdigest() + "@widemind.invalid>"


def _body_text(payload):
    pieces = []
    stack = [payload]
    total = 0
    while stack:
        part = stack.pop()
        if not isinstance(part, dict):
            raise ValueError("invalid Gmail MIME structure")
        children = part.get("parts", [])
        if len(stack) + len(children) > 100:
            raise ValueError("Gmail MIME part limit")
        stack.extend(reversed(children))
        if part.get("mimeType") == "text/plain":
            encoded = part.get("body", {}).get("data", "")
            if not isinstance(encoded, str) or len(encoded) > 100000:
                raise ValueError("Gmail message body limit")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            total += len(raw)
            if total > 65536:
                raise ValueError("Gmail message body limit")
            pieces.append(raw.decode("utf-8", errors="replace"))
    return "\n".join(pieces)


def _aligned_dmarc(header, domain):
    """Read actual result clauses, never result-like text in CFWS or strings.

    Authentication-Results permits nested comments and quoted property values.
    Neither is authority to introduce a method/result or a header.from property.
    Ambiguous, malformed, or multiple DMARC results fail closed.
    """
    if not isinstance(header, str) or len(header) > 65536:
        return False
    visible, depth, quoted, escaped = [], 0, False, False
    for char in header:
        if escaped:
            escaped = False
            continue
        if (depth or quoted) and char == "\\":
            escaped = True
        elif depth:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        elif quoted:
            if char == '"':
                quoted = False
        elif char == "(":
            visible.append(" ")
            depth = 1
        elif char == '"':
            # A marker prevents removed strings from joining visible tokens.
            visible.append('""')
            quoted = True
        elif char == ")" or ord(char) < 32 and char not in "\r\n\t":
            return False
        else:
            visible.append(char)
    if depth or quoted or escaped:
        return False
    clauses = "".join(visible).lower().split(";")
    if clauses[0].strip() != "mx.google.com":
        return False
    dmarc = [clause.strip() for clause in clauses[1:]
             if re.match(r"\s*dmarc(?:[\s=/]|$)", clause)]
    if len(dmarc) != 1 or not re.match(r"dmarc(?:/\d+)?\s*=\s*pass(?:\s|$)", dmarc[0]):
        return False
    aligned = re.findall(r"(?:^|\s)header\.from\s*=\s*([a-z0-9.-]+)(?=\s|$)", dmarc[0])
    return len(aligned) == 1 and aligned[0] == domain


def normalize_message(message):
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    if not isinstance(headers, list) or len(headers) > 200:
        raise ValueError("invalid Gmail headers")
    def values(name):
        return [entry.get("value", "") for entry in headers if entry.get("name", "").lower() == name]
    sender_fields = values("from")
    if len(sender_fields) != 1:
        raise ValueError("ambiguous sender")
    parsed = getaddresses(sender_fields)
    if len(parsed) != 1:
        raise ValueError("ambiguous sender")
    sender = address(parsed[0][1])
    # Gmail's top Authentication-Results header must attest aligned DMARC. Do
    # not accept a lower injected lookalike, SPF-only success, or bare From.
    auth = values("authentication-results")
    verified = bool(auth and _aligned_dmarc(auth[0], sender.rsplit("@", 1)[1]))
    body = _body_text(payload)
    try:
        command = json.loads(body)
        if not isinstance(command, dict) or len(json.dumps(command, ensure_ascii=True)) > 50000 or command.get("type") not in {"request", "accept", "opt_out", "acknowledge"}:
            command = {"type": "clarification"}
    except (ValueError, TypeError):
        command = {"type": "clarification"}
    return {"message_id": single_line(message.get("id"), "message ID", 300),
            "thread_id": single_line(message.get("threadId"), "thread ID", 300),
            "from": sender, "sender_verified": verified, "command": command,
            "in_reply_to": values("in-reply-to")[0] if values("in-reply-to") else None}


class GmailClient:
    API = "https://gmail.googleapis.com/gmail/v1/users/me"
    TOKEN = "https://oauth2.googleapis.com/token"

    def __init__(self, config, *, transport=None, clock=time.monotonic):
        self.config, self.transport, self.clock = config, transport or HTTPS(), clock
        self._access_token = None
        self._expires_at = 0
        self._identity_checked = False

    def _remaining(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TransportError("Gmail whole-operation deadline exceeded")
        return remaining

    def _headers(self, deadline, before_dispatch=None):
        if self._access_token is None or self._expires_at <= self.clock():
            if before_dispatch:
                before_dispatch()
            body = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": self.config.client_id,
                "client_secret": self.config.client_secret, "refresh_token": self.config.refresh_token}).encode()
            response = self.transport.request("POST", self.TOKEN, headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body, timeout=self._remaining(deadline), maximum_bytes=65536, before_dispatch=before_dispatch)
            if response.status != 200:
                raise ValueError("Gmail OAuth unavailable; inspect dedicated-account setup")
            data = response.json()
            token = text(data.get("access_token"), "OAuth access token")
            expires = data.get("expires_in")
            if type(expires) is not int or not 1 <= expires <= 86400:
                raise ValueError("invalid OAuth expiry")
            self._access_token = token
            self._expires_at = self.clock() + max(0, expires - 60)
            self._identity_checked = False
        return {"Authorization": "Bearer " + self._access_token, "Content-Type": "application/json"}

    def _request(self, method, path, body=None, *, ensure_identity=True, before_dispatch=None, before_network=None, deadline=None):
        deadline = self.clock() + self.config.timeout_seconds if deadline is None else deadline
        headers = self._headers(deadline, before_network)
        if ensure_identity and not self._identity_checked:
            if before_network:
                before_network()
            response = self.transport.request("GET", self.API + "/profile", headers=headers,
                timeout=self._remaining(deadline), maximum_bytes=65536, before_dispatch=before_network)
            if response.status != 200 or address(response.json().get("emailAddress")) != address(self.config.email_address):
                raise ValueError("OAuth account does not match WideMind's dedicated Gmail identity")
            self._identity_checked = True
        guard = before_dispatch or before_network
        if guard:
            guard()
        response = self.transport.request(method, self.API + path, headers=headers,
            body=json.dumps(body).encode() if body is not None else None, timeout=self._remaining(deadline), maximum_bytes=524288,
            before_dispatch=guard)
        return response

    def send(self, action_id, arguments, *, before_dispatch=None, before_network=None):
        recipient = address(arguments.get("to"))
        subject = single_line(arguments.get("subject"), "subject", 300)
        body = text(arguments.get("body"), "mail body", 100000)
        if "attachment" in arguments:
            body += ("\n\nPlease confirm receipt by replying to this email with:\n" +
                     json.dumps({"type": "acknowledge", "action_id": action_id}, separators=(",", ":")) +
                     "\nKeep the reply in this email thread so we can verify the delivery.\n")
        message = EmailMessage(policy=SMTP)
        message["From"] = address(self.config.email_address)
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = rfc_message_id(action_id)
        message["X-WideMind-Action"] = action_id
        message.set_content(body)
        if "attachment" in arguments:
            artifact = arguments["attachment"]
            from .validation import validate_artifact
            validate_artifact(artifact)
            kind, subtype = artifact["media_type"].split("/", 1)
            message.add_attachment(artifact["content"].encode(), maintype=kind, subtype=subtype, filename=artifact["filename"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        response = self._request("POST", "/messages/send", {"raw": raw}, before_dispatch=before_dispatch, before_network=before_network)
        if response.status in {400, 401, 403, 404, 413, 429}:
            return {"state": "failed", "reason": "Gmail rejected the send request"}
        if response.status != 200:
            return {"state": "unknown", "reason": "Gmail send outcome uncertain"}
        data = response.json()
        return {"state": "succeeded", "data": {"message_id": single_line(data.get("id"), "sent message ID", 300),
            "thread_id": single_line(data.get("threadId"), "sent thread ID", 300), "delivery_state": "accepted",
            "rfc_message_id": rfc_message_id(action_id)}}

    def reconcile_send(self, action_id):
        query = urllib.parse.urlencode({"q": "in:sent rfc822msgid:" + rfc_message_id(action_id)[1:-1], "maxResults": 2})
        response = self._request("GET", "/messages?" + query)
        if response.status != 200:
            return {"state": "unknown", "reason": "Gmail sent reconciliation unavailable"}
        matches = response.json().get("messages", [])
        if not isinstance(matches, list) or len(matches) != 1:
            return {"state": "unknown", "reason": "no unique Gmail sent evidence; do not resend"}
        message = matches[0]
        return {"state": "succeeded", "data": {"message_id": single_line(message.get("id"), "sent message ID", 300),
            "thread_id": single_line(message.get("threadId"), "sent thread ID", 300),
            "delivery_state": "accepted", "rfc_message_id": rfc_message_id(action_id)}}

    def read(self, arguments, *, before_dispatch=None):
        deadline = self.clock() + self.config.timeout_seconds
        cursor = arguments.get("cursor")
        params = {"maxResults": 10, "q": "in:inbox"}
        if cursor:
            params["pageToken"] = single_line(cursor, "Gmail page cursor", 1000)
        response = self._request("GET", "/messages?" + urllib.parse.urlencode(params), deadline=deadline, before_network=before_dispatch)
        if response.status != 200:
            raise ValueError("Gmail inbox unavailable")
        data = response.json()
        items = data.get("messages", [])
        if not isinstance(items, list) or len(items) > 10:
            raise ValueError("Gmail inbox batch limit")
        messages = []
        for item in items:
            message_id = single_line(item.get("id"), "message ID", 300)
            full = self._request("GET", "/messages/" + urllib.parse.quote(message_id, safe="") + "?format=full",
                                 deadline=deadline, before_network=before_dispatch)
            if full.status != 200:
                raise ValueError("Gmail message lookup unavailable")
            try:
                messages.append(normalize_message(full.json()))
            except (ValueError, TypeError, KeyError, AttributeError):
                # One malformed message is skipped; it cannot authorize an action.
                continue
        return {"messages": messages, "next_cursor": data.get("nextPageToken")}
