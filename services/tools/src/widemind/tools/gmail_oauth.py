"""Dedicated-account Gmail user OAuth, with explicit verification and no retries.

Only the injected transport and secret store handle credentials. None of these
functions starts a browser or performs interactive login. Setup URLs and results
belong in the private operator flow, never a public activity projection.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets as random_secrets
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from .contracts import Secrets, ToolContext, ToolError, Unavailable

REQUIRED_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)
AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"
PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
_REQUIRED_CONFIG = (
    "client_id", "redirect_uri", "expected_account", "client_secret_ref",
    "token_ref", "oauth_state_ref",
)
_MAX_SECRET_BYTES = 65_536
_MAX_RESPONSE_BYTES = 131_072
_STATE_LIFETIME = 600


def _text(value: Any, *, maximum: int = 16_384) -> bool:
    if not isinstance(value, str):
        return False
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        return False
    return (0 < size <= maximum and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _config(config: Mapping[str, Any]) -> dict:
    if any(not _text(config.get(key), maximum=2048) for key in _REQUIRED_CONFIG):
        raise Unavailable("Configure Gmail client_id, redirect_uri, expected_account, "
                          "client_secret_ref, token_ref and oauth_state_ref.")
    result = {key: config[key] for key in _REQUIRED_CONFIG}
    if len(set(result[key] for key in ("client_secret_ref", "token_ref", "oauth_state_ref"))) != 3:
        raise Unavailable("Use distinct Gmail client secret, token and OAuth state secret names.")
    try:
        redirect = urlsplit(result["redirect_uri"])
        redirect.port  # Reject malformed ports before any authorization URL is issued.
    except ValueError:
        raise Unavailable("Configure a valid fixed Gmail OAuth redirect URI.") from None
    secure = redirect.scheme == "https" and bool(redirect.hostname)
    loopback = redirect.scheme == "http" and redirect.hostname in ("localhost", "127.0.0.1", "::1")
    if (not (secure or loopback) or redirect.username or redirect.password
            or redirect.query or redirect.fragment or not redirect.path.startswith("/")):
        raise Unavailable("Configure a fixed HTTPS Gmail OAuth callback, or HTTP loopback callback, without query or fragment.")
    if ("@" not in result["expected_account"]
            or any(c.isspace() for c in result["expected_account"])):
        raise Unavailable("Configure the dedicated Gmail account's exact email address.")
    subject = config.get("expected_subject")
    if subject is not None and not _text(subject, maximum=256):
        raise Unavailable("Configure a valid expected Gmail subject identifier.")
    result["expected_subject"] = subject
    return result


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate owner settings without reading credentials or making requests."""
    _config(config)


def _get_secret(store: Secrets, name: str) -> str | None:
    try:
        value = store.get(name)
    except Exception:
        raise Unavailable("The Gmail secret store is unavailable; check private operator configuration.") from None
    try:
        valid = value is None or isinstance(value, str) and len(value.encode("utf-8")) <= _MAX_SECRET_BYTES
    except UnicodeError:
        valid = False
    if not valid:
        raise Unavailable("The configured Gmail secret is invalid; replace it through private operator setup.")
    return value


def _save_secret(store: Secrets, name: str, value: str) -> None:
    try:
        store.set(name, value)
    except Exception:
        raise Unavailable("Gmail credential state could not be saved; repair the private secret store before continuing.") from None


def _secret_json(store: Secrets, name: str, *, required: bool = True) -> dict:
    value = _get_secret(store, name)
    if not value:
        if required:
            raise Unavailable("Gmail authorization is required; complete private operator OAuth setup.")
        return {}
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (ValueError, TypeError, RecursionError):
        raise Unavailable("Stored Gmail authorization is invalid; repeat private operator OAuth setup.") from None


def availability(config: Mapping[str, Any], secrets: Secrets) -> dict:
    """Configuration alone never establishes mailbox or granted-scope readiness."""
    try:
        settings = _config(config)
        if not _get_secret(secrets, settings["client_secret_ref"]):
            raise Unavailable("Install the Gmail OAuth client secret in the configured secret store.")
        token = _secret_json(secrets, settings["token_ref"], required=False)
        if not token:
            return {"available": False, "configured": True, "status": "authorization_required",
                    "reason": "Complete private operator Gmail OAuth authorization."}
        return {"available": False, "configured": True, "status": "not_verified",
                "reason": "Verify the dedicated Gmail account and granted scopes before live use."}
    except ToolError as error:
        return {"available": False, "configured": False, "status": "unavailable", "reason": error.message}


def _request_json(ctx: ToolContext, method: str, url: str, *, token: str | None = None,
                  fields: Mapping[str, str] | None = None) -> dict:
    ctx.check()
    headers = {"accept": "application/json"}
    if token is not None:
        if not _text(token):
            raise Unavailable("Stored Gmail access token is invalid; repeat private OAuth setup.")
        headers["authorization"] = "Bearer " + token
    body = None
    if fields is not None:
        headers["content-type"] = "application/x-www-form-urlencoded"
        body = urlencode(fields).encode("ascii")
    try:
        response = ctx.transport.request(method, url, headers=headers, body=body,
                                         timeout=ctx.remaining(20), max_bytes=_MAX_RESPONSE_BYTES,
                                         cancel=ctx.check)
    except ToolError as error:
        if error.code in ("cancelled", "timeout"):
            raise ToolError(error.code, "Gmail authorization request was cancelled or exceeded its deadline.") from None
        raise ToolError("oauth_transport", "Gmail authorization request failed; check private operator diagnostics.") from None
    except Exception:
        raise ToolError("oauth_transport", "Gmail authorization request failed; check private operator connectivity.") from None
    if response.url != url or len(response.body) > _MAX_RESPONSE_BYTES:
        raise ToolError("oauth_response", "Gmail authorization returned an invalid response.")
    try:
        data = json.loads(response.body)
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ToolError("oauth_response", "Gmail authorization returned an invalid JSON response.") from None
    if not 200 <= response.status < 300:
        if response.status in (400, 401, 403):
            raise Unavailable("Gmail authorization was rejected; verify account consent and repeat private OAuth setup if needed.")
        if response.status == 429:
            raise ToolError("rate_limited", "Gmail authorization is rate limited; wait before another operator attempt.", retryable=True)
        raise ToolError("oauth_provider", "Gmail authorization provider is unavailable; retry private setup later.", retryable=True)
    return data


def _token_record(response: Mapping[str, Any], previous: Mapping[str, Any]) -> dict:
    token = response.get("access_token")
    seconds = response.get("expires_in")
    if (not _text(token) or isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not 0 < seconds <= 86_400 or not math.isfinite(seconds)):
        raise ToolError("oauth_response", "Gmail token response omitted a valid access token or expiry.")
    token_type = response.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.casefold() != "bearer":
        raise ToolError("oauth_response", "Gmail token response used an unsupported token type.")
    refresh = response.get("refresh_token", previous.get("refresh_token"))
    if refresh is not None and not _text(refresh):
        raise ToolError("oauth_response", "Gmail token response contained invalid authorization data.")
    record = {"access_token": token, "expires_at": time.time() + seconds}
    if refresh:
        record["refresh_token"] = refresh
    for key in ("account", "subject"):
        if previous.get(key) is not None:
            record[key] = previous[key]
    return record


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def access_token(ctx: ToolContext) -> str:
    """Obtain a cached or explicitly refreshed token; verify() remains required."""
    ctx.check()
    settings = _config(ctx.config)
    stored = _secret_json(ctx.secrets, settings["token_ref"])
    expiry = stored.get("expires_at")
    if (_text(stored.get("access_token")) and _finite_number(expiry)
            and expiry > time.time() + 60):
        return stored["access_token"]
    refresh = stored.get("refresh_token")
    secret = _get_secret(ctx.secrets, settings["client_secret_ref"])
    if not _text(refresh) or not _text(secret):
        raise Unavailable("Gmail offline authorization is missing or expired; repeat private operator OAuth setup.")
    response = _request_json(ctx, "POST", TOKEN_ENDPOINT, fields={
        "grant_type": "refresh_token", "refresh_token": refresh,
        "client_id": settings["client_id"], "client_secret": secret,
    })
    record = _token_record(response, stored)
    _save_secret(ctx.secrets, settings["token_ref"], json.dumps(record))
    return record["access_token"]


def verify(ctx: ToolContext, token: str) -> dict:
    """Verify provider audience/grants and the actual dedicated mailbox."""
    settings = _config(ctx.config)
    info = _request_json(ctx, "POST", TOKENINFO_ENDPOINT, token=token)
    if info.get("aud") != settings["client_id"]:
        raise Unavailable("Gmail authorization belongs to a different OAuth client; repeat private setup.")
    scope = info.get("scope")
    if not isinstance(scope, str) or not set(REQUIRED_SCOPES).issubset(scope.split()):
        raise Unavailable("Gmail authorization lacks required read and draft/send grants; repeat consent for the configured scopes.")
    subject = info.get("sub")
    if subject is not None and not _text(subject, maximum=256):
        raise ToolError("oauth_response", "Gmail account verification returned invalid identity data.")
    if settings["expected_subject"] is not None and subject != settings["expected_subject"]:
        raise Unavailable("Gmail authorization belongs to a different account subject; authorize the configured dedicated account.")
    profile = _request_json(ctx, "GET", PROFILE_ENDPOINT, token=token)
    email = profile.get("emailAddress")
    if not _text(email, maximum=320) or email.casefold() != settings["expected_account"].casefold():
        raise Unavailable("Gmail authorization belongs to a different mailbox; authorize the configured dedicated account.")
    return {"status": "verified", "account": email, "subject": subject,
            "scopes": sorted(set(scope.split()))}


def authorization_url(ctx: ToolContext) -> dict:
    """Create a private, expiring owner setup URL; do not open it automatically."""
    ctx.check()
    settings = _config(ctx.config)
    if not _text(_get_secret(ctx.secrets, settings["client_secret_ref"])):
        raise Unavailable("Install the Gmail OAuth client secret before starting private authorization.")
    state, verifier = random_secrets.token_urlsafe(32), random_secrets.token_urlsafe(64)
    expires_at = time.time() + _STATE_LIFETIME
    transient = {key: settings[key] for key in ("client_id", "redirect_uri", "expected_account", "expected_subject")}
    transient.update(state=state, verifier=verifier, expires_at=expires_at)
    _save_secret(ctx.secrets, settings["oauth_state_ref"], json.dumps(transient))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    query = urlencode({"client_id": settings["client_id"], "redirect_uri": settings["redirect_uri"],
                       "response_type": "code", "scope": " ".join(REQUIRED_SCOPES),
                       "state": state, "access_type": "offline", "prompt": "consent",
                       "login_hint": settings["expected_account"],
                       "code_challenge": challenge, "code_challenge_method": "S256"})
    return {"status": "authorization_required", "url": AUTHORIZATION_ENDPOINT + "?" + query,
            "expires_at": expires_at}


def complete_authorization(ctx: ToolContext, callback_url: str) -> dict:
    """Consume one matching callback, exchange its code once, verify, then save."""
    ctx.check()
    settings = _config(ctx.config)
    transient = _secret_json(ctx.secrets, settings["oauth_state_ref"])
    expiry = transient.get("expires_at")
    if not _finite_number(expiry) or expiry <= time.time():
        _save_secret(ctx.secrets, settings["oauth_state_ref"], "")
        raise Unavailable("Gmail authorization setup expired; create a new private setup URL.")
    if any(transient.get(key) != settings[key] for key in ("client_id", "redirect_uri", "expected_account", "expected_subject")):
        raise Unavailable("Gmail setup configuration changed; create a new private authorization URL.")
    try:
        if not _text(callback_url, maximum=16_384):
            raise ValueError
        supplied, expected = urlsplit(callback_url), urlsplit(settings["redirect_uri"])
        if (supplied.scheme != expected.scheme or supplied.hostname != expected.hostname
                or supplied.port != expected.port or supplied.path != expected.path
                or supplied.username or supplied.password or supplied.fragment):
            raise ValueError
        query = parse_qs(supplied.query, keep_blank_values=True, max_num_fields=20)
        states = query.get("state", [])
        if (len(states) != 1 or not _text(states[0], maximum=256)
                or not _text(transient.get("state"), maximum=256)
                or not hmac.compare_digest(states[0].encode("utf-8"), transient["state"].encode("utf-8"))):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ToolError("oauth_callback", "Gmail callback did not match the fixed redirect and pending authorization state.") from None
    _save_secret(ctx.secrets, settings["oauth_state_ref"], "")  # Single use, including denied or failed exchanges.
    if "error" in query:
        raise Unavailable("Gmail consent was denied or unavailable; start private authorization again when ready.")
    codes = query.get("code", [])
    if len(codes) != 1 or not _text(codes[0], maximum=4096) or not _text(transient.get("verifier"), maximum=128):
        raise ToolError("oauth_callback", "Gmail callback did not contain a valid authorization code.")
    secret = _get_secret(ctx.secrets, settings["client_secret_ref"])
    if not _text(secret):
        raise Unavailable("The Gmail OAuth client secret is missing; restore private configuration.")
    response = _request_json(ctx, "POST", TOKEN_ENDPOINT, fields={
        "grant_type": "authorization_code", "code": codes[0], "client_id": settings["client_id"],
        "client_secret": secret, "redirect_uri": settings["redirect_uri"],
        "code_verifier": transient["verifier"],
    })
    previous = _secret_json(ctx.secrets, settings["token_ref"], required=False)
    previous_account = previous.get("account")
    if not isinstance(previous_account, str) or previous_account.casefold() != settings["expected_account"].casefold():
        previous = {}  # An unrelated cached refresh token must not be joined to a new grant.
    record = _token_record(response, {})
    result = verify(ctx, record["access_token"])
    if (not record.get("refresh_token") and previous.get("subject") == result["subject"]
            and _text(previous.get("refresh_token"))):
        record["refresh_token"] = previous["refresh_token"]
    if not record.get("refresh_token"):
        raise Unavailable("Gmail returned no offline refresh token; repeat private authorization with consent.")
    record.update(account=result["account"], subject=result["subject"])
    _save_secret(ctx.secrets, settings["token_ref"], json.dumps(record))
    return result


def _setup_gateway(config_path: str) -> Any:
    # Import only when the owner invokes setup. Optional adapter imports never
    # require the rest of the application's control plane to be initialized.
    from widemind.gateway import Gateway
    return Gateway.from_config(config_path)


def main(argv: list[str] | None = None) -> int:
    """Owner-only setup entry point; callback secrets never appear in argv."""
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(description="Private dedicated Gmail OAuth setup; does not send email.")
    parser.add_argument("--config", required=True, help="Owner-only WideMind configuration path.")
    parser.add_argument("operation", choices=("start", "complete", "verify"))
    parser.add_argument("--callback-stdin", action="store_true", help="Read a callback URL from private standard input instead of a hidden prompt.")
    args = parser.parse_args(argv)
    if args.callback_stdin and args.operation != "complete":
        parser.error("--callback-stdin is only valid with complete")
    try:
        callback = None
        if args.operation == "complete":
            if args.callback_stdin:
                callback = sys.stdin.readline(16_386).strip()
            elif sys.stdin.isatty():
                callback = getpass.getpass("Paste the complete private Gmail callback URL (hidden): ")
            else:
                raise ToolError("oauth_callback", "Use a terminal's hidden prompt or --callback-stdin with private standard input.")
        gateway = _setup_gateway(args.config)
        # Root restricts this administrative, zero-cost context to Gmail OAuth.
        # It cannot be used as a generic bypass for paid tools or email sends.
        with gateway.setup_context("gmail") as ctx:
            if args.operation == "start":
                result = authorization_url(ctx)
            elif args.operation == "complete":
                result = complete_authorization(ctx, callback)
            else:
                result = verify(ctx, access_token(ctx))
        print(json.dumps(result, sort_keys=True))
        return 0
    except ToolError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"error": "cancelled", "message": "Private Gmail setup was cancelled."}), file=sys.stderr)
        return 130
    except Exception:
        print(json.dumps({"error": "oauth_setup", "message": "Private Gmail setup could not finish; check trusted operator configuration."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
