import hashlib
import io
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from widemind.tools.contracts import ToolContext, ToolError, Unavailable
from widemind.tools.graphics import GraphicsAdapter
from widemind.workspace import DedicatedWorkspace


@pytest.fixture
def context(tmp_path):
    records = {}
    with DedicatedWorkspace(tmp_path / "deliverables") as workspace:
        yield ToolContext("action-graphics", "run", "reservation", {}, {}, workspace,
                          SimpleNamespace(get=lambda name: None), None,
                          SimpleNamespace(load=lambda action: records.get(action, {}),
                                          update=lambda action, patch: records.setdefault(action, {}).update(patch)),
                          time.monotonic() + 30, lambda: None, job_id="graphics-job")


def scene(path="drawing.svg"):
    return {"path": path, "width": 64, "height": 48, "background": "#ffffff", "elements": [
        {"type": "rect", "x": 2, "y": 2, "width": 20, "height": 20, "fill": "#ff0000"},
        {"type": "circle", "cx": 40, "cy": 15, "radius": 8, "fill": "#00ff00"},
        {"type": "line", "x1": 0, "y1": 30, "x2": 63, "y2": 30, "stroke": "#0000ff", "stroke_width": 2},
        {"type": "text", "x": 2, "y": 46, "text": "Actual bytes", "font_size": 8},
    ]}


def test_actual_svg_primitives_and_metadata(context):
    adapter = GraphicsAdapter()
    quote = adapter.quote(scene(), {})
    assert quote.maximum_microusd == 0 and not quote.paid
    result = adapter.execute(scene(), context)
    content = context.workspace.read_bytes("drawing.svg")
    root = ET.fromstring(content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["width"] == "64"
    assert [element.tag.rsplit("}", 1)[-1] for element in root] == ["rect", "rect", "circle", "line", "text"]
    assert result.artifacts[0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert result.artifacts[0]["media_type"] == "image/svg+xml"
    assert result.artifacts[0]["job_id"] == context.job_id
    assert result.trust == "untrusted" and result.data["raster_rendered"] is False
    assert result.usage.details["allocated_cost_microusd"] is None


def test_svg_text_escapes_markup_and_never_becomes_active_content(context):
    injection = '</text><script>alert("x")</script><image href="https://host/secret"/>&'
    arguments = {"path": "safe.svg", "elements": [{"type": "text", "x": 0, "y": 30, "text": injection}]}
    GraphicsAdapter().execute(arguments, context)
    content = context.workspace.read_bytes("safe.svg")
    root = ET.fromstring(content)
    assert len(root) == 1 and root[0].tag.endswith("}text")
    assert root[0].text == injection
    assert b"<script" not in content and b"<image" not in content and b"<!DOCTYPE" not in content


def test_actual_png_signature_dimensions_and_pixels(context):
    image_module = pytest.importorskip("PIL.Image")
    result = GraphicsAdapter().execute(scene("drawing.png"), context)
    content = context.workspace.read_bytes("drawing.png")
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    with image_module.open(io.BytesIO(content)) as image:
        assert image.size == (64, 48)
        assert image.getpixel((5, 5)) == (255, 0, 0, 255)
        assert image.getpixel((40, 15)) == (0, 255, 0, 255)
        assert image.getpixel((63, 0)) == (255, 255, 255, 255)
    assert result.data["raster_rendered"] is True and result.data["renderer"] == "pillow"


def test_missing_pillow_disables_only_png(context, monkeypatch):
    adapter = GraphicsAdapter()
    monkeypatch.setattr(adapter, "_pillow_available", lambda: False)
    assert adapter.availability({}, None)["formats"] == ["svg"]
    with pytest.raises(Unavailable, match=r"widemind\[graphics\]"):
        adapter.quote(scene("drawing.png"), {})
    result = adapter.execute(scene(), context)
    assert result.data["format"] == "svg"


@pytest.mark.parametrize("patch", [
    {"path": "../escape.svg"}, {"path": "a.svg", "format": "png"}, {"width": True}, {"height": 0},
    {"width": 2048, "height": 2048}, {"width": 4097}, {"background": 'url("https://host/a")'},
    {"background": '#12345'}, {"markup": "<svg/>"}, {"overwrite": 1},
    {"elements": [{"type": "script", "text": "x"}]},
    {"elements": [{"type": "text", "x": 0, "y": 0, "text": "\x00bad"}]},
    {"elements": [{"type": "text", "x": 0, "y": 0, "text": "bad", "font_path": "/etc/passwd"}]},
    {"elements": [{"type": "text", "x": 0, "y": 0, "text": "bad", "anchor": 'start" onload="x'}]},
    {"elements": [{"type": "circle", "cx": float("nan"), "cy": 0, "radius": 1}]},
    {"elements": [{"type": "circle", "cx": 10 ** 1000, "cy": 0, "radius": 1}]},
    {"elements": [{"type": "circle", "cx": 0, "cy": 0, "radius": -1}]},
    {"elements": [{"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": float("inf")}]},
    {"elements": [{"type": "rect", "x": 0, "y": 0, "width": 1, "height": 1, "fill": "red;onload=x"}]},
])
def test_unsafe_or_unbounded_graphics_rejected_at_quote(patch):
    with pytest.raises(ToolError):
        GraphicsAdapter().quote({**scene(), **patch}, {})


def test_count_text_and_output_limits(context):
    adapter = GraphicsAdapter()
    with pytest.raises(ToolError):
        adapter.quote({**scene(), "elements": scene()["elements"] * 126}, {})
    with pytest.raises(ToolError):
        adapter.quote({"path": "a.svg", "elements": [{"type": "text", "x": 0, "y": 0, "text": "x" * 20_001}]}, {})
    context.config = {"max_output_bytes": 20}
    with pytest.raises(ToolError) as error:
        adapter.execute(scene(), context)
    assert error.value.code == "output_limit"
    assert context.workspace.list_files() == []


def test_png_text_mask_allocation_is_bounded_before_render():
    pytest.importorskip("PIL")
    arguments = {"path": "a.png", "elements": [{"type": "text", "x": 0, "y": 0, "text": "x" * 1000, "font_size": 256}]}
    with pytest.raises(ToolError, match="text masks"):
        GraphicsAdapter().quote(arguments, {})


def test_cancelled_drawing_loop_creates_no_file(context):
    checks = 0

    def authorize():
        nonlocal checks
        checks += 1

    context.authorize = authorize
    context.cancelled = lambda: checks >= 3
    with pytest.raises(ToolError) as error:
        GraphicsAdapter().execute(scene(), context)
    assert error.value.code == "cancelled"
    assert context.workspace.list_files() == []


def test_graphics_completed_receipt_and_explicit_overwrite(context):
    adapter = GraphicsAdapter()
    arguments = scene()
    result = adapter.execute(arguments, context)
    assert adapter.reconcile(arguments, context).result == result
    with pytest.raises(ToolError) as error:
        adapter.execute(arguments, context)
    assert error.value.code == "already_exists"
    updated = {**arguments, "background": "#000000", "overwrite": True}
    assert adapter.execute(updated, context).artifacts[0]["sha256"] != result.artifacts[0]["sha256"]
