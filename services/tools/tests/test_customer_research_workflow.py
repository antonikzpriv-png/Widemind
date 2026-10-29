"""Customer research over real fixture HTTP, native gateway and private artifacts.

Financial admission is an explicitly synthetic reservation-family fixture. No
live model/search/account, independent revenue or invoice settlement is claimed.
"""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from widemind.tools.research_synthesis import synthesis_arguments


_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location("offline_customer_research_demo", _ROOT / "scripts/customer-research-demo.py")
demo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(demo)


@pytest.fixture(autouse=True)
def isolated_owner_environment(monkeypatch):
    monkeypatch.delenv("WIDEMIND_RUNTIME_DATABASE", raising=False)
    monkeypatch.setenv("WIDEMIND_TOOLS_ACCOUNT", "dedicated-tools")


def setup(root, origin, *, customer="dental", maximum=100, corrections=1):
    economy = demo.ReservationFamilyFixture()
    owner_config = demo.config(root, origin, corrections=corrections)
    app = demo.application(owner_config, economy)
    action = demo.request(customer, maximum=maximum)
    reservation = economy.admit(action)
    return app, economy, owner_config, {"action": action, "reservation": reservation}


def completed(outcome):
    assert outcome.get("kind") == "receipt", outcome
    assert outcome["receipt"]["status"] == "succeeded", outcome
    return outcome["receipt"]["result"]["data"]


def documents(root):
    return list((root / "workspace").glob("research/*/research-brief.md"))


def test_two_customer_scopes_produce_distinct_real_artifacts_and_durable_child_receipts(tmp_path):
    with demo.provider_fixtures() as (origin, provider):
        summaries = []
        for customer in ("dental", "bicycle"):
            root = tmp_path / customer
            app, economy, config, payload = setup(root, origin, customer=customer)
            result = app.execute(payload)
            data = completed(result)
            content = app.workspace.read_bytes(data["document"]["artifact"]["path"]).decode()
            manifest = json.loads(app.workspace.read_bytes(data["evidence_manifest"]["artifact"]["path"]))
            assert data["quality"]["passed"] and all(data["checks"].values())
            assert content == data["document"]["content"]
            assert content.startswith('> **OFFLINE CONTROLLED FIXTURE:**')
            assert customer in content.lower() and manifest["job_id"] == "customer-" + customer
            assert "Inferences and assumptions" in content and "Evidence and validation limits" in content
            assert manifest["evidence"]["sufficient"] and manifest["synthesis"]["sections"]
            assert len(manifest["evidence"]["sources"]) == 2
            reversed_mappings = json.loads(json.dumps(manifest), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
            original_arguments = synthesis_arguments(manifest["plan"], manifest["evidence"], settings=config["tools"]["research.collect"])
            replay_arguments = synthesis_arguments(reversed_mappings["plan"], reversed_mappings["evidence"], settings=config["tools"]["research.collect"])
            assert original_arguments == replay_arguments, "Journal mapping order must not change the immutable paid request"
            assert all(span["text"] == next(s for s in manifest["evidence"]["sources"] if s["id"] == span["source_id"])["text"][span["start"]:span["end"]] for span in manifest["evidence"]["spans"])
            children = data["child_receipts"]
            paid = [item for item in children if item["tool"] == "ai.generate"]
            assert len(paid) == 1 and paid[0]["usage"]["provisional"] is True
            assert all(item["reservation_id"] and item["receipt_ref"] for item in children)
            assert len({item["action_id"] for item in children}) == len(children)
            assert len(economy.reservation_children(payload["reservation"]["reservationId"])) == len(children)
            assert result["receipt"]["usage"]["actualCharge"]["micros"] == "0", "Parent must not charge its paid children again"
            assert data["document"]["artifact"].get("job_id") == "customer-" + customer
            assert not (root / "control/control.sqlite3").exists(), "Integrated workflow must not create a second wallet"
            with sqlite3.connect(app.journal.path) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert not tables & {"ledger", "runs", "funding"}
            ai_count = len(provider["ai_posts"])
            restarted = demo.application(config, economy)
            assert restarted.execute(payload) == result
            assert len(provider["ai_posts"]) == ai_count
            summaries.append((data["plan"], content))
        assert summaries[0][0]["sections"] != summaries[1][0]["sections"]
        assert "$40" in summaries[0][1] and "$12" in summaries[1][1]
        assert summaries[0][1] != summaries[1][1] and len(provider["ai_posts"]) == 2
        assert "Dental" in json.dumps(provider["ai_posts"][0]["data"]["offset_verified_evidence"])
        assert "Bicycle" in json.dumps(provider["ai_posts"][1]["data"]["offset_verified_evidence"])


@pytest.mark.parametrize("scenario", ["unrelated", "insufficient"])
def test_unrelated_or_insufficient_sources_never_trigger_paid_synthesis(tmp_path, scenario):
    with demo.provider_fixtures(scenario=scenario) as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin)
        outcome = app.execute(payload)
        assert not (outcome.get("kind") == "receipt" and outcome["receipt"]["status"] == "succeeded")
        state = app.journal.load(payload["action"]["actionId"])
        assert state["research_quality_failure"]["code"] == "insufficient_evidence"
        assert provider["ai_posts"] == [] and documents(tmp_path) == []
        assert not [r for r in economy.rows.values() if r["provider"] == "ai.generate"]


def test_long_multiline_customer_context_uses_valid_bounded_search_requests(tmp_path):
    with demo.provider_fixtures() as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin)
        context = "A dental clinic needs appointment scheduling.\n" + "Staff need a calendar rollout.\t" * 40
        payload["action"]["input"]["context"] = context
        data = completed(app.execute(payload))
        assert data["plan"]["context"] == context.strip()
        assert all(len(query) <= 400 and "\n" not in query and "\t" not in query
                   for query in data["plan"]["queries"])
        assert len(provider["ai_posts"]) == 1


@pytest.mark.parametrize("scenario", ["fabricated", "conflict_omitted"])
def test_invalid_citations_or_ignored_conflicts_cannot_be_delivered_as_completed(tmp_path, scenario):
    with demo.provider_fixtures(scenario=scenario) as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin, corrections=0)
        outcome = app.execute(payload)
        assert not (outcome.get("kind") == "receipt" and outcome["receipt"]["status"] == "succeeded")
        state = app.journal.load(payload["action"]["actionId"])
        validation = state["research_quality_failure"]
        assert validation["passed"] is False and validation["errors"]
        assert len(provider["ai_posts"]) == 1 and documents(tmp_path) == []
        paid = [row for row in economy.rows.values() if row["provider"] == "ai.generate"]
        assert len(paid) == 1 and paid[0]["charged_micro_usd"] > 0


def test_conflicting_sources_remain_explicitly_unresolved_in_actual_document(tmp_path):
    with demo.provider_fixtures(scenario="conflict") as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin)
        data = completed(app.execute(payload))
        manifest = json.loads(app.workspace.read_bytes(data["evidence_manifest"]["artifact"]["path"]))
        assert manifest["evidence"]["conflicts"] and data["checks"]["conflicts_addressed"]
        assert "$60" in data["document"]["content"] and "unresolved" in data["document"]["content"]
        assert len(provider["ai_posts"]) == 1


def test_bounded_correction_has_two_distinct_paid_child_receipts(tmp_path):
    with demo.provider_fixtures(scenario="correction") as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin)
        data = completed(app.execute(payload))
        paid = [row for row in data["child_receipts"] if row["tool"] == "ai.generate"]
        assert len(provider["ai_posts"]) == len(paid) == 2
        assert len({row["reservation_id"] for row in paid}) == 2
        assert len({row["action_id"] for row in paid}) == 2
        assert all(row["usage"]["actualCharge"]["micros"] == "1" for row in paid)
        assert provider["ai_posts"][1]["data"]["revision_errors"]
        assert provider["ai_posts"][1]["data"]["untrusted_previous_draft"]
        assert sum(row["charged_micro_usd"] for row in economy.rows.values()) == 2


@pytest.mark.parametrize("scenario,maximum,expected_ai", [("valid", 21, 0), ("budget_correction", 43, 1)])
def test_total_parent_budget_bounds_all_paid_stages(tmp_path, scenario, maximum, expected_ai):
    with demo.provider_fixtures(scenario=scenario) as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin, maximum=maximum)
        outcome = app.execute(payload)
        assert not (outcome.get("kind") == "receipt" and outcome["receipt"]["status"] == "succeeded")
        assert len(provider["ai_posts"]) == expected_ai and documents(tmp_path) == []
        children = economy.reservation_children(payload["reservation"]["reservationId"])
        assert sum(row["amount_micro_usd"] for row in children) <= maximum
        assert all(row["remaining_micro_usd"] >= 0 for row in economy.rows.values())


def test_interruption_after_saved_generation_resumes_without_another_ai_post(tmp_path, monkeypatch):
    with demo.provider_fixtures() as (origin, provider):
        app, economy, config, payload = setup(tmp_path, origin)
        parent_id = payload["action"]["actionId"]
        update = app.journal.update
        stopped = []

        def save_then_stop(action_id, patch):
            result = update(action_id, patch)
            if "ai_usage" in patch and not stopped:
                stopped.append(True)
                app.journal.save(parent_id, cancelled=1)
            return result

        monkeypatch.setattr(app.journal, "update", save_then_stop)
        first = app.execute(payload)
        assert stopped and first["kind"] == "unknown"
        assert len(provider["ai_posts"]) == 1 and documents(tmp_path) == []
        app.journal.save(parent_id, cancelled=0)  # Explicit controlled operator resume.
        restarted = demo.application(config, economy)
        restarted.reconcile(payload)
        assert len(provider["ai_posts"]) == 1, "Reconciliation is observational"
        result = completed(restarted.execute(payload))
        assert result["quality"]["passed"] and documents(tmp_path)
        assert len(provider["ai_posts"]) == 1


def test_offline_demo_writes_two_customer_documents_with_explicit_financial_label(tmp_path):
    path, summary = demo.run_demo(tmp_path / "demo")
    assert path.is_file() and len(summary["customers"]) == 2
    assert "UNVERIFIED" in summary["financial_verification"]
    assert summary["mode"] == "OFFLINE CONTROLLED FIXTURES" and summary["ai_http_posts"] == 2
    assert all(Path(item["document_path"]).is_file() and Path(item["manifest_path"]).is_file() for item in summary["customers"])
