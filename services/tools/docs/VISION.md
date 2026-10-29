# WideMind product vision

## Product statement

WideMind is an autonomous online operator that begins with $5 of operating credit and decides how to earn enough to keep working.

It has its own online identity, dedicated Gmail, configured service accounts, working files, tools, and persistent memory. It can pursue inbound customer requests and discover opportunities itself.

Its website is its public headquarters. The agent runs as a persistent background service.

## Confirmed direction

The owner wants broad real-world capabilities:
- Internet research and browser interaction.
- Real email conversations with people.
- Graphics and content creation.
- Software development and useful digital products.
- Publishing through connected accounts.
- Offers, negotiation, orders, delivery, and payment reconciliation.
- Learning from previous work and changing business strategy.

These are intended product capabilities. They are not claims that integrations already exist.

Do not hard-code a single occupation, a single revenue source, or an order queue as the only way WideMind finds work. An initial demonstration can use one bounded offering.

## Operating loop

Receive an event or identify an opportunity → review balance and commitments → choose an approach → estimate cost → obtain a spending reservation → execute through tools → inspect the result → deliver or adjust → reconcile charges and payments → update memory.

Events include customer requests, replies, payment updates, scheduled work, and completed tool operations. Idle time should be event-driven rather than a continuous paid reasoning loop.

## Autonomy and control

WideMind chooses useful work within its configured capabilities and available funds. It can communicate and act through its own provisioned accounts.

A separate control plane owns the ledger, credentials, runtime capability configuration, and operator controls. WideMind cannot increase its own allowance, rewrite accounting, access unrelated funds, or grant itself new credentials.

Browser-based registration and provider onboarding are supported where technically available. Some services require human verification or contractual/payment setup; show those dependencies clearly rather than claiming every signup can be automated.

## Public experience

Visitors should be able to:
- Understand the experiment and current run status.
- See available offerings and examples of completed work.
- Contact or hire WideMind.
- Receive their own deliverables through an appropriate private flow.
- See financial summaries and selected public activity.

Financial summaries distinguish opening credit, customer receipts, earned revenue, operating charges, reserved funds, available credit, owner funding, and subsidies.

Do not expose private inbox content, customer details, unpublished work, secrets, or internal reasoning in the public feed.

## Private operations

The operator needs account setup, integration health, reconciliation, customer/job administration, and pause/resume/stop controls.

Routine authorized business actions should use the configured autonomy profile. Interventions and configuration changes must be recorded so the experiment remains understandable.

## What survival means

Paid execution continues while available credit can fund the next action. If funds are insufficient, WideMind may wait for replies or settlement, choose an affordable alternative, or enter an exhausted state.

Its history and payment reconciliation remain recoverable. Record reactivation, additional owner funding, or a new run explicitly.

Report elapsed run time alongside productive work, paid jobs, costs, and interventions. Waiting for free is not evidence of profitable work.

## Success criteria

The first real-world milestone is an independent customer paying for useful delivered work, with net receipts exceeding measured delivery costs and funding another action.

Longer-term evaluation includes:
- Independent customer receipts and fulfilled orders.
- Net earnings after defined operating costs.
- Refunds, failures, and unmet commitments.
- Productive actions funded by earnings.
- Human interventions and outside subsidies.
- Quality and customer acceptance.

A model-generated invoice or a simulated benchmark payout is not customer revenue.

## Foundation scope

This repository currently supplies documentation for building WideMind. The selected stack, providers, accounts, credentials, hosting, and live-run configuration remain implementation work.

The five Codex builders are development roles. They are not five funded WideMind instances. Start with one runtime identity and one auditable survival run.
