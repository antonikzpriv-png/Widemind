# Getting started: your first hour with WideMind

WideMind is self-hosted: one owner, one agent, one server. This is what a new owner does, in order. Hosting details are in [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Install and sign in

```sh
npm ci && npm run services:setup && npm run services:configure
npm run -s operator:hash       # type your password, then Ctrl-D; put the printed hash in .env as WIDEMIND_OPERATOR_PASSWORD_HASH
npm run migrate
npm run dev:all                # services, website and worker
```

`services:configure` generates the agent's identity (`WIDEMIND_COMMERCE_RUN_ID`) and internal service tokens in `var/integration.env`. Open `/operator` and sign in. Until the agent exists you land on **Getting started** (`/operator/start`).

## 2. Create your agent

On Getting started, give the agent:

- **Name** and **mission**: shown on the public homepage and used as the business goal.
- **What it sells**: any of a sourced research brief, an SVG graphic, or a simple web page, each with your price (at least $1.00, whole cents).
- **Spending limits**: an experiment budget and the most it may spend on one job, both within the $5.00 opening credit.
- **Outreach**: off by default, so it only answers people who come to it.

Saving creates the agent under the configured identity and sends the settings to the business service. Every save is kept as a new version; the page says whether the business service accepted them.

## 3. Follow the setup steps

The right-hand column lists every remaining step in six stages: create your agent, connect the services, connect accounts, protect the data, rehearse in sandbox, go live. The box at the top always names the **next** step and exactly what to do. It reads the same checks as `npm run pilot:preflight`.

The accounts stage needs you personally: PayPal and Google verify the identity of whoever opens the agent's PayPal Business and Gmail accounts. That cannot be automated.

## 4. Approve the first hour

Once the agent exists, Getting started proposes its first moves from your settings and the tools that are connected right now. No model is used to propose them and nothing runs until you approve:

- **Sell each offering** whose tool is available, at your price.
- **Look for people who need one offering**: you give one to five public HTTPS pages where people ask for that kind of work (forums, job boards, request threads). Discovery scans them for matching requests and proposes bounded experiments, capped at the lower of your experiment budget and $0.50. It needs the research tool (OpenAI).
- **Start working**: resumes the agent once the opening $5 is funded (sandbox: `npm run pilot:init -- --sandbox`).

Items that can't run yet are shown with what's missing. The server re-checks availability when you approve, and each item reports its own result.

## 5. Watch what it does

**Activity** (`/operator/activity`) shows, in plain language, what the agent is doing now and why, when it next checks in, what it did ("Prepared and sent a quote", "Checked a payment update", "Created a graphic"), what each step cost, and every change you made. It shows tool names, states and amounts, never customer messages or work content.

## 6. Rehearse, then go live

Run `npm run pilot:rehearse -- --email <your inbox>` in sandbox; a passing rehearsal completes that setup stage. The go-live stage then walks you through HTTPS, live payments, live actions and a verified live $5 opening. [PILOT.md](PILOT.md) covers the run record, alerts, snapshots and the public `/pilot` page.
