import { commercePage, commerceScript, integrationsPage } from "./commerce.ts";
import {
  listSnapshots,
  publishedPilot,
  retractSnapshot,
  takeSnapshot,
  verifyChain,
} from "../runtime/pilot-snapshots.ts";
import { pilotPage } from "./pilot-page.ts";
import { readFileSync } from "node:fs";
// Served from the repository's public/ directory in both src/ and dist/ layouts.
const geistFont = readFileSync(
  new URL("../../public/fonts/geist-variable.woff2", import.meta.url),
);
import {
  pilotOperatorPage,
  type OperatorPilotData,
} from "./pilot-operator-page.ts";
import { pilotReadiness, pilotReport } from "../runtime/pilot.ts";
import {
  applyProfile,
  approveFirstPlan,
  currentProfile,
  latestPlanDecision,
  profileFromInput,
  proposeFirstPlan,
  saveAgent,
  sourceUrls,
} from "../runtime/agent.ts";
import { setupGuide } from "../runtime/setup.ts";
import { activityTimeline } from "../runtime/timeline.ts";
import { activityPage, startPage, type StartData } from "./agent-pages.ts";
import {
  outsideLedgerCosts,
  recordAttestation,
  recordCost,
  usdToMicros,
  type CostCategory,
} from "../runtime/pilot-records.ts";
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
  env?: NodeJS.ProcessEnv;
}) {
  const { db, config, secrets, services } = context;
  const env = context.env ?? process.env;
  const startData = async (): Promise<StartData> => {
    const runId =
      config.publicRunId && /^[a-f0-9-]{36}$/.test(config.publicRunId)
        ? config.publicRunId
        : null;
    const run = runId ? store.run(runId) : undefined;
    // Read the summary once; readiness and the plan both use it.
    let summary = null;
    if (run)
      try {
        summary = await services.economy.summary(runId!);
      } catch {
        summary = null;
      }
    const funded = Boolean(summary);
    const readiness = await pilotReadiness({
      env,
      config,
      operatorPasswordConfigured: Boolean(secrets.operatorPasswordHash),
      services,
      db,
      runId,
      summary,
    });
    const profile = run ? currentProfile(db, runId!) : null;
    const applied = run
      ? (db
          .prepare(
            "SELECT kind,at,detail FROM runtime_events WHERE run_id=? AND kind IN ('agent.applied','agent.apply_failed') ORDER BY id DESC LIMIT 1",
          )
          .get(runId!) as
          | { kind: string; at: number; detail: string }
          | undefined)
      : undefined;
    return {
      runId,
      running: run?.state === "active" || run?.state === "waiting",
      guide: setupGuide(db, run ? runId : null, readiness),
      profile,
      applied: applied
        ? {
            ok: applied.kind === "agent.applied",
            detail: applied.detail,
            at: new Date(Number(applied.at)).toISOString(),
          }
        : null,
      plan:
        profile && run
          ? await proposeFirstPlan({
              services,
              profile,
              runState: run.state,
              funded,
            })
          : null,
      decision: run ? latestPlanDecision(db, runId!) : null,
    };
  };
  const pilotData = async (): Promise<OperatorPilotData | null> => {
    const runId = config.publicRunId;
    if (!runId || !store.run(runId)) return null;
    return {
      runId,
      readiness: await pilotReadiness({
        env,
        config,
        operatorPasswordConfigured: Boolean(secrets.operatorPasswordHash),
        services,
        db,
        runId,
      }),
      report: await pilotReport({ db, services, runId, view: "private" }),
      costs: outsideLedgerCosts(db, runId, true).records ?? [],
      snapshots: listSnapshots(db, runId),
      chain: verifyChain(db, runId),
    };
  };
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
        "default-src 'none'; style-src 'self'; font-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
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
        if (method === "GET" && path === "/fonts/geist-variable.woff2") {
          response.writeHead(200, {
            "Content-Type": "font/woff2",
            "Cache-Control": "public, max-age=31536000, immutable",
          });
          response.end(geistFont);
          return;
        }
        if (method === "GET" && path === "/healthz") {
          send(200, { ok: true, service: "web", profile: config.profile });
          return;
        }
        if (method === "GET" && (path === "/pilot" || path === "/api/pilot")) {
          // Only operator-published, unretracted snapshots; never a live read.
          const data =
            config.publicRunId && store.run(config.publicRunId)
              ? publishedPilot(db, config.publicRunId)
              : null;
          if (path === "/pilot") send(200, pilotPage(data), "text/html");
          else send(200, data ?? { latest: null, timeline: [], chain: null });
          return;
        }
        if (method === "GET" && (path === "/" || path === "/api/public")) {
          const data = await publicProjection(
            store,
            services,
            config.dependencyMs ?? 5000,
            config.publicRunId,
            config.publicRunId && store.run(config.publicRunId)
              ? currentProfile(db, config.publicRunId)
              : null,
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
        if (
          method === "GET" &&
          path === "/operator" &&
          session &&
          config.publicRunId &&
          /^[a-f0-9-]{36}$/.test(config.publicRunId) &&
          !(
            store.run(config.publicRunId) &&
            currentProfile(db, config.publicRunId)
          )
        ) {
          // First sign-in: the agent is configured but not created yet.
          redirect("/operator/start");
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
          const formLogin = path === "/operator/login";
          if (
            !sessions.allowAttempt(request.socket.remoteAddress ?? "unknown")
          ) {
            const error = "Too many sign-in attempts; retry after 15 minutes";
            if (formLogin) send(429, loginPage(true, error), "text/html");
            else send(429, { error });
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
            if (formLogin)
              send(
                401,
                loginPage(true, "That password didn't match. Try again."),
                "text/html",
              );
            else send(401, { error: "Invalid credentials" });
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
        if (
          method === "GET" &&
          (path === "/operator/start" || path === "/api/operator/start")
        ) {
          const data = await startData();
          if (path === "/operator/start")
            send(200, startPage(data, session.csrf), "text/html");
          else send(200, data);
          return;
        }
        if (
          method === "GET" &&
          (path === "/operator/activity" || path === "/api/operator/activity")
        ) {
          const runId = config.publicRunId;
          const data =
            runId && store.run(runId) ? activityTimeline(db, runId) : null;
          if (path === "/operator/activity")
            send(
              200,
              activityPage(
                data,
                runId && data
                  ? (currentProfile(db, runId)?.name ?? null)
                  : null,
              ),
              "text/html",
            );
          else send(data ? 200 : 404, data ?? { error: "No agent run" });
          return;
        }
        if (
          method === "GET" &&
          (path === "/operator/pilot" || path === "/api/operator/pilot")
        ) {
          const data = await pilotData();
          if (path === "/operator/pilot")
            send(200, pilotOperatorPage(data, session.csrf), "text/html");
          else send(data ? 200 : 404, data ?? { error: "No pilot run" });
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
        if (route === "/operator/agent" || route === "/operator/agent/plan") {
          const fail = (message: string) => {
            if (api) send(400, { error: message });
            else
              send(
                400,
                operatorFormErrorPage({ error: message }, "/operator/start"),
                "text/html",
              );
          };
          const runId = config.publicRunId;
          if (!runId || !/^[a-f0-9-]{36}$/.test(runId)) {
            fail(
              "Run npm run services:configure and restart the website first",
            );
            return;
          }
          if (route === "/operator/agent") {
            let saved;
            try {
              saved = saveAgent({
                db,
                config,
                runId,
                profile: profileFromInput(input),
              });
            } catch (error) {
              fail(
                error instanceof Error
                  ? error.message.slice(0, 500)
                  : "Invalid agent settings",
              );
              return;
            }
            const applied = await applyProfile(services, runId, saved.profile);
            store.event(
              runId,
              applied.applied ? "agent.applied" : "agent.apply_failed",
              applied.detail,
            );
            if (api) send(saved.created ? 201 : 200, { ...saved, applied });
            else redirect("/operator/start");
            return;
          }
          const profile = currentProfile(db, runId);
          const run = store.run(runId);
          if (!profile || !run) {
            fail("Create the agent first");
            return;
          }
          let funded = false;
          try {
            funded = Boolean(await services.economy.summary(runId));
          } catch {
            funded = false;
          }
          // Recompute the plan server-side; only available items can be approved.
          const plan = await proposeFirstPlan({
            services,
            profile,
            runState: run.state,
            funded,
          });
          const requested = Array.isArray(input.items)
            ? input.items.filter(
                (item): item is string => typeof item === "string",
              )
            : Object.keys(input)
                .filter((key) => key.startsWith("item:") && input[key])
                .map((key) => key.slice(5));
          const selected = plan.items
            .filter((item) => item.available && requested.includes(item.id))
            .map((item) => item.id);
          if (!selected.length) {
            fail("Choose at least one available step");
            return;
          }
          let sources: string[] = [];
          if (selected.includes("discovery"))
            try {
              sources = sourceUrls(input.sources);
            } catch (error) {
              fail(error instanceof Error ? error.message : "Invalid sources");
              return;
            }
          const results = await approveFirstPlan({
            db,
            services,
            runId,
            profile,
            plan,
            selected,
            sources,
          });
          if (api) send(200, { results });
          else redirect("/operator/start");
          return;
        }
        const pilotAction =
          /^\/operator\/pilot\/(attest|cost|snapshot|retract)$/.exec(route);
        if (pilotAction) {
          const runId = config.publicRunId;
          const text = (name: string) =>
            typeof input[name] === "string" ? (input[name] as string) : "";
          let result: unknown;
          try {
            if (!runId || !store.run(runId))
              throw new Error("No pilot run is configured");
            if (pilotAction[1] === "attest")
              result = recordAttestation(db, {
                runId,
                stance:
                  text("stance") === "withdrawn" ? "withdrawn" : "affirmed",
                evidenceRef: text("evidence"),
                note: text("note"),
              });
            else if (pilotAction[1] === "cost") {
              const amountMicros = usdToMicros(text("amount"));
              const common = {
                runId,
                amountMicros,
                evidenceRef: text("evidence"),
                note: text("note"),
              };
              result = text("corrects")
                ? recordCost(db, {
                    ...common,
                    correctsId: /^[1-9][0-9]{0,15}$/.test(text("corrects"))
                      ? Number(text("corrects"))
                      : NaN,
                  })
                : recordCost(db, {
                    ...common,
                    category: text("category") as CostCategory,
                    periodStart: text("from"),
                    periodEnd: text("to"),
                  });
            } else if (pilotAction[1] === "snapshot")
              result = await takeSnapshot({
                db,
                services,
                runId,
                publish: input.publish === true || input.publish === "true",
              });
            else {
              const id = text("snapshotId") || String(input.snapshotId ?? "");
              if (!/^[1-9][0-9]{0,15}$/.test(id))
                throw new Error("Snapshot id required");
              result = retractSnapshot(db, runId, Number(id));
            }
          } catch (error) {
            const message =
              error instanceof Error
                ? error.message.slice(0, 500)
                : "Invalid request";
            if (api) send(400, { error: message });
            else
              send(
                400,
                operatorFormErrorPage({ error: message }, "/operator/pilot"),
                "text/html",
              );
            return;
          }
          if (api) send(200, result);
          else redirect("/operator/pilot");
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
