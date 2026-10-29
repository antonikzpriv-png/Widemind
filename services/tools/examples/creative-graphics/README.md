# Field Notes graphic example

These are explicitly **synthetic local fixtures** for the enhanced graphics workflow. The brief and layout are deterministic test inputs, not evidence of a live model call, customer, payment or profitable job.

- [brief.json](brief.json) freezes the 800 × 600 canvas, five required phrases, four colors, visual preferences and one included revision.
- [design.json](design.json) places safe shapes and six text lines around that brief.
- [graphic.svg](graphic.svg) is the downloadable vector original; [graphic.png](graphic.png) is the full-resolution raster original/preview.
- [checks.json](checks.json) records actual renderer checks and exact output hashes/sizes. The direct local rendering charge is zero; owner compute/storage costs are explicitly excluded. Paid design generation would have its own authoritative receipt.
- [browser-svg-preview.png](browser-svg-preview.png) shows the actual SVG rendered by Chromium. [browser-checks.json](browser-checks.json) records that all six rendered text bounding boxes fit inside the canvas, with zero page errors.

All required copy is visible. “BUILD SMALL. THINK BIG.” intentionally wraps across two text primitives; the date, time and studio remain visible. Both images retain the mint circle and gold rule without clipping. SVG and PNG use different local font renderers and are not promised to be pixel-identical.

The automated checks cover geometry, required text, palette, supported glyphs, contrast and rendering. Qualitative styling preferences remain customer review notes. The initial bundled font is limited to printable ASCII required text, which the business service checks before quoting. See [the adapter guide](../../docs/CREATIVE-GRAPHICS.md) for bounds and recovery behavior. Business delivery adds the separate private, versioned manifest with accepted scope, creation costs and revision history.

[manifest.synthetic.json](manifest.synthetic.json) is the private version-1 manifest produced by the integrated services and persistent worker using explicit local model/PayPal protocol fixtures. Its file hashes match these originals; its provisional fixture usage is not a live provider invoice. No customer access token is included.
