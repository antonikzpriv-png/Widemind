import test from "node:test";
import assert from "node:assert/strict";
import { webServer } from "../src/web/server.ts";
import { fixture } from "./helpers.ts";
import { hashPassword } from "../src/auth/password.ts";
test("commerce shell is public and integration controls require operator authentication", async () => {
  const f = fixture();
  const server = webServer({
    ...f,
    secrets: {
      operatorPasswordHash: await hashPassword("fixture-shell-password"),
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
    const page = await fetch(origin + "/commerce");
    assert.equal(page.status, 200);
    const html = await page.text();
    assert.match(html, /Request work/);
    assert.match(html, /name="title"/);
    assert.doesNotMatch(html, /name="scope"/);
    assert.match(
      page.headers.get("content-security-policy")!,
      /script-src 'self'/,
    );
    assert.equal((await fetch(origin + "/operator/integrations")).status, 401);
    const login = await fetch(origin + "/api/operator/login", {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ password: "fixture-shell-password" }),
    });
    const cookie = login.headers.get("set-cookie")!.split(";")[0]!;
    const privatePage = await fetch(origin + "/operator/integrations", {
      headers: { Cookie: cookie },
    });
    assert.equal(privatePage.status, 200);
    assert.match(await privatePage.text(), /Connected operations/);
    const script = await fetch(origin + "/commerce.js");
    assert.equal(script.status, 200);
    assert.match(script.headers.get("content-type")!, /text\/javascript/);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    f.close();
  }
});
