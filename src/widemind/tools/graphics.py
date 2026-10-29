"""Real local SVG construction and optional Pillow raster rendering.

Inputs describe primitives, never executable markup, URLs, or font paths.
"""
from __future__ import annotations

import html
import importlib.util
import io
import math
import re
from typing import Any, Mapping

from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Usage
from .files import _SUBSIDY, _boolean, _bound, _local_intent, _local_receipt, _reconcile_local, _text
from ..workspace import validate_workspace_path


_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")


def _number(value: Any, name: str, minimum: float, maximum: float) -> int | float:
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ToolError("invalid_arguments", f"{name} must be a finite number between {minimum} and {maximum}.")
    return value


def _color(value: Any, name: str) -> str:
    if not isinstance(value, str) or (value != "none" and not _COLOR.fullmatch(value)):
        raise ToolError("invalid_arguments", f"{name} must be a hex color (#RGB, #RRGGBB, #RRGGBBAA) or none.")
    return value


def _xml_text(value: Any, maximum: int) -> str:
    value = _text(value, "text", maximum=maximum)
    if any(not (char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
                    or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF) for char in value):
        raise ToolError("invalid_arguments", "Text must contain valid XML Unicode characters.")
    return value


class GraphicsAdapter:
    spec = ToolSpec("graphics", "Create actual local SVG or PNG graphics from safe bounded primitives and text.", "local")

    @staticmethod
    def _pillow_available() -> bool:
        try:
            return importlib.util.find_spec("PIL") is not None
        except (ImportError, ValueError):
            return False

    def availability(self, config, secrets) -> dict:
        enabled = config.get("enabled", True) is True
        return {"available": enabled, "provider": "local", "formats": ["svg"] + (["png"] if self._pillow_available() else []),
                "png_setup": "Install widemind[graphics] to enable the optional Pillow PNG renderer.",
                "cost_disclosure": dict(_SUBSIDY)}

    def _validate(self, arguments: Mapping[str, Any], config: Mapping[str, Any]) -> dict:
        if config.get("enabled", True) is not True:
            raise Unavailable("Enable the local graphics adapter in owner configuration.")
        if not isinstance(arguments, Mapping) or set(arguments) - {
            "path", "format", "width", "height", "background", "elements", "overwrite"
        }:
            raise ToolError("invalid_arguments", "Supply documented graphics fields only.")
        path = _text(arguments.get("path"), "path")
        validate_workspace_path(path)
        output_format = arguments.get("format", path.rsplit(".", 1)[-1].lower())
        if output_format not in ("svg", "png") or not path.lower().endswith("." + output_format):
            raise ToolError("invalid_arguments", "Choose svg or png and a matching output filename extension.")
        if output_format == "png" and not self._pillow_available():
            raise Unavailable("PNG rendering requires Pillow. Install widemind[graphics], or request SVG which needs no optional package.")
        max_width = _bound(config, "max_width", 2048, 4096)
        max_height = _bound(config, "max_height", 2048, 4096)
        max_pixels = _bound(config, "max_pixels", 4_000_000, 16_777_216)
        width, height = arguments.get("width", 800), arguments.get("height", 600)
        if type(width) is not int or type(height) is not int or not 1 <= width <= max_width or not 1 <= height <= max_height or width * height > max_pixels:
            raise ToolError("invalid_arguments", "Canvas dimensions must fit the configured dimension and pixel limits.")
        max_elements = _bound(config, "max_elements", 500, 2000)
        max_text = _bound(config, "max_text_characters", 20_000, 100_000)
        elements = arguments.get("elements", [])
        if not isinstance(elements, list) or len(elements) > max_elements:
            raise ToolError("invalid_arguments", "elements must be a bounded list of drawing primitives.")
        validated = []
        text_count = 0
        raster_text_pixels = 0
        max_raster_text_pixels = _bound(config, "max_raster_text_pixels", 16_000_000, 64_000_000)
        coordinate_bound = max(max_width, max_height) * 4
        common = {"type", "fill", "stroke", "stroke_width"}
        schemas = {"rect": {"x", "y", "width", "height"}, "circle": {"cx", "cy", "radius"},
                   "line": {"x1", "y1", "x2", "y2"}, "text": {"x", "y", "text", "font_size", "anchor"}}
        for element in elements:
            if not isinstance(element, Mapping) or not isinstance(element.get("type"), str) or element["type"] not in schemas:
                raise ToolError("invalid_arguments", "Primitive type must be rect, circle, line, or text.")
            kind = element["type"]
            if set(element) - (common | schemas[kind]):
                raise ToolError("invalid_arguments", "Primitive contains unsupported fields; markup, URLs, and fonts are not accepted.")
            item = {"type": kind,
                    "fill": _color(element.get("fill", "#000000"), "fill"),
                    "stroke": _color(element.get("stroke", "#000000" if kind == "line" else "none"), "stroke"),
                    "stroke_width": _number(element.get("stroke_width", 1), "stroke_width", 0, 256)}
            for field in schemas[kind] - {"text", "font_size", "anchor"}:
                item[field] = _number(element.get(field), field, 0 if field in ("width", "height", "radius") else -coordinate_bound, coordinate_bound)
            if kind == "text":
                item["text"] = _xml_text(element.get("text"), max_text)
                text_count += len(item["text"])
                item["font_size"] = _number(element.get("font_size", 24), "font_size", 1, 256)
                item["anchor"] = element.get("anchor", "start")
                if item["anchor"] not in ("start", "middle", "end"):
                    raise ToolError("invalid_arguments", "Text anchor must be start, middle, or end.")
                # Pillow creates glyph masks before clipping to the canvas.
                # Bound that allocation even for long text outside the image.
                raster_text_pixels += len(item["text"]) * (item["font_size"] + 2 * item["stroke_width"]) ** 2 * 4
            validated.append(item)
        if text_count > max_text:
            raise ToolError("input_limit", "Total graphics text exceeds the configured character limit.")
        if output_format == "png" and raster_text_pixels > max_raster_text_pixels:
            raise ToolError("input_limit", "PNG text masks exceed the configured raster text work limit; reduce text, font size, or stroke width.")
        return {"path": path, "format": output_format, "width": width, "height": height,
                "background": _color(arguments.get("background", "none"), "background"),
                "elements": validated, "overwrite": _boolean(arguments.get("overwrite", False), "overwrite"),
                "max_output_bytes": _bound(config, "max_output_bytes", 8_000_000, 8_000_000)}

    def quote(self, arguments, config) -> Quote:
        values = self._validate(arguments, config)
        return Quote(limits={"width": values["width"], "height": values["height"], "pixels": values["width"] * values["height"],
                             "elements": len(values["elements"]), "max_output_bytes": values["max_output_bytes"],
                             "renderer": "local-svg" if values["format"] == "svg" else "pillow",
                             "cost_disclosure": dict(_SUBSIDY)})

    @staticmethod
    def _svg(values: dict, ctx: ToolContext) -> bytes:
        width, height = values["width"], values["height"]
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
        if values["background"] != "none":
            lines.append(f'<rect width="{width}" height="{height}" fill="{values["background"]}"/>')
        for item in values["elements"]:
            ctx.check()
            kind = item["type"]
            common = f'fill="{item["fill"]}" stroke="{item["stroke"]}" stroke-width="{item["stroke_width"]}"'
            if kind == "rect":
                lines.append(f'<rect x="{item["x"]}" y="{item["y"]}" width="{item["width"]}" height="{item["height"]}" {common}/>')
            elif kind == "circle":
                lines.append(f'<circle cx="{item["cx"]}" cy="{item["cy"]}" r="{item["radius"]}" {common}/>')
            elif kind == "line":
                lines.append(f'<line x1="{item["x1"]}" y1="{item["y1"]}" x2="{item["x2"]}" y2="{item["y2"]}" {common}/>')
            else:
                escaped = html.escape(item["text"], quote=True)
                lines.append(f'<text x="{item["x"]}" y="{item["y"]}" font-family="sans-serif" font-size="{item["font_size"]}" text-anchor="{item["anchor"]}" {common}>{escaped}</text>')
        lines.append("</svg>\n")
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def _png(values: dict, ctx: ToolContext) -> bytes:
        try:
            from PIL import Image, ImageColor, ImageDraw, ImageFont
        except ImportError:
            raise Unavailable("Install widemind[graphics] for PNG rendering, or request SVG.") from None

        def color(value):
            return None if value == "none" else ImageColor.getcolor(value, "RGBA")

        image = Image.new("RGBA", (values["width"], values["height"]), color(values["background"]) or (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for item in values["elements"]:
            ctx.check()
            fill, stroke = color(item["fill"]), color(item["stroke"])
            stroke_width = round(item["stroke_width"])
            if stroke_width == 0:
                stroke = None
            kind = item["type"]
            if kind == "rect":
                if fill is not None or stroke is not None:
                    draw.rectangle((item["x"], item["y"], item["x"] + item["width"], item["y"] + item["height"]),
                                   fill=fill, outline=stroke, width=max(1, stroke_width))
            elif kind == "circle":
                cx, cy, radius = item["cx"], item["cy"], item["radius"]
                if fill is not None or stroke is not None:
                    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill, outline=stroke, width=max(1, stroke_width))
            elif kind == "line":
                if stroke is not None:
                    draw.line((item["x1"], item["y1"], item["x2"], item["y2"]), fill=stroke, width=max(1, stroke_width))
            elif fill is not None:
                font = ImageFont.load_default(size=max(1, round(item["font_size"])))
                anchor = {"start": "ls", "middle": "ms", "end": "rs"}[item["anchor"]]
                # A bounded list of lines avoids Pillow's multiline baseline
                # restriction while keeping each line cancellable.
                for offset, line in enumerate(item["text"].splitlines()):
                    ctx.check()
                    draw.text((item["x"], item["y"] + offset * item["font_size"] * 1.2), line,
                              fill=fill, font=font, anchor=anchor, stroke_width=stroke_width if stroke else 0,
                              stroke_fill=stroke)
        ctx.check()
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False)
        image.close()
        return output.getvalue()

    def execute(self, arguments, ctx: ToolContext) -> ToolResult:
        ctx.check()
        values = self._validate(arguments, ctx.config)
        encoded = self._svg(values, ctx) if values["format"] == "svg" else self._png(values, ctx)
        if len(encoded) > values["max_output_bytes"]:
            raise ToolError("output_limit", "Rendered output exceeds the configured byte limit; reduce the canvas or content.")
        ctx.check()
        _local_intent(arguments, ctx)
        artifact = {**ctx.workspace.write_bytes(values["path"], encoded, overwrite=values["overwrite"]),
                    "job_id": ctx.job_id, "visibility": "private"}
        result = ToolResult({"status": "created", "format": values["format"],
                           "renderer": "local-svg" if values["format"] == "svg" else "pillow",
                           "raster_rendered": values["format"] == "png", "width": values["width"], "height": values["height"],
                           "job_id": ctx.job_id, "visibility": "private", "artifact": artifact},
                          Usage(0, units={"pixels": values["width"] * values["height"], "elements": len(values["elements"]),
                                          "bytes_written": len(encoded)}, details=dict(_SUBSIDY)), artifacts=[artifact])
        _local_receipt(arguments, ctx, result)
        ctx.check()
        return result

    def reconcile(self, arguments, ctx: ToolContext) -> Reconciliation:
        self._validate(arguments, ctx.config)
        return _reconcile_local(arguments, ctx)
