"""Deterministic source-quality checks, with synthetic retrieved pages only."""
from copy import deepcopy
import hashlib
import json

import pytest

from widemind.tools.contracts import ToolError
from widemind.tools.research_evidence import evaluate_sources, make_plan


AS_OF = "2026-10-29"


def request(**changes):
    return {"topic": "Dublin primary school solar canopies",
            "sections": ["Installation costs", "Grant eligibility"],
            "min_sources": 2, "as_of": AS_OF, **changes}


def source(url, text, **changes):
    return {"url": url, "title": "Dublin school solar canopy research",
            "text": text, "retrieved_at": "2026-10-29T12:00:00Z",
            "references": [], **changes}


def scoped_sources():
    return [
        source("https://council.example/costs", "Dublin primary school solar canopy installation costs include foundations, grid connection and maintenance. Buyers should request site-specific installation costs before choosing a solar canopy.", published_date="2026-10-01"),
        source("https://agency.example/grants", "Grant eligibility for Dublin primary school solar canopies depends on the school applicant and approved project. School boards must confirm grant eligibility with the Irish programme administrator.", published_date="2026-10-10"),
    ]


def test_customer_specific_frozen_plan_is_deterministic_and_preserves_scope():
    arguments = request(context="A rural Kilkenny school board needs a euro-denominated decision brief.",
                        requirements=["Explain grid connection installation costs", "State grant eligibility for school applicants"])
    original = deepcopy(arguments)
    first = make_plan(arguments, {})
    assert first == make_plan(arguments, {})
    assert arguments == original
    assert first["topic"] == arguments["topic"]
    assert first["context"] == arguments["context"]
    assert first["as_of"] == AS_OF
    assert first["min_sources"] == 2
    assert [section["id"] for section in first["sections"]] == ["q1", "q2"]
    assert [section["heading"] for section in first["sections"]] == arguments["sections"]
    assert all(section["question"].strip() for section in first["sections"])
    assert [item["id"] for item in first["requirements"]] == ["r1", "r2"]
    assert [item["text"] for item in first["requirements"]] == arguments["requirements"]
    assert "q1" in first["requirements"][0]["section_ids"]
    assert "q2" in first["requirements"][1]["section_ids"]
    assert first["queries"] and all(isinstance(query, str) for query in first["queries"])
    assert "Kilkenny" in json.dumps(first["queries"])
    json.dumps(first, allow_nan=False)


@pytest.mark.parametrize("changes", [
    {"topic": ""}, {"topic": 5}, {"sections": []}, {"sections": ["a"] * 9},
    {"sections": ["x" * 101]}, {"min_sources": True}, {"min_sources": 0},
    {"min_sources": 13}, {"context": []}, {"requirements": "not a list"},
    {"requirements": [None]}, {"as_of": "2026-02-30"},
    {"as_of": "29 October 2026"}, {"max_age_days": True}, {"max_age_days": -1},
])
def test_plan_rejects_invalid_or_unbounded_arguments(changes):
    with pytest.raises(ToolError):
        make_plan(request(**changes), {})


def test_relevant_independent_sources_cover_customer_questions_with_actual_offsets():
    plan = make_plan(request(), {})
    fetched = scoped_sources()
    original = deepcopy(fetched)
    result = evaluate_sources(plan, fetched, {})
    assert fetched == original
    assert result == evaluate_sources(plan, fetched, {})
    assert result["sufficient"] is True
    assert [item["id"] for item in result["sources"]] == ["S1", "S2"]
    assert set(result["coverage"]) == {"q1", "q2"}
    assert all(result["coverage"].values())
    sources = {item["id"]: item for item in result["sources"]}
    spans = {item["id"]: item for item in result["spans"]}
    for span in spans.values():
        retrieved = sources[span["source_id"]]["text"]
        assert 0 <= span["start"] < span["end"] <= len(retrieved)
        assert retrieved[span["start"]:span["end"]] == span["text"]
        assert len(span["text"]) <= 700
        assert span["question_ids"]
        assert set(span["question_ids"]) <= {"q1", "q2"}
    for question, evidence_ids in result["coverage"].items():
        assert all(question in spans[evidence_id]["question_ids"] for evidence_id in evidence_ids)
    assert all(item["relevance"] for item in sources.values())
    json.dumps(result, allow_nan=False)


def test_unrelated_pages_cannot_satisfy_minimum_or_question_coverage():
    plan = make_plan(request(), {})
    fetched = [source("https://travel.example/one", "Paris bakery opening hours, pastry recipes and croissant recommendations."),
               source("https://sport.example/two", "Cricket scores, tournament results and player statistics from Australia.")]
    result = evaluate_sources(plan, fetched, {})
    assert result["sufficient"] is False
    assert len(result["sources"]) == 0
    assert len(result["rejected"]) == 2
    assert all(item["reason"] for item in result["rejected"])
    assert not any(result["coverage"].values())
    assert result["issues"]


def test_topic_overlap_alone_does_not_cover_missing_customer_question():
    plan = make_plan(request(), {})
    fetched = [source("https://one.example/overview", "Dublin primary school solar canopies provide shade and generate electricity. Installation costs include foundations and connection."),
               source("https://two.example/build", "A Dublin primary school solar canopy project requires careful installation planning. Installation costs vary by ground conditions and structural design.")]
    result = evaluate_sources(plan, fetched, {})
    assert result["sufficient"] is False
    assert result["coverage"]["q1"]
    assert result["coverage"]["q2"] == []


def test_customer_grid_connection_requirement_needs_specific_evidence():
    plan = make_plan(request(min_sources=1, sections=["Installation costs"],
                             requirements=["Explain grid connection installation costs"]), {})
    generic = "Dublin primary school solar canopy installation costs include steelwork and foundations. Contractors should provide detailed quotations for the structure."
    missing = evaluate_sources(plan, [source("https://school.example/costs", generic)], {})
    assert missing["coverage"]["q1"]
    assert missing["sufficient"] is False
    assert missing["issues"]
    specific = generic + " Grid connection installation costs require a utility assessment and a connection quotation."
    covered = evaluate_sources(plan, [source("https://school.example/costs", specific)], {})
    assert covered["sufficient"] is True


def test_unknown_dates_are_explicit_and_never_inferred_from_retrieval_or_body():
    plan = make_plan(request(min_sources=1, sections=["Installation costs"]), {})
    fetched = [source("https://unknown.example/costs", "Dublin primary school solar canopy installation costs depend on site conditions. The 2026 school programme is discussed here.")]
    result = evaluate_sources(plan, fetched, {})
    assert result["sources"][0]["published_date"] is None
    assert result["sources"][0]["date_status"] == "unknown"
    assert result["sources"][0]["retrieved_at"] == "2026-10-29T12:00:00Z"
    assert result["issues"]
    freshness = make_plan(request(min_sources=1, sections=["Installation costs"], max_age_days=90), {})
    assert evaluate_sources(freshness, fetched, {})["sufficient"] is False


def test_stale_and_future_dates_are_labeled_and_do_not_prove_current_coverage():
    plan = make_plan(request(min_sources=1, sections=["Installation costs"], max_age_days=90), {})
    fetched = [source("https://old.example/costs", "Dublin primary school solar canopy installation costs include steelwork and foundations.", published_date="2025-01-01"),
               source("https://future.example/costs", "Dublin primary school solar canopy installation costs must include current site surveys and connection estimates.", published_date="2026-11-01")]
    result = evaluate_sources(plan, fetched, {})
    statuses = {item["url"]: item["date_status"] for item in result["sources"]}
    assert statuses["https://old.example/costs"] == "stale"
    assert statuses["https://future.example/costs"] == "future"
    assert result["sufficient"] is False
    assert result["issues"]


def test_future_source_is_ineligible_even_without_freshness_window():
    plan = make_plan(request(min_sources=1, sections=["Installation costs"]), {})
    fetched = [source("https://future.example/costs", "Dublin primary school solar canopy installation costs include structural work and grid connection.", published_date="2026-11-01")]
    result = evaluate_sources(plan, fetched, {})
    assert result["sources"][0]["date_status"] == "future"
    assert result["sources"][0]["published_date"] == "2026-11-01"
    assert result["sufficient"] is False


def test_canonical_url_duplicates_do_not_count_as_independent_sources():
    plan = make_plan(request(min_sources=2, sections=["Installation costs"]), {})
    fetched = [source("https://SCHOOL.example/costs?utm_source=mail#top", "Dublin primary school solar canopy installation costs include structural work and connection."),
               source("https://school.example/costs", "Dublin primary school solar canopy installation costs also include a site survey and maintenance plan.")]
    result = evaluate_sources(plan, fetched, {})
    assert result["sufficient"] is False
    assert len(result["sources"]) == 1
    assert any("duplic" in item["reason"].lower() for item in result["rejected"])


def test_content_duplicates_ignore_different_urls_and_untrusted_claimed_hashes():
    plan = make_plan(request(min_sources=2, sections=["Installation costs"]), {})
    content = "Dublin primary school solar canopy installation costs include structural foundations, electrical connection and annual maintenance."
    fetched = [source("https://original.example/costs", content, content_sha256="a" * 64),
               source("https://mirror.example/costs", content, content_sha256="b" * 64)]
    result = evaluate_sources(plan, fetched, {})
    assert result["sufficient"] is False
    assert len(result["sources"]) == 1
    assert result["sources"][0]["content_sha256"] == hashlib.sha256(content.encode()).hexdigest()
    assert result["rejected"]


def test_near_duplicate_copy_cannot_satisfy_minimum_sources():
    plan = make_plan(request(min_sources=2, sections=["Installation costs"]), {})
    original = "Dublin primary school solar canopy installation costs include structural foundations, electrical connection and annual maintenance. School boards should obtain detailed site-specific quotations before selecting suppliers."
    copied = original + " Summary."
    result = evaluate_sources(plan, [source("https://one.example/costs", original), source("https://two.example/summary", copied)], {})
    assert len(result["sources"]) == 1
    assert result["sufficient"] is False
    assert result["rejected"]


@pytest.mark.parametrize("first,second", [
    ("Dublin primary school solar canopy installation costs are €12000 for this project.",
     "Dublin primary school solar canopy installation costs are €18000 for this project."),
    ("Dublin primary school solar canopy grant eligibility is available to primary school applicants.",
     "Dublin primary school solar canopy grant eligibility is not available to primary school applicants."),
])
def test_potential_conflicts_keep_distinct_facts_and_both_provenances(first, second):
    heading = "Grant eligibility" if "eligibility" in first else "Installation costs"
    plan = make_plan(request(min_sources=2, sections=[heading]), {})
    result = evaluate_sources(plan, [source("https://one.example/report", first), source("https://two.example/report", second)], {})
    assert len(result["sources"]) == 2
    assert result["conflicts"]
    assert result["sufficient"] is False
    spans = {span["id"]: span for span in result["spans"]}
    for conflict in result["conflicts"]:
        assert conflict["id"].startswith("C") and conflict["description"]
        assert len(conflict["evidence_ids"]) >= 2
        assert len({spans[item]["source_id"] for item in conflict["evidence_ids"]}) >= 2


def test_source_and_span_work_is_bounded_without_fabricating_offsets():
    plan = make_plan(request(min_sources=1, sections=["Installation costs"]), {})
    content = " ".join(f"Dublin primary school solar canopy installation costs include project phase {index}." for index in range(100))
    result = evaluate_sources(plan, [source("https://school.example/costs", content)], {"max_source_chars": 1200})
    assert len(result["sources"][0]["text"]) <= 1200
    assert result["sources"][0]["content_sha256"] == hashlib.sha256(result["sources"][0]["text"].encode()).hexdigest()
    assert 1 <= len(result["spans"]) <= 6
    for span in result["spans"]:
        assert len(span["text"]) <= 700
        assert result["sources"][0]["text"][span["start"]:span["end"]] == span["text"]
