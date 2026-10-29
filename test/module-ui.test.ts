import assert from "node:assert/strict";
import test from "node:test";
import { hashPassword } from "../src/auth/password.ts";
import { registerModules } from "../src/modules/registry.ts";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";

test("module private pages and form redirects cannot escape their registered namespace", () => {
  const f = fixture("foundation");
  try {
    for (const path of [
      "/operator",
      "/operator/modules/another/console",
      "/operator/modules/example/../console",
      "https://attacker.invalid/operator/modules/example/console",
    ]) {
      assert.throws(() =>
        registerModules(f, [
          {
            id: "example",
            contractVersion: 1,
            create: () => ({
              operatorPages: [{ path, render: () => "private fixture" }],
            }),
          },
        ]),
      );
    }
    const page = "/operator/modules/example/console";
    for (const operatorRedirect of ["/operator", "//attacker.invalid", page]) {
      assert.throws(() =>
        registerModules(f, [
          {
            id: "example",
            contractVersion: 1,
            create: () => ({
              routes: [
                {
                  path: "/api/modules/example/command",
                  method: "POST",
                  access: "operator",
                  operatorRedirect,
                  handle: async () => ({ status: 200, body: null }),
                },
              ],
            }),
          },
        ]),
      );
    }
    assert.throws(() =>
      registerModules(f, [
        {
          id: "example",
          contractVersion: 1,
          create: () => ({
            operatorPages: [{ path: page, render: () => "" }],
            routes: [
              {
                path: "/api/modules/example/public",
                method: "POST",
                access: "public",
                operatorRedirect: page,
                handle: async () => ({ status: 200, body: null }),
              },
            ],
          }),
        },
      ]),
    );
  } finally {
    f.close();
  }
});

test("private module rendering and HTML commands retain core session, Origin and CSRF enforcement", async () => {
  const f = fixture("foundation");
  const page = "/operator/modules/example/console";
  let rendered = 0;
  let commanded = 0;
  let renderedCsrf = "";
  let forwardedBody: unknown;
  const services = registerModules(f, [
    {
      id: "example",
      contractVersion: 1,
      create: () => ({
        operatorPages: [
          {
            path: page,
            render: ({ csrf }) => {
              rendered++;
              renderedCsrf = csrf;
              return "<!doctype html><title>Private module fixture</title>private-marker";
            },
          },
        ],
        routes: [
          {
            path: "/api/modules/example/command",
            method: "POST",
            access: "operator",
            operatorRedirect: page,
            handle: async ({ body, operator }) => {
              assert.equal(operator, true);
              commanded++;
              forwardedBody = body;
              return { status: 200, body: { ok: true } };
            },
          },
        ],
      }),
    },
  ]);
  const server = webServer({
    ...f,
    services,
    secrets: {
      operatorPasswordHash: await hashPassword(
        "module-ui-explicit-fixture-password",
      ),
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
    const unauthenticated = await fetch(origin + page, { redirect: "manual" });
    assert.ok([303, 401].includes(unauthenticated.status));
    assert.equal(rendered, 0);
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "module-ui-explicit-fixture-password" }),
    });
    assert.equal(login.status, 200);
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const { csrf } = (await login.json()) as { csrf: string };
    const response = await fetch(origin + page + "?csrf=forged-query-value", {
      headers: { Cookie: cookie },
    });
    assert.equal(response.status, 200);
    assert.match(await response.text(), /private-marker/);
    assert.equal(renderedCsrf, csrf);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match(
      response.headers.get("content-security-policy")!,
      /default-src 'none'/,
    );
    assert.doesNotMatch(
      await (await fetch(origin + "/")).text(),
      /private-marker/,
    );
    const post = (fields: Record<string, string>, requestOrigin = origin) =>
      fetch(origin + "/api/modules/example/command", {
        method: "POST",
        redirect: "manual",
        headers: {
          Origin: requestOrigin,
          Cookie: cookie,
          "Content-Type": "application/x-www-form-urlencoded",
          Accept: "text/html",
        },
        body: new URLSearchParams(fields),
      });
    assert.equal(
      (await post({ csrf }, "https://attacker.invalid")).status,
      403,
    );
    assert.equal((await post({})).status, 403);
    assert.equal(commanded, 0);
    const accepted = await post({
      csrf,
      runId: "not-a-return-url",
      view: "https://attacker.invalid",
    });
    assert.equal(accepted.status, 303);
    assert.equal(
      new URL(accepted.headers.get("location")!, origin).origin,
      origin,
    );
    assert.equal(
      new URL(accepted.headers.get("location")!, origin).pathname,
      page,
    );
    assert.equal(commanded, 1);
    assert.equal((forwardedBody as Record<string, unknown>).csrf, undefined);
    const api = await fetch(origin + "/api/modules/example/command", {
      method: "POST",
      headers: {
        Cookie: cookie,
        Origin: origin,
        "X-CSRF-Token": csrf,
        "Content-Type": "application/json",
      },
      body: "{}",
      redirect: "manual",
    });
    assert.equal(api.status, 200);
    assert.deepEqual(await api.json(), { ok: true });
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    f.close();
  }
});
