import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { fixture } from "./helpers.ts";
import { money } from "../src/contracts/index.ts";
import { webServer } from "../src/web/server.ts";
import { hashPassword } from "../src/auth/password.ts";

test("a retired lease cannot own a recreated idle job with the same worker owner", () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const lease = f.store.acquire("same-worker", Date.now(), 3000)!;
    f.store.finish(lease, "waiting", "Idle", null, f.store.run(id)!.revision);
    f.store.control(id, "wake");
    const next = f.store.acquire("same-worker", Date.now(), 3000)!;
    assert.ok(next);
    assert.equal(
      f.store.owned(lease),
      false,
      "Old lease identity must remain fenced after queue recreation",
    );
  } finally {
    f.close();
  }
});
test("transport rejection after dispatch remains scheduled for reconciliation", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    f.services.tools.execute = async () => {
      throw new Error("provider connection lost");
    };
    await f.runtime.tick();
    assert.equal(f.store.actions(id)[0]?.state, "unresolved");
    assert.equal(f.store.run(id)?.state, "waiting");
    assert.ok(f.store.run(id)?.next_wake_at);
  } finally {
    f.close();
  }
});
test("money rejects JavaScript number input rather than silently converting it", () => {
  assert.throws(() => money(5000000 as unknown as string));
});
test("action input is bounded in UTF-8 bytes before any authorization or tool call", async () => {
  const f = fixture();
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    const select = f.services.business.selectWork;
    f.services.business.selectWork = async (snapshot) => {
      const result = await select(snapshot);
      if (result.kind === "work")
        result.candidate.input = { text: "漢".repeat(24000) };
      return result;
    };
    await f.runtime.tick();
    assert.equal(f.store.run(id)?.state, "failed");
    assert.equal(f.store.actions(id).length, 0);
  } finally {
    f.close();
  }
});
test("a stalled free dependency yields its lease instead of blocking the whole worker", async () => {
  const f = fixture();
  let release!: () => void;
  try {
    const id = f.store.create("demo");
    f.store.control(id, "resume");
    Object.assign(f.config, { dependencyMs: 40 });
    f.services.economy.summary = async () => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      return null;
    };
    let finished = false;
    const tick = f.runtime.tick().then(() => {
      finished = true;
    });
    await delay(160);
    const bounded = finished;
    release();
    await tick;
    assert.equal(
      bounded,
      true,
      "Dependency deadline must allow other jobs to run",
    );
    assert.equal(f.store.run(id)?.state, "waiting");
  } finally {
    f.close();
  }
});
test("logout revokes a mutation already waiting for its request body", async () => {
  const f = fixture();
  const server = webServer({
    ...f,
    secrets: {
      operatorPasswordHash: await hashPassword("disposable-audit-password"),
    },
  });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const origin = `http://127.0.0.1:${address.port}`;
    f.config.publicOrigin = origin;
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "disposable-audit-password" }),
    });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    let request!: http.ClientRequest;
    const result = new Promise<number>((resolve) => {
      request = http.request(
        origin + "/api/operator/runs",
        {
          method: "POST",
          headers: {
            Origin: origin,
            "Content-Type": "application/json",
            Cookie: cookie,
            "x-csrf-token": csrf,
          },
        },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode!));
        },
      );
      request.write("{");
    });
    await delay(50);
    const logout = await fetch(origin + "/api/operator/logout", {
      method: "POST",
      headers: {
        Origin: origin,
        "Content-Type": "application/json",
        Cookie: cookie,
        "x-csrf-token": csrf,
      },
      body: "{}",
    });
    assert.equal(logout.status, 200);
    request.end("}");
    assert.equal(
      await result,
      401,
      "Session must be revalidated after reading a body",
    );
    assert.equal(f.store.runs().length, 0);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});

test("chunked UTF-8 request bodies preserve multibyte event data", async () => {
  const f = fixture();
  f.services.routes.push({
    path: "/api/modules/fixture/echo",
    method: "POST",
    access: "public",
    handle: async (input) => ({ status: 200, body: input.body }),
  });
  const server = webServer({ ...f, secrets: { operatorPasswordHash: null } });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    assert.ok(address && typeof address !== "string");
    const payload = Buffer.from(JSON.stringify({ text: "漢字" }));
    const split = payload.indexOf(Buffer.from("漢")) + 1;
    let request!: http.ClientRequest;
    const response = new Promise<string>((resolve) => {
      request = http.request(
        `http://127.0.0.1:${address.port}/api/modules/fixture/echo`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
        (res) => {
          let text = "";
          res.on("data", (chunk) => {
            text += chunk;
          });
          res.on("end", () => resolve(text));
        },
      );
    });
    request.write(payload.subarray(0, split));
    await delay(10);
    request.end(payload.subarray(split));
    assert.deepEqual(JSON.parse(await response), { text: "漢字" });
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
