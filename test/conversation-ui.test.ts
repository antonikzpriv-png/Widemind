import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { createContext, runInContext, Script } from "node:vm";
import {
  commercePage,
  commerceScript,
  integrationsPage,
} from "../src/web/commerce.ts";

class Element {
  children: Element[] = [];
  textContent = "";
  value = "";
  name = "";
  type = "";
  disabled = false;
  onclick: (() => Promise<void>) | null = null;
  onsubmit: ((event: { preventDefault(): void }) => Promise<void>) | null =
    null;
  elements: Record<string, Element> = {};
  readonly tagName: string;
  constructor(tagName: string) {
    this.tagName = tagName;
  }
  append(...children: Element[]) {
    this.children.push(...children);
  }
  replaceChildren(...children: Element[]) {
    this.children = children;
  }
}

function all(element: Element): Element[] {
  return [element, ...element.children.flatMap(all)];
}

function fixture(
  handler: (path: string, body: Record<string, unknown>) => Promise<unknown>,
) {
  const storage = new Map<string, string>();
  const selectors = new Map<string, Element>();
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const location = { hash: "", pathname: "/commerce" };
  let replacedFragment = "";
  const context = createContext({
    document: {
      querySelector: (selector: string) => selectors.get(selector) ?? null,
      createElement: (name: string) => new Element(name),
    },
    crypto: webcrypto,
    TextEncoder,
    Uint8Array,
    URLSearchParams,
    URL,
    location,
    history: {
      replaceState(_state: unknown, _unused: string, fragment: string) {
        replacedFragment = fragment;
      },
    },
    sessionStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    },
    fetch: async (path: string, options: { body?: string; method: string }) => {
      const body = JSON.parse(options.body ?? "{}") as Record<string, unknown>;
      calls.push({ path, body });
      const result = await handler(path, body);
      return { ok: true, json: async () => result };
    },
  });
  new Script(commerceScript).runInContext(context);
  const functions = runInContext(
    "({proposalCard,conversationDetails,sendCustomerMessage,customerConversationForm,keepToken})",
    context,
  ) as {
    proposalCard(
      quote: Record<string, unknown>,
      token: string,
      status: Element,
      conversationId: string,
      currentQuote: Record<string, unknown>,
    ): Element;
    conversationDetails(
      conversation: Record<string, unknown>,
      operator?: boolean,
    ): Element;
    sendCustomerMessage(token: string, body: string): Promise<unknown>;
    customerConversationForm(
      token: string,
      status: Element,
      conversation: Record<string, unknown>,
    ): Element;
    keepToken(token: string): void;
  };
  return {
    ...functions,
    selectors,
    calls,
    storage,
    location,
    replacedFragment: () => replacedFragment,
  };
}

test("acceptance submits exactly the displayed current quote version and digest", async () => {
  const f = fixture(async () => ({
    status: "accepted",
    mode: "test",
    quotes: [],
    orders: [],
    notifications: [],
  }));
  const token = "fixture-token-" + "x".repeat(40);
  const open = new Element("form");
  open.elements.token = Object.assign(new Element("input"), {
    value: token,
  });
  const status = new Element("p");
  f.selectors.set("#customer-open", open);
  f.selectors.set("#customer-status", status);
  f.selectors.set("#customer-content", new Element("div"));
  const quote = {
    quoteId: "quote-current",
    version: 3,
    digest: "fixture-digest-current",
    priceMicros: "2000000",
    scope: { title: "Controlled SVG" },
    deliverables: ["One SVG"],
    exclusions: ["No trademark clearance"],
    turnaroundSeconds: 86400,
    revisionAllowance: 0,
    status: "offered",
    expiresAt: Date.now() / 1000 + 3600,
  };
  const old = f.proposalCard(
    { ...quote, version: 2, digest: "old" },
    token,
    status,
    "conversation-1",
    quote,
  );
  assert.equal(all(old).filter((item) => item.tagName === "button").length, 0);
  const card = f.proposalCard(quote, token, status, "conversation-1", quote);
  const accept = all(card).find((item) => item.tagName === "button")!;
  assert.ok(accept.textContent.includes("v3"));
  await accept.onclick!();
  assert.deepEqual(f.calls[0], {
    path: "/api/modules/business/accept",
    body: {
      token,
      quoteId: "quote-current",
      conversationId: "conversation-1",
      version: 3,
      digest: "fixture-digest-current",
    },
  });
});

test("an uncertain message reuses its identity and refuses changed text", async () => {
  let attempts = 0;
  const f = fixture(async (_path, body) => {
    if (++attempts === 1) throw new Error("controlled lost acknowledgment");
    return { messageId: body.messageId, queued: true };
  });
  const token = "private-fixture-token-" + "t".repeat(40);
  const body = "Can you create a simple logo for my cafe?";
  await assert.rejects(
    f.sendCustomerMessage(token, body),
    /lost acknowledgment/,
  );
  assert.equal(f.storage.size, 1);
  const saved = JSON.stringify([...f.storage.entries()]);
  assert.ok(!saved.includes(token));
  assert.ok(!saved.includes(body));
  await assert.rejects(
    f.sendCustomerMessage(token, "Actually, I need a new website"),
    /unchanged text/,
  );
  assert.equal(attempts, 1);
  await f.sendCustomerMessage(token, body);
  assert.equal(f.calls[0]!.body.messageId, f.calls[1]!.body.messageId);
  assert.deepEqual(Object.keys(f.calls[1]!.body).sort(), [
    "body",
    "messageId",
    "token",
  ]);
  assert.equal(f.storage.size, 0);
});

test("customer message content remains text and operator details stay on the private page", () => {
  const f = fixture(async () => ({}));
  const attack = "<img src=x onerror=\"fetch('/private')\">";
  const view = f.conversationDetails({
    status: "needs_clarification",
    messages: [{ direction: "inbound", body: attack, created_at: 1000 }],
    proposals: [],
    intervention: "Check a conflicting request",
  });
  assert.ok(all(view).some((item) => item.textContent === attack));
  assert.ok(!all(view).some((item) => item.tagName === "img"));
  assert.match(commercePage(), /Start with a message/);
  assert.ok(!commercePage().includes('id="business-conversations"'));
  assert.match(integrationsPage(), /id="business-conversations"/);
});

test("a private email link preserves its run and keeps access tokens in fragments and POST bodies", async () => {
  const f = fixture(async (_path, body) => ({ messageId: body.messageId }));
  const token = "controlled-private-token-" + "x".repeat(32);
  f.location.hash = "#customer=" + token + "&runId=email-run";
  f.keepToken(token);
  const fragment = new URLSearchParams(f.replacedFragment().slice(1));
  assert.equal(fragment.get("runId"), "email-run");
  assert.equal(fragment.get("customer"), token);
  await f.sendCustomerMessage(token, "Please clarify the quote.");
  assert.equal(f.calls[0]!.body.runId, "email-run");
  assert.equal(f.calls[0]!.body.token, token);
  assert.equal(f.calls[0]!.path, "/api/modules/business/message");
  assert.ok(!f.calls[0]!.path.includes(token));
});

test("opt-out is an immediate token-scoped request without customer-supplied authority", async () => {
  const f = fixture(async () => ({ optedOut: true }));
  const status = new Element("p");
  const section = f.customerConversationForm("fixture-token", status, {
    status: "awaiting_reply",
  });
  const stop = all(section).find((item) =>
    item.textContent.includes("Stop replies"),
  )!;
  await stop.onclick!();
  assert.deepEqual(f.calls[0], {
    path: "/api/modules/business/opt-out",
    body: { token: "fixture-token" },
  });
  const stopped = f.customerConversationForm("fixture-token", status, {
    status: "opted_out",
  });
  assert.equal(
    all(stopped).filter((item) => item.tagName === "form").length,
    0,
  );
});
