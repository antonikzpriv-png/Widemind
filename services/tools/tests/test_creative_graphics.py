"""Actual local graphics, immutable recovery, and negative visual checks."""
import base64
from copy import deepcopy
import hashlib
import io
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from widemind.tools.contracts import ToolContext, ToolError, Unavailable, Uncertain
from widemind.tools.creative_graphics import CreativeGraphicsAdapter, graphic_scope
from widemind.tools.graphics import GraphicsAdapter
from widemind.workspace import DedicatedWorkspace


def arguments():
    return {
        "scope": {
            "title": "Field Notes workshop poster",
            "description": "A customer-specific workshop poster with a strong typographic hierarchy and geometric accents.",
            "width": 800, "height": 600,
            "required_text": ["FIELD NOTES", "BUILD SMALL. THINK BIG.", "A creative workshop for curious people.",
                              "SATURDAY 14 NOVEMBER", "10:00 - 16:00 | STUDIO 12"],
            "palette": ["#101820", "#ffffff", "#ecac32", "#1cc1ae"],
            "visual_requirements": ["Strong typographic hierarchy", "A mint circular accent", "A warm gold left rule"],
            "max_revisions": 1,
        },
        "design": {
            "background": "#101820",
            "elements": [
                {"type": "rect", "x": 36, "y": 40, "width": 12, "height": 520, "fill": "#ecac32"},
                {"type": "circle", "cx": 700, "cy": 110, "radius": 50, "fill": "#1cc1ae"},
                {"type": "line", "x1": 84, "y1": 330, "x2": 728, "y2": 330, "stroke": "#ecac32", "stroke_width": 2},
                {"type": "text", "x": 84, "y": 104, "text": "FIELD NOTES", "font_size": 18, "fill": "#ffffff"},
                {"type": "text", "x": 84, "y": 210, "text": "BUILD SMALL.", "font_size": 48, "fill": "#ffffff"},
                {"type": "text", "x": 84, "y": 295, "text": "THINK BIG.", "font_size": 48, "fill": "#ffffff"},
                {"type": "text", "x": 84, "y": 390, "text": "A creative workshop for curious people.", "font_size": 22, "fill": "#ffffff"},
                {"type": "text", "x": 84, "y": 470, "text": "SATURDAY 14 NOVEMBER", "font_size": 16, "fill": "#ffffff"},
                {"type": "text", "x": 84, "y": 510, "text": "10:00 - 16:00 | STUDIO 12", "font_size": 16, "fill": "#ffffff"},
            ],
        },
    }


@pytest.fixture
def context(tmp_path):
    records = {}
    with DedicatedWorkspace(tmp_path / "graphics-workspace") as workspace:
        yield ToolContext("customer-graphic-action", "run", "reservation", {"enabled": True}, {}, workspace,
                          SimpleNamespace(get=lambda name: None), None,
                          SimpleNamespace(load=lambda action: records.get(action, {}),
                                          update=lambda action, patch: records.setdefault(action, {}).update(patch)),
                          time.monotonic() + 30, lambda: None, job_id="private-job")


def test_real_customer_specific_svg_png_hashes_and_measured_checks(context):
    image_module = pytest.importorskip("PIL.Image")
    adapter = CreativeGraphicsAdapter()
    quote = adapter.quote(arguments(), context.config)
    assert quote.maximum_microusd == 0 and not quote.paid
    assert quote.limits["model_calls"] == 0
    result = adapter.execute(arguments(), context)
    assert result.data["verified"] is True
    assert all(value is True or type(value) is int and value > 0 for value in result.data["checks"].values())
    assert result.usage.cost_microusd == 0
    assert result.usage.details["allocated_cost_microusd"] is None
    assert result.data["review_notes"] == arguments()["scope"]["visual_requirements"]
    assert len(result.data["files"]) == len(result.artifacts) == 2
    raw = {}
    for item in result.data["files"]:
        content = item["content"].encode() if item["encoding"] == "utf-8" else base64.b64decode(item["content"], validate=True)
        assert hashlib.sha256(content).hexdigest() == item["sha256"]
        assert len(content) == item["size"]
        assert context.workspace.read_bytes(item["path"]) == content
        raw[item["filename"]] = content
    root = ET.fromstring(raw["graphic.svg"])
    assert root.attrib["viewBox"] == "0 0 800 600"
    actual_text = [element.text for element in root if element.tag.endswith("}text")]
    assert "BUILD SMALL." in actual_text and "THINK BIG." in actual_text
    assert all(element.attrib["textLength"] for element in root if element.tag.endswith("}text"))
    with image_module.open(io.BytesIO(raw["graphic.png"])) as image:
        assert image.size == (800, 600)
        assert image.getpixel((700, 110)) == (28, 193, 174, 255)
        assert image.getpixel((40, 50)) == (236, 172, 50, 255)
    assert adapter.reconcile(arguments(), context).result == result


@pytest.mark.parametrize(("kind", "mutate"), [
    ("required_text", lambda args: args["design"]["elements"].pop()),
    ("bounds", lambda args: args["design"]["elements"][4].update(x=750)),
    ("bounds", lambda args: args["design"]["elements"][1].update(cx=790)),
    ("bounds", lambda args: args["design"]["elements"][0].update(x=0, stroke="#ffffff", stroke_width=20)),
    ("text_visibility", lambda args: args["design"]["elements"][4].update(fill="#101820")),
    ("text_visibility", lambda args: args["design"]["elements"][4].update(font_size=3)),
    ("text_occlusion", lambda args: args["design"]["elements"].append({"type": "rect", "x": 80, "y": 150, "width": 400, "height": 70, "fill": "#101820"})),
    ("palette", lambda args: args["design"]["elements"][0].update(fill="#123456")),
    ("palette", lambda args: args["scope"]["palette"].append("#334455")),
    ("font_glyphs", lambda args: args["design"]["elements"][3].update(text="FIELD NOTES \U0001f600")),
])
def test_failed_visual_checks_never_publish_or_claim_success(context, kind, mutate):
    pytest.importorskip("PIL")
    args = arguments()
    mutate(args)
    result = CreativeGraphicsAdapter().execute(args, context)
    assert result.data["verified"] is False
    assert any(error["check"] == kind for error in result.data["errors"])
    assert "files" not in result.data and "content" not in result.data
    assert context.workspace.list_files() == []
    recovered = CreativeGraphicsAdapter().reconcile(args, context)
    assert recovered.outcome == "completed" and recovered.result == result


@pytest.mark.parametrize("patch", [
    {"width": True}, {"height": 127}, {"width": 2049}, {"required_text": []},
    {"palette": ["none"]}, {"palette": ["#ffffff00"]}, {"visual_requirements": []},
    {"max_revisions": 3}, {"max_revisions": True}, {"title": "bad\nline"},
    {"title": "bad\ud800text"},
    {"font_path": "/operator/secret"},
])
def test_frozen_scope_is_typed_bounded_and_not_an_execution_configuration(patch):
    with pytest.raises(ToolError):
        graphic_scope({**arguments()["scope"], **patch})


def test_untrusted_svg_markup_and_multiline_text_rejected_before_effects(context):
    pytest.importorskip("PIL")
    args = arguments()
    args["design"]["elements"][0] = {"type": "script", "text": "alert(1)"}
    with pytest.raises(ToolError):
        CreativeGraphicsAdapter().quote(args, context.config)
    args = arguments()
    args["design"]["elements"][3]["text"] = "FIELD\nNOTES"
    with pytest.raises(ToolError):
        CreativeGraphicsAdapter().execute(args, context)
    assert context.workspace.list_files() == []


def test_customer_brief_may_contain_paragraphs_but_measured_text_work_is_bounded(context):
    pytest.importorskip("PIL")
    args = arguments()
    args["scope"]["description"] += "\nA second brief paragraph."
    assert "\n" in graphic_scope(args["scope"])["description"]
    args["design"]["elements"] = [deepcopy(args["design"]["elements"][3]) for _ in range(41)]
    with pytest.raises(ToolError, match="forty"):
        CreativeGraphicsAdapter().quote(args, context.config)
    assert context.workspace.list_files() == []


def test_missing_graphics_dependency_disables_enhanced_offering(monkeypatch):
    monkeypatch.setattr(GraphicsAdapter, "_pillow_available", staticmethod(lambda: False))
    adapter = CreativeGraphicsAdapter()
    assert adapter.availability({"enabled": True}, None)["available"] is False
    with pytest.raises(Unavailable):
        adapter.quote(arguments(), {"enabled": True})


def test_cancel_and_result_caps_prevent_mutation(context):
    pytest.importorskip("PIL")
    adapter = CreativeGraphicsAdapter()
    context.cancelled = lambda: True
    with pytest.raises(ToolError, match="cancelled"):
        adapter.execute(arguments(), context)
    context.cancelled = lambda: False
    context.config = {"enabled": True, "max_output_bytes": 50}
    with pytest.raises(ToolError, match="result byte cap") as caught:
        adapter.execute(arguments(), context)
    assert caught.value.usage.cost_microusd == 0
    assert context.workspace.list_files() == []


def test_aggregate_original_byte_cap_is_enforced_before_any_file_write(context):
    pytest.importorskip("PIL")
    context.config = {"enabled": True, "max_artifact_bytes": 1000}
    with pytest.raises(ToolError, match="aggregate artifact cap") as caught:
        CreativeGraphicsAdapter().execute(arguments(), context)
    assert caught.value.usage.cost_microusd == 0
    assert context.workspace.list_files() == []


def test_completed_outputs_survive_restart_and_never_render_or_write_again(context, monkeypatch):
    pytest.importorskip("PIL")
    adapter = CreativeGraphicsAdapter()
    result = adapter.execute(arguments(), context)
    monkeypatch.setattr(GraphicsAdapter, "_png", staticmethod(lambda *_args: (_ for _ in ()).throw(AssertionError("Must not render"))))
    monkeypatch.setattr(context.workspace, "write_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Must not write")))
    assert adapter.reconcile(arguments(), context).result == result
    assert adapter.execute(arguments(), context) == result
    changed = deepcopy(arguments())
    changed["scope"]["description"] += " A new scope."
    with pytest.raises(ToolError, match="changed"):
        adapter.execute(changed, context)


@pytest.mark.parametrize("crash_after", [1, 2])
def test_interrupted_local_effects_reconcile_hashes_without_repeating_mutation(context, monkeypatch, crash_after):
    pytest.importorskip("PIL")
    adapter = CreativeGraphicsAdapter()
    original = context.workspace.write_bytes
    writes = []
    def interrupted(path, content, **kwargs):
        value = original(path, content, **kwargs)
        writes.append(path)
        if len(writes) == crash_after:
            raise ToolError("cancelled", "Synthetic interruption after durable file write")
        return value
    monkeypatch.setattr(context.workspace, "write_bytes", interrupted)
    with pytest.raises(ToolError):
        adapter.execute(arguments(), context)
    monkeypatch.setattr(context.workspace, "write_bytes", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Reconciliation is read-only")))
    recovered = adapter.reconcile(arguments(), context)
    if crash_after == 2:
        assert recovered.outcome == "completed"
        assert recovered.result.data["verified"] is True
        assert adapter.execute(arguments(), context) == recovered.result
    else:
        assert recovered.outcome == "uncertain"
        with pytest.raises(Uncertain):
            adapter.execute(arguments(), context)
    assert len(writes) == crash_after


def test_tampered_or_missing_output_cannot_reconcile_as_success(context):
    pytest.importorskip("PIL")
    adapter = CreativeGraphicsAdapter()
    result = adapter.execute(arguments(), context)
    first = result.artifacts[0]
    context.workspace.write_bytes(first["path"], b"conflicting bytes", overwrite=True)
    assert adapter.reconcile(arguments(), context).outcome == "uncertain"
    assert adapter.reconcile({**arguments(), "scope": {**arguments()["scope"], "max_revisions": 2}}, context).outcome == "uncertain"
