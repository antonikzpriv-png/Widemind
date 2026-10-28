"""Fixed-settings paid image generation with durable receipts and artifacts.

Images has no generation-retrieval or provider max-cost parameter. Quotes fail
closed without an owner-certified charge ceiling for the exact configured
model/size/quality/prompt limit. See docs/AI-PROVIDERS.md before enabling.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Any, Mapping

from .ai import (_credentials, _decode_body, _integer, _json, _save_response,
                 _settings, _text, _usage)
from .contracts import (Quote, Reconciliation, Secrets, ToolContext, ToolError,
                        ToolResult, ToolSpec, Unavailable, Uncertain)


def _image_settings(config: Mapping[str, Any]) -> dict:
    settings = _settings(config, require_context=False)
    try:
        size = config.get("size")
        quality = config.get("quality")
        if not isinstance(size, str) or not isinstance(quality, str) or size not in {"1024x1024", "1536x1024", "1024x1536"} or quality not in {"low", "medium", "high"}:
            raise ToolError("invalid_input", "Configure an explicit supported size and low/medium/high quality; auto is unbounded.")
        # The certification binds every setting relevant to the owner's bound.
        certificate = config.get("charge_bound")
        if not isinstance(certificate, dict) or certificate.get("certified") is not True:
            raise ToolError("invalid_input", "Images API has no maximum-cost parameter. Configure an owner-certified charge_bound before enabling paid generation.")
        expected = {"model": settings["model"], "size": size, "quality": quality,
                    "max_prompt_bytes": settings["max_input_bytes"]}
        if any(certificate.get(key) != value for key, value in expected.items()):
            raise ToolError("invalid_input", "charge_bound must match the exact model, size, quality and max_prompt_bytes.")
        evidence = _text(certificate.get("evidence"), "charge-bound evidence", 2000)
        maximum_per_image = _integer(certificate.get("maximum_microusd_per_image"), "maximum_microusd_per_image", 1, 10**12)
        settings.update(size=size, quality=quality, charge_bound_evidence=evidence,
                        maximum_per_image=maximum_per_image,
                        max_images=_integer(config.get("max_images", 1), "max_images", 1, 4),
                        max_image_bytes=_integer(config.get("max_image_bytes", 8_000_000), "max_image_bytes", 64, 32_000_000))
        if "max_response_bytes" not in config:
            settings["max_response_bytes"] = min(64_000_000,
                settings["max_images"] * 4 * ((settings["max_image_bytes"] + 2) // 3) + 65_536)
        return settings
    except ToolError as exc:
        raise Unavailable(f"Image provider configuration: {exc.message}") from exc


class ImageGenerationAdapter:
    spec = ToolSpec("image.generate", "Generate fixed-size PNG images with a configured paid provider.", "external")

    def availability(self, config: Mapping[str, Any], secrets: Secrets) -> dict:
        try:
            settings = _image_settings(config)
            _credentials(settings, secrets)
            return {"available": True, "provider": "openai", "model": settings["model"],
                    "charge_bound": "owner-certified; provider has no request max-cost parameter"}
        except ToolError as exc:
            return {"available": False, "reason": exc.message}

    def _request(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[dict, dict, Quote]:
        settings = _image_settings(config)
        if not isinstance(arguments, Mapping) or set(arguments) - {"prompt", "n", "output_prefix", "max_cost_microusd"}:
            raise ToolError("invalid_input", "Image arguments are prompt, n, output_prefix and max_cost_microusd; provider settings belong to the owner.")
        prompt = _text(arguments.get("prompt"), "prompt", settings["max_input_bytes"])
        n = _integer(arguments.get("n", 1), "n", 1, settings["max_images"])
        prefix = arguments.get("output_prefix", "generated/image")
        _text(prefix, "output_prefix", 200)
        path = PurePosixPath(prefix)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in prefix.split("/")) or not re.fullmatch(r"[A-Za-z0-9_/-]+", prefix):
            raise ToolError("invalid_input", "output_prefix must be a relative workspace path using letters, digits, slash, dash or underscore.")
        maximum = n * settings["maximum_per_image"]
        allowance = _integer(arguments.get("max_cost_microusd", settings["maximum_microusd"]), "max_cost_microusd", 1, settings["maximum_microusd"])
        if maximum > allowance:
            raise ToolError("budget_limit", f"Certified image bound {maximum} micro-USD exceeds the allowed {allowance}.")
        body = {"model": settings["model"], "prompt": prompt, "n": n, "size": settings["size"],
                "quality": settings["quality"], "output_format": "png", "background": "opaque"}
        return body, settings, Quote(maximum, "openai", True, {"n": n, "size": settings["size"], "quality": settings["quality"],
                       "max_prompt_bytes": settings["max_input_bytes"], "max_image_bytes": settings["max_image_bytes"],
                       "charge_bound_basis": "owner-certified for exact fixed settings; no provider max-cost parameter"})

    def quote(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> Quote:
        return self._request(arguments, config)[2]

    def _result(self, arguments: Mapping[str, Any], ctx: ToolContext, payload: dict) -> ToolResult:
        body, settings, _ = self._request(arguments, ctx.config)
        state = ctx.load_state()
        records = payload.get("data")
        count = len(records) if isinstance(records, list) else 0
        usage = _usage(payload, state.get("image_request_id"), settings, images=count)
        ctx.save_state({"image_usage": asdict(usage)})
        if usage.cost_microusd is None:
            raise Uncertain("Image usage missing or invalid; receipt saved and reservation retained.", usage=usage)
        try:
            ctx.check()
            if state.get("image_http_status", 200) != 200:
                raise ToolError("provider_error", "Image provider returned an unsuccessful response.")
            if not isinstance(records, list) or not records or len(records) > body["n"]:
                raise ToolError("invalid_output", "Image response must contain 1..n images.")
            # Fewer images are deliverable partial work; actual usage still priced.
            artifacts = []
            suffix = hashlib.sha256(ctx.action_id.encode()).hexdigest()[:20]
            for index, record in enumerate(records):
                ctx.check()
                if not isinstance(record, dict) or not isinstance(record.get("b64_json"), str):
                    raise ToolError("invalid_output", "Expected inline b64_json; arbitrary image URLs are never fetched.")
                encoded = record["b64_json"]
                if len(encoded) > 4 * ((settings["max_image_bytes"] + 2) // 3):
                    raise ToolError("output_limit", "Encoded image exceeds the configured output bound.")
                image = base64.b64decode(encoded, validate=True)
                if len(image) > settings["max_image_bytes"] or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ToolError("invalid_output", "Provider image is not a bounded PNG file.")
                path = f"{arguments.get('output_prefix', 'generated/image')}-{suffix}-{index + 1}.png"
                artifact = {"path": path, "bytes": len(image), "sha256": hashlib.sha256(image).hexdigest(), "media_type": "image/png"}
                try:
                    existing = ctx.workspace.read_bytes(path, max_bytes=settings["max_image_bytes"])
                except FileNotFoundError:
                    existing = None
                except ToolError as exc:
                    if exc.code != "not_found":
                        raise
                    existing = None
                if existing is None:
                    ctx.check()
                    artifact.update(ctx.workspace.write_bytes(path, image, overwrite=False))
                elif existing != image:
                    raise ToolError("artifact_conflict", "Existing generated artifact differs from the saved paid response; operator review required.")
                artifacts.append(artifact)
                ctx.save_state({"image_artifacts": artifacts})
            return ToolResult({"images": count, "requested_images": body["n"], "partial": count != body["n"],
                               "request_id": state.get("image_request_id")}, usage, artifacts=artifacts)
        except (ToolError, ValueError, TypeError, binascii.Error, OSError) as exc:
            raise Uncertain(f"Paid image response saved; artifact recovery required: {getattr(exc, 'message', str(exc))}", usage=usage) from exc

    def execute(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        ctx.check()
        body, settings, _ = self._request(arguments, ctx.config)
        state = ctx.load_state()
        if state.get("image_intent") or state.get("image_response_body"):
            recovery = self.reconcile(arguments, ctx)
            if recovery.outcome == "completed" and recovery.result:
                return recovery.result
            raise Uncertain(recovery.detail or "Unknown existing paid image request; never repeat blindly.",
                            usage=recovery.result.usage if recovery.result else None)
        headers = _credentials(settings, ctx.secrets)
        encoded = _json(body)
        intent = {"provider": "openai", "operation": "images.generations", "model": settings["model"],
                  "endpoint": settings["endpoint"], "body_sha256": hashlib.sha256(encoded).hexdigest()}
        ctx.save_state({"image_intent": intent})
        ctx.mark_dispatched(intent)
        headers["X-Client-Request-Id"] = hashlib.sha256(ctx.action_id.encode()).hexdigest()
        try:
            ctx.check()
            response = ctx.transport.request("POST", settings["endpoint"] + "/images/generations", headers=headers,
                                             body=encoded, timeout=ctx.remaining(), max_bytes=settings["max_response_bytes"], cancel=ctx.check)
            payload = _save_response(ctx, response, "image")
        except Exception as exc:
            if isinstance(exc, Uncertain):
                raise
            raise Uncertain("Paid image request dispatched without a complete receipt. Images API has no retrieval endpoint; retain reservation and reconcile with provider.") from exc
        return self._result(arguments, ctx, payload)

    def reconcile(self, arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
        ctx.check()
        state = ctx.load_state()
        if not state.get("image_response_body"):
            return Reconciliation("uncertain", detail="No saved Images response. The provider offers no generation retrieval; do not repeat an unknown paid request.")
        try:
            return Reconciliation("completed", self._result(arguments, ctx, _decode_body(state["image_response_body"])))
        except Exception as exc:
            usage = getattr(exc, "usage", None)
            result = ToolResult({"reconciliation_error": getattr(exc, "message", "Artifact recovery failed.")}, usage) if usage is not None else None
            return Reconciliation("uncertain", result, detail=getattr(exc, "message", "Saved image response needs operator reconciliation."))
