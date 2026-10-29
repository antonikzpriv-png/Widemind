"""Production composition root. Missing providers never fall back to fixtures.

The host selects credentials and protection paths; the agent sees service clients,
not secrets. Deploy gateway/accounting/control outside the agent OS sandbox.
"""
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import re

from .business.engine import Business
from .business.store import Store
from .runtime import DEFAULT_CAPABILITIES, RuntimeControl


class ConfigurationError(ValueError):
    """Safe operator-facing configuration failure; contains names, not secret values."""


@dataclass(frozen=True)
class HostConfig:
    run_id: str
    mode: str
    business_db: Path
    runtime_db: Path
    economy_db: Path
    tools_db: Path
    workspace: Path
    credential_directory: Path | None
    gmail_address: str
    model: str
    input_price_micro_usd_per_million: int
    output_price_micro_usd_per_million: int
    max_output_tokens: int
    max_input_bytes: int
    source_urls: tuple[str, ...]
    allowed_recipients: tuple[str, ...] | None
    success_url: str
    cancel_url: str


REQUIRED_NAMES = (
    "WIDEMIND_RUN_ID", "WIDEMIND_BUSINESS_DB", "WIDEMIND_RUNTIME_DB", "WIDEMIND_ECONOMY_DB",
    "WIDEMIND_TOOLS_DB", "WIDEMIND_WORKSPACE", "WIDEMIND_GMAIL_ADDRESS", "WIDEMIND_OPERATOR_TOKEN",
    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "OPENAI_API_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL",
    "OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS", "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS",
)


def _env(environ):
    # Never return or log the environment. Only the explicitly selected keys below
    # cross this composition boundary, directly into their credential-owning client.
    return os.environ if environ is None else environ


def _required(values, name):
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Configure {name}")
    return value


def _path(values, name):
    raw = _required(values, name)
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute private path")
    return path.resolve()


def _integer(values, name, default=None, minimum=1, maximum=10**12):
    raw = values.get(name, default)
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]+", raw):
        raise ConfigurationError(f"{name} must be an explicit positive integer")
    result = int(raw)
    if not minimum <= result <= maximum:
        raise ConfigurationError(f"{name} is outside the supported bounds")
    return result


def _list(values, name, *, default="[]", maximum=100):
    try:
        result = json.loads(values.get(name, default))
    except (ValueError, TypeError):
        raise ConfigurationError(f"{name} must be a JSON list") from None
    if not isinstance(result, list) or len(result) > maximum or any(not isinstance(v, str) or not v.strip() for v in result):
        raise ConfigurationError(f"{name} must be a bounded JSON string list")
    return tuple(result)


def load_config(environ=None):
    values = _env(environ)
    for name in REQUIRED_NAMES:
        _required(values, name)
    mode = values.get("WIDEMIND_MODE", "test")
    if mode not in {"test", "live"}:
        raise ConfigurationError("WIDEMIND_MODE must be test or live; fixtures use the separate demo command")
    run_id = values["WIDEMIND_RUN_ID"].strip()
    if len(run_id) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in run_id):
        raise ConfigurationError("WIDEMIND_RUN_ID is invalid")
    if len(values["WIDEMIND_OPERATOR_TOKEN"]) < 32:
        raise ConfigurationError("WIDEMIND_OPERATOR_TOKEN requires at least 32 characters")
    business = _path(values, "WIDEMIND_BUSINESS_DB")
    runtime = _path(values, "WIDEMIND_RUNTIME_DB")
    economy = _path(values, "WIDEMIND_ECONOMY_DB")
    tools = _path(values, "WIDEMIND_TOOLS_DB")
    workspace = _path(values, "WIDEMIND_WORKSPACE")
    credential = _path(values, "WIDEMIND_CREDENTIALS_DIR") if values.get("WIDEMIND_CREDENTIALS_DIR") else None
    databases = (business, runtime, economy, tools)
    if len(set(databases)) != len(databases):
        raise ConfigurationError("Business, runtime, economy, and tools require distinct database files")
    if any(path.is_relative_to(workspace) or workspace.is_relative_to(path) for path in databases):
        raise ConfigurationError("Private state and enforcement databases must be outside the agent workspace")
    if any(path.is_relative_to(business.parent) for path in (runtime, economy, tools)):
        raise ConfigurationError("Enforcement databases must be outside the business data directory")
    if any(business.is_relative_to(path.parent) for path in (runtime, economy, tools)):
        raise ConfigurationError("Business data must not share the protected control directory")
    if any(a != b and (a.is_relative_to(b) or b.is_relative_to(a)) for a in databases for b in databases):
        raise ConfigurationError("Database paths cannot contain one another")
    if credential and (credential.is_relative_to(workspace) or workspace.is_relative_to(credential) or
                       credential.is_relative_to(business.parent) or business.is_relative_to(credential)):
        raise ConfigurationError("Credentials must be outside business data and the agent workspace")
    gmail_address = values["WIDEMIND_GMAIL_ADDRESS"].strip()
    if not re.fullmatch(r"[^\s<>@,;\x00-\x1f\x7f]+@[^\s<>@,;\x00-\x1f\x7f]+\.[^\s<>@,;\x00-\x1f\x7f]+", gmail_address):
        raise ConfigurationError("WIDEMIND_GMAIL_ADDRESS must identify the dedicated mailbox")
    model = values.get("OPENAI_MODEL", "gpt-4.1-mini")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", model):
        raise ConfigurationError("OPENAI_MODEL is invalid")
    from .business.services import web_url
    try:
        success = web_url(values["STRIPE_SUCCESS_URL"])
        cancel = web_url(values["STRIPE_CANCEL_URL"])
        sources = tuple(web_url(url) for url in _list(values, "WIDEMIND_RESEARCH_SOURCE_URLS", maximum=30))
    except ValueError:
        raise ConfigurationError("Configure valid HTTPS source and Stripe redirect URLs") from None
    recipients = _list(values, "WIDEMIND_ALLOWED_RECIPIENTS", default="[]")
    if mode == "test" and not recipients:
        raise ConfigurationError("Provider-test mode requires explicit WIDEMIND_ALLOWED_RECIPIENTS")
    if any(not re.fullmatch(r"[^\s<>@,;\x00-\x1f\x7f]+@[^\s<>@,;\x00-\x1f\x7f]+\.[^\s<>@,;\x00-\x1f\x7f]+", email) for email in recipients):
        raise ConfigurationError("WIDEMIND_ALLOWED_RECIPIENTS contains an invalid address")
    return HostConfig(run_id, mode, business, runtime, economy, tools, workspace, credential, gmail_address,
        model, _integer(values, "OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS"),
        _integer(values, "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS"),
        _integer(values, "OPENAI_MAX_OUTPUT_TOKENS", "1800", maximum=20000),
        _integer(values, "OPENAI_MAX_INPUT_BYTES", "24000", minimum=1000, maximum=500000),
        sources, recipients or None, success, cancel)


def readiness(environ=None):
    """Safe setup diagnostics. No secret values, filesystem paths, or provider calls."""
    values = _env(environ)
    groups = (
        ("runtime", ("WIDEMIND_RUN_ID", "WIDEMIND_RUNTIME_DB", "WIDEMIND_WORKSPACE")),
        ("economy", ("WIDEMIND_ECONOMY_DB",)),
        ("stripe", ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL")),
        ("gmail", ("WIDEMIND_GMAIL_ADDRESS", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")),
        ("research", ("OPENAI_API_KEY", "OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS", "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS")),
        ("operator_api", ("WIDEMIND_OPERATOR_TOKEN", "WIDEMIND_BUSINESS_DB", "WIDEMIND_TOOLS_DB")),
    )
    results = []
    for name, required in groups:
        missing = [key for key in required if not isinstance(values.get(key), str) or not values[key].strip()]
        results.append({"name": name, "status": "configuration_required" if missing else "configured_unverified",
                        "detail": "Configure: " + ", ".join(missing) if missing else "Configuration present; live provider qualification is still required."})
    try:
        load_config(values)
    except ConfigurationError as exc:
        results.append({"name": "host", "status": "configuration_required", "detail": str(exc)})
    else:
        results.append({"name": "host", "status": "configured_unverified",
                        "detail": "Separate paths validated; OS isolation and live connectivity still require qualification."})
    return results


def _close(resources):
    for resource in reversed(resources):
        close = getattr(resource, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup cannot expose credential-bearing provider exceptions.
                pass


class _ManagedStore(Store):
    def __init__(self, path, resources):
        self._resources = resources
        self._closed = False
        super().__init__(path)

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                super().close()
            finally:
                _close(self._resources)


def _providers(config, values, resources):
    try:
        economy_module = importlib.import_module("widemind.economy")
        tools_module = importlib.import_module("widemind.tools")
    except ImportError:
        raise ConfigurationError("Production economy and tools packages must be installed") from None
    runtime = RuntimeControl(config.runtime_db, mode=config.mode)
    resources.append(runtime)
    stripe = economy_module.StripeClient(api_key=values["STRIPE_SECRET_KEY"],
        webhook_secret=values["STRIPE_WEBHOOK_SECRET"], mode=config.mode,
        success_url=config.success_url, cancel_url=config.cancel_url)
    resources.append(stripe)
    economy = economy_module.EconomyService(config.economy_db, mode=config.mode, stripe=stripe)
    resources.append(economy)
    gmail = tools_module.GmailClient(tools_module.GmailConfig(email_address=config.gmail_address,
        client_id=values["GMAIL_CLIENT_ID"], client_secret=values["GMAIL_CLIENT_SECRET"], refresh_token=values["GMAIL_REFRESH_TOKEN"]))
    resources.append(gmail)
    research = tools_module.OpenAIResearch(tools_module.ResearchConfig(api_key=values["OPENAI_API_KEY"], model=config.model,
        input_micro_usd_per_million_tokens=config.input_price_micro_usd_per_million,
        output_micro_usd_per_million_tokens=config.output_price_micro_usd_per_million,
        max_output_tokens=config.max_output_tokens, max_input_bytes=config.max_input_bytes,
        source_urls=config.source_urls))
    resources.append(research)
    gateway = tools_module.LiveTools(config.tools_db, economy=economy, runtime=runtime, gmail=gmail,
        research=research, mode=config.mode, enabled_actions=DEFAULT_CAPABILITIES, allowed_recipients=config.allowed_recipients)
    resources.append(gateway)
    return runtime, economy, gateway


def business_factory(environ=None):
    """Construct real-provider clients without funding, resuming, or provider requests."""
    values = _env(environ)
    config = load_config(values)
    resources = []
    store = None
    try:
        runtime, economy, gateway = _providers(config, values, resources)
        if runtime.health(config.run_id)["status"] != "ready":
            raise ConfigurationError("Initialize the reviewed provider run before starting API or worker")
        # balance is a local authoritative economy read; never auto-fund construction.
        economy.balance(run_id=config.run_id)
        store = _ManagedStore(config.business_db, resources)
        business = Business(store, run_id=config.run_id, economy=economy, tools=gateway, runtime=runtime, mode=config.mode)
        business.execution_profile = "provider-test" if config.mode == "test" else "live"
        business.uses_fixtures = False
        business.contact_email = config.gmail_address
        business.integration_status = lambda: readiness(values)
        return business
    except Exception:
        if store is not None:
            store.close()
        else:
            _close(resources)
        raise


def initialize_live(*, evidence_reference, environ=None):
    """Explicit operator setup. Starts paused; records an owner opening grant, never revenue."""
    if not isinstance(evidence_reference, str) or not evidence_reference.strip():
        raise ConfigurationError("Opening-credit evidence is required")
    values = _env(environ)
    config = load_config(values)
    resources = []
    try:
        runtime, economy, _ = _providers(config, values, resources)
        funding = economy.create_run(config.run_id, opening_micro_usd=5_000_000, evidence_reference=evidence_reference)
        control = runtime.initialize(config.run_id, initial_state="paused", capabilities=DEFAULT_CAPABILITIES,
                                     evidence_reference=evidence_reference)
        return {"run_id": config.run_id, "mode": config.mode,
                "execution_profile": "provider-test" if config.mode == "test" else "live",
                "uses_fixtures": False, "runtime_state": control.data.get("state"),
                "opening_credit_micro_usd": 5_000_000, "funding_kind": "owner_opening_grant",
                "provider_qualification": "not_performed", "funding": funding}
    finally:
        _close(resources)
