"""Owner-controlled entry point for durable, budget-authorized tool calls.

This is a tools runtime, not an autonomous scheduler. Returned plans and other
tool results are untrusted data and cannot invoke tools or change this gateway.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .config import load_config, validate_accounting_authority
from .control import ControlStore, JournalState, json_text
from .secrets import SecretStore
from .tools.contracts import Quote, ToolContext, ToolError, ToolResult, Unavailable, Uncertain, Usage


def default_adapters():
    from .tools.ai import AIAdapter
    from .tools.browser import BrowserAdapter
    from .tools.code import CodeAdapter
    from .tools.files import FilesAdapter
    from .tools.gmail import GmailAdapter
    from .tools.graphics import GraphicsAdapter
    from .tools.image_generation import ImageGenerationAdapter
    from .tools.publish import PublishAdapter
    from .tools.research import ResearchAdapter
    return [AIAdapter(), BrowserAdapter(), CodeAdapter(), FilesAdapter(), GmailAdapter(),
            GraphicsAdapter(), ImageGenerationAdapter(), PublishAdapter(), ResearchAdapter()]


class AuthorizedTransport:
    def __init__(self, transport, ctx, *, setup=False):
        self.transport, self.ctx, self.setup = transport, ctx, setup

    def request(self, method, url, **kwargs):
        self.ctx.check()
        if self.setup:
            allowed = {("POST", "https://oauth2.googleapis.com/token"),
                       ("POST", "https://oauth2.googleapis.com/tokeninfo"),
                       ("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")}
            if (method.upper(), url) not in allowed:
                raise ToolError("authorization", "Owner OAuth setup permits only token and account-verification endpoints.")
        kwargs["timeout"] = self.ctx.remaining(kwargs.get("timeout", 30))
        original_cancel = kwargs.get("cancel")

        def check():
            self.ctx.check()
            if original_cancel:
                original_cancel()

        kwargs["cancel"] = check
        return self.transport.request(method, url, **kwargs)


class Gateway:
    def __init__(self, config, *, adapters=None, transport=None):
        # API callers are trusted owners, just as the config file's owner is.
        # Task data is only accepted by execute(arguments), never here.
        self.config = json.loads(json_text(config))
        validate_accounting_authority(self.config.get("policy", {}))
        self.store = ControlStore(config["control_dir"])
        self.secrets = SecretStore(config["control_dir"])
        from .workspace import DedicatedWorkspace
        limits = config.get("workspace_limits", {})
        self.workspace = DedicatedWorkspace(config["workspace_dir"], **limits)
        if self.workspace.root == self.store.root or self.workspace.root in self.store.root.parents or self.store.root in self.workspace.root.parents:
            raise ToolError("configuration", "Workspace and control store must be disjoint.")
        if transport is None:
            from .http import HTTPTransport
            transport = HTTPTransport(config.get("policy", {}))
        self.transport = transport
        self.adapters = {a.spec.name: a for a in (adapters if adapters is not None else default_adapters())}

    @classmethod
    def from_config(cls, filename: str | Path, **kwargs):
        return cls(load_config(filename), **kwargs)

    def status(self):
        result = {}
        for name, adapter in self.adapters.items():
            config = self.config.get("tools", {}).get(name, {})
            if config.get("enabled") is not True:
                result[name] = {"available": False, "status": "disabled", "reason": "Enable this tool in private owner configuration."}
                continue
            try:
                result[name] = adapter.availability(config, self.secrets)
            except Exception:
                result[name] = {"available": False, "reason": "Check this tool's owner configuration and setup documentation."}
        return {"tools": result, "profile": self.config.get("policy", {}).get("profile", "development"),
                "live_provider_verification": "not performed by status", "visibility": "private"}

    @staticmethod
    def _seconds(config):
        seconds = config.get("timeout_seconds", 120)
        if type(seconds) not in (float, int) or not math.isfinite(seconds) or not .1 <= seconds <= 300:
            raise ToolError("configuration", "timeout_seconds must be between 0.1 and 300.")
        return seconds

    def _context(self, row, claim, seconds, *, reconciliation=False, setup=False):
        ctx = ToolContext(action_id=row["id"],run_id=row["run_id"],reservation_id=row["reservation_id"],
                          config=json.loads(row["config"]),policy=json.loads(row["policy"]),
                          workspace=self.workspace,secrets=self.secrets,transport=self.transport,
                          state=JournalState(self.store,claim,row["id"]),deadline=time.monotonic()+seconds,
                          authorize=lambda: self.store.authorize(row["id"],claim,reconciliation=reconciliation),
                          cancelled=lambda: False if reconciliation else bool(self.store.get(row["id"])["cancelled"]),
                          job_id=row["job_id"])
        ctx.transport = AuthorizedTransport(self.transport,ctx,setup=setup)
        return ctx

    def _adapter(self, name):
        if name not in self.adapters:
            raise ToolError("unknown_tool", "Unknown tool; use status to inspect the installed adapters.")
        return self.adapters[name]

    def _central_accounting_selected(self):
        """A standalone development wallet cannot replace the runtime authority.

        Owner configuration may opt into the central boundary, but it cannot
        override an inherited central service/signing configuration. No request
        input, secret value, or provider response selects accounting authority.
        """
        return bool(os.environ.get("WIDEMIND_ECONOMY_URL") or
                    os.environ.get("WIDEMIND_RUNTIME_AUTH_SECRET") or
                    self.config.get("policy", {}).get("accounting_authority") == "central")

    def execute(self, tool, arguments, *, run_id, action_id=None, job_id=None):
        adapter = self._adapter(tool)
        if not isinstance(arguments, dict):
            raise ToolError("invalid_input", "Tool input must be a JSON object.")
        arguments = json.loads(json_text(arguments))
        action_id = action_id or str(uuid.uuid4())
        config = self.config.get("tools", {}).get(tool, {})
        policy = self.config.get("policy", {})
        # Existing identities are checked before credentials/configuration so
        # replay still returns the original receipt after owner config changes.
        try:
            old = self.store.get(action_id)
        except ToolError as exc:
            if exc.code != "not_found":
                raise
            old = None
        error = None
        if old is not None:
            config, policy = json.loads(old["config"]), json.loads(old["policy"])
            quote = Quote(**json.loads(old["quote"]))
        else:
            try:
                self._seconds(config)
                if config.get("enabled") is not True:
                    raise Unavailable("Enable this tool in private owner configuration; see status and setup documentation.")
                availability = adapter.availability(config,self.secrets)
                if availability.get("available") is False and availability.get("status") != "not_verified":
                    raise Unavailable(availability.get("reason", "Complete this tool's owner setup before execution."))
                quote = adapter.quote(arguments,config)
            except ToolError as exc:
                quote, error = Quote(), exc
        if quote.paid and self._central_accounting_selected():
            terminal = {"succeeded", "failed", "unavailable", "cancelled"}
            if old is None or old["status"] not in terminal:
                # Refuse before local wallet admission, claiming, or dispatch.
                # Existing terminal replays still pass prepare's input identity
                # check below; observational recovery remains in reconcile().
                raise ToolError("central_economy_required",
                                "Standalone paid execution is disabled while central runtime accounting is configured. "
                                "Use a registered central-authorized tool adapter; recover existing standalone actions with reconcile.")
        row, fresh = self.store.prepare(action_id=action_id,run_id=run_id,job_id=job_id,tool=tool,
                                        version=old["version"] if old else adapter.spec.version,
                                        arguments=arguments,config=config,policy=policy,quote=quote)
        if row["version"] != adapter.spec.version and row["status"] not in {"succeeded","failed","unavailable","cancelled"}:
            raise ToolError("adapter_version", "Recover this pending action using its original adapter version.")
        seconds = 120 if error else self._seconds(config)
        claim = self.store.claim(action_id,seconds)
        if claim is None:
            return self.store.receipt(action_id)
        if error:
            return self.store.finish(action_id,claim,"unavailable" if error.code == "unavailable" else "failed",
                                     error={"code":error.code,"message":error.message})
        ctx = self._context(row,claim,seconds)
        try:
            ctx.check()
            result = adapter.execute(arguments,ctx)
            if not isinstance(result, ToolResult):
                raise ToolError("adapter_protocol", "Adapter did not return a structured ToolResult.")
            if quote.paid and result.usage.cost_microusd is None:
                return self.store.finish(action_id,claim,"uncertain",result=replace(result,trust="untrusted"),
                                         error={"code":"unknown_usage","message":"Provider usage requires reconciliation; reservation retained."})
            return self.store.finish(action_id,claim,"succeeded",result=replace(result,trust="untrusted"))
        except BaseException as exc:
            return self._failure(row,claim,exc)

    def _failure(self, row, claim, exc):
        current = self.store.get(row["id"])
        if isinstance(exc, KeyboardInterrupt):
            self.store.cancel(row["id"])
        local_state = json.loads(current["state"])
        uncertain = isinstance(exc, Uncertain) or bool(current["dispatched"]) or bool(local_state.get("local_intent"))
        status = "uncertain" if uncertain else ("cancelled" if isinstance(exc,KeyboardInterrupt) or getattr(exc,"code",None) == "cancelled" else
                                                 "unavailable" if isinstance(exc,Unavailable) else "failed")
        code = exc.code if isinstance(exc,ToolError) else "cancelled" if isinstance(exc,KeyboardInterrupt) else "adapter_error"
        message = exc.message if isinstance(exc,ToolError) else "Action stopped unexpectedly; inspect its private receipt and reconcile before retrying."
        usage = getattr(exc,"usage",None)
        return self.store.finish(row["id"],claim,status,usage=usage,error={"code":code,"message":message})

    def reconcile(self, action_id):
        row = self.store.get(action_id)
        if row["status"] in {"succeeded","failed","unavailable","cancelled"}:
            return self.store.receipt(action_id)
        if row["status"] == "planned":
            raise ToolError("not_dispatched", "Action has not started; execute its original input to claim it.")
        adapter = self._adapter(row["tool"])
        if row["version"] != adapter.spec.version:
            raise ToolError("adapter_version", "Recover this pending action using its original adapter version.")
        config = json.loads(row["config"])
        seconds = self._seconds(config)
        claim = self.store.claim(action_id,seconds,reconciliation=True)
        if claim is None:
            return self.store.receipt(action_id)
        ctx = self._context(row,claim,seconds,reconciliation=True)
        try:
            result = adapter.reconcile(json.loads(row["arguments"]),ctx)
            if result.outcome not in {"completed","not_executed","uncertain"}:
                raise ToolError("adapter_protocol", "Adapter returned an invalid reconciliation outcome.")
            if result.outcome == "completed" and result.result is None:
                raise ToolError("adapter_protocol", "Completed reconciliation must include a measured result.")
            status = {"completed":"succeeded","not_executed":"failed","uncertain":"uncertain"}[result.outcome]
            # not_executed is affirmative adapter proof, including no billable
            # request. Unknown is a distinct outcome and keeps the reservation.
            usage = Usage() if result.outcome == "not_executed" else None
            return self.store.finish(action_id,claim,status,
                                     result=replace(result.result,trust="untrusted") if result.result else None,
                                     usage=usage,error=None if status=="succeeded" else {"code":result.outcome,"message":result.detail})
        except BaseException as exc:
            return self._failure(row,claim,exc)

    @contextmanager
    def setup_context(self, tool):
        if tool != "gmail":
            raise ToolError("authorization", "Only dedicated Gmail OAuth has an owner setup context.")
        config = self.config.get("tools",{}).get(tool,{})
        if config.get("enabled") is not True:
            raise Unavailable("Enable Gmail in private owner configuration before OAuth setup.")
        self.store.create_run("operator-setup",0)
        row,_ = self.store.prepare(action_id=str(uuid.uuid4()),run_id="operator-setup",job_id=None,
                                   tool="gmail.oauth_setup",version=1,arguments={},config=config,
                                   policy=self.config.get("policy",{}),quote=Quote())
        claim = self.store.claim(row["id"],120)
        try:
            yield self._context(row,claim,120,setup=True)
        except BaseException as exc:
            self._failure(row,claim,exc)
            raise
        else:
            self.store.finish(row["id"],claim,"succeeded",result=ToolResult({"setup":"completed","live_email_sent":False}))
