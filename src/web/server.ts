import { commercePage, commerceScript, integrationsPage } from "./commerce.ts";
import { callDependency, DependencyTimeout } from "../runtime/dependencies.ts";
import { RequestError } from "./errors.ts";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { DatabaseSync } from "node:sqlite";
import type { Json, RuntimeConfig, ServerSecrets } from "../contracts/index.ts";
import type { Services } from "../modules/registry.ts";
import { Store, EventConflict } from "../runtime/store.ts";
import { Sessions } from "../auth/sessions.ts";
import { validPasswordHash, verifyPassword } from "../auth/password.ts";
import { privateProjection, publicProjection } from "./projections.ts";
import { agentGraphProjection } from "./agent-graph.ts";
import { thinkingScript } from "./thinking-network.ts";
import {
  css,
  loginPage,
  operatorPage,
  operatorFormErrorPage,
  publicPage,
} from "./pages.ts";
async function body(
  request: IncomingMessage,
  capture?: (bytes: Buffer) => void,
): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > 16384) throw new RequestError(413, "Request too large");
    chunks.push(bytes);
  }
  const raw = Buffer.concat(chunks);
  capture?.(raw);
  let value: string;
  try {
    value = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  } catch {
    throw new RequestError(400, "Invalid UTF-8 body");
  }
  const type = request.headers["content-type"]?.split(";")[0];
  if (type === "application/x-www-form-urlencoded")
    return Object.fromEntries(new URLSearchParams(value));
  if (type === "application/json") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value || "{}");
    } catch {
      throw new RequestError(400, "Malformed JSON body");
    }
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
      return parsed as Record<string, unknown>;
  }
  throw new RequestError(400, "Expected JSON object or form");
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
  return createServer(
    { requestTimeout: 15000, headersTimeout: 10000 },
    async (request, response) => {
      response.setHeader("Cache-Control", "no-store");
      response.setHeader("X-Content-Type-Options", "nosniff");
      response.setHeader("Referrer-Policy", "same-origin");
      response.setHeader("X-Frame-Options", "DENY");
      response.setHeader(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
      );
      const send = (
        status: number,
        value: unknown,
        type = "application/json",
      ) => {
        response.writeHead(status, {
          "Content-Type": `${type}; charset=utf-8`,
        });
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
        if (method === "GET" && path === "/commerce") {
          send(200, commercePage(), "text/html");
          return;
        }
        if (method === "GET" && path === "/commerce.js") {
          send(200, commerceScript, "text/javascript");
          return;
        }
        if (method === "GET" && path === "/style.css") {
          send(200, css, "text/css");
          return;
        }
        if (method === "GET" && path === "/thinking-network.js") {
          send(200, thinkingScript, "text/javascript");
          return;
        }
        if (method === "GET" && path === "/api/public/graph") {
          send(200, agentGraphProjection(store, config.publicRunId));
          return;
        }
        if (method === "GET" && path === "/healthz") {
          send(200, { ok: true, service: "web", profile: config.profile });
          return;
        }
        if (method === "GET" && (path === "/" || path === "/api/public")) {
          const data = await publicProjection(
            store,
            services,
            config.dependencyMs ?? 5000,
            config.publicRunId,
          );
          send(
            200,
            path === "/"
              ? services.website.renderPublic
                ? await callDependency(
                    "website.render",
                    () => services.website.renderPublic!(data),
                    config.dependencyMs ?? 5000,
                  )
                : publicPage(data)
              : data,
            path === "/" ? "text/html" : "application/json",
          );
          return;
        }
        const session = secrets.operatorPasswordHash
          ? sessions.get(request)
          : null;
        const operatorPageRoute = (services.operatorPages ?? []).find(
          (page) => page.path === path,
        );
        if (method === "GET" && operatorPageRoute) {
          if (!session) {
            send(
              401,
              loginPage(
                !!secrets.operatorPasswordHash,
                "Sign in to open the private business console.",
              ),
              "text/html",
            );
            return;
          }
          const html = await callDependency(
            "module.operatorPage",
            () =>
              Promise.resolve(
                operatorPageRoute.render({
                  query: Object.fromEntries(
                    new URL(request.url!, config.publicOrigin).searchParams,
                  ),
                  csrf: session.csrf,
                }),
              ),
            config.dependencyMs ?? 5000,
          );
          if (sessions.get(request)?.tokenHash !== session.tokenHash) {
            send(
              401,
              loginPage(
                !!secrets.operatorPasswordHash,
                "Your session expired. Sign in again.",
              ),
              "text/html",
            );
            return;
          }
          send(200, html, "text/html");
          return;
        }
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
          let rawBody: Buffer | undefined;
          const input =
            method === "POST"
              ? await body(request, (bytes) => {
                  rawBody = bytes;
                })
              : {};
          if (
            moduleRoute.access === "operator" &&
            sessions.get(request)?.tokenHash !== session!.tokenHash
          ) {
            send(401, { error: "Operator session revoked or expired" });
            return;
          }
          if (
            moduleRoute.access === "operator" &&
            method === "POST" &&
            (request.headers["x-csrf-token"] ?? input.csrf) !== session!.csrf
          ) {
            send(403, { error: "CSRF token required" });
            return;
          }
          const result = await callDependency(
            "module.request",
            () =>
              moduleRoute.handle({
                body: (moduleRoute.access === "operator"
                  ? Object.fromEntries(
                      Object.entries(input).filter(([key]) => key !== "csrf"),
                    )
                  : input) as Json,
                ...(moduleRoute.access === "public" && rawBody
                  ? { rawBody }
                  : {}),
                remoteAddress: request.socket.remoteAddress ?? "unknown",
                query: Object.fromEntries(
                  new URL(request.url!, config.publicOrigin).searchParams,
                ),
                headers: Object.fromEntries(
                  Object.entries(request.headers)
                    .filter(([key]) => key !== "x-csrf-token")
                    .map(([key, value]) => {
                      const text = Array.isArray(value)
                        ? value.join(",")
                        : (value ?? "");
                      return [
                        key,
                        key === "cookie"
                          ? text
                              .split(";")
                              .map((part) => part.trim())
                              .filter(
                                (part) => !part.startsWith("widemind_session="),
                              )
                              .join("; ")
                          : text,
                      ];
                    }),
                ),
                operator: moduleRoute.access === "operator" && !!session,
              }),
            config.dependencyMs ?? 5000,
          );
          if (
            !Number.isInteger(result.status) ||
            result.status < 200 ||
            result.status > 599
          )
            throw new Error("Invalid module response");
          if (
            moduleRoute.access === "operator" &&
            method === "POST" &&
            moduleRoute.operatorRedirect &&
            request.headers["content-type"]?.split(";")[0] ===
              "application/x-www-form-urlencoded"
          ) {
            const destination = new URL(
              moduleRoute.operatorRedirect,
              config.publicOrigin,
            );
            if (
              typeof input.runId === "string" &&
              /^[a-f0-9-]{36}$/.test(input.runId)
            )
              destination.searchParams.set("runId", input.runId);
            const views = [
              "overview",
              "opportunities",
              "orders",
              "finances",
              "recovery",
              "settings",
            ];
            if (typeof input.view === "string" && views.includes(input.view))
              destination.searchParams.set("view", input.view);
            const target = destination.pathname + destination.search;
            if (result.status >= 200 && result.status < 300) redirect(target);
            else
              send(
                result.status,
                operatorFormErrorPage(result.body, target),
                "text/html",
              );
            return;
          }
          send(result.status, result.body);
          return;
        }
        if (method === "GET" && path === "/operator") {
          send(
            200,
            session
              ? operatorPage(
                  privateProjection(store, services, config.profile),
                  session.csrf,
                  config.profile,
                  (services.operatorPages ?? []).map((page) => page.path),
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
        if (
          method === "POST" &&
          request.headers.origin !== config.publicOrigin
        ) {
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
          if (
            !sessions.allowAttempt(request.socket.remoteAddress ?? "unknown")
          ) {
            send(429, {
              error: "Too many sign-in attempts; retry after 15 minutes",
            });
            return;
          }
          const input = await body(request);
          if (
            typeof input.password !== "string" ||
            !(await verifyPassword(
              input.password,
              secrets.operatorPasswordHash,
            ))
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
        if (method === "GET" && path === "/operator/integrations") {
          send(200, integrationsPage(), "text/html");
          return;
        }
        if (method === "GET" && path === "/api/operator/runtime") {
          send(200, store.runtimeStatus(config.profile));
          return;
        }
        if (method === "GET" && path === "/api/operator/status") {
          send(200, {
            ...privateProjection(store, services, config.profile),
            csrf: session.csrf,
          });
          return;
        }
        if (method !== "POST") {
          send(404, { error: "Not found" });
          return;
        }
        const input = await body(request);
        if (sessions.get(request)?.tokenHash !== session.tokenHash) {
          send(401, { error: "Operator session revoked or expired" });
          return;
        }
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
        const eventRoute = /^\/operator\/runs\/([a-f0-9-]{36})\/events$/.exec(
          route,
        );
        if (eventRoute) {
          if (!store.run(eventRoute[1]!)) {
            send(404, { error: "Unknown run" });
            return;
          }
          if (
            typeof input.eventId !== "string" ||
            typeof input.kind !== "string"
          ) {
            send(400, { error: "Event ID and kind required" });
            return;
          }
          let delivered;
          try {
            const payload =
              typeof input.payload === "string"
                ? JSON.parse(input.payload)
                : (input.payload ?? null);
            delivered = store.deliverEvent(eventRoute[1]!, {
              source: "operator",
              accountRef: "operator",
              externalEventId: input.eventId,
              kind: input.kind,
              payload: payload as Json,
            });
          } catch (error) {
            throw new RequestError(
              error instanceof EventConflict ? 409 : 400,
              "Invalid or conflicting event delivery",
            );
          }
          if (api) send(delivered.duplicate ? 200 : 202, delivered);
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
      } catch (error) {
        if (!response.headersSent) {
          response.writeHead(
            error instanceof RequestError
              ? error.status
              : error instanceof DependencyTimeout
                ? 503
                : 500,
            { "Content-Type": "application/json" },
          );
          response.end(
            JSON.stringify({
              error:
                error instanceof RequestError
                  ? error.message
                  : error instanceof DependencyTimeout
                    ? "Dependency timed out; outcome may be pending"
                    : "Server dependency failed; inspect private server configuration",
            }),
          );
        } else response.end();
      }
    },
  );
}
