import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { DatabaseSync } from "node:sqlite";
import type { Json, RuntimeConfig, ServerSecrets } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { Store } from "../runtime/store.ts";
import { Sessions } from "../auth/sessions.ts";
import { validPasswordHash, verifyPassword } from "../auth/password.ts";
import { privateProjection, publicProjection } from "./projections.ts";
import { css, loginPage, operatorPage, publicPage } from "./pages.ts";
async function body(
  request: IncomingMessage,
): Promise<Record<string, unknown>> {
  let value = "";
  for await (const chunk of request) {
    value += chunk;
    if (Buffer.byteLength(value) > 16384) throw new Error("Request too large");
  }
  const type = request.headers["content-type"]?.split(";")[0];
  if (type === "application/x-www-form-urlencoded")
    return Object.fromEntries(new URLSearchParams(value));
  if (type === "application/json") {
    const parsed: unknown = JSON.parse(value || "{}");
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      return parsed as Record<string, unknown>;
  }
  throw new Error("Expected JSON object or form");
}
export function webServer(context: {
  db: DatabaseSync;
  config: RuntimeConfig;
  secrets: ServerSecrets;
  services: Services;
}) {
  const { db, config, secrets, services } = context;
  if (
    secrets.operatorPasswordHash &&
    !validPasswordHash(secrets.operatorPasswordHash)
  )
    throw new Error("Invalid operator password hash configuration");
  const store = new Store(db),
    sessions = new Sessions(db, secrets.operatorPasswordHash ?? "disabled");
  return createServer(async (request, response) => {
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader("Referrer-Policy", "same-origin");
    response.setHeader("X-Frame-Options", "DENY");
    response.setHeader(
      "Content-Security-Policy",
      "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    );
    const send = (
      status: number,
      value: unknown,
      type = "application/json",
    ) => {
      response.writeHead(status, { "Content-Type": `${type}; charset=utf-8` });
      response.end(
        type === "application/json" ? JSON.stringify(value) : String(value),
      );
    };
    const redirect = (path: string) => {
      response.writeHead(303, { Location: path });
      response.end();
    };
    try {
      const path = new URL(request.url ?? "/", config.publicOrigin).pathname;
      const method = request.method ?? "GET";
      if (method === "GET" && path === "/style.css") {
        send(200, css, "text/css");
        return;
      }
      if (method === "GET" && path === "/healthz") {
        send(200, { ok: true, service: "web", profile: config.profile });
        return;
      }
      if (method === "GET" && (path === "/" || path === "/api/public")) {
        const data = await publicProjection(store, services);
        send(
          200,
          path === "/"
            ? services.website.renderPublic
              ? await services.website.renderPublic(data)
              : publicPage(data)
            : data,
          path === "/" ? "text/html" : "application/json",
        );
        return;
      }
      const session = secrets.operatorPasswordHash
        ? sessions.get(request)
        : null;
      const moduleRoute = services.routes.find(
        (route) => route.path === path && route.method === method,
      );
      if (moduleRoute) {
        if (moduleRoute.access === "operator" && !session) {
          send(401, { error: "Operator authentication required" });
          return;
        }
        if (
          moduleRoute.access === "operator" &&
          method === "POST" &&
          request.headers.origin !== config.publicOrigin
        ) {
          send(403, { error: "Same-origin request required" });
          return;
        }
        const input = method === "POST" ? await body(request) : {};
        if (
          moduleRoute.access === "operator" &&
          method === "POST" &&
          (request.headers["x-csrf-token"] ?? input.csrf) !== session!.csrf
        ) {
          send(403, { error: "CSRF token required" });
          return;
        }
        const result = await moduleRoute.handle({
          body: input as Json,
          query: Object.fromEntries(
            new URL(request.url!, config.publicOrigin).searchParams,
          ),
          headers: Object.fromEntries(
            Object.entries(request.headers).map(([key, value]) => [
              key,
              Array.isArray(value) ? value.join(",") : (value ?? ""),
            ]),
          ),
          operator: moduleRoute.access === "operator" && !!session,
        });
        if (
          !Number.isInteger(result.status) ||
          result.status < 200 ||
          result.status > 599
        )
          throw new Error("Invalid module response");
        send(result.status, result.body);
        return;
      }
      if (method === "GET" && path === "/operator") {
        send(
          200,
          session
            ? operatorPage(
                privateProjection(store, services),
                session.csrf,
                config.profile,
              )
            : loginPage(!!secrets.operatorPasswordHash),
          "text/html",
        );
        return;
      }
      const operator =
        path.startsWith("/api/operator/") || path.startsWith("/operator/");
      if (!operator) {
        send(404, { error: "Not found" });
        return;
      }
      if (method === "POST" && request.headers.origin !== config.publicOrigin) {
        send(403, { error: "Same-origin request required" });
        return;
      }
      if (
        method === "POST" &&
        (path === "/operator/login" || path === "/api/operator/login")
      ) {
        if (!secrets.operatorPasswordHash) {
          send(503, { error: "Operator authentication is not configured" });
          return;
        }
        if (!sessions.allowAttempt(request.socket.remoteAddress ?? "unknown")) {
          send(429, {
            error: "Too many sign-in attempts; retry after 15 minutes",
          });
          return;
        }
        const input = await body(request);
        if (
          typeof input.password !== "string" ||
          !(await verifyPassword(input.password, secrets.operatorPasswordHash))
        ) {
          send(401, { error: "Invalid credentials" });
          return;
        }
        const created = sessions.create();
        response.setHeader(
          "Set-Cookie",
          `widemind_session=${created.token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800${config.secureCookies ? "; Secure" : ""}`,
        );
        if (path.startsWith("/api/")) send(200, { csrf: created.csrf });
        else redirect("/operator");
        return;
      }
      if (!session) {
        send(401, { error: "Operator authentication required" });
        return;
      }
      if (method === "GET" && path === "/api/operator/status") {
        send(200, {
          ...privateProjection(store, services),
          csrf: session.csrf,
        });
        return;
      }
      if (method !== "POST") {
        send(404, { error: "Not found" });
        return;
      }
      const input = await body(request);
      if ((request.headers["x-csrf-token"] ?? input.csrf) !== session.csrf) {
        send(403, { error: "CSRF token required" });
        return;
      }
      if (path === "/operator/logout" || path === "/api/operator/logout") {
        sessions.revoke(session);
        response.setHeader(
          "Set-Cookie",
          `widemind_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0${config.secureCookies ? "; Secure" : ""}`,
        );
        if (path.startsWith("/api/")) send(200, { ok: true });
        else redirect("/operator");
        return;
      }
      const api = path.startsWith("/api/");
      const route = path.replace(/^\/api/, "");
      if (route === "/operator/runs") {
        const runId = store.create(config.profile);
        if (api) send(201, { runId });
        else redirect("/operator");
        return;
      }
      const match =
        /^\/operator\/runs\/([a-f0-9-]{36})\/(resume|pause|stop|wake)$/.exec(
          route,
        );
      if (match) {
        if (!store.run(match[1]!)) {
          send(404, { error: "Unknown run" });
          return;
        }
        store.control(
          match[1]!,
          match[2] as "resume" | "pause" | "stop" | "wake",
        );
        if (api) send(200, { ok: true });
        else redirect("/operator");
        return;
      }
      send(404, { error: "Not found" });
    } catch {
      if (!response.headersSent) {
        response.writeHead(400, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            error:
              "Request or dependency failed; inspect private server configuration",
          }),
        );
      } else response.end();
    }
  });
}
