"""Operator-owned economy configuration; never placed in the agent workspace."""
from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_path: str
    operator_token: str = field(repr=False)
    gateway_token: str = field(repr=False)
    agent_token: str = field(repr=False)
    agent_run_id: str
    stripe_api_key: str | None = field(default=None, repr=False)
    stripe_webhook_secret: str | None = field(default=None, repr=False)
    stripe_account: str | None = None
    stripe_mode: str = "test"
    checkout_success_url: str | None = None
    checkout_cancel_url: str | None = None
    runtime_auth_secret: str | None = field(default=None, repr=False)
    runtime_tool_profiles: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        tokens = (self.operator_token, self.gateway_token, self.agent_token)
        for token in tokens:
            if not isinstance(token, str) or not 32 <= len(token) <= 512 or not token.isascii() or any(c.isspace() for c in token):
                raise ValueError("Each economy bearer token must be 32–512 ASCII characters without whitespace")
        if len(set(tokens)) != 3:
            raise ValueError("Operator, gateway and agent tokens must be distinct")
        if not isinstance(self.agent_run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.agent_run_id):
            raise ValueError("WIDEMIND_AGENT_RUN_ID must identify the one run authorized to the agent")
        if not self.database_path or self.database_path == ":memory:":
            raise ValueError("Use a persistent economy database path")
        if self.stripe_mode not in {"test", "live"}:
            raise ValueError("WIDEMIND_STRIPE_MODE must be test or live")
        if not isinstance(self.runtime_tool_profiles, dict):
            raise ValueError("Runtime tool profiles must be an object")
        if self.runtime_tool_profiles and not self.runtime_auth_secret:
            raise ValueError("Runtime tool profiles require a persistent authorization secret")
        if self.runtime_auth_secret:
            secret = self.runtime_auth_secret
            if not isinstance(secret, str) or not 32 <= len(secret) <= 512 or not secret.isascii() or any(c.isspace() for c in secret):
                raise ValueError("Runtime authorization secret must contain 32–512 ASCII characters without whitespace")
            if secret in tokens:
                raise ValueError("Runtime authorization secret must differ from bearer identities")
            from .runtime import validate_profiles
            object.__setattr__(self, 'runtime_tool_profiles', validate_profiles(self.runtime_tool_profiles))
        stripe_values = (self.stripe_api_key, self.stripe_webhook_secret, self.stripe_account,
                         self.checkout_success_url, self.checkout_cancel_url)
        if any(stripe_values) and not all(stripe_values):
            raise ValueError("Stripe requires an API key, webhook secret, account ID and both checkout URLs")
        if self.stripe_api_key:
            prefix = "sk_test_" if self.stripe_mode == "test" else "sk_live_"
            # Stripe restricted keys are supported when granted the documented permissions.
            restricted = "rk_test_" if self.stripe_mode == "test" else "rk_live_"
            if not self.stripe_api_key.startswith((prefix, restricted)):
                raise ValueError("Stripe API key does not match the configured mode")
            if not self.stripe_webhook_secret.startswith("whsec_") or len(self.stripe_webhook_secret) < 12:
                raise ValueError("Invalid Stripe webhook signing-secret format")
            if not re.fullmatch(r"acct_[A-Za-z0-9_]+", self.stripe_account):
                raise ValueError("Configure the expected Stripe acct_ account ID")
            for address in (self.checkout_success_url, self.checkout_cancel_url):
                try:
                    url = urlsplit(address)
                    port = url.port
                except ValueError as exc:
                    raise ValueError("Invalid checkout redirect URL") from exc
                local_test = self.stripe_mode == "test" and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
                if (url.scheme != "https" and not local_test) or not url.hostname or url.username or url.password or any(ord(c) < 32 for c in address) or (port is not None and port == 0):
                    raise ValueError("Checkout redirects require HTTPS; loopback HTTP is permitted in test mode")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_api_key)

    def create_payments(self, service):
        """Create the configured trusted adapter without contacting the provider."""
        if not self.stripe_enabled:
            return None
        from .stripe import StripePayments
        return StripePayments(service, api_key=self.stripe_api_key, account=self.stripe_account,
                              mode=self.stripe_mode, webhook_secret=self.stripe_webhook_secret,
                              success_url=self.checkout_success_url, cancel_url=self.checkout_cancel_url)

    @classmethod
    def from_env(cls) -> "Settings":
        required = ("WIDEMIND_OPERATOR_TOKEN", "WIDEMIND_GATEWAY_TOKEN", "WIDEMIND_AGENT_TOKEN", "WIDEMIND_AGENT_RUN_ID")
        for name in required:
            if not os.environ.get(name):
                raise ValueError(f"Missing required environment variable: {name}")
        profile_json = os.environ.get("WIDEMIND_RUNTIME_TOOL_PROFILES", "{}")
        if len(profile_json.encode()) > 65536:
            raise ValueError("Runtime tool profile configuration exceeds 64 KiB")
        # Optional blank fields in the shared .env template mean unconfigured,
        # just like an absent variable; nonempty malformed JSON still fails.
        if not profile_json.strip():
            profile_json = "{}"
        try:
            profiles = json.loads(profile_json)
        except (ValueError, RecursionError):
            raise ValueError("Runtime tool profile configuration must be valid JSON") from None
        if not isinstance(profiles, dict):
            raise ValueError("Runtime tool profiles must be a JSON object")
        return cls(
            database_path=os.environ.get("WIDEMIND_ECONOMY_DB", str(Path("data") / "economy.sqlite3")),
            operator_token=os.environ[required[0]], gateway_token=os.environ[required[1]],
            agent_token=os.environ[required[2]], agent_run_id=os.environ[required[3]],
            stripe_api_key=os.environ.get("WIDEMIND_STRIPE_API_KEY") or None,
            stripe_webhook_secret=os.environ.get("WIDEMIND_STRIPE_WEBHOOK_SECRET") or None,
            stripe_account=os.environ.get("WIDEMIND_STRIPE_ACCOUNT") or None,
            stripe_mode=os.environ.get("WIDEMIND_STRIPE_MODE", "test"),
            checkout_success_url=os.environ.get("WIDEMIND_CHECKOUT_SUCCESS_URL") or None,
            checkout_cancel_url=os.environ.get("WIDEMIND_CHECKOUT_CANCEL_URL") or None,
            runtime_auth_secret=os.environ.get("WIDEMIND_RUNTIME_AUTH_SECRET") or None,
            runtime_tool_profiles=profiles,
        )
