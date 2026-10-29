# Private creative delivery workspace

The integrated PayPal customer workspace at `/commerce` supports `custom-graphic` and `interactive-calculator` alongside the existing offerings. Capability availability and accepted terms come from the business service. The static `svg-graphic` and `software-starter` offerings remain explicitly labeled templates.

A custom graphic request includes its canvas size, exact required words (printable ASCII for the initial typeface), palette, visual preferences and included revision limit. Visual preferences are qualitative notes; the preview lets the customer judge those preferences. A calculator request includes its named numerical inputs and ranges, named results, at least two expected calculation examples, rounding tolerance, supported interface requirements and revision limit. The examples are reviewed with the quote before acceptance. The existing short title/description forms remain available for template offerings and the sourced-research form remains available separately.

Each published artifact version has individual downloads for originals, preview images, source and instructions; the UI shows file sizes/hashes and provides a private manifest download. Older delivery records still expose their original single-file download. A customer can submit an included revision within the accepted scope/budget and remaining time/round limits, or submit a complete new scope for a separately quoted request. New requirements do not silently extend a paid order. Repeated submission of unchanged feedback retains the same change-request ID while that form remains open. The worker, not the webpage, performs accepted work.

## Preview isolation

Previews use an ordinary HTML response loaded by a hidden same-origin POST form into an iframe. Customer tokens are sent in the POST body, never in a preview URL or query string. Ownership is checked by the business service before any artifact bytes are returned. The frame uses `sandbox="allow-scripts"` without `allow-same-origin`, forms, popups, top navigation or downloads; `credentialless` provides additional isolation in supporting browsers. Generated code has an opaque origin and cannot read the parent document, operator session or cookies.

The parent site's CSP retains same-origin scripts and connections and gains only `frame-src 'self'`. The dedicated preview response is subject to a separate host-enforced policy:

```text
sandbox allow-scripts;
default-src 'none';
script-src 'unsafe-inline';
style-src 'unsafe-inline';
img-src data:;
connect-src 'none';
form-action 'none';
frame-ancestors 'self';
base-uri 'none';
object-src 'none';
child-src 'none'
```

This response uses `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN` and restrictive browser permissions. Its inline-script allowance applies only inside the opaque preview document. Neither a `srcdoc` nor a blob document is used for executing previews, because those inherit the parent's script policy. Ordinary artifact downloads remain separate from execution. Do not deploy generated source under the operator website's origin with privileged sessions; use an independent origin for published customer software.

The additive `ModuleRoute.responseKind: 'sandboxed-html'` is accepted only for registered public POST routes. The host requires the exact configured request Origin, validates a bounded `{html:string}` response, and always supplies the fixed sandbox policy. Modules cannot choose their own execution permissions. Authorization errors retain JSON responses and never become executable previews.

## Private endpoints

All customer operations remain under `/api/modules/business/`:

- `POST /manifest`: `{token,orderId,version?}` reads a published version's private manifest.
- `POST /delivery`: `{token,orderId,version?,filename?}` reads the selected original/source/preview file. Legacy requests without version/file retain the primary delivery behavior.
- `POST /preview`: the same selected-file body, from a same-origin form, renders HTML or an SVG/PNG image in the isolated frame.
- `POST /change-request`: `{token,orderId,requestId,kind,feedback,scope?}`, where kind is `revision` or `scope_change`. A scope change includes the complete new scope and returns a separate private request link.

The gateway never accepts an arbitrary HTML document, filesystem path or provider URL from a customer preview request. The selected file must be returned by the authenticated business delivery endpoint after its ownership/publication checks.

## Verify

```sh
npm run typecheck
node --test test/creative-portal.test.ts test/commerce-shell.test.ts
npm run build
node scripts/creative-portal-browser.mjs
```

The browser check uses the existing local Chromium harness and a disposable, explicitly synthetic business service. It verifies a real interactive calculator result, actual source download, structured calculator intake, revision submission, desktop/390/320-pixel layouts, and hostile preview attempts to read the parent, read cookies, or fetch private operator data. The frame's message origin is verified as `null`; all three access attempts are blocked. Screenshots/report are local ignored files under `var/creative-portal-browser/`. No provider call, funding, payment, customer or live delivery is established by that fixture. In a restricted execution environment the localhost/browser checks need the normal authorized loopback capability.
