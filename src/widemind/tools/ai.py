"""Bounded OpenAI Responses requests through the trusted tool gateway.

No SDK, implicit retries, remote tools, arbitrary URLs or caller credentials.
All returned content (including a proposed plan) remains untrusted task data.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import asdict
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from .contracts import (Quote, Reconciliation, Secrets, ToolContext, ToolError,
                        ToolResult, ToolSpec, Unavailable, Uncertain, Usage)


def _integer(value: Any, name: str, minimum: int = 1, maximum: int = 1_000_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ToolError("invalid_input", f"{name} must be an integer in {minimum}..{maximum}.")
    return value


def _text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ToolError("invalid_input", f"{name} must be a nonempty string.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ToolError("invalid_input", f"{name} must contain valid Unicode.") from exc
    if size > maximum:
        raise ToolError("invalid_input", f"{name} exceeds its {maximum}-byte limit.")
    return value


def _json(value: Any) -> bytes:
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ToolError("invalid_input", "Input must contain finite, serializable JSON values.") from exc


def _settings(config: Mapping[str, Any], *, require_context: bool = True) -> dict:
    if not isinstance(config, Mapping):
        raise Unavailable("Configure an owner-controlled AI provider object.")
    try:
        model = _text(config.get("model"), "configured model", 200)
        secret_ref = _text(config.get("secret_ref"), "configured secret_ref", 200)
        endpoint = _text(config.get("endpoint", "https://api.openai.com/v1"), "configured endpoint", 2048).rstrip("/")
        parts = urlsplit(endpoint)
        local = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        if (parts.scheme != "https" and not (local and config.get("allow_local_http") is True)) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ToolError("invalid_input", "Endpoint must be HTTPS without credentials/query/fragment; explicit local HTTP is test-only.")
        rates = config.get("tariff", {})
        if not isinstance(rates, Mapping):
            raise ToolError("invalid_input", "tariff must be an owner-controlled object.")
        input_rate = _integer(rates.get("input_microusd_per_million_tokens"), "input tariff", 0, 10**12)
        output_rate = _integer(rates.get("output_microusd_per_million_tokens"), "output tariff", 0, 10**12)
        # Use the largest configured rate for all input categories. Cache writes
        # can cost more than ordinary input; discounts are never assumed.
        input_rate = max(input_rate, _integer(rates.get("cache_write_microusd_per_million_tokens", input_rate), "cache-write tariff", 0, 10**12))
        if not input_rate and not output_rate:
            raise ToolError("invalid_input", "A paid provider requires a positive configured tariff.")
        return dict(model=model, secret_ref=secret_ref, endpoint=endpoint, input_rate=input_rate,
                    output_rate=output_rate,
                    max_input_bytes=_integer(config.get("max_input_bytes", 32_000), "max_input_bytes", 1, 1_000_000),
                    max_output_bytes=_integer(config.get("max_output_bytes", 128_000), "max_output_bytes", 1, 8_000_000),
                    max_response_bytes=_integer(config.get("max_response_bytes", 2_000_000), "max_response_bytes", 1024, 64_000_000),
                    max_output_tokens=_integer(config.get("max_output_tokens", 4096), "configured max_output_tokens", 1, 1_000_000),
                    # A model's documented context ceiling, never a tokenizer estimate.
                    context_tokens=_integer(config.get("model_context_tokens") if require_context else 1, "model_context_tokens", 1, 10_000_000),
                    maximum_microusd=_integer(config.get("maximum_microusd", 5_000_000), "configured maximum_microusd", 1, 10**12))
    except ToolError as exc:
        raise Unavailable(f"AI provider configuration: {exc.message}") from exc


def _cost(input_tokens: int, output_tokens: int, settings: Mapping[str, Any]) -> int:
    # Ceiling once after summing exact integer numerators, never binary floats.
    numerator = input_tokens * settings["input_rate"] + output_tokens * settings["output_rate"]
    return (numerator + 999_999) // 1_000_000


def _usage(payload: Mapping[str, Any], request_id: str | None, settings: Mapping[str, Any], *, images: int | None = None) -> Usage:
    raw = payload.get("usage")
    units = {} if images is None else {"images": images}
    if not isinstance(raw, dict):
        return Usage(None, units, request_id, True, {"reason": "Provider omitted usage; retain reservation."})
    try:
        inp = _integer(raw.get("input_tokens"), "provider input_tokens", 0, 10**12)
        out = _integer(raw.get("output_tokens"), "provider output_tokens", 0, 10**12)
        total = _integer(raw.get("total_tokens"), "provider total_tokens", 0, 2 * 10**12)
        if total != inp + out:
            raise ValueError("Inconsistent usage total")
    except (ToolError, ValueError):
        return Usage(None, units, request_id, True, {"reason": "Provider usage is incomplete or inconsistent; retain reservation."})
    units.update(input_tokens=inp, output_tokens=out, total_tokens=total)
    return Usage(_cost(inp, out, settings), units, request_id, True,
                 {"tariff_basis": "owner integer micro-USD per million tokens; ceiling after sum", "provider_usage": raw})


def _credentials(settings: Mapping[str, Any], secrets: Secrets) -> dict:
    key = secrets.get(settings["secret_ref"])
    if not key:
        raise Unavailable(f"Set the dedicated provider secret referenced by {settings['secret_ref']} in the trusted secret store.")
    if not isinstance(key, str) or any(c in key for c in "\r\n"):
        raise Unavailable("The configured provider secret is invalid.")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _decode_body(encoded: str) -> dict:
    try:
        value = json.loads(base64.b64decode(encoded, validate=True))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise Uncertain("Provider returned an invalid JSON object; saved response needs operator reconciliation.") from exc


def _save_response(ctx: ToolContext, response: Any, prefix: str) -> dict:
    # Save raw receipt and request identity before JSON/output processing.
    ctx.save_state({f"{prefix}_response_body": base64.b64encode(response.body).decode("ascii"),
                    f"{prefix}_request_id": response.headers.get("x-request-id"),
                    f"{prefix}_http_status": response.status})
    payload = _decode_body(base64.b64encode(response.body).decode("ascii"))
    if isinstance(payload.get("id"), str):
        ctx.save_state({f"{prefix}_response_id": payload["id"]})
    return payload


def _schema(schema: Any, *, depth: int = 0) -> None:
    """Small explicit strict-schema subset, validated locally as well as remotely."""
    if not isinstance(schema, dict) or depth > 8:
        raise ToolError("invalid_input", "JSON schema must be an object at depth <=8.")
    kind = schema.get("type")
    if not isinstance(kind, str):
        raise ToolError("invalid_input", "Schema type must be a supported string.")
    allowed = {"type", "description", "enum"}
    if kind == "object":
        allowed |= {"properties", "required", "additionalProperties"}
        props = schema.get("properties")
        required = schema.get("required")
        if not isinstance(props, dict) or len(props) > 64 or schema.get("additionalProperties") is not False or not isinstance(required, list) or any(not isinstance(key, str) for key in required) or sorted(required) != sorted(props):
            raise ToolError("invalid_input", "Strict objects require <=64 properties, every property required, and additionalProperties:false.")
        for name, child in props.items():
            _text(name, "schema property name", 100)
            _schema(child, depth=depth + 1)
    elif kind == "array":
        allowed |= {"items", "minItems", "maxItems"}
        _schema(schema.get("items"), depth=depth + 1)
        _integer(schema.get("maxItems"), "schema maxItems", 0, 100)
        _integer(schema.get("minItems", 0), "schema minItems", 0, schema["maxItems"])
    elif kind == "string":
        allowed |= {"minLength", "maxLength"}
        _integer(schema.get("maxLength"), "schema maxLength", 0, 100_000)
        _integer(schema.get("minLength", 0), "schema minLength", 0, schema["maxLength"])
    elif kind in {"number", "integer"}:
        allowed |= {"minimum", "maximum"}
        for field in ("minimum", "maximum"):
            value = schema.get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ToolError("invalid_input", "Numeric schema bounds must be finite numbers.")
        if schema["minimum"] > schema["maximum"]:
            raise ToolError("invalid_input", "Numeric schema bounds are reversed.")
    elif kind not in {"boolean", "null"}:
        raise ToolError("invalid_input", "Unsupported JSON schema type; use the documented strict bounded subset.")
    if set(schema) - allowed:
        raise ToolError("invalid_input", "Unsupported JSON schema keywords.")
    if "description" in schema:
        _text(schema["description"], "schema description", 1000, empty=True)
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not 1 <= len(values) <= 64:
            raise ToolError("invalid_input", "Schema enum must contain 1..64 values.")
        for value in values:
            _validate(value, {k: v for k, v in schema.items() if k != "enum"})


def _validate(value: Any, schema: Mapping[str, Any]) -> None:
    kind = schema["type"]
    valid = True
    if kind == "object":
        valid = isinstance(value, dict) and set(value) == set(schema["properties"])
        if valid:
            for key, child in schema["properties"].items():
                _validate(value[key], child)
    elif kind == "array":
        valid = isinstance(value, list) and schema.get("minItems", 0) <= len(value) <= schema["maxItems"]
        if valid:
            for item in value:
                _validate(item, schema["items"])
    elif kind == "string":
        valid = isinstance(value, str) and schema.get("minLength", 0) <= len(value) <= schema["maxLength"]
    elif kind in {"integer", "number"}:
        valid = type(value) is int if kind == "integer" else type(value) in (int, float)
        valid = valid and math.isfinite(value) and schema["minimum"] <= value <= schema["maximum"]
    elif kind == "boolean":
        valid = type(value) is bool
    elif kind == "null":
        valid = value is None
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ToolError("invalid_output", "Generated JSON does not satisfy its bounded schema.")


PLAN_SCHEMA = {"type": "object", "properties": {
    "objective": {"type": "string", "maxLength": 2000},
    "steps": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "suggested_tool": {"type": "string", "maxLength": 100},
        "success_criterion": {"type": "string", "maxLength": 1000}},
        "required": ["description", "suggested_tool", "success_criterion"], "additionalProperties": False}},
    "risks": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 1000}}},
    "required": ["objective", "steps", "risks"], "additionalProperties": False}


class AIAdapter:
    spec = ToolSpec("ai.generate", "Generate content or a bounded structured plan using OpenAI Responses.", "external")

    def availability(self, config: Mapping[str, Any], secrets: Secrets) -> dict:
        try:
            settings = _settings(config)
            _credentials(settings, secrets)
            return {"available": True, "provider": "openai", "model": settings["model"]}
        except ToolError as exc:
            return {"available": False, "reason": exc.message}

    def _request(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[dict, dict, Quote]:
        settings = _settings(config)
        if not isinstance(arguments, Mapping) or set(arguments) - {"prompt", "instructions", "purpose", "format", "schema", "max_output_tokens", "max_output_bytes", "max_cost_microusd"}:
            raise ToolError("invalid_input", "Unsupported AI argument; models, endpoints, credentials and prices are owner configuration only.")
        prompt = _text(arguments.get("prompt"), "prompt", settings["max_input_bytes"])
        instructions = _text(arguments.get("instructions", ""), "instructions", settings["max_input_bytes"], empty=True)
        purpose = arguments.get("purpose", "content")
        if not isinstance(purpose, str) or purpose not in {"content", "plan"}:
            raise ToolError("invalid_input", "purpose must be content or plan.")
        output_tokens = _integer(arguments.get("max_output_tokens", settings["max_output_tokens"]), "max_output_tokens", 1, settings["max_output_tokens"])
        if output_tokens > settings["context_tokens"]:
            raise ToolError("invalid_input", "max_output_tokens exceeds the configured model context ceiling.")
        output_bytes = _integer(arguments.get("max_output_bytes", settings["max_output_bytes"]), "max_output_bytes", 1, settings["max_output_bytes"])
        body = {"model": settings["model"], "input": prompt, "max_output_tokens": output_tokens, "store": True,
                "tools": [], "stream": False}
        if instructions:
            body["instructions"] = instructions
        fmt = arguments.get("format", "json" if purpose == "plan" else "text")
        if not isinstance(fmt, str) or fmt not in {"text", "json"} or (purpose == "plan" and fmt != "json"):
            raise ToolError("invalid_input", "format must be text or json; plans require json.")
        if fmt == "json":
            if purpose == "plan" and "schema" in arguments:
                raise ToolError("invalid_input", "Plans use the built-in bounded schema; omit schema.")
            schema = PLAN_SCHEMA if purpose == "plan" else arguments.get("schema")
            if len(_json(schema)) > settings["max_input_bytes"]:
                raise ToolError("invalid_input", "Structured schema exceeds max_input_bytes.")
            _schema(schema)
            if schema["type"] != "object":
                raise ToolError("invalid_input", "The root structured schema must have type object.")
            body["text"] = {"format": {"type": "json_schema", "name": "widemind_output", "schema": schema, "strict": True}}
        elif "schema" in arguments:
            raise ToolError("invalid_input", "schema requires format:json.")
        if len(_json({"input": prompt, "instructions": instructions, "text": body.get("text")})) > settings["max_input_bytes"]:
            raise ToolError("invalid_input", "Combined prompt, instructions and schema exceed max_input_bytes.")
        maximum = max(1, _cost(settings["context_tokens"], output_tokens, settings))
        allowance = _integer(arguments.get("max_cost_microusd", settings["maximum_microusd"]), "max_cost_microusd", 1, settings["maximum_microusd"])
        if maximum > allowance:
            raise ToolError("budget_limit", f"Conservative provider bound {maximum} micro-USD exceeds the allowed {allowance}.")
        return body, settings, Quote(maximum, "openai", True, {"max_output_tokens": output_tokens, "max_output_bytes": output_bytes,
                        "max_input_bytes": settings["max_input_bytes"], "model_context_tokens": settings["context_tokens"]})

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        return self._request(arguments, config)[2]

    def _result(self, arguments: Mapping[str, Any], ctx: ToolContext, payload: dict) -> ToolResult:
        body, settings, bound = self._request(arguments, ctx.config)
        state = ctx.load_state()
        usage = _usage(payload, state.get("ai_request_id"), settings)
        ctx.save_state({"ai_usage": asdict(usage)})
        if usage.cost_microusd is None:
            raise Uncertain("Provider usage is missing or invalid; retain the reservation for reconciliation.", usage=usage)
        try:
            ctx.check()
            if state.get("ai_http_status", 200) != 200 or payload.get("status") != "completed":
                raise ToolError("provider_incomplete", "Provider response was unsuccessful, incomplete, cancelled, or still running.")
            output = payload.get("output")
            if not isinstance(output, list):
                raise ToolError("invalid_output", "Provider output is not a message list.")
            chunks = []
            for item in output:
                if not isinstance(item, dict):
                    raise ToolError("invalid_output", "Invalid provider output item.")
                if item.get("type") == "message":
                    for block in item.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "output_text" or not isinstance(block.get("text"), str):
                            raise ToolError("invalid_output", "Provider refused or returned unsupported output.")
                        chunks.append(block["text"])
                elif item.get("type") != "reasoning":
                    raise ToolError("invalid_output", "Remote tool/output types are not enabled.")
            text = "".join(chunks)
            if not text or len(text.encode("utf-8")) > bound.limits["max_output_bytes"]:
                raise ToolError("output_limit", "Generated content is empty or exceeds max_output_bytes.")
            data: dict = {"text": text, "response_id": payload.get("id"), "purpose": arguments.get("purpose", "content")}
            if "text" in body:
                value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                _validate(value, body["text"]["format"]["schema"])
                data["json"] = value
            return ToolResult(data, usage)
        except (ToolError, ValueError, TypeError, RecursionError) as exc:
            raise Uncertain(f"Paid response saved; output requires reconciliation: {getattr(exc, 'message', str(exc))}", usage=usage) from exc

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        body, settings, _ = self._request(arguments, ctx.config)
        state = ctx.load_state()
        if state.get("ai_intent") or state.get("ai_response_body"):
            recovery = self.reconcile(arguments, ctx)
            if recovery.outcome == "completed" and recovery.result:
                return recovery.result
            raise Uncertain(recovery.detail or "Existing paid request has an unknown outcome; do not repeat it.",
                            usage=recovery.result.usage if recovery.result else None)
        headers = _credentials(settings, ctx.secrets)
        encoded = _json(body)
        intent = {"provider": "openai", "operation": "responses.create", "model": settings["model"],
                  "endpoint": settings["endpoint"], "body_sha256": hashlib.sha256(encoded).hexdigest()}
        ctx.save_state({"ai_intent": intent})
        ctx.mark_dispatched(intent)
        headers["X-Client-Request-Id"] = hashlib.sha256(ctx.action_id.encode()).hexdigest()
        try:
            ctx.check()
            response = ctx.transport.request("POST", settings["endpoint"] + "/responses", headers=headers,
                                             body=encoded, timeout=ctx.remaining(), max_bytes=settings["max_response_bytes"], cancel=ctx.check)
            payload = _save_response(ctx, response, "ai")
        except Exception as exc:
            if isinstance(exc, Uncertain):
                raise
            raise Uncertain("Paid request was dispatched but no complete receipt was saved; reconcile before retrying.") from exc
        return self._result(arguments, ctx, payload)

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        state = ctx.load_state()
        try:
            if state.get("ai_response_body"):
                payload = _decode_body(state["ai_response_body"])
                if payload.get("status") == "completed" or not state.get("ai_response_id"):
                    try:
                        return Reconciliation("completed", self._result(arguments, ctx, payload))
                    except Uncertain as exc:
                        # Retrieval can fill in a missing usage receipt, but is
                        # unnecessary for already measured malformed output.
                        if not state.get("ai_response_id") or (exc.usage and exc.usage.cost_microusd is not None):
                            raise
            response_id = state.get("ai_response_id")
            if not isinstance(response_id, str) or not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,200}", response_id):
                return Reconciliation("uncertain", detail="No known stored response ID; OpenAI cannot safely deduplicate an unknown paid request.")
            settings = _settings(ctx.config)
            headers = _credentials(settings, ctx.secrets)
            ctx.check()
            response = ctx.transport.request("GET", settings["endpoint"] + "/responses/" + quote(response_id, safe=""),
                                             headers=headers, timeout=ctx.remaining(), max_bytes=settings["max_response_bytes"], cancel=ctx.check)
            payload = _save_response(ctx, response, "ai")
            return Reconciliation("completed", self._result(arguments, ctx, payload))
        except Exception as exc:
            usage = getattr(exc, "usage", None)
            result = ToolResult({"reconciliation_error": getattr(exc, "message", "Provider retrieval failed.")}, usage) if usage is not None else None
            return Reconciliation("uncertain", result, detail=getattr(exc, "message", "Retrieval failed; retain the original reservation and request identity."))
