# Getting started

WideMind is self-hosted: one owner, one agent, one server. From a fresh checkout to a running agent takes one command and about a minute. Hosting on a real domain is in [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Run one command

You need Node.js 24 (`nvm install` reads `.nvmrc`) and Python 3.12+.

```sh
npm run quickstart
```

It installs what's missing, creates your agent's identity and private service tokens, creates an owner password, prepares the database and starts everything. At the end it prints:

```
✓ WideMind is ready.

  Sign in:  http://127.0.0.1:3000/operator
  Password: abcdef-ghjkmn-pqrstu-vwxyz2
```

The password is shown **once**; save it. Only its hash is stored, in `var/integration.env`. Running `npm run quickstart` again skips everything already done (about 50 seconds the first time, instant after). Use `--no-start` to set up without starting; start later with `npm run dev:all`.

## 2. Create your agent

Sign in. The first time, you see a short welcome and the **Create your agent** card:

- **Name** and **what it's for**: shown on your public homepage. A sensible default is filled in.
- **What it sells**: an SVG graphic, a simple web page and a sourced research brief, each at your price ($1.00 to $10,000.00, whole cents). Research needs OpenAI connected, so it starts unticked.
- **Spending limits and outreach** (folded away): an experiment budget and the most it may spend on one job, both within the $5.00 opening credit, and whether it may contact people first (off by default).

**Create my agent** saves it and puts the offerings on your site. Every later save is kept as a new version.

## 3. Your first hour

Getting started then shows four cards:

1. **Add $5.00 of test credit**: one click. It's sandbox money, labelled as test funding.
2. **Start your agent**: one click. Waiting for work never triggers paid model calls.
3. **Try it yourself**: open your public site and request something. A quote arrives in seconds. Paying needs PayPal sandbox credentials (see the checklist).
4. **Watch what it does**: Activity shows every request, quote, payment and delivery in plain language, with what it cost.

**Optional: let it look for customers** scans one to five public pages you choose (forums, job boards, request threads) for matching requests, capped at the lower of your experiment budget and $0.50. It needs OpenAI.

## 4. Before going live

The right-hand column lists what's left in six stages: create your agent, connect the services, connect accounts, protect the data, rehearse in sandbox, go live. A progress bar shows how far along you are, and the next step is spelled out. These are the same checks as `npm run pilot:preflight`, and the service checks really contact each service.

The accounts stage needs you personally: PayPal and Google verify the identity of whoever opens the agent's PayPal Business and Gmail accounts.

When those are done, `npm run pilot:rehearse -- --email <your inbox>` runs a full sandbox order, and a passing rehearsal completes its stage. [PILOT.md](PILOT.md) covers the run record, alerts, snapshots and the public `/pilot` page.

## Doing it by hand

`npm run quickstart` runs these steps, which still work on their own: `npm ci`, `npm run services:setup`, `npm run services:configure`, an owner password hash (`npm run -s operator:hash`, then set `WIDEMIND_OPERATOR_PASSWORD_HASH` in `.env`), `npm run migrate` and `npm run dev:all`.
