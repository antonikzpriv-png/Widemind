import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import test from "node:test";
import { hashPassword } from "../src/auth/password.ts";
import {
  policyFromProfile,
  profileFromInput,
  saveAgent,
} from "../src/runtime/agent.ts";
import {
  addSample,
  attributionCounts,
  checkSample,
  countNewLeads,
  countPublishedSamples,
  ownerSample,
  referralCounts,
  feeEstimate,
  feeModel,
  listLeads,
  listSamples,
  mailtoLink,
  orderLink,
  prototypes,
  recordAttribution,
  sampleFromArtifact,
  setSampleState,
  shareCardSvg,
  shareKit,
  syncLeads,
  updateLead,
} from "../src/runtime/growth.ts";
import { pilotReadiness } from "../src/runtime/pilot.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

const ORIGIN = "https://agent.example.test";
const input = {
  name: "Pixel & <Proof>",
  mission: "Make small useful graphics.",
  "offer_svg-graphic": "on",
  "price_svg-graphic": "19.00",
  "offer_software-starter": "on",
};
const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>JavaScript logo</title><circle cx="5" cy="5" r="4"/></svg>';

function status() {
  return {
    config: {
      markets: [
        {
          id: "logos",
          service: "svg-graphic",
          kind: "lead",
          lead_email: "Owner@Bakery.example",
        },
        { id: "pages", service: "software-starter", kind: "experiment" },
        { id: "briefs", service: "research-brief", kind: "experiment" },
      ],
    },
    discoveries: [
      {
        opportunity_id: "opp-logo-1",
        market_id: "logos",
        kind: "lead",
        observed_need: "logo",
        evidence: {
          url: "https://forum.example.test/t/1",
          excerpt: "Looking for a logo <b>fast</b>",
          matched_term: "logo",
        },
      },
      {
        opportunity_id: "opp-logo-2",
        market_id: "logos",
        kind: "lead",
        evidence: { url: "javascript:alert(1)", excerpt: "Another logo" },
      },
      {
        opportunity_id: "opp-page-1",
        market_id: "pages",
        kind: "experiment",
        evidence: [
          { url: "https://forum.example.test/t/2", excerpt: "A page" },
        ],
      },
      // Not sold by this agent, so it never becomes a lead.
      {
        opportunity_id: "opp-brief-1",
        market_id: "briefs",
        kind: "experiment",
      },
      { market_id: "logos" },
    ],
    experiments: [
      {
        id: "experiment-1",
        opportunity_id: "opp-page-1",
        hypothesis: "A starter page fits this request",
        artifact: { filename: "starter.svg", media_type: "image/svg+xml" },
      },
      { id: "experiment-2", opportunity_id: "opp-logo-1", artifact: null },
    ],
  };
}

test("fee estimates are exact and flag prices the fee eats", () => {
  const model = feeModel({});
  assert.deepEqual(model, { bps: 349, fixedMicros: "490000" });
  assert.deepEqual(feeEstimate("2000000", model), {
    feeMicros: "560000",
    keepMicros: "1440000",
    heavy: true,
  });
  assert.deepEqual(feeEstimate("19000000", model), {
    feeMicros: "1160000",
    keepMicros: "17840000",
    heavy: false,
  });
  assert.deepEqual(
    feeModel({ WIDEMIND_FEE_BPS: "290", WIDEMIND_FEE_FIXED_MICROS: "300000" }),
    { bps: 290, fixedMicros: "300000" },
  );
  // Invalid or absurd settings fall back to the defaults.
  assert.deepEqual(
    feeModel({ WIDEMIND_FEE_BPS: "-1", WIDEMIND_FEE_FIXED_MICROS: "1e9" }),
    model,
  );
  assert.equal(JSON.stringify(model), '{"bps":349,"fixedMicros":"490000"}');
});

test("suggested prices and included revisions map onto the profile and policy", () => {
  const plain = profileFromInput(input);
  assert.equal(plain.offerings["svg-graphic"], "19000000");
  assert.equal("includedRevisions" in plain, false);
  assert.equal(
    "conversation_revision_allowance" in policyFromProfile(plain, null),
    false,
  );
  const chosen = profileFromInput({
    ...input,
    "preset_svg-graphic": "premium",
    "preset_software-starter": "",
    revisions: "2",
  });
  assert.equal(chosen.offerings["svg-graphic"], "39000000");
  assert.equal(chosen.offerings["software-starter"], "3000000");
  assert.equal(chosen.includedRevisions, 2);
  assert.equal(
    policyFromProfile(chosen, { conversation_revision_allowance: 0 })
      .conversation_revision_allowance,
    2,
  );
  assert.throws(
    () => profileFromInput({ ...input, revisions: "9" }),
    /between 0 and 3/,
  );
  // An unknown preset is ignored rather than trusted.
  assert.equal(
    profileFromInput({ ...input, "preset_svg-graphic": "free" }).offerings[
      "svg-graphic"
    ],
    "19000000",
  );
});

test("leads are drafted once, sent by the owner, and never written twice", () => {
  const f = fixture("foundation");
  try {
    const runId = randomUUID();
    const { profile } = saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput(input),
    });
    const sync = () =>
      syncLeads({ db: f.db, runId, status: status(), profile, origin: ORIGIN });
    assert.equal(sync(), 3);
    assert.equal(sync(), 0);
    assert.equal(
      syncLeads({ db: f.db, runId, status: null, profile, origin: ORIGIN }),
      0,
    );
    const leads = listLeads(f.db, runId);
    assert.equal(leads.length, 3);
    const first = leads.find((lead) => lead.need === "logo")!;
    assert.equal(first.contact, "owner@bakery.example");
    assert.equal(first.sourceUrl, "https://forum.example.test/t/1");
    assert.match(first.draft, /Pixel & <Proof>/);
    assert.match(first.draft, /an SVG graphic/);
    assert.match(first.draft, /\$19\.00/);
    assert.match(first.draft, /automated agent/);
    assert.ok(
      first.draft.includes(
        `${ORIGIN}/commerce?service=svg-graphic&ref=lead-${first.id}`,
      ),
    );
    const unsafe = leads.find((lead) => lead.excerpt === "Another logo")!;
    assert.equal(unsafe.sourceUrl, "");
    const page = leads.find((lead) => lead.service === "software-starter")!;
    assert.equal(page.contact, null);
    assert.equal(mailtoLink(page, profile), null);
    const mail = mailtoLink(first, profile)!;
    assert.match(mail, /^mailto:owner%40bakery\.example\?subject=/);
    assert.doesNotMatch(mail, /[\s<>"]/);

    assert.throws(
      () =>
        updateLead({
          db: f.db,
          runId,
          id: first.id,
          action: "save",
          draft: " ",
        }),
      /reply is required/,
    );
    assert.throws(
      () =>
        updateLead({
          db: f.db,
          runId: randomUUID(),
          id: first.id,
          action: "dismiss",
        }),
      /Unknown lead/,
    );
    // A lead that names an address needs a readable opt-out list first.
    const send = (optedOut?: ReadonlySet<string> | null) =>
      updateLead({
        db: f.db,
        runId,
        id: first.id,
        action: "contacted",
        draft: "Hello\r\nthere",
        ...(optedOut === undefined ? {} : { optedOut }),
      });
    assert.throws(() => send(), /could not be checked/);
    assert.throws(() => send(null), /could not be checked/);
    assert.throws(
      () => send(new Set(["owner@bakery.example"])),
      /asked not to be contacted/,
    );
    assert.equal(
      listLeads(f.db, runId).find((lead) => lead.id === first.id)!.state,
      "new",
    );
    // Browser line endings are normalized before the length is checked.
    const long = ("x".repeat(98) + "\r\n").repeat(40) + "y".repeat(30);
    assert.equal(long.length, 4030);
    assert.equal(
      updateLead({
        db: f.db,
        runId,
        id: first.id,
        action: "save",
        draft: long,
      }).draft.length,
      3990,
    );
    const edited = send(new Set(["someone@else.example"]));
    assert.equal(edited.state, "contacted");
    assert.equal(edited.draft, "Hello\nthere");
    assert.throws(
      () => updateLead({ db: f.db, runId, id: first.id, action: "dismiss" }),
      /already decided/,
    );
    // The second lead names the same address, so it cannot be sent as well.
    assert.throws(
      () =>
        updateLead({
          db: f.db,
          runId,
          id: unsafe.id,
          action: "contacted",
          optedOut: new Set(),
        }),
      /already contacted/,
    );
    // A lead with no address is answered where it was posted; nothing to check.
    assert.equal(
      updateLead({ db: f.db, runId, id: page.id, action: "contacted" }).state,
      "contacted",
    );
    assert.equal(
      updateLead({ db: f.db, runId, id: unsafe.id, action: "dismiss" }).state,
      "dismissed",
    );
    assert.equal(
      updateLead({ db: f.db, runId, id: unsafe.id, action: "reopen" }).state,
      "new",
    );
    // A later sync does not resurrect or re-draft a decided lead.
    assert.equal(sync(), 0);
    assert.equal(
      listLeads(f.db, runId).find((lead) => lead.id === first.id)!.draft,
      "Hello\nthere",
    );
    assert.equal(
      (
        f.db
          .prepare(
            "SELECT COUNT(*) AS n FROM runtime_events WHERE run_id=? AND kind='growth.lead.contacted'",
          )
          .get(runId) as { n: number }
      ).n,
      // The logo lead and the page lead; refused attempts record nothing.
      2,
    );
  } finally {
    f.close();
  }
});

test("the share kit links, posts and card use the saved profile and escape it", () => {
  const profile = profileFromInput(input);
  const kit = shareKit(profile, ORIGIN);
  assert.deepEqual(
    kit.map((item) => item.offering.id),
    ["svg-graphic", "software-starter"],
  );
  assert.equal(
    kit[0]!.link,
    `${ORIGIN}/commerce?service=svg-graphic&ref=share`,
  );
  assert.equal(kit[0]!.posts.length, 3);
  for (const post of kit[0]!.posts) {
    assert.ok(post.text.includes(kit[0]!.link));
    assert.match(post.text, /\$19\.00/);
  }
  assert.equal(orderLink(ORIGIN), `${ORIGIN}/commerce`);
  const card = shareCardSvg(profile, "svg-graphic", ORIGIN)!;
  assert.match(card, /^<svg /);
  assert.match(card, /Pixel &amp; &lt;Proof&gt;/);
  assert.doesNotMatch(card, /<Proof>/);
  assert.match(card, /agent\.example\.test\/commerce/);
  assert.equal(shareCardSvg(profile, "research-brief", ORIGIN), null);
  assert.equal(shareCardSvg(profile, "nope", ORIGIN), null);
});

test("samples refuse active content and only published ones are listed publicly", () => {
  assert.equal(checkSample("svg", SVG), SVG);
  for (const bad of [
    "<svg><script>alert(1)</script></svg>",
    '<svg onload="alert(1)"></svg>',
    '<svg><a href="javascript:alert(1)">x</a></svg>',
    "<svg><foreignObject><p>x</p></foreignObject></svg>",
    '<!DOCTYPE svg [<!ENTITY x "y">]><svg/>',
    "<p>not an svg</p>",
    '<!DOCTYPE svg PUBLIC "x" "y" [<!ELEMENT a ANY>]><svg/>',
  ])
    assert.throws(() => checkSample("svg", bad), /SVG/, bad);
  // The plain DOCTYPE that design tools export is fine.
  const exported =
    '<?xml version="1.0" encoding="UTF-8"?>\n<!-- made by a tool -->\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n<svg xmlns="http://www.w3.org/2000/svg"/>';
  assert.equal(checkSample("svg", exported), exported);
  assert.throws(() => checkSample("svg", "x".repeat(100_001)), /at most/);
  assert.throws(() => checkSample("pdf", "x"), /type/);
  assert.throws(() => checkSample("text", ""), /required/);
  assert.deepEqual(
    sampleFromArtifact({
      filename: "badge.svg",
      media_type: "image/svg+xml",
      content: SVG,
    }),
    { kind: "svg", content: SVG, title: "badge.svg" },
  );
  assert.equal(
    sampleFromArtifact({
      media_type: "text/markdown; charset=utf-8",
      content: "# Hi",
    }).kind,
    "text",
  );
  assert.throws(
    () => sampleFromArtifact({ media_type: "application/zip", content: "x" }),
    /cannot be shown/,
  );
  assert.deepEqual(prototypes(status()), [
    {
      experimentId: "experiment-1",
      title: "A starter page fits this request",
      filename: "starter.svg",
      service: "software-starter",
    },
  ]);

  const f = fixture("foundation");
  try {
    const runId = randomUUID();
    saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput(input),
    });
    const add = (extra: Record<string, unknown> = {}) =>
      addSample({
        db: f.db,
        runId,
        service: "svg-graphic",
        title: "Badge",
        kind: "svg",
        content: SVG,
        origin: "owner",
        ...extra,
      });
    assert.throws(() => add({ service: "unknown" }), /which offering/);
    assert.throws(() => add({ title: "  " }), /title/);
    const id = add();
    assert.equal(listSamples(f.db, runId, true).length, 0);
    assert.equal(listSamples(f.db, runId)[0]!.state, "draft");
    setSampleState({ db: f.db, runId, id, action: "publish" });
    assert.throws(
      () => setSampleState({ db: f.db, runId, id, action: "publish" }),
      /already published/,
    );
    assert.equal(listSamples(f.db, runId, true).length, 1);
    assert.equal(countPublishedSamples(f.db, runId), 1);
    assert.equal(ownerSample(f.db, runId, id)!.content, SVG);
    setSampleState({ db: f.db, runId, id, action: "retract" });
    assert.equal(listSamples(f.db, runId).length, 0);
    assert.equal(countPublishedSamples(f.db, runId), 0);
    assert.equal(ownerSample(f.db, runId, id), null);
    // Taking a sample down erases what was stored.
    assert.deepEqual(
      { ...f.db.prepare("SELECT state,content FROM growth_samples").get() },
      { state: "retracted", content: "" },
    );
    assert.throws(
      () => setSampleState({ db: f.db, runId, id, action: "publish" }),
      /Unknown sample/,
    );
  } finally {
    f.close();
  }
});

test("attribution counts each request once, by channel, and ignores bad tags", () => {
  const f = fixture("foundation");
  try {
    const runId = f.store.create("foundation");
    assert.equal(recordAttribution(f.db, runId, "share", "opp-1"), true);
    assert.equal(recordAttribution(f.db, runId, "lead-7", "opp-1"), false);
    assert.equal(recordAttribution(f.db, runId, "lead-7", "opp-2"), true);
    assert.equal(
      recordAttribution(f.db, runId, "friend-0a1b2c3d4e5f", "opp-3"),
      true,
    );
    assert.equal(recordAttribution(f.db, runId, "newsletter", "opp-4"), true);
    for (const bad of ["Share", "a b", "", "x".repeat(41), 7, null, "<script>"])
      assert.equal(recordAttribution(f.db, runId, bad, "opp-9"), false);
    assert.equal(recordAttribution(f.db, runId, "share", "bad id"), false);
    assert.deepEqual(attributionCounts(f.db, runId), {
      share: 1,
      lead: 1,
      sample: 0,
      repeat: 0,
      friend: 1,
      other: 1,
      total: 4,
    });
    assert.deepEqual(referralCounts(f.db, runId), [
      { ref: "friend-0a1b2c3d4e5f", requests: 1 },
    ]);
  } finally {
    f.close();
  }
});

test("portal-only mode satisfies the email check without a mailbox", async () => {
  const f = fixture("foundation");
  try {
    const check = async (env: NodeJS.ProcessEnv) =>
      (
        await pilotReadiness({
          env,
          config: f.config,
          operatorPasswordConfigured: true,
          services: f.services,
          db: f.db,
          runId: null,
        })
      ).checks.find((item) => item.id === "email_capability")!;
    assert.equal((await check({})).ok, false);
    const portal = await check({ WIDEMIND_PORTAL_ONLY: "true" });
    assert.equal(portal.ok, true);
    assert.match(portal.detail, /Portal-only/);
  } finally {
    f.close();
  }
});

test("owner pages are private, samples are sandboxed and tracked requests are counted", async () => {
  const f = fixture("foundation");
  const runId = randomUUID();
  f.config.publicRunId = runId;
  const password = "fixture-only-long-password";
  const server = webServer({
    ...f,
    secrets: { operatorPasswordHash: await hashPassword(password) },
    env: {},
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  f.config.publicOrigin = origin;
  try {
    // Before the agent exists nothing is published and nothing fails.
    assert.equal((await fetch(origin + "/samples")).status, 200);
    assert.equal((await fetch(origin + "/share/svg-graphic.svg")).status, 404);
    saveAgent({
      db: f.db,
      config: f.config,
      runId,
      profile: profileFromInput(input),
    });
    f.services.business.health = () => ({ available: true });
    f.services.business.readDiscoveries = async () => status();
    let optedOut: string[] | null = [];
    f.services.business.readOptOuts = async () => {
      if (!optedOut) throw new Error("business service unavailable");
      return optedOut;
    };
    f.services.business.readPrototype = async () => ({
      filename: "starter.svg",
      media_type: "image/svg+xml",
      content: SVG,
    });
    let requests = 0;
    f.services.routes.push({
      path: "/api/modules/business/request",
      method: "POST",
      access: "public",
      handle: async () => ({
        status: 202,
        body: { opportunityId: `opp-web-${(requests += 1)}` },
      }),
    });

    for (const path of [
      "/operator/leads",
      "/operator/share",
      "/operator/samples",
      "/operator/sample-preview/1.svg",
      "/api/operator/leads",
    ])
      assert.equal((await fetch(origin + path)).status, 401, path);

    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const get = (path: string) =>
      fetch(origin + path, { headers: { Cookie: cookie } });
    const post = (path: string, fields: Record<string, string>) =>
      fetch(origin + path, {
        method: "POST",
        redirect: "manual",
        headers: {
          Cookie: cookie,
          Origin: origin,
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams(fields),
      });

    // Getting started: fee note, suggested prices and the first-customer card.
    const start = await (await get("/operator/start")).text();
    assert.match(start, /Find your first customer/);
    assert.match(start, /name="preset_svg-graphic"/);
    assert.match(start, /Premium · \$39\.00/);
    assert.match(start, /At \$19\.00 you keep about \$17\.84/);
    assert.match(start, /name="revisions"/);
    const startJson = await get("/api/operator/start");
    assert.equal(startJson.status, 200);
    assert.equal(
      ((await startJson.json()) as { fees: { bps: number } }).fees.bps,
      349,
    );

    // Lead inbox.
    const leads = await (await get("/operator/leads")).text();
    assert.match(leads, /3 new lead\(s\) added/);
    assert.match(leads, /Looking for a logo &lt;b&gt;fast&lt;\/b&gt;/);
    assert.doesNotMatch(leads, /<b>fast|javascript:alert/);
    assert.match(leads, /href="mailto:owner%40bakery\.example\?/);
    assert.match(leads, /Pixel &amp; &lt;Proof&gt;/);
    const lead = listLeads(f.db, runId).find((item) => item.need === "logo")!;
    assert.equal(countNewLeads(f.db, runId), 3);
    // Opt-outs unknown, then listed: the lead cannot be sent either way.
    const attempt = () =>
      post("/operator/leads", {
        csrf,
        id: String(lead.id),
        action: "contacted",
        draft: lead.draft,
      });
    optedOut = null;
    const unknown = await attempt();
    assert.equal(unknown.status, 400);
    assert.match(await unknown.text(), /could not be checked/);
    const unknownPage = await (await get("/operator/leads")).text();
    assert.match(unknownPage, /Opt-outs could not be checked right now/);
    assert.doesNotMatch(unknownPage, /href="mailto:/);
    optedOut = ["owner@bakery.example"];
    const refusedLead = await attempt();
    assert.equal(refusedLead.status, 400);
    assert.match(await refusedLead.text(), /asked not to be contacted/);
    const stoppedPage = await (await get("/operator/leads")).text();
    assert.match(stoppedPage, /This person asked not to be contacted/);
    assert.doesNotMatch(stoppedPage, /href="mailto:/);
    assert.equal(countNewLeads(f.db, runId), 3);
    optedOut = [];
    assert.equal(
      (
        await post("/operator/leads", {
          id: String(lead.id),
          action: "contacted",
        })
      ).status,
      403,
    );
    const sent = await post("/operator/leads", {
      csrf,
      id: String(lead.id),
      action: "contacted",
      draft: lead.draft,
    });
    assert.equal(sent.status, 303);
    assert.equal(sent.headers.get("location"), "/operator/leads");
    const again = await post("/operator/leads", {
      csrf,
      id: String(lead.id),
      action: "dismiss",
    });
    assert.equal(again.status, 400);
    assert.match(await again.text(), /already decided/);
    assert.match(await (await get("/operator/leads")).text(), /Sent \(1\)/);

    // Share kit and card.
    const share = await (await get("/operator/share")).text();
    assert.match(share, /These links only work on this computer/);
    assert.ok(
      share.includes(`${origin}/commerce?service=svg-graphic&amp;ref=share`),
    );
    const card = await fetch(origin + "/share/svg-graphic.svg");
    assert.equal(card.status, 200);
    assert.match(card.headers.get("content-type")!, /^image\/svg\+xml/);
    assert.match(card.headers.get("content-security-policy")!, /sandbox/);
    assert.equal(
      (await fetch(origin + "/share/research-brief.svg")).status,
      404,
    );

    // Samples: refuse active SVG, keep drafts private, sandbox what is public.
    const refused = await post("/operator/samples", {
      csrf,
      action: "add",
      title: "Bad",
      service: "svg-graphic",
      kind: "svg",
      content: "<svg><script>alert(1)</script></svg>",
    });
    assert.equal(refused.status, 400);
    assert.equal(
      (
        await post("/operator/samples", {
          csrf,
          action: "add",
          title: "Badge <one>",
          service: "svg-graphic",
          kind: "svg",
          content: SVG + " ".repeat(40_000),
        })
      ).status,
      303,
    );
    assert.equal(
      (
        await post("/operator/samples", {
          csrf,
          action: "add",
          title: "Landing page",
          service: "software-starter",
          kind: "html",
          content: "<h1>Hello</h1><script>parent.document.title='x'</script>",
        })
      ).status,
      303,
    );
    assert.equal(
      (
        await post("/operator/samples", {
          csrf,
          action: "import",
          experimentId: "experiment-1",
        })
      ).status,
      303,
    );
    assert.equal(
      (
        await post("/operator/samples", {
          csrf,
          action: "import",
          experimentId: "missing",
        })
      ).status,
      400,
    );
    const all = listSamples(f.db, runId);
    assert.equal(all.length, 3);
    const svg = all.find((item) => item.title === "Badge <one>")!;
    const html = all.find((item) => item.kind === "html")!;
    const imported = all.find(
      (item) => item.origin === "experiment:experiment-1",
    )!;
    assert.equal(imported.service, "software-starter");
    assert.equal((await fetch(`${origin}/samples/${svg.id}.svg`)).status, 404);
    assert.equal(
      (await get(`/operator/sample-preview/${svg.id}.svg`)).status,
      200,
    );
    const console = await get("/operator/samples");
    assert.match(
      console.headers.get("content-security-policy")!,
      /frame-src 'self'/,
    );
    const consoleHtml = await console.text();
    assert.match(consoleHtml, /Badge &lt;one&gt;/);
    assert.doesNotMatch(consoleHtml, /Add as a draft/);
    for (const item of [svg, html])
      assert.equal(
        (
          await post("/operator/samples", {
            csrf,
            action: "publish",
            id: String(item.id),
          })
        ).status,
        303,
      );
    const image = await fetch(`${origin}/samples/${svg.id}.svg`);
    assert.equal(image.status, 200);
    assert.match(image.headers.get("content-type")!, /^image\/svg\+xml/);
    assert.match(
      image.headers.get("content-security-policy")!,
      /default-src 'none'.*sandbox/,
    );
    assert.equal((await fetch(`${origin}/samples/${svg.id}.html`)).status, 404);
    const frame = await fetch(`${origin}/samples/${html.id}.html`);
    assert.equal(frame.status, 200);
    assert.equal(frame.headers.get("x-frame-options"), "SAMEORIGIN");
    const framePolicy = frame.headers.get("content-security-policy")!;
    assert.match(framePolicy, /sandbox/);
    assert.match(framePolicy, /frame-ancestors 'self'/);
    assert.doesNotMatch(framePolicy, /script-src/);
    assert.equal(
      (await fetch(`${origin}/samples/${imported.id}.svg`)).status,
      404,
    );

    const gallery = await fetch(origin + "/samples");
    assert.match(
      gallery.headers.get("content-security-policy")!,
      /frame-src 'self'/,
    );
    const galleryHtml = await gallery.text();
    assert.match(galleryHtml, /Badge &lt;one&gt;/);
    assert.match(galleryHtml, new RegExp(`src="/samples/${svg.id}\\.svg"`));
    assert.match(
      galleryHtml,
      new RegExp(
        `<iframe class="sample-frame" sandbox src="/samples/${html.id}\\.html"`,
      ),
    );
    assert.ok(
      galleryHtml.includes(
        `/commerce?service=svg-graphic&amp;ref=sample-${svg.id}`,
      ),
    );
    assert.doesNotMatch(galleryHtml, /starter\.svg|<script/);
    const home = await (await fetch(origin + "/")).text();
    assert.match(home, /href="\/samples">Samples</);
    assert.doesNotMatch(home, /<img/);
    // Other pages keep the strict policy.
    assert.doesNotMatch(
      (await fetch(origin + "/")).headers.get("content-security-policy")!,
      /frame-src|unsafe-inline/,
    );

    // Tracked requests.
    const request = (body: Record<string, unknown>) =>
      fetch(origin + "/api/modules/business/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    assert.equal((await request({ ref: "share" })).status, 202);
    assert.equal((await request({ ref: `sample-${svg.id}` })).status, 202);
    assert.equal((await request({ ref: "Not Valid" })).status, 202);
    assert.equal((await request({})).status, 202);
    const counts = (
      (await (await get("/api/operator/share")).json()) as {
        counts: Record<string, number>;
      }
    ).counts;
    assert.equal(counts.share, 1);
    assert.equal(counts.sample, 1);
    assert.equal(counts.total, 2);
    assert.equal((await request({ ref: "friend-0a1b2c3d4e5f" })).status, 202);
    const shared = (await (await get("/api/operator/share")).json()) as {
      referrals: unknown;
    };
    assert.deepEqual(shared.referrals, [
      { ref: "friend-0a1b2c3d4e5f", requests: 1 },
    ]);
    assert.match(
      await (await get("/operator/share")).text(),
      /<code>friend-0a1b2c3d4e5f<\/code>/,
    );
    const activity = await (await get("/operator/activity")).text();
    assert.match(activity, /You sent a reply to a lead/);
    assert.match(activity, /You published a sample/);
    const script = await (await fetch(origin + "/commerce.js")).text();
    assert.match(script, /function linkRef\(\)/);
    assert.match(script, /ref=friend-/);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    f.close();
  }
});
