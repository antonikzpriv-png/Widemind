"""Owner-configured conditional HTTP PUT publisher with digest reconciliation."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from .ai import _integer, _json, _text
from .contracts import (Quote, Reconciliation, Secrets, ToolContext, ToolError,
                        ToolResult, ToolSpec, Unavailable, Uncertain, Usage)


def _url(value: Any, name: str, *, local: bool) -> str:
    value = _text(value, name, 2048).rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate malformed ports before any dispatch.
    except ValueError as exc:
        raise Unavailable(f"{name} must be a valid base URL.") from exc
    local_http = local and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(ord(c) < 33 for c in value):
        raise Unavailable(f"{name} must be a credential-free HTTPS base URL; explicit sandbox localhost HTTP is test-only.")
    if any(part in {".", ".."} for part in unquote(parsed.path).split("/")) or "\\" in unquote(parsed.path):
        raise Unavailable(f"{name} cannot contain path traversal.")
    return value


def _settings(config: Mapping[str, Any]) -> dict:
    try:
        if not isinstance(config, Mapping) or type(config.get("sandbox")) is not bool or type(config.get("paid")) is not bool:
            raise Unavailable("Configure explicit sandbox and paid booleans for publishing.")
        sandbox = config["sandbox"]
        base_url = _url(config.get("base_url"), "base_url", local=sandbox and config.get("allow_local_http") is True)
        public_base = config.get("public_base_url")
        if public_base is not None:
            public_base = _url(public_base, "public_base_url", local=sandbox and config.get("allow_local_http") is True)
        allocation = _text(config.get("hosting_allocation"), "hosting_allocation", 2000)
        charge = _integer(config.get("publication_microusd"), "publication_microusd", 1 if config["paid"] else 0, 10**12)
        if not config["paid"] and charge != 0:
            raise Unavailable("Unpaid publishing requires publication_microusd:0 and an explicit owner hosting allocation.")
        secret_ref = config.get("secret_ref")
        auth_scheme = config.get("auth_scheme", "bearer")
        if auth_scheme not in ("bearer", "basic", "none"):
            raise Unavailable("auth_scheme must be bearer, basic or explicitly none.")
        if auth_scheme != "none":
            secret_ref = _text(secret_ref, "secret_ref", 200)
        elif secret_ref is not None:
            raise Unavailable("secret_ref must be absent when auth_scheme is none.")
        suffix = config.get("suffix", ".bin")
        if not isinstance(suffix, str) or not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            raise Unavailable("suffix must be a configured file extension such as .html or .png.")
        content_type = _text(config.get("content_type", "application/octet-stream"), "content_type", 150)
        if any(ord(c) < 32 or ord(c) > 126 for c in content_type):
            raise Unavailable("content_type must be a printable ASCII header value.")
        return {"base_url": base_url, "public_base_url": public_base, "sandbox": sandbox,
                "paid": config["paid"], "publication_microusd": charge, "hosting_allocation": allocation,
                "secret_ref": secret_ref, "auth_scheme": auth_scheme, "suffix": suffix, "content_type": content_type,
                "max_artifact_bytes": _integer(config.get("max_artifact_bytes", 8_000_000), "max_artifact_bytes", 1, 32_000_000),
                "max_receipt_bytes": _integer(config.get("max_receipt_bytes", 64_000), "max_receipt_bytes", 1, 1_000_000)}
    except ToolError as exc:
        if isinstance(exc, Unavailable):
            raise
        raise Unavailable(f"Publishing configuration: {exc.message}") from exc


def _path(arguments: Mapping[str, Any]) -> str:
    if not isinstance(arguments, Mapping) or set(arguments) != {"artifact_path"}:
        raise ToolError("invalid_input", "Publishing accepts only artifact_path in the dedicated workspace.")
    path = _text(arguments["artifact_path"], "artifact_path", 500)
    if path.startswith("/") or "\\" in path or any(p in {"", ".", ".."} for p in path.split("/")) or any(ord(c) < 32 for c in path):
        raise ToolError("invalid_input", "artifact_path must be a relative workspace file path without traversal.")
    return path


def _policy(ctx: ToolContext, settings: Mapping[str, Any]) -> None:
    ctx.check()
    profile = ctx.policy.get("profile", "development")
    if not isinstance(profile, str) or profile not in {"development", "sandbox", "production"}:
        raise ToolError("forbidden", "Unknown owner runtime profile.")
    if ctx.policy.get("allow_external_effects") is not True:
        raise ToolError("forbidden", "Publishing requires owner policy.allow_external_effects:true.")
    if profile in {"development", "sandbox"} and settings["sandbox"] is not True:
        raise ToolError("forbidden", "Development and sandbox profiles require a configured sandbox publishing destination.")


def _headers(settings: Mapping[str, Any], secrets: Secrets) -> dict:
    headers = {"Content-Type": settings["content_type"]}
    if settings["auth_scheme"] == "none":
        return headers
    value = secrets.get(settings["secret_ref"])
    if not value:
        raise Unavailable(f"Set the dedicated publishing secret referenced by {settings['secret_ref']} in the trusted secret store.")
    if not isinstance(value, str) or any(c in value for c in "\r\n"):
        raise Unavailable("The configured publishing secret is invalid.")
    if settings["auth_scheme"] == "basic":
        import base64
        value = base64.b64encode(value.encode()).decode("ascii")
    headers["Authorization"] = ("Basic " if settings["auth_scheme"] == "basic" else "Bearer ") + value
    return headers


class PublishAdapter:
    spec = ToolSpec("publish.artifact", "Publish one workspace artifact through an owner-configured conditional HTTP PUT endpoint.", "external")

    def availability(self, config: Mapping[str, Any], secrets: Secrets) -> dict:
        try:
            settings = _settings(config)
            _headers(settings, secrets)
            return {"available": True, "backend": "http_put", "sandbox": settings["sandbox"]}
        except ToolError as exc:
            return {"available": False, "reason": exc.message}

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        _path(arguments)
        settings = _settings(config)
        return Quote(settings["publication_microusd"], "owner_http_put", settings["paid"],
                     {"max_artifact_bytes": settings["max_artifact_bytes"], "conditional_create": True,
                      "hosting_allocation": settings["hosting_allocation"]})

    def _intent(self, arguments: Mapping[str, Any], ctx: ToolContext, settings: dict) -> tuple[dict, bytes]:
        path = _path(arguments)
        ctx.check()
        data = ctx.workspace.read_bytes(path, max_bytes=settings["max_artifact_bytes"])
        if not isinstance(data, bytes) or len(data) > settings["max_artifact_bytes"]:
            raise ToolError("output_limit", "Workspace artifact exceeds its publishing byte limit.")
        digest = hashlib.sha256(data).hexdigest()
        key = hashlib.sha256(ctx.action_id.encode()).hexdigest() + "-" + digest + settings["suffix"]
        intent = {"version": 1, "operation": "http_put", "artifact_path": path, "sha256": digest,
                  "bytes": len(data), "key": key, "target": settings["base_url"] + "/" + key,
                  "config_sha256": hashlib.sha256(_json(settings)).hexdigest()}
        return intent, data

    def _recover(self, ctx: ToolContext, settings: dict, intent: dict) -> Reconciliation:
        headers = _headers(settings, ctx.secrets)
        headers["Cache-Control"] = "no-cache"
        headers["Accept-Encoding"] = "identity"
        try:
            ctx.check()
            response = ctx.transport.request("GET", intent["target"], headers=headers, timeout=ctx.remaining(),
                max_bytes=settings["max_artifact_bytes"], cancel=ctx.check)
            # Receipt identity/status/digest are private, durable evidence.
            actual = hashlib.sha256(response.body).hexdigest()
            ctx.save_state({"publish_verification": {"status": response.status, "sha256": actual,
                "bytes": len(response.body), "etag": response.headers.get("etag"),
                "request_id": response.headers.get("x-request-id")}})
            if response.status != 200 or actual != intent["sha256"] or len(response.body) != intent["bytes"]:
                return Reconciliation("uncertain", detail="Published object absent, unreadable or conflicting; absence may be eventual consistency and never authorizes another PUT.")
            usage = Usage(settings["publication_microusd"], {"publications": 1, "bytes": intent["bytes"]},
                          response.headers.get("x-request-id"), settings["paid"],
                          {"basis": "owner fixed per-publication allocation, not provider invoice", "hosting_allocation": settings["hosting_allocation"]})
            result_data = {"key": intent["key"], "sha256": actual, "bytes": len(response.body),
                           "verified": True, "visibility": "sandbox" if settings["sandbox"] else "private"}
            if settings["public_base_url"]:
                result_data["url"] = settings["public_base_url"] + "/" + intent["key"]
                result_data["url_basis"] = "owner-configured public mapping; bytes verified at publishing endpoint"
                if not settings["sandbox"]:
                    result_data["visibility"] = "public"
            ctx.save_state({"publish_completed": True})
            return Reconciliation("completed", ToolResult(result_data, usage))
        except Exception as exc:
            return Reconciliation("uncertain", detail=getattr(exc, "message", "Object verification failed; retain intent and reconcile without another PUT."))

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        settings = _settings(ctx.config)
        _policy(ctx, settings)
        intent, data = self._intent(arguments, ctx, settings)
        saved = ctx.load_state().get("publish_intent")
        if saved:
            if saved != intent:
                raise ToolError("intent_mismatch", "Publishing input, artifact bytes or owner configuration changed; recover the original immutable action.")
            result = self._recover(ctx, settings, saved)
            if result.outcome == "completed" and result.result:
                return result.result
            raise Uncertain(result.detail)
        headers = _headers(settings, ctx.secrets)
        headers["If-None-Match"] = "*"
        ctx.save_state({"publish_intent": intent})
        ctx.mark_dispatched(intent)
        try:
            ctx.check()
            response = ctx.transport.request("PUT", intent["target"], headers=headers, body=data,
                timeout=ctx.remaining(), max_bytes=settings["max_receipt_bytes"], cancel=ctx.check)
            ctx.save_state({"publish_put_receipt": {"status": response.status, "etag": response.headers.get("etag"),
                "request_id": response.headers.get("x-request-id"), "body_sha256": hashlib.sha256(response.body).hexdigest()}})
        except Exception as exc:
            raise Uncertain("Publication PUT was dispatched but no complete receipt was saved; recover by verifying GET, never resend blindly.") from exc
        result = self._recover(ctx, settings, intent)
        if result.outcome == "completed" and result.result:
            return result.result
        raise Uncertain(result.detail)

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        settings = _settings(ctx.config)
        _policy(ctx, settings)
        saved = ctx.load_state().get("publish_intent")
        if not saved:
            return Reconciliation("uncertain", detail="No immutable publishing intent; no evidence authorizes a PUT retry.")
        intent, _ = self._intent(arguments, ctx, settings)
        if intent != saved:
            return Reconciliation("uncertain", detail="Publishing input, content or configuration differs from the saved immutable intent.")
        return self._recover(ctx, settings, saved)
