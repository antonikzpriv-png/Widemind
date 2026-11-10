# First customers: design and plan

WideMind can quote, take payment and deliver, but a new owner finishes setup with a storefront nobody visits. These six additions aim at the first paying customer. They live in the Node web app (`src/runtime/growth.ts`, `src/web/growth-pages.ts`) and reuse the business service through its existing private API. None of them calls a model, spends credit or sends a message by itself.

## Principles

- **The owner sends every outbound message.** WideMind drafts; a person reviews and sends from their own mail client or pastes the reply where the request was posted. Nothing here widens `allow_outbound`.
- **Evidence is not demand.** A lead is a page excerpt that matched a need term. It does not show willingness to pay.
- **Attribution counts requests, not revenue.** A `ref` on an order link records which channel a request came from. Money is still credited only from verified payments.
- **Published samples are owner-approved.** Nothing private becomes public without an explicit publish action, and it can be retracted.

## 1. Lead inbox (`/operator/leads`)

Discovery already records matching requests as opportunities. The inbox turns each one into something the owner can act on.

- Opening the page copies new discoveries from the business service into `growth_leads` (a free local read; no research runs).
- Each lead shows the matched need, the source excerpt and link, and a drafted reply with a tracked order link (`ref=lead-<id>`).
- The owner can edit the draft, open it in their mail app (when the market names a contact address that occurred in the source text), mark it sent, or dismiss it.
- A contact address can be marked sent once per run. Dismissed and sent leads are never re-drafted.
- Opt-outs are honored. The business service lists the addresses that asked not to be contacted (`/v1/opt-outs`); a lead naming one of them cannot be sent, and when the list cannot be read no lead with an address can be marked sent.
- The draft says the service is run by an automated agent and that a person reviewed the message.

Not built: sending through the agent's own Gmail account. That needs an economy reservation, the outreach policy and opt-out checks in the business service, and belongs there.

## 2. Share kit (`/operator/share`)

For each offering the agent sells:

- a direct order link (`/commerce?service=<id>&ref=share`) that preselects the service,
- three ready-to-post texts (short post, longer post, direct message),
- a card image at `/share/<id>.svg` with the agent's name, the offering and its price.

The page warns when the public origin is a loopback address, because those links only work on the owner's machine. It also shows how many requests each channel has produced.

## 3. Samples gallery (`/samples`, `/operator/samples`)

- The owner adds a sample by pasting SVG, HTML or text, or imports the private prototype of a finished discovery experiment.
- Samples start as drafts. Publishing puts them on the public `/samples` page with an "Order one like this" link (`ref=sample-<id>`). Taking one down removes it and erases its stored content.
- A sample holds at most 100,000 characters, and an agent at most 60 samples.
- Untrusted markup never runs in the site's origin: SVG is validated on save and served as an image with a sandboxing policy; HTML is served only inside a sandboxed frame; text is escaped.

Not built: pay-to-unlock previews. Samples are complete examples, not withheld deliverables.

## 4. Priced packages

- Each offering has three suggested prices (starter, standard, premium) in the agent form. They are starting points, not market research.
- The form shows what the owner keeps after an estimated payment fee and warns when the fee takes more than a fifth of the price. The estimate uses `WIDEMIND_FEE_BPS` (default 349) and `WIDEMIND_FEE_FIXED_MICROS` (default 490000); check the provider's actual rates.
- "Included revisions" (0 to 3) maps to the existing `conversation_revision_allowance`. "Leave as it is" keeps a value set directly in the business policy. Changes beyond the allowance still need a new quote.

Not built: tiers with different scopes per price. The business policy has one price per service.

## 5. Lower setup cost

- **Portal-only mode.** `WIDEMIND_PORTAL_ONLY=true` lets the go-live checklist pass without a Gmail account. Customers use their private link; the agent cannot read or send email.
- **Container deploy.** `Dockerfile` and `docker-compose.yml` run every service behind Caddy for HTTPS. See [DEPLOYMENT.md](DEPLOYMENT.md).

## 6. Repeat business

After a delivery is available, the customer's private page shows:

- an "Order again" link (`ref=repeat`),
- a personal referral link (`ref=friend-<code>`). The code is derived in the browser from the private token, so it cannot be turned back into the token, and the server only stores it when someone uses the link.

The share page counts requests per channel and per referral code. A customer confirms a referral by showing their link; the owner compares its code with the list. There is no automatic reward; the owner decides what a referral is worth.

Not built: an emailed follow-up. The business service's follow-up and opt-out rules own outbound mail.

## Build plan

1. Storage: core migration 6 adds `growth_leads`, `growth_samples` and `growth_attributions`.
2. Logic: `src/runtime/growth.ts` (leads, drafts, share kit, samples, attribution, fee estimate).
3. Business contract: optional `readDiscoveries` and `readPrototype` reads, implemented by the business module.
4. Pages and routes: operator pages, the public gallery, sample and card images, and the attribution hook on the existing request route.
5. Customer page: preselect a service from the link, send `ref`, show repeat and referral links.
6. Agent form: suggested prices, fee estimate, included revisions.
7. Readiness: portal-only mode in the checklist and guide.
8. Deploy: container files and documentation.
9. Tests: `test/growth.test.ts` for the logic and the web routes.
