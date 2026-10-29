import json
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from widemind.business.contracts import Outcome
from widemind.host import ConfigurationError, business_factory, initialize_live, load_config, readiness
from widemind.runtime import RuntimeControl


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.runtime = RuntimeControl(self.path, mode="test")

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def initialize(self):
        return self.runtime.initialize("run", evidence_reference="test-owned run")

    def command(self, state, action="action-1"):
        return self.runtime.change_state(run_id="run", state=state, action_id=action, evidence_reference="operator test request")

    def test_initialization_is_paused_and_repeating_setup_does_not_resume(self):
        self.assertEqual(self.initialize().data["state"], "paused")
        self.assertTrue(self.runtime.cancelled("run"))
        self.command("active")
        self.command("stopped", "stop")
        self.initialize()
        self.assertEqual(self.runtime.state("run"), "stopped")
        self.assertEqual(self.runtime.db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()[0], 1)

    def test_pause_stop_and_resume_generation_survive_restart(self):
        self.initialize()
        self.command("active")
        token = self.runtime.cancellation_generation("run")
        self.assertFalse(self.runtime.cancelled("run"))
        self.command("stopped", "stop")
        self.assertTrue(self.runtime.cancelled("run"))
        self.command("active", "resume")
        self.assertNotEqual(self.runtime.cancellation_generation("run"), token)
        self.runtime.close()
        self.runtime = RuntimeControl(self.path, mode="test")
        self.assertEqual(self.runtime.state("run"), "active")
        self.assertEqual(self.runtime.reconcile(action_id="stop").data["state"], "stopped")

    def test_duplicate_control_is_idempotent_and_conflicting_reuse_rejected(self):
        self.initialize()
        first = self.command("active")
        self.assertEqual(self.command("active"), first)
        with self.assertRaises(ValueError):
            self.command("paused")
        self.assertEqual(self.runtime.state("run"), "active")
        self.assertEqual(self.runtime.db.execute("SELECT revision FROM runtime_runs").fetchone()[0], 1)
        self.assertEqual(self.runtime.db.execute("SELECT COUNT(*) FROM runtime_actions").fetchone()[0], 2)

    def test_unknown_run_fails_closed_and_invalid_state_cannot_dispatch(self):
        self.assertEqual(self.runtime.state("missing"), "failed")
        self.assertTrue(self.runtime.cancelled("missing"))
        self.assertFalse(self.runtime.capability_enabled("missing", "gmail.send"))
        self.assertEqual(self.command("active").state, "failed")
        with self.assertRaises(ValueError):
            self.command("invented", "bad")
        self.assertEqual(self.runtime.reconcile(action_id="absent").state, "unknown")

    def test_mode_binding_covers_restart_idempotency_and_reconcile(self):
        self.initialize()
        self.command("active")
        other = RuntimeControl(self.path, mode="live")
        try:
            for fn in (lambda: other.initialize("run"), lambda: other.state("run"),
                       lambda: other.reconcile(action_id="action-1"),
                       lambda: other.change_state(run_id="run", state="active", action_id="action-1", evidence_reference="operator test request")):
                with self.assertRaises(ValueError):
                    fn()
        finally:
            other.close()

    def test_capability_changes_are_audited_and_invalidate_running_token(self):
        self.initialize()
        self.command("active")
        token = self.runtime.cancellation_generation("run")
        self.assertTrue(self.runtime.capability_enabled("run", "research.collect"))
        outcome = self.runtime.configure_capabilities(run_id="run", capabilities=["gmail.read"], action_id="policy", evidence_reference="revoke paid tools")
        self.assertEqual(outcome.state, "succeeded")
        self.assertFalse(self.runtime.capability_enabled("run", "research.collect"))
        self.assertTrue(self.runtime.capability_enabled("run", "gmail.read"))
        self.assertNotEqual(self.runtime.cancellation_generation("run"), token)
        with self.assertRaises(ValueError):
            self.runtime.configure_capabilities(run_id="run", capabilities="all", action_id="bad", evidence_reference="cannot grant wildcard")

    def test_control_audit_is_append_only(self):
        self.initialize()
        self.command("active")
        for sql in ("DELETE FROM runtime_actions", "UPDATE runtime_actions SET resulting_state='active'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.runtime.db.execute(sql)
        self.assertEqual(self.runtime.db.execute("SELECT COUNT(*) FROM runtime_actions").fetchone()[0], 2)

    def test_concurrent_duplicate_control_is_one_revision(self):
        self.initialize()
        def command(_):
            service = RuntimeControl(self.path, mode="test")
            try:
                return service.change_state(run_id="run", state="active", action_id="concurrent", evidence_reference="one operator event")
            finally:
                service.close()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(command, range(4)))
        self.assertEqual(len({json.dumps(result.data, sort_keys=True) for result in results}), 1)
        self.assertEqual(self.runtime.db.execute("SELECT revision FROM runtime_runs").fetchone()[0], 1)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = {
            "WIDEMIND_RUN_ID": "live-run", "WIDEMIND_MODE": "live",
            "WIDEMIND_BUSINESS_DB": str(root / "business" / "business.sqlite3"),
            "WIDEMIND_RUNTIME_DB": str(root / "control" / "runtime.sqlite3"),
            "WIDEMIND_ECONOMY_DB": str(root / "control" / "economy.sqlite3"),
            "WIDEMIND_TOOLS_DB": str(root / "control" / "tools.sqlite3"),
            "WIDEMIND_WORKSPACE": str(root / "workspace"),
            "WIDEMIND_CREDENTIALS_DIR": str(root / "credentials"),
            "WIDEMIND_GMAIL_ADDRESS": "widemind@example.invalid",
            "WIDEMIND_OPERATOR_TOKEN": "operator-test-only-" * 3,
            "GMAIL_CLIENT_ID": "fixture-client", "GMAIL_CLIENT_SECRET": "not-a-real-secret",
            "GMAIL_REFRESH_TOKEN": "not-a-real-refresh-token", "OPENAI_API_KEY": "not-a-real-api-key",
            "STRIPE_SECRET_KEY": "not-a-real-stripe-key", "STRIPE_WEBHOOK_SECRET": "not-a-real-webhook-secret",
            "STRIPE_SUCCESS_URL": "https://example.invalid/success", "STRIPE_CANCEL_URL": "https://example.invalid/cancel",
            "OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS": "400000",
            "OPENAI_OUTPUT_MICRO_USD_PER_MILLION_TOKENS": "1600000",
            "WIDEMIND_RESEARCH_SOURCE_URLS": '["https://example.invalid/source"]',
            "WIDEMIND_ALLOWED_RECIPIENTS": '["controlled@example.invalid"]',
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_configuration_missing_credentials_fails_before_provider_construction(self):
        with patch("widemind.host.importlib.import_module") as imports:
            with self.assertRaises(ConfigurationError):
                business_factory({})
            imports.assert_not_called()
        result = readiness({"OPENAI_API_KEY": "do-not-display-this"})
        self.assertNotIn("do-not-display-this", json.dumps(result))
        self.assertTrue(all(entry["status"] == "configuration_required" for entry in result))

    def test_protected_database_and_workspace_paths_are_separate(self):
        self.assertEqual(load_config(self.env).run_id, "live-run")
        for key, value in (
            ("WIDEMIND_ECONOMY_DB", self.env["WIDEMIND_BUSINESS_DB"]),
            ("WIDEMIND_RUNTIME_DB", str(Path(self.env["WIDEMIND_WORKSPACE"]) / "runtime.sqlite3")),
            ("WIDEMIND_TOOLS_DB", str(Path(self.env["WIDEMIND_BUSINESS_DB"]).parent / "tools.sqlite3")),
            ("WIDEMIND_CREDENTIALS_DIR", self.env["WIDEMIND_WORKSPACE"]),
            ("WIDEMIND_ECONOMY_DB", "relative/economy.sqlite3"),
        ):
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                load_config({**self.env, key: value})

    def test_invalid_pricing_limits_and_secret_readiness_are_sanitized(self):
        for key, value in (("OPENAI_INPUT_MICRO_USD_PER_MILLION_TOKENS", "0.4"),
                           ("OPENAI_MAX_OUTPUT_TOKENS", "1000000"),
                           ("STRIPE_SUCCESS_URL", "http://untrusted.invalid"),
                           ("WIDEMIND_GMAIL_ADDRESS", "bad\x00@example.invalid")):
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                load_config({**self.env, key: value})
        self.assertNotIn("not-a-real-secret", json.dumps(readiness(self.env)))
        self.assertTrue(all(entry["status"] == "configured_unverified" for entry in readiness(self.env)))

    def test_provider_test_is_explicit_and_recipient_restricted(self):
        test_env = dict(self.env)
        test_env.pop("WIDEMIND_MODE")
        self.assertEqual(load_config(test_env).mode, "test")
        self.assertEqual(load_config(test_env).allowed_recipients, ("controlled@example.invalid",))
        with self.assertRaises(ConfigurationError):
            load_config({**test_env, "WIDEMIND_ALLOWED_RECIPIENTS": "[]"})
        with self.assertRaises(ConfigurationError):
            load_config({**test_env, "WIDEMIND_MODE": "demo"})

    def test_providers_unavailable_never_fall_back_to_fixtures(self):
        with patch("widemind.host.importlib.import_module", side_effect=ImportError("unavailable")):
            with self.assertRaises(ConfigurationError):
                business_factory(self.env)

    def test_explicit_setup_is_paused_and_factory_never_funds_or_resumes(self):
        resources = []
        creates = []
        class Economy:
            def __init__(self, path, **kwargs):
                self.path = path
                self.mode = kwargs["mode"]
                resources.append(self)
            def create_run(self, run_id, **kwargs):
                creates.append((run_id, kwargs))
                return {"run_id": run_id, "opening_micro_usd": kwargs["opening_micro_usd"]}
            def balance(self, **kwargs):
                return {"spendable_micro_usd": 5000000}
            def close(self):
                self.closed = True
        class Provider:
            def __init__(self, *args, **kwargs):
                resources.append(self)
            def close(self):
                self.closed = True
        economy_module = SimpleNamespace(EconomyService=Economy, StripeClient=Provider)
        tools_module = SimpleNamespace(GmailConfig=lambda **kwargs: SimpleNamespace(**kwargs), GmailClient=Provider,
            ResearchConfig=lambda **kwargs: SimpleNamespace(**kwargs), OpenAIResearch=Provider, LiveTools=Provider)
        modules = {"widemind.economy": economy_module, "widemind.tools": tools_module}
        with patch.dict("sys.modules", modules):
            result = initialize_live(environ=self.env, evidence_reference="owner authorizes opening credit")
            self.assertEqual(result["runtime_state"], "paused")
            self.assertEqual(result["funding_kind"], "owner_opening_grant")
            self.assertEqual(len(creates), 1)
            business = business_factory(self.env)
            self.assertEqual(business.mode, "live")
            self.assertEqual(business.runtime.state("live-run"), "paused")
            self.assertEqual(len(creates), 1)  # Per-request construction funds nothing.
            business.store.close()
            self.assertTrue(all(getattr(resource, "closed", False) for resource in resources if not isinstance(resource, RuntimeControl)))

    def test_factory_rejects_uninitialized_run_and_closes_resources(self):
        resources = []
        class Provider:
            def __init__(self, *args, **kwargs):
                resources.append(self)
            def close(self):
                self.closed = True
        module = SimpleNamespace(EconomyService=Provider, StripeClient=Provider, GmailConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            GmailClient=Provider, ResearchConfig=lambda **kwargs: SimpleNamespace(**kwargs), OpenAIResearch=Provider, LiveTools=Provider)
        with patch.dict("sys.modules", {"widemind.economy": module, "widemind.tools": module}):
            with self.assertRaises(ConfigurationError):
                business_factory(self.env)
        self.assertTrue(all(getattr(resource, "closed", False) for resource in resources if hasattr(resource, "close")))


if __name__ == "__main__":
    unittest.main()
