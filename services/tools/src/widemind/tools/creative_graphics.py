"""Customer-specific, bounded graphics from safe primitives with measured checks.

The paid interpretation/design step is a separate authorized ``ai.generate``
action. This adapter never calls a model, accepts SVG markup, or fetches assets.
It reuses the existing local graphics renderers and confined files adapter.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, replace
import hashlib
import io
import json
import math
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Uncertain, Usage
from .files import FilesAdapter, _SUBSIDY, _arguments_hash, _bound
from .graphics import GraphicsAdapter, _color


_STATE = "creative_graphics_v1"
_SVG = "http://www.w3.org/2000/svg"


def _text(value: Any, name: str, maximum: int, *, single_line: bool = True) -> str:
    try:
        valid = (isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= maximum
                 and not any((ord(char) < 32 and (single_line or char not in "\n\r\t")) or ord(char) == 127 for char in value))
    except UnicodeError:
        valid = False
    if not valid:
        raise ToolError("invalid_arguments", f"{name} must be nonempty bounded text without unsupported control characters.")
    return value.strip()


def _string_list(value: Any, name: str, maximum: int, item_bytes: int, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ToolError("invalid_arguments", f"{name} must be a bounded list.")
    result = [_text(item, name, item_bytes) for item in value]
    if len(set(result)) != len(result):
        raise ToolError("invalid_arguments", f"{name} must not contain duplicates.")
    return result


def graphic_scope(value: Any) -> dict:
    """Validate the version-one frozen brief; unknown fields are not authority."""
    fields = {"title", "description", "width", "height", "required_text", "palette", "visual_requirements", "max_revisions"}
    if not isinstance(value, Mapping) or set(value) - fields:
        raise ToolError("invalid_arguments", "Supply only the documented frozen graphic scope fields.")
    title = _text(value.get("title"), "title", 300)
    description = _text(value.get("description"), "description", 4000, single_line=False)
    width, height = value.get("width"), value.get("height")
    if any(type(n) is not int or not 128 <= n <= 2048 for n in (width, height)):
        raise ToolError("invalid_arguments", "Graphic width and height must be integers from 128 to 2048.")
    palette = _string_list(value.get("palette"), "palette", 8, 9, 1)
    for color in palette:
        _color(color, "palette color")
        if color == "none" or len(color) not in {4, 7}:
            raise ToolError("invalid_arguments", "The initial graphic offering requires an opaque hex-color palette.")
    revisions = value.get("max_revisions", 1)
    if type(revisions) is not int or not 0 <= revisions <= 2:
        raise ToolError("invalid_arguments", "max_revisions must be an integer from zero to two.")
    return {"title": title, "description": description, "width": width, "height": height,
            "required_text": _string_list(value.get("required_text"), "required_text", 12, 500, 1),
            "palette": palette, "visual_requirements": _string_list(value.get("visual_requirements", []), "visual_requirements", 12, 500, 1),
            "max_revisions": revisions}


class _ChildState:
    """Keep composed FilesAdapter mutation receipts separate from the workflow."""
    def __init__(self, parent, key):
        self.parent, self.key = parent, key

    def load(self, action_id):
        return dict(self.parent.load(action_id).get(self.key, {}))

    def update(self, action_id, patch):
        state = self.load(action_id)
        state.update(patch)
        self.parent.update(action_id, {self.key: state})

    def dispatched(self, action_id, intent):
        self.update(action_id, {"external_intent": dict(intent)})


def _rgba(value: str):
    from PIL import ImageColor
    return None if value == "none" else ImageColor.getcolor(value, "RGBA")


def _overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def _bounds(item):
    """Measured PNG ink and conservative SVG bounds; strokes count as content."""
    from PIL import ImageFont
    stroke = item["stroke_width"] / 2 if item["stroke"] != "none" else 0
    kind = item["type"]
    if kind == "rect":
        x1, y1, x2, y2 = item["x"], item["y"], item["x"] + item["width"], item["y"] + item["height"]
    elif kind == "circle":
        x1, y1, x2, y2 = item["cx"] - item["radius"], item["cy"] - item["radius"], item["cx"] + item["radius"], item["cy"] + item["radius"]
    elif kind == "line":
        x1, y1, x2, y2 = min(item["x1"], item["x2"]), min(item["y1"], item["y2"]), max(item["x1"], item["x2"]), max(item["y1"], item["y2"])
    else:
        font = ImageFont.load_default(size=max(1, round(item["font_size"])))
        anchor = {"start": "ls", "middle": "ms", "end": "rs"}[item["anchor"]]
        ink = font.getbbox(item["text"], anchor=anchor, stroke_width=math.ceil(stroke * 2))
        advance = float(font.getlength(item["text"]))
        left = item["x"] - (advance / 2 if item["anchor"] == "middle" else advance if item["anchor"] == "end" else 0)
        # SVG text is constrained to this measured width with textLength. Its
        # browser-selected monospace font still gets conservative vertical room.
        x1, x2 = min(item["x"] + ink[0], left), max(item["x"] + ink[2], left + advance)
        y1 = min(item["y"] + ink[1], item["y"] - item["font_size"] * 1.3)
        y2 = max(item["y"] + ink[3], item["y"] + item["font_size"] * .4)
    return (x1 - stroke, y1 - stroke, x2 + stroke, y2 + stroke)


class CreativeGraphicsAdapter:
    spec = ToolSpec("creative.graphics", "Render and check a customer-specific SVG and PNG from a frozen brief and safe primitive layout.", "local")

    def availability(self, config, secrets):
        if config.get("enabled") is not True:
            return {"available": False, "reason": "Enable creative.graphics in owner configuration."}
        if not GraphicsAdapter._pillow_available():
            return {"available": False, "reason": "Install the existing tools graphics extra (Pillow) for measured text and real PNG rendering."}
        return {"available": True, "provider": "local", "formats": ["svg", "png"], "cost_disclosure": dict(_SUBSIDY)}

    def _validate(self, arguments, config):
        if not self.availability(config, None)["available"]:
            raise Unavailable(self.availability(config, None)["reason"])
        if not isinstance(arguments, Mapping) or set(arguments) != {"scope", "design"}:
            raise ToolError("invalid_arguments", "creative.graphics accepts scope and design only.")
        scope = graphic_scope(arguments["scope"])
        design = arguments["design"]
        if not isinstance(design, Mapping) or set(design) != {"background", "elements"}:
            raise ToolError("invalid_arguments", "A graphic design contains background and safe primitive elements only.")
        drawing = {"path": "graphic.png", "format": "png", "width": scope["width"], "height": scope["height"],
                   "background": design["background"], "elements": design["elements"]}
        values = GraphicsAdapter()._validate(drawing, {**config, "enabled": True})
        values["max_artifact_bytes"] = _bound(config, "max_artifact_bytes", 600000, 600000)
        if sum(item["type"] == "text" for item in values["elements"]) > 40:
            raise ToolError("input_limit", "The initial graphic workflow allows at most forty measured text lines.")
        # SVG and PNG have identical one-line semantics. Newlines must be laid
        # out as separate primitives rather than silently disappearing in SVG.
        if any(item["type"] == "text" and (not item["text"].strip() or any(ord(c) < 32 or ord(c) == 127 for c in item["text"]))
               for item in values["elements"]):
            raise ToolError("invalid_arguments", "Lay out nonempty text as separate single-line primitives.")
        return scope, values

    def quote(self, arguments, config):
        scope, values = self._validate(arguments, config)
        return Quote(limits={"width": scope["width"], "height": scope["height"], "elements": len(values["elements"]),
                             "formats": ["svg", "png"], "max_output_bytes": values["max_output_bytes"],
                             "max_artifact_bytes": values["max_artifact_bytes"],
                             "model_calls": 0, "cost_disclosure": dict(_SUBSIDY)})

    def _checks(self, scope, values, ctx):
        from PIL import ImageFont
        errors, boxes, texts = [], [], []
        palette = {_rgba(color) for color in scope["palette"]}
        used = {_rgba(values["background"])}
        if _rgba(values["background"]) not in palette:
            errors.append({"check": "palette", "reason": "The opaque background must use the frozen palette."})
        for index, item in enumerate(values["elements"]):
            ctx.check()
            box = _bounds(item)
            boxes.append(box)
            if box[0] < 0 or box[1] < 0 or box[2] > values["width"] or box[3] > values["height"]:
                errors.append({"check": "bounds", "element": index, "reason": "Primitive, text or stroke clips or overflows the canvas."})
            for color in ([item["stroke"]] if item["type"] == "line" else [item["fill"], item["stroke"]]):
                if color != "none":
                    used.add(_rgba(color))
                    if _rgba(color) not in palette:
                        errors.append({"check": "palette", "element": index, "reason": "Primitive uses a color outside the frozen palette."})
            if item["type"] != "text":
                continue
            texts.append((index, item, box))
            if item["fill"] == "none" or _rgba(item["fill"])[3] != 255 or item["font_size"] < 10:
                errors.append({"check": "text_visibility", "element": index, "reason": "Text must be opaque and at least 10 pixels high."})
            font = ImageFont.load_default(size=max(1, round(item["font_size"])))
            replacement = font.getmask("\ufffd")
            signature = (replacement.size, bytes(replacement))
            for char in set(item["text"]):
                ctx.check()
                if ord(char) < 128:
                    continue
                mask = font.getmask(char)
                if (mask.size, bytes(mask)) == signature:
                    errors.append({"check": "font_glyphs", "element": index, "reason": "The configured local renderer cannot show a requested glyph; choose a supported font/rendering capability."})
                    break
        for index, item, box in texts:
            # Conservative occlusion checks protect required copy: a later
            # primitive cannot paint across it, even if a bounding rectangle
            # overestimates the exact circle/line coverage.
            for later_index in range(index + 1, len(values["elements"])):
                later = values["elements"][later_index]
                visible = (later["type"] == "line" and later["stroke"] != "none" and later["stroke_width"] > 0
                           or later["type"] != "line" and (later["fill"] != "none" or later["stroke"] != "none"))
                if visible and _overlap(box, boxes[later_index]):
                    errors.append({"check": "text_occlusion", "element": index, "reason": "A later primitive overlaps text; move it behind the text or change the layout."})
                    break
        # Check literal content in actual reading order, not assertions made by
        # the model. Wrapping across separate lines preserves normal spaces.
        visible_text = " ".join(item["text"] for _, item, _ in sorted(texts, key=lambda row: (row[1]["y"], row[1]["x"])))
        content = " ".join(visible_text.split())
        for required in scope["required_text"]:
            if " ".join(required.split()) not in content:
                errors.append({"check": "required_text", "reason": "The drawing omits frozen required text."})
        if not texts:
            errors.append({"check": "required_text", "reason": "The drawing has no visible text."})
        if not palette <= used:
            errors.append({"check": "palette", "reason": "The layout omits a frozen palette color."})
        return errors, texts

    @staticmethod
    def _svg(values, ctx):
        from PIL import ImageFont
        root = ET.fromstring(GraphicsAdapter._svg(values, ctx))
        for element in root:
            if element.tag != f"{{{_SVG}}}text":
                continue
            font = ImageFont.load_default(size=max(1, round(float(element.attrib["font-size"]))))
            # Constrain SVG advance to the actual font measurement used by PNG.
            # Markup remains exclusively generated from validated primitives.
            element.set("font-family", "monospace")
            element.set("textLength", format(float(font.getlength(element.text or "")), ".4f"))
            element.set("lengthAdjust", "spacingAndGlyphs")
        ET.register_namespace("", _SVG)
        return ET.tostring(root, encoding="utf-8", xml_declaration=False) + b"\n"

    @staticmethod
    def _contrast(values, texts, ctx):
        """Measure required ink against the actual rasterized background."""
        from PIL import Image, ImageDraw, ImageFont
        errors = []
        for index, item, _ in texts:
            ctx.check()
            # Render precisely the background existing at this text layer.
            background = GraphicsAdapter._png({**values, "elements": values["elements"][:index]}, ctx)
            with Image.open(io.BytesIO(background)) as under:
                under.load()
                mask = Image.new("L", under.size)
                draw = ImageDraw.Draw(mask)
                font = ImageFont.load_default(size=max(1, round(item["font_size"])))
                draw.text((item["x"], item["y"]), item["text"], fill=255, font=font,
                          anchor={"start": "ls", "middle": "ms", "end": "rs"}[item["anchor"]])
                box = mask.getbbox()
                if box is None:
                    errors.append({"check": "text_visibility", "element": index, "reason": "Text produced no raster ink."})
                    mask.close()
                    continue
                foreground = _rgba(item["fill"])
                total = distinct = 0
                # Sample all strong ink at a bounded stride; complete glyph masks
                # have already been bounded by GraphicsAdapter's quote limits.
                pixels = mask.load()
                step = max(1, int(math.sqrt(max(1, (box[2]-box[0])*(box[3]-box[1])) / 20000)))
                for y in range(box[1], box[3], step):
                    ctx.check()
                    for x in range(box[0], box[2], step):
                        if pixels[x, y] < 180:
                            continue
                        total += 1
                        rgb = under.getpixel((x, y))
                        def luminance(color):
                            linear = [channel / 255 / 12.92 if channel / 255 <= .04045
                                      else ((channel / 255 + .055) / 1.055) ** 2.4 for channel in color[:3]]
                            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
                        a, b = luminance(foreground), luminance(rgb)
                        ratio = (max(a, b) + .05) / (min(a, b) + .05)
                        if foreground and ratio >= (3 if item["font_size"] >= 24 else 4.5):
                            distinct += 1
                mask.close()
                if not total or distinct * 10 < total * 9:
                    errors.append({"check": "text_visibility", "element": index, "reason": "Text fails measured background contrast (3:1 large text, 4.5:1 smaller text)."})
        return errors

    def execute(self, arguments, ctx: ToolContext):
        ctx.check()
        scope, values = self._validate(arguments, ctx.config)
        digest = _arguments_hash(arguments)
        saved = ctx.load_state().get(_STATE)
        if saved:
            if saved.get("arguments_sha256") != digest:
                raise ToolError("intent_mismatch", "The frozen graphic scope or design changed for an existing action ID.")
            recovered = self.reconcile(arguments, ctx)
            if recovered.outcome == "completed":
                return recovered.result
            raise Uncertain("Graphic output is incomplete or conflicting; reconcile its original immutable artifacts before retrying.")
        errors, texts = self._checks(scope, values, ctx)
        if not errors:
            errors.extend(self._contrast(values, texts, ctx))
        checks = {"required_text": not any(e["check"] == "required_text" for e in errors),
                  "no_clipping_or_overflow": not any(e["check"] == "bounds" for e in errors),
                  "palette": not any(e["check"] == "palette" for e in errors),
                  "visible_text": not any(e["check"] in {"text_visibility", "text_occlusion", "font_glyphs"} for e in errors)}
        if errors:
            data = {"verified": False, "checks": checks, "errors": errors[:30], "review_notes": scope["visual_requirements"]}
            ctx.save_state({_STATE: {"arguments_sha256": digest, "phase": "checked_failure", "data": data}})
            return ToolResult(data, Usage(0, details=dict(_SUBSIDY)))
        svg = self._svg(values, ctx)
        png = GraphicsAdapter._png(values, ctx)
        from PIL import Image
        with Image.open(io.BytesIO(png)) as image:
            image.verify()
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            if image.size != (scope["width"], scope["height"]) or image.format != "PNG":
                raise ToolError("render_failed", "The actual raster output failed frozen dimensions or PNG decoding.", usage=Usage(0))
        root = ET.fromstring(svg)
        if root.tag != f"{{{_SVG}}}svg" or (int(root.attrib["width"]), int(root.attrib["height"])) != (scope["width"], scope["height"]):
            raise ToolError("render_failed", "The actual SVG failed parsing or frozen dimensions.", usage=Usage(0))
        if len(svg) + len(png) > values["max_artifact_bytes"]:
            raise ToolError("output_limit", "The graphic originals exceed their authorized aggregate artifact cap; simplify the layout.", usage=Usage(0))
        base = "deliveries/" + hashlib.sha256(ctx.action_id.encode()).hexdigest() + "/creative-graphics/"
        files, records = [], []
        for filename, media, encoding, content, raw, role in [
            ("graphic.svg", "image/svg+xml", "utf-8", svg.decode("utf-8"), svg, "original"),
            ("graphic.png", "image/png", "base64", base64.b64encode(png).decode("ascii"), png, "preview"),
        ]:
            path = base + filename
            files.append({"filename": filename, "media_type": media, "encoding": encoding, "content": content,
                          "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "role": role, "path": path})
            records.append({"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                            "media_type": media, "job_id": ctx.job_id, "visibility": "private", "trust": "untrusted"})
        checks.update({"svg_parsed": True, "png_rendered": True, "width": scope["width"], "height": scope["height"]})
        data = {"verified": True, "filename": "graphic.svg", "media_type": "image/svg+xml", "content": svg.decode("utf-8"),
                "checks": checks, "files": files, "preview": {"filename": "graphic.png", "media_type": "image/png"},
                "design": {"background": values["background"], "elements": values["elements"]},
                "review_notes": scope["visual_requirements"], "artifact": records[0]}
        usage = Usage(0, units={"pixels": scope["width"] * scope["height"], "elements": len(values["elements"]),
                               "bytes_written": sum(item["size"] for item in files)}, details=dict(_SUBSIDY))
        if len(json.dumps({"data": data, "artifacts": records}, ensure_ascii=True).encode()) > values["max_output_bytes"]:
            raise ToolError("output_limit", "The graphic bundle exceeds its authorized result byte cap; reduce dimensions or layout complexity.", usage=Usage(0))
        # Save exact bytes/hash intent before mutation. If interrupted after the
        # final write, reconciliation can finish observationally without a rewrite.
        intent = {"arguments_sha256": digest, "phase": "writing", "data": data, "artifacts": records, "usage": asdict(usage)}
        ctx.save_state({_STATE: intent, "local_intent": {"arguments_sha256": digest, "job_id": ctx.job_id}})
        for item in files:
            ctx.check()
            child = replace(ctx, state=_ChildState(ctx.state, _STATE + ":" + item["filename"]))
            FilesAdapter().execute({"operation": "create", "path": item["path"],
                                   "text" if item["encoding"] == "utf-8" else "content_base64": item["content"]}, child)
        ctx.save_state({_STATE: {**intent, "phase": "completed"}})
        return ToolResult(data, usage, artifacts=records)

    def reconcile(self, arguments, ctx: ToolContext):
        ctx.check()
        saved = ctx.load_state().get(_STATE)
        if not isinstance(saved, dict) or saved.get("arguments_sha256") != _arguments_hash(arguments):
            return Reconciliation("uncertain", detail="No matching frozen graphic intent exists.")
        if saved.get("phase") == "checked_failure":
            return Reconciliation("completed", ToolResult(saved["data"], Usage(0, details=dict(_SUBSIDY))), "Recovered failed visual checks; no artifact was published.")
        try:
            for item in saved["artifacts"]:
                ctx.check()
                raw = ctx.workspace.read_bytes(item["path"], max_bytes=item["size"])
                if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    return Reconciliation("uncertain", detail="A graphic artifact conflicts with its immutable recorded digest.")
        except (ToolError, KeyError, TypeError):
            return Reconciliation("uncertain", detail="A graphic artifact is missing or unreadable; no write or paid generation was repeated.")
        usage = Usage(**saved["usage"])
        return Reconciliation("completed", ToolResult(saved["data"], usage, artifacts=saved["artifacts"]),
                              "Both original graphic outputs match their recorded hashes; no renderer or writer was invoked.")
