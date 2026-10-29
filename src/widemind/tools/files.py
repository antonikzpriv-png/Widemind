"""Private deliverables in the dedicated workspace; file contents grant no authority."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, Mapping

from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Usage
from ..workspace import validate_workspace_path


_SUBSIDY = {"local_compute_storage": "owner-provided; excluded from this tool charge",
            "allocated_cost_microusd": None}


def _bound(config: Mapping[str, Any], name: str, default: int, maximum: int) -> int:
    value = config.get(name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise Unavailable(f"Configure {name} as a positive integer no greater than {maximum}.")
    return value


def _text(value: Any, name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ToolError("invalid_arguments", f"{name} must be a bounded string.")
    return value


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ToolError("invalid_arguments", f"{name} must be a boolean.")
    return value


def _arguments_hash(arguments: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(arguments), sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _local_intent(arguments: Mapping[str, Any], ctx: ToolContext) -> None:
    ctx.save_state({"local_intent": {"arguments_sha256": _arguments_hash(arguments), "job_id": ctx.job_id}})


def _local_receipt(arguments: Mapping[str, Any], ctx: ToolContext, result: ToolResult) -> None:
    ctx.save_state({"local_receipt": {"arguments_sha256": _arguments_hash(arguments), "job_id": ctx.job_id,
                                    "data": dict(result.data), "artifacts": result.artifacts,
                                    "units": dict(result.usage.units), "details": dict(result.usage.details)}})


def _reconcile_local(arguments: Mapping[str, Any], ctx: ToolContext) -> Reconciliation:
    ctx.check()
    receipt = ctx.load_state().get("local_receipt")
    if isinstance(receipt, dict) and receipt.get("arguments_sha256") == _arguments_hash(arguments) and receipt.get("job_id") == ctx.job_id:
        return Reconciliation("completed", ToolResult(receipt["data"], Usage(0, units=receipt["units"], details=receipt["details"]),
                                                       artifacts=receipt["artifacts"]), "Recovered the private completed local receipt.")
    return Reconciliation("uncertain", detail="No completed private receipt proves the interrupted local mutation; inspect its artifact hash before retrying.")


class FilesAdapter:
    spec = ToolSpec("files", "Create, read, list, delete, and manifest private dedicated-workspace deliverables.", "local")

    def availability(self, config, secrets) -> dict:
        return {"available": config.get("enabled", True) is True, "provider": "local",
                "cost_disclosure": dict(_SUBSIDY)}

    def _validate(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
        if config.get("enabled", True) is not True:
            raise Unavailable("Enable the dedicated workspace files adapter in owner configuration.")
        if not isinstance(arguments, Mapping):
            raise ToolError("invalid_arguments", "Supply a files operation object.")
        operation = arguments.get("operation")
        allowed = {
            "create": {"operation", "path", "text", "content_base64", "overwrite"},
            "read": {"operation", "path", "encoding", "max_bytes"},
            "list": {"operation", "path", "limit"},
            "delete": {"operation", "path"},
            "manifest": {"operation", "path", "paths", "overwrite"},
        }
        if not isinstance(operation, str) or operation not in allowed or set(arguments) - allowed[operation]:
            raise ToolError("invalid_arguments", "Use create, read, list, delete, or manifest with only its documented fields.")
        validated = dict(arguments)
        validated["path"] = _text(arguments.get("path", "" if operation == "list" else None), "path")
        validate_workspace_path(validated["path"], empty=operation == "list")
        if operation in ("create", "manifest"):
            validated["overwrite"] = _boolean(arguments.get("overwrite", False), "overwrite")
        if operation == "create":
            bound = _bound(config, "max_input_bytes", 1_000_000, 8_000_000)
            if ("text" in arguments) == ("content_base64" in arguments):
                raise ToolError("invalid_arguments", "Supply exactly one of text or content_base64.")
            try:
                if "text" in arguments:
                    data = _text(arguments["text"], "text", maximum=bound).encode("utf-8", errors="strict")
                else:
                    encoded = _text(arguments["content_base64"], "content_base64", maximum=((bound + 2) // 3) * 4)
                    data = base64.b64decode(encoded, validate=True)
                    if base64.b64encode(data).decode("ascii") != encoded:
                        raise ValueError("Noncanonical base64")
            except (UnicodeError, binascii.Error, ValueError):
                raise ToolError("invalid_arguments", "Content must be strict UTF-8 text or canonical base64.") from None
            if len(data) > bound:
                raise ToolError("input_limit", "Content exceeds the configured input byte limit.")
            validated["bytes"] = data
        elif operation == "read":
            bound = _bound(config, "max_read_bytes", 1_000_000, 8_000_000)
            maximum = arguments.get("max_bytes", bound)
            if type(maximum) is not int or not 0 <= maximum <= bound:
                raise ToolError("invalid_arguments", "max_bytes must fit the configured read limit.")
            encoding = arguments.get("encoding", "utf-8")
            if encoding not in ("utf-8", "base64"):
                raise ToolError("invalid_arguments", "encoding must be utf-8 or base64.")
            validated.update(max_bytes=maximum, encoding=encoding)
        elif operation == "list":
            limit = arguments.get("limit", 100)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ToolError("invalid_arguments", "limit must be an integer from 1 to 1000.")
            validated["limit"] = limit
        elif operation == "manifest":
            bound = _bound(config, "max_manifest_files", 100, 1000)
            paths = arguments.get("paths")
            if not isinstance(paths, list) or not 1 <= len(paths) <= bound:
                raise ToolError("invalid_arguments", "paths must be a nonempty bounded list of deliverable paths.")
            paths = [_text(path, "deliverable path") for path in paths]
            for path in paths:
                validate_workspace_path(path)
            if len(set(paths)) != len(paths) or validated["path"] in paths:
                raise ToolError("invalid_arguments", "Manifest paths must be distinct and exclude the manifest itself.")
            validated["paths"] = paths
        return validated

    def quote(self, arguments, config) -> Quote:
        values = self._validate(arguments, config)
        return Quote(limits={"operation": values["operation"],
                             "max_input_bytes": _bound(config, "max_input_bytes", 1_000_000, 8_000_000),
                             "max_read_bytes": _bound(config, "max_read_bytes", 1_000_000, 8_000_000),
                             "cost_disclosure": dict(_SUBSIDY)})

    @staticmethod
    def _private(artifact: dict, ctx: ToolContext) -> dict:
        return {**artifact, "job_id": ctx.job_id, "visibility": "private"}

    def execute(self, arguments, ctx: ToolContext) -> ToolResult:
        ctx.check()
        values = self._validate(arguments, ctx.config)
        operation, path = values["operation"], values["path"]
        artifacts: list[dict] = []
        data: dict = {"operation": operation, "job_id": ctx.job_id, "visibility": "private"}
        units: dict = {}
        if operation not in ("read", "list"):
            _local_intent(arguments, ctx)
        if operation == "create":
            ctx.check()
            artifact = ctx.workspace.write_bytes(path, values["bytes"], overwrite=values["overwrite"])
            artifacts.append(self._private(artifact, ctx))
            data["artifact"] = artifacts[0]
            units["bytes_written"] = len(values["bytes"])
        elif operation == "read":
            content = ctx.workspace.read_bytes(path, max_bytes=values["max_bytes"])
            ctx.check()
            try:
                data["text" if values["encoding"] == "utf-8" else "content_base64"] = (
                    content.decode("utf-8", errors="strict") if values["encoding"] == "utf-8"
                    else base64.b64encode(content).decode("ascii"))
            except UnicodeError:
                raise ToolError("invalid_encoding", "File is not UTF-8; read it with encoding=base64.") from None
            data.update(path=path, encoding=values["encoding"], size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest())
            units["bytes_read"] = len(content)
        elif operation == "list":
            data["files"] = [self._private(item, ctx) for item in ctx.workspace.list_files(path, limit=values["limit"])]
            data["limit"] = values["limit"]
        elif operation == "delete":
            ctx.check()
            data.update(ctx.workspace.delete(path))
        else:
            entries = []
            for selected in values["paths"]:
                ctx.check()
                content = ctx.workspace.read_bytes(selected, max_bytes=8_000_000)
                entries.append({"path": selected, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            manifest = {"version": 1, "job_id": ctx.job_id, "visibility": "private", "files": entries}
            encoded = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
            ctx.check()
            artifact = ctx.workspace.write_bytes(path, encoded, overwrite=values["overwrite"])
            artifacts.append(self._private(artifact, ctx))
            data.update(manifest=manifest, artifact=artifacts[0])
            units.update(files_manifested=len(entries), bytes_written=len(encoded))
        result = ToolResult(data, Usage(0, units=units, details=dict(_SUBSIDY)), artifacts=artifacts)
        if operation not in ("read", "list"):
            _local_receipt(arguments, ctx, result)
        ctx.check()
        return result

    def reconcile(self, arguments, ctx: ToolContext) -> Reconciliation:
        values = self._validate(arguments, ctx.config)
        if values["operation"] in ("read", "list"):
            ctx.check()
            return Reconciliation("not_executed", detail="Local inspection has no persistent external effect and may safely run again.")
        return _reconcile_local(arguments, ctx)
