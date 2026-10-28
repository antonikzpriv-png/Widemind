"""OAuth fixtures only: no browser, credentials, DNS or live Gmail requests."""
import base64
import hashlib
import json
import io
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from widemind.tools import gmail_oauth as oauth
from widemind.tools.contracts import HTTPResponse, ToolContext, ToolError, Unavailable


class MemorySecrets:
    def __init__(self):
        self.values = {"gmail.client": "fixture-client-secret"}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


class FixtureTransport:
    def __init__(self, responses=()):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, data = response
        return HTTPResponse(status, {}, json.dumps(data).encode(), url)


class GmailOAuthTests(unittest.TestCase):
    def setUp(self):
        self.config = {"client_id": "fixture-client", "redirect_uri": "http://127.0.0.1:8123/oauth/callback",
                       "expected_account": "dedicated@example.test", "client_secret_ref": "gmail.client",
                       "token_ref": "gmail.token", "oauth_state_ref": "gmail.oauth-state"}
        self.secrets = MemorySecrets()
        self.transport = FixtureTransport()
        self.ctx = ToolContext("action", "run", "reservation", self.config, {}, None,
                               self.secrets, self.transport, None, time.monotonic() + 60, lambda: None)

    def info(self, **overrides):
        result = {"aud": "fixture-client", "scope": " ".join(oauth.REQUIRED_SCOPES), "sub": "fixture-subject"}
        result.update(overrides)
        return result

    def grant(self, **overrides):
        result = {"access_token": "fixture-access-token", "refresh_token": "fixture-refresh-token",
                  "expires_in": 3600, "token_type": "Bearer"}
        result.update(overrides)
        return result

    def authorize_callback(self, **overrides):
        url = oauth.authorization_url(self.ctx)["url"]
        query = parse_qs(urlsplit(url).query)
        values = {"state": query["state"][0], "code": "fixture-authorization-code"}
        values.update(overrides)
        return self.config["redirect_uri"] + "?" + urlencode(values)

    def assert_sanitized(self, error):
        for value in ("fixture-client-secret", "fixture-access-token", "fixture-refresh-token",
                      "fixture-authorization-code", "private-provider-content"):
            self.assertNotIn(value, str(error))
            self.assertNotIn(value, repr(error))

    def test_configuration_and_supplied_token_metadata_never_claim_ready(self):
        self.assertEqual(oauth.availability({}, self.secrets)["status"], "unavailable")
        self.assertEqual(oauth.availability(self.config, self.secrets)["status"], "authorization_required")
        self.secrets.set("gmail.token", json.dumps({"access_token": "fixture-access-token", "account": self.config["expected_account"],
                                                   "scopes": list(oauth.REQUIRED_SCOPES), "verified": True}))
        result = oauth.availability(self.config, self.secrets)
        self.assertEqual(result["status"], "not_verified")
        self.assertFalse(result["available"])
        self.assertFalse(self.transport.calls)

    def test_authorization_uses_expiring_random_state_pkce_and_private_storage(self):
        first, second = oauth.authorization_url(self.ctx), oauth.authorization_url(self.ctx)
        query = parse_qs(urlsplit(second["url"]).query)
        transient = json.loads(self.secrets.get("gmail.oauth-state"))
        self.assertNotEqual(first["url"], second["url"])
        self.assertEqual(query["scope"], [" ".join(oauth.REQUIRED_SCOPES)])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(transient["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["state"], [transient["state"]])
        self.assertNotIn(transient["verifier"], second["url"])
        self.assertLessEqual(second["expires_at"] - time.time(), 600)
        self.assertNotIn("fixture-client-secret", repr(second))
        self.assertFalse(self.transport.calls)

    def test_complete_exchanges_once_verifies_real_grants_and_profile_then_saves(self):
        callback = self.authorize_callback()
        self.transport.responses = [(200, self.grant()), (200, self.info()),
                                    (200, {"emailAddress": "Dedicated@Example.Test"})]
        result = oauth.complete_authorization(self.ctx, callback)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["account"], "Dedicated@Example.Test")
        self.assertEqual(self.secrets.get("gmail.oauth-state"), "")
        saved = json.loads(self.secrets.get("gmail.token"))
        self.assertEqual(saved["refresh_token"], "fixture-refresh-token")
        self.assertGreater(saved["expires_at"], time.time())
        self.assertEqual(len(self.transport.calls), 3)
        exchange = parse_qs(self.transport.calls[0][2]["body"].decode())
        self.assertEqual(exchange["redirect_uri"], [self.config["redirect_uri"]])
        self.assertIn("code_verifier", exchange)
        self.assertEqual(self.transport.calls[1][0:2], ("POST", oauth.TOKENINFO_ENDPOINT))
        self.assertIsNone(self.transport.calls[1][2]["body"])
        self.assertEqual(self.transport.calls[1][2]["headers"]["authorization"], "Bearer fixture-access-token")
        self.assertNotIn("fixture-access-token", repr(result))
        with self.assertRaises(Unavailable):
            oauth.complete_authorization(self.ctx, callback)
        self.assertEqual(len(self.transport.calls), 3)

    def test_wrong_state_duplicate_state_redirect_and_expiry_make_no_request(self):
        callback = self.authorize_callback()
        wrong = callback.replace("state=", "state=wrong")
        for candidate in (wrong, callback + "&state=duplicate", callback.replace("127.0.0.1", "attacker.example")):
            with self.subTest(callback=candidate):
                with self.assertRaises(ToolError) as failure:
                    oauth.complete_authorization(self.ctx, candidate)
                self.assert_sanitized(failure.exception)
                self.assertTrue(self.secrets.get("gmail.oauth-state"))
        with patch.object(oauth.time, "time", return_value=time.time() + 601):
            with self.assertRaises(Unavailable):
                oauth.complete_authorization(self.ctx, callback)
        self.assertEqual(self.secrets.get("gmail.oauth-state"), "")
        self.assertFalse(self.transport.calls)

    def test_denied_callback_consumes_matching_state_without_echoing_provider_error(self):
        callback = self.authorize_callback(error="private-provider-content")
        with self.assertRaises(Unavailable) as failure:
            oauth.complete_authorization(self.ctx, callback)
        self.assert_sanitized(failure.exception)
        self.assertEqual(self.secrets.get("gmail.oauth-state"), "")
        self.assertFalse(self.transport.calls)

    def test_configuration_changes_invalidate_pending_authorization(self):
        callback = self.authorize_callback()
        self.config["expected_account"] = "different@example.test"
        with self.assertRaises(Unavailable):
            oauth.complete_authorization(self.ctx, callback)
        self.assertFalse(self.transport.calls)

    def test_grant_audience_and_subject_mismatch_stop_before_profile(self):
        for info in (self.info(scope=oauth.REQUIRED_SCOPES[0]), self.info(aud="different-client"), self.info(sub="wrong-subject")):
            with self.subTest(info=info):
                self.config["expected_subject"] = "fixture-subject"
                self.transport.responses, self.transport.calls = [(200, info)], []
                with self.assertRaises(Unavailable) as failure:
                    oauth.verify(self.ctx, "fixture-access-token")
                self.assert_sanitized(failure.exception)
                self.assertEqual(len(self.transport.calls), 1)

    def test_account_verification_does_not_trust_supplied_metadata_or_aliases(self):
        self.secrets.set("gmail.token", json.dumps({"account": "dedicated@example.test", "scopes": list(oauth.REQUIRED_SCOPES)}))
        self.transport.responses = [(200, self.info()), (200, {"emailAddress": "unrelated@example.test"})]
        with self.assertRaises(Unavailable):
            oauth.verify(self.ctx, "fixture-access-token")
        self.assertEqual(len(self.transport.calls), 2)

    def test_refresh_retains_refresh_token_and_does_not_claim_verification(self):
        self.secrets.set("gmail.token", json.dumps({"access_token": "expired-token", "refresh_token": "fixture-refresh-token",
                                                   "expires_at": time.time() - 1}))
        grant = self.grant()
        del grant["refresh_token"]
        self.transport.responses = [(200, grant)]
        self.assertEqual(oauth.access_token(self.ctx), "fixture-access-token")
        stored = json.loads(self.secrets.get("gmail.token"))
        self.assertEqual(stored["refresh_token"], "fixture-refresh-token")
        self.assertNotIn("verified", stored)
        self.assertEqual(len(self.transport.calls), 1)
        form = parse_qs(self.transport.calls[0][2]["body"].decode())
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertEqual(oauth.access_token(self.ctx), "fixture-access-token")
        self.assertEqual(len(self.transport.calls), 1)

    def test_rejected_refresh_and_transport_exception_are_sanitized_without_retries(self):
        self.secrets.set("gmail.token", json.dumps({"refresh_token": "fixture-refresh-token"}))
        failures = [(400, {"error": "invalid_grant", "error_description": "private-provider-content"}),
                    RuntimeError("fixture-client-secret fixture-refresh-token private-provider-content")]
        for response in failures:
            self.transport.responses, self.transport.calls = [response], []
            with self.assertRaises(ToolError) as failure:
                oauth.access_token(self.ctx)
            self.assert_sanitized(failure.exception)
            self.assertEqual(len(self.transport.calls), 1)

    def test_incorrect_mailbox_never_persists_new_tokens(self):
        callback = self.authorize_callback()
        self.transport.responses = [(200, self.grant()), (200, self.info()), (200, {"emailAddress": "unrelated@example.test"})]
        with self.assertRaises(Unavailable):
            oauth.complete_authorization(self.ctx, callback)
        self.assertIsNone(self.secrets.get("gmail.token"))
        self.assertEqual(self.secrets.get("gmail.oauth-state"), "")

    def test_repeat_authorization_retains_only_same_verified_account_subject_refresh(self):
        for old_account, old_subject, succeeds in (("dedicated@example.test", "fixture-subject", True),
                                                  ("unrelated@example.test", "fixture-subject", False),
                                                  ("dedicated@example.test", "different-subject", False)):
            with self.subTest(account=old_account, subject=old_subject):
                previous = {"account": old_account, "subject": old_subject, "refresh_token": "fixture-refresh-token"}
                self.secrets.set("gmail.token", json.dumps(previous))
                callback = self.authorize_callback()
                grant = self.grant()
                del grant["refresh_token"]
                self.transport.responses = [(200, grant), (200, self.info()), (200, {"emailAddress": "dedicated@example.test"})]
                if succeeds:
                    oauth.complete_authorization(self.ctx, callback)
                    self.assertEqual(json.loads(self.secrets.get("gmail.token"))["refresh_token"], "fixture-refresh-token")
                else:
                    with self.assertRaises(Unavailable):
                        oauth.complete_authorization(self.ctx, callback)
                    self.assertEqual(json.loads(self.secrets.get("gmail.token")), previous)

    def test_invalid_provider_shape_remains_sanitized(self):
        self.secrets.set("gmail.token", json.dumps({"refresh_token": "fixture-refresh-token"}))
        for bad in (self.grant(token_type=123), self.grant(expires_in=float("inf")), self.grant(access_token="fixture-access-token\r\nInjected:secret")):
            self.transport.responses = [(200, bad)]
            with self.assertRaises(ToolError) as failure:
                oauth.access_token(self.ctx)
            self.assert_sanitized(failure.exception)

    def test_cancelled_context_stops_before_secret_or_http_work(self):
        self.ctx.cancelled = lambda: True
        for call in (lambda: oauth.authorization_url(self.ctx), lambda: oauth.verify(self.ctx, "fixture-access-token"),
                     lambda: oauth.access_token(self.ctx), lambda: oauth.complete_authorization(self.ctx, "invalid")):
            with self.assertRaises(ToolError) as failure:
                call()
            self.assertEqual(failure.exception.code, "cancelled")
        self.assertFalse(self.transport.calls)

    def test_unsafe_redirect_and_colliding_secret_names_are_unavailable(self):
        for uri in ("http://public.example.test/callback", "https://example.test/callback?configured=query",
                    "https://user:secret@example.test/callback", "https://example.test/callback#fragment"):
            self.config["redirect_uri"] = uri
            self.assertEqual(oauth.availability(self.config, self.secrets)["status"], "unavailable")
        self.config["redirect_uri"] = "https://example.test/callback"
        self.config["oauth_state_ref"] = self.config["token_ref"]
        self.assertEqual(oauth.availability(self.config, self.secrets)["status"], "unavailable")

    def test_owner_cli_uses_restricted_setup_context_and_hidden_callback_input(self):
        contexts = []
        ctx = self.ctx

        class Gateway:
            @contextmanager
            def setup_context(self, tool):
                contexts.append(tool)
                yield ctx

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(oauth, "_setup_gateway", return_value=Gateway()), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(oauth.main(["--config", "/owner/config.json", "start"]), 0)
            started = json.loads(stdout.getvalue())
            state = parse_qs(urlsplit(started["url"]).query)["state"][0]
            callback = self.config["redirect_uri"] + "?" + urlencode({"state": state, "code": "fixture-authorization-code"})
            self.transport.responses = [(200, self.grant()), (200, self.info()), (200, {"emailAddress": "dedicated@example.test"})]
            stdout.seek(0)
            stdout.truncate()
            with patch("sys.stdin", io.StringIO(callback + "\n")):
                self.assertEqual(oauth.main(["--config", "/owner/config.json", "complete", "--callback-stdin"]), 0)
            self.assertEqual(json.loads(stdout.getvalue())["status"], "verified")
        self.assertEqual(contexts, ["gmail", "gmail"])
        self.assertNotIn("fixture-authorization-code", stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("fixture-access-token", stdout.getvalue() + stderr.getvalue())

    def test_owner_cli_sanitizes_unexpected_gateway_errors(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(oauth, "_setup_gateway", side_effect=RuntimeError("fixture-client-secret private-provider-content")), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(oauth.main(["--config", "/owner/config.json", "verify"]), 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"], "oauth_setup")
        self.assertNotIn("fixture-client-secret", stderr.getvalue())
        self.assertNotIn("private-provider-content", stderr.getvalue())

    def test_config_validation_and_invalid_unicode_are_sanitized_without_io(self):
        oauth.validate_config(self.config)
        self.config["client_id"] = "invalid-\ud800"
        with self.assertRaises(Unavailable) as failure:
            oauth.validate_config(self.config)
        self.assertNotIn("invalid-", str(failure.exception))
        self.assertFalse(self.transport.calls)

    def test_huge_untrusted_numeric_expiry_fails_safely(self):
        callback = self.authorize_callback()
        transient = json.loads(self.secrets.get("gmail.oauth-state"))
        transient["expires_at"] = 10 ** 1000
        self.secrets.set("gmail.oauth-state", json.dumps(transient))
        with self.assertRaises(Unavailable):
            oauth.complete_authorization(self.ctx, callback)
        self.assertFalse(self.transport.calls)


if __name__ == "__main__":
    unittest.main()
