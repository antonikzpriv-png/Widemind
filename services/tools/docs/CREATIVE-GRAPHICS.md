# Customer-specific graphics adapter

`creative.graphics` renders a frozen customer scope and a model-produced layout into actual SVG and PNG originals. It is separate from the honestly labeled `graphics.create` template offering. Interpretation/design is performed by the business workflow through a separately authorized `ai.generate` action; this renderer makes no paid model or network calls.

Install the existing graphics extra in the integrated tools environment:

```sh
python -m pip install -e 'services/tools[test,graphics]'
python -m pytest services/tools/tests/test_creative_graphics.py -q
```

Enable `creative.graphics` in trusted owner configuration. Availability fails closed without Pillow or explicit enablement. The business offering also requires its existing model, files and graphics capabilities; enabling a renderer never proves model credentials or operating funds.

```json
{
  "tools": {
    "creative.graphics": {
      "enabled": true,
      "max_width": 2048,
      "max_height": 2048,
      "max_pixels": 4000000,
      "max_elements": 80,
      "max_output_bytes": 1000000
    }
  }
}
```

This fragment augments the existing private owner configuration; it does not replace policy, workspace, accounting or credential settings.

## Inputs and checked behavior

The action accepts only `{scope, design}`. Frozen scope has `title`, `description`, integer `width`/`height` (128–2048), 1–12 `required_text` strings, 1–8 opaque hex `palette` colors, 1–12 `visual_requirements`, and `max_revisions` (0–2). The renderer cannot alter the accepted scope, price, budget or revision limit.

Design contains `background` and an `elements` array using the existing safe `rect`, `circle`, `line` and `text` primitives. Markup, event handlers, external assets, URLs, arbitrary font paths and executable content are rejected by the existing graphics validator. Canvas/pixel, primitive count, raster text allocation, output bytes, deadline and cancellation remain bounded by the existing adapters and tool authorization.

The renderer checks:

- Canvas dimensions and every primitive's bounds, including SVG stroke extents.
- Required text from the actual reading-order layout; separate text lines can preserve a wrapped phrase.
- Real Pillow font measurements, conservative SVG vertical bounds, and no later primitive painting across text.
- Visible text: opaque fill, at least 10-pixel type, supported glyphs, actual raster ink and background contrast (3:1 for type at least 24 pixels, 4.5:1 for smaller type).
- Frozen palette membership and use, safe SVG parsing and actual PNG decoding/dimensions.

SVG `textLength` constrains text advance to the measured PNG layout. The bundled Pillow font currently supports a limited glyph set; unsupported characters explicitly fail `font_glyphs` rather than shipping replacement boxes. Text must use separate single-line primitives, with at most 40 measured text lines. Keep background/decorative elements before the text, allow vertical room from `y − 1.3 × font_size` to `y + 0.4 × font_size`, and separate text boxes. Conservative occlusion rules can reject an overlap that a human designer might accept.

Qualitative requirements such as “playful” or “premium” remain `review_notes`; the adapter does not claim a machine verified subjective taste. The result records the literal brief and primitive design so the customer can judge those properties and request an included revision through the business workflow.

Successful output has `verified:true`, a primary SVG, flat `checks`, full-size PNG preview, and `files` with downloadable content, media types, exact byte sizes and SHA-256 hashes. The original files together must fit 600,000 bytes (or a lower owner limit), and the encoded result must independently fit its authorized output cap. Failed visual checks return `verified:false` with bounded structured errors and no completed files. Known resource failures before any write report real zero local usage; interrupted writes remain uncertain until hash reconciliation. The outer business workflow decides whether a separately authorized repair fits the accepted attempt/budget limits. Local rendering reports its real zero direct tool charge and discloses owner-provided compute/storage as excluded costs; model costs belong to their own economy receipts.

## Private persistence and recovery

Files live under an action-derived confined private directory. Exact intended contents and hashes are journaled before mutation. Existing files are never overwritten. Composed file mutation receipts use separate state namespaces. A completed call returns its recorded output; changing the scope/design under the same action ID conflicts.

Reconciliation reads both original file hashes without invoking a model, renderer, writer or publisher. It recovers an interruption after both durable writes, even before the final workflow receipt. Missing/conflicting files remain uncertain and prevent an automatic replay; completed stages and original file bytes are retained for operator review. The business module adds the versioned private artifact manifest, actual stage costs and revision history; the tools adapter does not invent those facts.

## Synthetic visual example

[Field Notes brief](../examples/creative-graphics/brief.json), [layout](../examples/creative-graphics/design.json), [SVG original](../examples/creative-graphics/graphic.svg), [PNG original/preview](../examples/creative-graphics/graphic.png) and [actual renderer checks](../examples/creative-graphics/checks.json) are deterministic local fixtures. They do not claim a customer, live model generation, payment, revenue or deployment.

The example is an 800 × 600 workshop poster with customer-specific copy, a gold rule, a mint circle, and measured type. Chromium also rendered the generated SVG and inspected actual SVG text bounding boxes; [browser checks](../examples/creative-graphics/browser-checks.json) and [browser preview](../examples/creative-graphics/browser-svg-preview.png) are separate visual evidence. That browser fixture uses only this validated, script-free local SVG; it does not execute arbitrary generated software outside its required isolation.
